// dotnet-script: extract HSD PObj geometry as OBJ + per-PObj stats.
// Applies parent JObj world transform + SingleBoundJOBJ transform per HSDRawViewer
// ModelExporter behavior, so output sits at correct world position/rotation.
//
// usage:
//   dotnet script hsd_export_pobj.csx -- <dat-path> <out-obj-path>
//                          [--filter-tex-sha1=<sha1>] [--root=<substr>]
//                          [--no-transform]   # skip transforms (debug)
//
// Walks scene_data only by default to avoid alias-root double-walk; if no
// scene_data root exists, falls back to all roots filtered by --root.
#r "C:\Users\naari\src\github.com\Ploaj\HSDLib\HSDRaw\bin\Release\netstandard2.0\HSDRaw.dll"

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Numerics;
using System.Security.Cryptography;
using System.Text;
using HSDRaw;
using HSDRaw.Common;
using HSDRaw.GX;
using HSDRaw.Tools;

if (Args.Count < 2) { Console.WriteLine("usage: hsd_export_pobj.csx <dat-path> <out-obj-path> [--filter-tex-sha1=<sha1>] [--root=<substr>] [--no-transform]"); return; }

string datPath = Args[0];
string objPath = Args[1];
string filterSha1 = null;
string rootFilter = null;
bool noTransform = false;
foreach (var a in Args.Skip(2)) {
    if (a.StartsWith("--filter-tex-sha1=")) filterSha1 = a.Substring("--filter-tex-sha1=".Length);
    if (a.StartsWith("--root="))            rootFilter = a.Substring("--root=".Length);
    if (a == "--no-transform")              noTransform = true;
}

var file = new HSDRawFile(datPath);
Console.WriteLine($"File: {datPath}");
Console.WriteLine($"Roots: {file.Roots.Count}, root-filter={rootFilter ?? "(all)"}, sha1-filter={filterSha1 ?? "(none)"}, no-transform={noTransform}");

string Sha1(byte[] b) { using var s = SHA1.Create(); return BitConverter.ToString(s.ComputeHash(b)).Replace("-", "").Substring(0, 12); }

// HSDRawViewer-compatible Euler XYZ → Matrix4x4 (row-vector convention).
static Matrix4x4 MatrixFromEuler(float X, float Y, float Z)
{
    float sx = MathF.Sin(X), cx = MathF.Cos(X);
    float sy = MathF.Sin(Y), cy = MathF.Cos(Y);
    float sz = MathF.Sin(Z), cz = MathF.Cos(Z);
    return new Matrix4x4(
        cy * cz,                 cy * sz,                 -sy,    0,
        cz * sx * sy - cx * sz,  sz * sx * sy + cx * cz,  sx*cy,  0,
        cz * cx * sy + sx * sz,  sz * cx * sy - sx * cz,  cx*cy,  0,
        0, 0, 0, 1
    );
}

static Matrix4x4 LocalMatrix(HSD_JOBJ j) {
    // S * R * T  (row-vector: vertex experiences scale, then rotation, then translation)
    var s = Matrix4x4.CreateScale(j.SX, j.SY, j.SZ);
    var r = MatrixFromEuler(j.RX, j.RY, j.RZ);
    var t = Matrix4x4.CreateTranslation(j.TX, j.TY, j.TZ);
    return s * r * t;
}

// Build JObj → world matrix dictionary for the tree rooted at root.
var worldByJobj = new Dictionary<HSD_JOBJ, Matrix4x4>();
void BuildWorldMatrices(HSD_JOBJ j, Matrix4x4 parentWorld)
{
    while (j != null)
    {
        var local = LocalMatrix(j);
        var world = local * parentWorld;
        worldByJobj[j] = world;
        if (j.Child != null) BuildWorldMatrices(j.Child, world);
        j = j.Next;
    }
}

// OBJ writer state
var obj = new StringBuilder();
int globalVOff = 0, globalVtOff = 0, globalVnOff = 0;
int matchedPobjs = 0, matchedTris = 0, matchedVerts = 0;

