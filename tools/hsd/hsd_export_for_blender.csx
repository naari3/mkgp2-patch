// dotnet-script: extract HSD DAT to a Blender-friendly JSON + PNG bundle.
//
// usage:
//   dotnet script hsd_export_for_blender.csx -- <dat-path> <out-dir>
//
// Output:
//   <out-dir>/scene.json          One file per .dat. Schema documented below.
//   <out-dir>/tex/<sha1>.png      Unique textures, deduped by SHA-1.
//
// Vertex positions and normals are pre-baked to world space via JObj forward
// kinematics + SingleBoundJOBJ, matching HSDRawViewer's ModelExporter.  Joint
// hierarchy is also recorded so a future write-back path can reconstruct it.
// Alias roots (multiple roots sharing one HSDStruct) are deduped: each unique
// JObj is emitted once, and root names that point at the shared struct are
// listed under "joint_aliases".
#r "C:\Users\naari\src\github.com\Ploaj\HSDLib\HSDRaw\bin\Release\netstandard2.0\HSDRaw.dll"
#r "nuget: SixLabors.ImageSharp, 3.1.5"

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Numerics;
using System.Security.Cryptography;
using System.Text.Json;
using HSDRaw;
using HSDRaw.Common;
using HSDRaw.GX;
using HSDRaw.Tools;
using SixLabors.ImageSharp;
using SixLabors.ImageSharp.PixelFormats;

if (Args.Count < 2) { Console.WriteLine("usage: hsd_export_for_blender.csx <dat-path> <out-dir>"); return; }

string datPath = Args[0];
string outDir = Args[1];
string texDir = Path.Combine(outDir, "tex");
Directory.CreateDirectory(texDir);

var file = new HSDRawFile(datPath);
Console.WriteLine($"File: {datPath}");
Console.WriteLine($"Roots: {file.Roots.Count}");

string Sha1(byte[] b) { using var s = SHA1.Create(); return BitConverter.ToString(s.ComputeHash(b)).Replace("-", "").Substring(0, 12); }

// ---------- HSDRawViewer-compatible Euler XYZ → Matrix4x4 (row-vector) ----------
static Matrix4x4 MatrixFromEuler(float X, float Y, float Z) {
    float sx = MathF.Sin(X), cx = MathF.Cos(X);
    float sy = MathF.Sin(Y), cy = MathF.Cos(Y);
    float sz = MathF.Sin(Z), cz = MathF.Cos(Z);
    return new Matrix4x4(
        cy*cz,                cy*sz,                -sy,    0,
        cz*sx*sy - cx*sz,     sz*sx*sy + cx*cz,     sx*cy,  0,
        cz*cx*sy + sx*sz,     sz*cx*sy - sx*cz,     cx*cy,  0,
        0, 0, 0, 1
    );
}
static Matrix4x4 LocalMatrix(HSD_JOBJ j)
    => Matrix4x4.CreateScale(j.SX, j.SY, j.SZ) *
       MatrixFromEuler(j.RX, j.RY, j.RZ) *
       Matrix4x4.CreateTranslation(j.TX, j.TY, j.TZ);

// ---------- JSON DTOs ----------
record JointDto(
    string id, string name, List<string> flags,
    float[] translation, float[] rotation, float[] scale,
    float[] world_matrix, string parent, List<string> children);

record TextureDto(string id, string file, int width, int height, string format);

record TextureRefDto(string tex_id, string tex_map_id, string wrap_s, string wrap_t,
                     int repeat_s, int repeat_t, string mag_filter, string color_op, string alpha_op,
                     float blending);

record MaterialDto(string id, string render_flags, uint render_flags_raw,
                   int[] diffuse_rgba, float alpha, List<TextureRefDto> textures);

record PrimitiveDto(string type, int[] indices);

record MeshDto(
    string id, string joint, string single_bind_joint, string material,
    string cull, string source_path,
    List<float[]> vertices, List<float[]> uvs, List<float[]> normals, List<float[]> colors,
    List<PrimitiveDto> primitives);