void EmitObj(GX_DisplayList dl, string objectName, Matrix4x4 transform)
{
    obj.AppendLine($"o {objectName}");
    bool hasUV = dl.Vertices.Any(v => v.TEX0.X != 0f || v.TEX0.Y != 0f);
    bool hasNRM = dl.Vertices.Any(v => v.NRM.X != 0f || v.NRM.Y != 0f || v.NRM.Z != 0f);

    foreach (var v in dl.Vertices) {
        var p = noTransform ? new Vector3(v.POS.X, v.POS.Y, v.POS.Z)
                            : Vector3.Transform(new Vector3(v.POS.X, v.POS.Y, v.POS.Z), transform);
        obj.AppendLine($"v {p.X:R} {p.Y:R} {p.Z:R}");
    }
    if (hasUV)
        foreach (var v in dl.Vertices)
            obj.AppendLine($"vt {v.TEX0.X:R} {(1f - v.TEX0.Y):R}");
    if (hasNRM) {
        // Normals: use 3x3 of transform (no translation).  Don't bother
        // re-orthonormalizing; scale=1 is the common case for course meshes.
        var rotOnly = transform; rotOnly.M41 = 0; rotOnly.M42 = 0; rotOnly.M43 = 0;
        foreach (var v in dl.Vertices) {
            var n = noTransform ? new Vector3(v.NRM.X, v.NRM.Y, v.NRM.Z)
                                : Vector3.Normalize(Vector3.TransformNormal(new Vector3(v.NRM.X, v.NRM.Y, v.NRM.Z), rotOnly));
            obj.AppendLine($"vn {n.X:R} {n.Y:R} {n.Z:R}");
        }
    }

    string FaceVert(int li) {
        int v  = globalVOff  + li + 1;
        int vt = globalVtOff + li + 1;
        int vn = globalVnOff + li + 1;
        if (hasUV && hasNRM) return $"{v}/{vt}/{vn}";
        if (hasUV)           return $"{v}/{vt}";
        if (hasNRM)          return $"{v}//{vn}";
        return $"{v}";
    }

    int cursor = 0;
    int triCount = 0;
    foreach (var pg in dl.Primitives) {
        int n = pg.Indices.Length;
        switch (pg.PrimitiveType) {
            case GXPrimitiveType.Triangles:
                for (int i = 0; i + 2 < n; i += 3) {
                    obj.AppendLine($"f {FaceVert(cursor + i)} {FaceVert(cursor + i + 1)} {FaceVert(cursor + i + 2)}");
                    triCount++;
                }
                break;
            case GXPrimitiveType.TriangleStrip:
                for (int i = 0; i + 2 < n; i++) {
                    if ((i & 1) == 0)
                        obj.AppendLine($"f {FaceVert(cursor + i)} {FaceVert(cursor + i + 1)} {FaceVert(cursor + i + 2)}");
                    else
                        obj.AppendLine($"f {FaceVert(cursor + i + 1)} {FaceVert(cursor + i)} {FaceVert(cursor + i + 2)}");
                    triCount++;
                }
                break;
            case GXPrimitiveType.TriangleFan:
                for (int i = 1; i + 1 < n; i++) {
                    obj.AppendLine($"f {FaceVert(cursor)} {FaceVert(cursor + i)} {FaceVert(cursor + i + 1)}");
                    triCount++;
                }
                break;
            case GXPrimitiveType.Quads:
                for (int i = 0; i + 3 < n; i += 4) {
                    obj.AppendLine($"f {FaceVert(cursor + i)} {FaceVert(cursor + i + 1)} {FaceVert(cursor + i + 2)}");
                    obj.AppendLine($"f {FaceVert(cursor + i)} {FaceVert(cursor + i + 2)} {FaceVert(cursor + i + 3)}");
                    triCount += 2;
                }
                break;
            default:
                Console.WriteLine($"      WARN: unhandled PrimitiveType {pg.PrimitiveType}");
                break;
        }
        cursor += n;
    }
    matchedTris += triCount;
    matchedVerts += dl.Vertices.Count;
    int vc = dl.Vertices.Count;
    globalVOff += vc;
    if (hasUV)  globalVtOff += vc;
    if (hasNRM) globalVnOff += vc;
}