record SceneDto(
    string source_dat, string tex_dir,
    List<TextureDto> textures, List<MaterialDto> materials,
    List<JointDto> joints, Dictionary<string, string> joint_aliases,
    List<MeshDto> meshes);

// ---------- State ----------
var textures = new List<TextureDto>();
var materials = new List<MaterialDto>();
var joints = new List<JointDto>();
var meshes = new List<MeshDto>();
var jointAliases = new Dictionary<string, string>();

var imageIdBySha = new Dictionary<string, string>();   // sha → "F63770FB0547" (== id)
var jobjIdByStruct = new Dictionary<HSDStruct, string>(); // alias dedup
var worldByJobj = new Dictionary<HSDStruct, Matrix4x4>();
int jointCounter = 0, materialCounter = 0, meshCounter = 0;

// ---------- Texture intern ----------
string InternTexture(HSD_TOBJ t) {
    var img = t.ImageData;
    if (img == null || img.ImageData == null) return null;
    string sha = Sha1(img.ImageData);
    if (!imageIdBySha.ContainsKey(sha)) {
        try {
            byte[] rgba = t.GetDecodedImageData(0);
            int w = img.Width, h = img.Height;
            if (rgba != null && rgba.Length == w*h*4) {
                // HSDLib quirk: CMP and RGBA8 decoders pack BGRA, others pack RGBA.
                if (img.Format == GXTexFmt.CMP || img.Format == GXTexFmt.RGBA8)
                    for (int i = 0; i + 2 < rgba.Length; i += 4) { var tmp = rgba[i]; rgba[i] = rgba[i+2]; rgba[i+2] = tmp; }
                using var bmp = SixLabors.ImageSharp.Image.LoadPixelData<Rgba32>(rgba, w, h);
                bmp.SaveAsPng(Path.Combine(texDir, $"{sha}.png"));
                textures.Add(new TextureDto(sha, $"tex/{sha}.png", w, h, img.Format.ToString()));
                imageIdBySha[sha] = sha;
            } else {
                Console.WriteLine($"  WARN: tex {sha} decoded length mismatch ({rgba?.Length}/{w*h*4})");
                return null;
            }
        } catch (Exception ex) {
            Console.WriteLine($"  ERR: tex {sha} decode failed: {ex.Message}");
            return null;
        }
    }
    return sha;
}

// ---------- Material intern ----------
string EmitMaterial(HSD_MOBJ m) {
    string id = $"mat_{materialCounter++}";
    var texList = new List<TextureRefDto>();
    var t = m.Textures;
    while (t != null) {
        var texId = InternTexture(t);
        if (texId != null)
            texList.Add(new TextureRefDto(
                texId, t.TexMapID.ToString(),
                t.WrapS.ToString(), t.WrapT.ToString(),
                t.RepeatS, t.RepeatT,
                t.MagFilter.ToString(),
                t.ColorOperation.ToString(), t.AlphaOperation.ToString(),
                t.Blending
            ));
        t = t.Next;
    }
    int[] difRgba = new int[]{255,255,255,255};
    float alpha = 1f;
    if (m.Material != null) {
        difRgba = new int[]{ m.Material.DIF_R, m.Material.DIF_G, m.Material.DIF_B, m.Material.DIF_A };
        alpha = m.Material.Alpha;
    }
    materials.Add(new MaterialDto(
        id, m.RenderFlags.ToString(), (uint)m.RenderFlags,
        difRgba, alpha, texList));
    return id;
}

// ---------- World matrix builder ----------
void BuildWorld(HSD_JOBJ j, Matrix4x4 parentWorld) {
    while (j != null) {
        if (j._s == null || worldByJobj.ContainsKey(j._s)) { j = j.Next; continue; }
        var world = LocalMatrix(j) * parentWorld;
        worldByJobj[j._s] = world;
        if (j.Child != null) BuildWorld(j.Child, world);
        j = j.Next;
    }
}

// ---------- JObj/DObj/PObj walker ----------
string EmitJoint(HSD_JOBJ j, string parentId) {
    if (j._s != null && jobjIdByStruct.TryGetValue(j._s, out var existing))
        return existing;
    string id = $"jobj_{jointCounter++}";
    if (j._s != null) jobjIdByStruct[j._s] = id;

    var w = worldByJobj.TryGetValue(j._s, out var ww) ? ww : Matrix4x4.Identity;
    var wm = new float[] {
        w.M11, w.M12, w.M13, w.M14,
        w.M21, w.M22, w.M23, w.M24,
        w.M31, w.M32, w.M33, w.M34,
        w.M41, w.M42, w.M43, w.M44
    };
    var children = new List<string>();
    joints.Add(new JointDto(
        id, null,
        new List<string>(j.Flags.ToString().Split(", ").Where(s => !string.IsNullOrWhiteSpace(s))),
        new float[] { j.TX, j.TY, j.TZ },
        new float[] { j.RX, j.RY, j.RZ },
        new float[] { j.SX, j.SY, j.SZ },
        wm, parentId, children));

    if (j.Child != null) {
        var c = j.Child;
        while (c != null) {
            children.Add(EmitJoint(c, id));
            c = c.Next;
        }
    }
    return id;
}

void EmitMeshes(HSD_JOBJ j) {
    while (j != null) {
        if (j._s == null) { j = j.Next; continue; }
        if (!jobjIdByStruct.TryGetValue(j._s, out var jId)) { j = j.Next; continue; }
        var d = j.Dobj;
        int dIdx = 0;
        while (d != null) {
            string matId = d.Mobj != null ? EmitMaterial(d.Mobj) : null;
            var p = d.Pobj;
            int pIdx = 0;
            while (p != null) {
                var dl = new GX_DisplayList(p);
                Matrix4x4 parentT = worldByJobj.TryGetValue(j._s, out var pw) ? pw : Matrix4x4.Identity;
                Matrix4x4 sbT = Matrix4x4.Identity;
                string sbId = null;
                if (p.SingleBoundJOBJ != null && p.SingleBoundJOBJ._s != null
                    && worldByJobj.TryGetValue(p.SingleBoundJOBJ._s, out var sw)) {
                    sbT = sw;
                    jobjIdByStruct.TryGetValue(p.SingleBoundJOBJ._s, out sbId);
                }
                Matrix4x4 finalT = parentT * sbT;
                var rotOnly = finalT; rotOnly.M41 = 0; rotOnly.M42 = 0; rotOnly.M43 = 0;

                bool hasUV = dl.Vertices.Any(v => v.TEX0.X != 0 || v.TEX0.Y != 0);
                bool hasNRM = dl.Vertices.Any(v => v.NRM.X != 0 || v.NRM.Y != 0 || v.NRM.Z != 0);
                bool hasCLR = dl.Attributes.Any(a => a.AttributeName == GXAttribName.GX_VA_CLR0);

                var verts = new List<float[]>(dl.Vertices.Count);
                var uvs = hasUV ? new List<float[]>(dl.Vertices.Count) : null;
                var nrms = hasNRM ? new List<float[]>(dl.Vertices.Count) : null;
                var cols = hasCLR ? new List<float[]>(dl.Vertices.Count) : null;

                foreach (var v in dl.Vertices) {
                    var pos = Vector3.Transform(new Vector3(v.POS.X, v.POS.Y, v.POS.Z), finalT);
                    verts.Add(new float[] { pos.X, pos.Y, pos.Z });
                    if (hasUV) uvs.Add(new float[] { v.TEX0.X, v.TEX0.Y });
                    if (hasNRM) {
                        var n = Vector3.Normalize(Vector3.TransformNormal(new Vector3(v.NRM.X, v.NRM.Y, v.NRM.Z), rotOnly));
                        nrms.Add(new float[] { n.X, n.Y, n.Z });
                    }
                    if (hasCLR) cols.Add(new float[] { v.CLR0.R, v.CLR0.G, v.CLR0.B, v.CLR0.A });
                }

                var prims = new List<PrimitiveDto>();
                int cursor = 0;
                foreach (var pg in dl.Primitives) {
                    int n = pg.Indices.Length;
                    var idx = new int[n];
                    for (int i = 0; i < n; i++) idx[i] = cursor + i;
                    prims.Add(new PrimitiveDto(pg.PrimitiveType.ToString(), idx));
                    cursor += n;
                }

                string cull = "NONE";
                if (p.Flags.HasFlag(POBJ_FLAG.CULLBACK) && p.Flags.HasFlag(POBJ_FLAG.CULLFRONT)) cull = "BOTH";
                else if (p.Flags.HasFlag(POBJ_FLAG.CULLBACK)) cull = "BACK";
                else if (p.Flags.HasFlag(POBJ_FLAG.CULLFRONT)) cull = "FRONT";

                meshes.Add(new MeshDto(
                    $"mesh_{meshCounter++}", jId, sbId, matId, cull,
                    $"{jId}/DObj{dIdx}/PObj{pIdx}",
                    verts, uvs, nrms, cols, prims));
                p = p.Next; pIdx++;
            }
            d = d.Next; dIdx++;
        }
        if (j.Child != null) EmitMeshes(j.Child);
        j = j.Next;
    }
}