int dobjGlobal = 0, pobjGlobal = 0;
void WalkDobj(HSD_DOBJ d, HSD_JOBJ parentJobj, string tag)
{
    int dIdx = 0;
    while (d != null) {
        bool texMatch = (filterSha1 == null);
        if (filterSha1 != null && d.Mobj?.Textures != null) {
            var t = d.Mobj.Textures;
            while (t != null) {
                if (t.ImageData?.ImageData != null && Sha1(t.ImageData.ImageData) == filterSha1) { texMatch = true; break; }
                t = t.Next;
            }
        }

        var p = d.Pobj;
        int pIdx = 0;
        while (p != null) {
            int globalP = pobjGlobal++;
            var dl = new GX_DisplayList(p);
            var attrSummary = string.Join(",", dl.Attributes.Select(a => a.AttributeName.ToString().Replace("GX_VA_", "")));
            var primSummary = string.Join(",", dl.Primitives.GroupBy(pg => pg.PrimitiveType).Select(g => $"{g.Key}x{g.Count()}"));

            // HSDRawViewer compatible: vertex_world = vertex_local * parentTransform * singleBindTransform
            Matrix4x4 parentT = (parentJobj != null && worldByJobj.TryGetValue(parentJobj, out var pw)) ? pw : Matrix4x4.Identity;
            Matrix4x4 singleBindT = Matrix4x4.Identity;
            var sb = p.SingleBoundJOBJ;
            if (sb != null && worldByJobj.TryGetValue(sb, out var sw)) singleBindT = sw;
            Matrix4x4 finalT = parentT * singleBindT;

            string mark = texMatch ? "[MATCH]" : "       ";
            string sbTag = sb != null ? " sb=Y" : "";
            Console.WriteLine($"  {mark} POBJ#{globalP} ({tag}/DObj{dIdx}/PObj{pIdx}) flags={p.Flags} verts={dl.Vertices.Count} prims=[{primSummary}] attrs=[{attrSummary}]{sbTag}");

            if (texMatch) {
                EmitObj(dl, $"POBJ_{globalP}_{tag.Replace('/', '_')}_DObj{dIdx}_PObj{pIdx}", finalT);
                matchedPobjs++;
            }
            p = p.Next; pIdx++;
        }
        d = d.Next; dIdx++;
    }
}

// HSDStruct identity tracking — alias roots share underlying _s, so we visit
// each unique struct once across all roots.
var visitedJobjs = new HashSet<HSDStruct>();
int jobjGlobal = 0;
void WalkJobjForRender(HSD_JOBJ j, string tag)
{
    while (j != null) {
        if (j._s != null && !visitedJobjs.Add(j._s)) {
            // Already walked through another root path — skip subtree.
            j = j.Next;
            continue;
        }
        int jIdx = jobjGlobal++;
        if (j.Dobj != null) WalkDobj(j.Dobj, j, $"{tag}/JObj{jIdx}");
        if (j.Child != null) WalkJobjForRender(j.Child, $"{tag}/JObj{jIdx}");
        j = j.Next;
    }
}

// Walk all roots: scene_data first (so its JObj naming gets priority for tags),
// then independent *_joint roots.  HSDStruct dedup avoids double-walking shared
// subtrees.  Build world matrices on first visit only.
var visitedForMatrix = new HashSet<HSDStruct>();
void BuildWorldMatricesDedup(HSD_JOBJ j, Matrix4x4 parentWorld)
{
    while (j != null) {
        if (j._s != null && !visitedForMatrix.Add(j._s)) { j = j.Next; continue; }
        var local = LocalMatrix(j);
        var world = local * parentWorld;
        worldByJobj[j] = world;
        if (j.Child != null) BuildWorldMatricesDedup(j.Child, world);
        j = j.Next;
    }
}

void WalkRoot(HSDRootNode root, string tag)
{
    if (root.Data is HSD_JOBJ rj) {
        BuildWorldMatricesDedup(rj, Matrix4x4.Identity);
        WalkJobjForRender(rj, tag);
    } else if (root.Data is HSD_SOBJ sobj && sobj.JOBJDescs != null) {
        for (int i = 0; i < sobj.JOBJDescs.Length; i++) {
            var jd = sobj.JOBJDescs[i];
            if (jd?.RootJoint == null) continue;
            BuildWorldMatricesDedup(jd.RootJoint, Matrix4x4.Identity);
            WalkJobjForRender(jd.RootJoint, $"{tag}/JObjDesc{i}");
        }
    }
}

// scene_data first
foreach (var r in file.Roots) if (r.Name == "scene_data") {
    Console.WriteLine($"Walking scene_data first");
    WalkRoot(r, "scene_data");
}
// then independent roots (filtered if --root=)
foreach (var r in file.Roots) {
    if (r.Name == "scene_data") continue;
    if (rootFilter != null && !r.Name.Contains(rootFilter)) continue;
    WalkRoot(r, r.Name);
}

File.WriteAllText(objPath, obj.ToString());
Console.WriteLine($"\nMatched POBJs: {matchedPobjs}, total verts: {matchedVerts}, total tris: {matchedTris}");
Console.WriteLine($"World matrices computed: {worldByJobj.Count}");
Console.WriteLine($"Wrote OBJ: {objPath}");