// ---------- Drive: scene_data first, then independent roots; record alias names ----------
HSDRootNode sceneDataRoot = null;
foreach (var r in file.Roots) if (r.Name == "scene_data") { sceneDataRoot = r; break; }

if (sceneDataRoot != null && sceneDataRoot.Data is HSD_SOBJ sobj && sobj.JOBJDescs != null) {
    for (int i = 0; i < sobj.JOBJDescs.Length; i++) {
        var jd = sobj.JOBJDescs[i];
        if (jd?.RootJoint == null) continue;
        BuildWorld(jd.RootJoint, Matrix4x4.Identity);
        EmitJoint(jd.RootJoint, null);
        EmitMeshes(jd.RootJoint);
    }
}
foreach (var r in file.Roots) {
    if (r.Name == "scene_data") continue;
    if (r.Data is HSD_JOBJ rj) {
        BuildWorld(rj, Matrix4x4.Identity);
        if (rj._s != null && jobjIdByStruct.TryGetValue(rj._s, out var existId)) {
            // alias of an already-emitted joint — record name → id mapping only
            jointAliases[r.Name] = existId;
        } else {
            string newId = EmitJoint(rj, null);
            jointAliases[r.Name] = newId;
            EmitMeshes(rj);
        }
    }
}

// ---------- Serialize ----------
var scene = new SceneDto(
    Path.GetFileName(datPath),
    "tex",
    textures, materials, joints, jointAliases, meshes
);
var jsonOpts = new JsonSerializerOptions { WriteIndented = false };
File.WriteAllText(Path.Combine(outDir, "scene.json"), JsonSerializer.Serialize(scene, jsonOpts));

Console.WriteLine($"\nWrote: {Path.Combine(outDir, "scene.json")}");
Console.WriteLine($"  textures: {textures.Count}");
Console.WriteLine($"  materials: {materials.Count}");
Console.WriteLine($"  joints: {joints.Count}  (aliases: {jointAliases.Count})");
Console.WriteLine($"  meshes: {meshes.Count}");
long jsonSize = new FileInfo(Path.Combine(outDir, "scene.json")).Length;
long texTotal = 0;
foreach (var f in Directory.GetFiles(texDir, "*.png")) texTotal += new FileInfo(f).Length;
Console.WriteLine($"  scene.json: {jsonSize:N0} bytes");
Console.WriteLine($"  textures total: {texTotal:N0} bytes");
