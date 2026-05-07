// dotnet-script: dump HSD DAT texture (TObj) details across all roots, with
// identical-image detection (sha1 over ImageData buffer) so we can see when
// the same texture is reused vs. embedded fresh per material.
//
// usage: dotnet script hsd_dump_textures.csx -- <path-to-dat> [root-name-substring]
#r "C:\Users\naari\src\github.com\Ploaj\HSDLib\HSDRaw\bin\Release\netstandard2.0\HSDRaw.dll"

using System;
using System.Collections.Generic;
using System.Linq;
using System.Security.Cryptography;
using HSDRaw;
using HSDRaw.Common;
using HSDRaw.GX;

if (Args.Count < 1) { Console.WriteLine("usage: hsd_dump_textures.csx <path> [root-substr]"); return; }

string path = Args[0];
string filter = Args.Count >= 2 ? Args[1] : null;
var file = new HSDRawFile(path);

Console.WriteLine($"File: {path}");
Console.WriteLine($"Roots: {file.Roots.Count}, filter={filter ?? "(none)"}");

// ImageData hash → first observed location.  Lets us see reuse.
var imageHash = new Dictionary<string, string>();
var paletteHash = new Dictionary<string, string>();
int globalTobj = 0;

string Sha1(byte[] b)
{
    if (b == null) return "<null>";
    using var sha = SHA1.Create();
    return BitConverter.ToString(sha.ComputeHash(b)).Replace("-", "").Substring(0, 12);
}

void DumpTobj(HSD_TOBJ t, string tag)
{
    int idx = 0;
    while (t != null)
    {
        var img = t.ImageData;
        var pal = t.TlutData;
        string imgHash = img != null ? Sha1(img.ImageData) : "<no-image>";
        string palHash = pal != null ? Sha1(pal.TlutData) : "<no-tlut>";

        string imgWhereFirst = "";
        if (img != null && imgHash != "<no-image>")
        {
            if (imageHash.TryGetValue(imgHash, out var first))
                imgWhereFirst = $"  REUSE-OF: {first}";
            else
                imageHash[imgHash] = $"{tag}/Tobj{idx}";
        }
        string palWhereFirst = "";
        if (pal != null && palHash != "<no-tlut>")
        {
            if (paletteHash.TryGetValue(palHash, out var first))
                palWhereFirst = $"  REUSE-OF: {first}";
            else
                paletteHash[palHash] = $"{tag}/Tobj{idx}";
        }

        Console.WriteLine($"      TOBJ#{globalTobj++}/local{idx} TexMapID={t.TexMapID} GenSrc={t.GXTexGenSrc}");
        Console.WriteLine($"        Wrap=({t.WrapS},{t.WrapT}) Repeat=({t.RepeatS},{t.RepeatT}) MagFilter={t.MagFilter}");
        Console.WriteLine($"        ColorOp={t.ColorOperation} AlphaOp={t.AlphaOperation} Blending={t.Blending}");
        Console.WriteLine($"        Flags={t.Flags} CoordType={t.CoordType}");
        Console.WriteLine($"        S=({t.SX:F3},{t.SY:F3},{t.SZ:F3}) T=({t.TX:F3},{t.TY:F3},{t.TZ:F3}) R=({t.RX:F3},{t.RY:F3},{t.RZ:F3})");
        if (img != null)
        {
            int byteLen = img.ImageData?.Length ?? 0;
            Console.WriteLine($"        Image  W={img.Width} H={img.Height} Format={img.Format} MipMap={img.MipMap} bytes={byteLen} sha1={imgHash}{imgWhereFirst}");
        }
        else
        {
            Console.WriteLine($"        Image  (null)");
        }
        if (pal != null)
        {
            int palLen = pal.TlutData?.Length ?? 0;
            Console.WriteLine($"        Tlut   Format={pal.Format} ColorCount={pal.ColorCount} bytes={palLen} sha1={palHash}{palWhereFirst}");
        }
        if (t.LOD != null)
        {
            var l = t.LOD;
            Console.WriteLine($"        LOD    MinFilter={l.MinFilter} Bias={l.Bias} BiasClamp={l.BiasClamp} EdgeLOD={l.EnableEdgeLOD} Aniso={l.Anisotropy}");
        }
        idx++;
        t = t.Next;
    }
}

int mobjGlobal = 0;
void DumpMobj(HSD_MOBJ m, string tag)
{
    if (m == null) { Console.WriteLine($"    MOBJ: (null)"); return; }
    int mIdx = mobjGlobal++;
    Console.WriteLine($"    MOBJ#{mIdx} RenderFlags={m.RenderFlags} (0x{(int)m.RenderFlags:X8})");
    if (m.Material != null)
    {
        var mat = m.Material;
        Console.WriteLine($"      Mat DIF=({mat.DIF_R},{mat.DIF_G},{mat.DIF_B},{mat.DIF_A}) Alpha={mat.Alpha} Shininess={mat.Shininess}");
    }
    if (m.PEDesc != null)
    {
        var pe = m.PEDesc;
        Console.WriteLine($"      PE  Flags={pe.Flags} BlendMode={pe.BlendMode} Src={pe.SrcFactor} Dst={pe.DstFactor} DepthFn={pe.DepthFunction}");
    }
    if (m.Textures != null) DumpTobj(m.Textures, $"{tag}/MObj{mIdx}");
    else Console.WriteLine($"      (no textures)");
}

int dobjGlobal = 0, jobjGlobal = 0;
void WalkDobj(HSD_DOBJ d, string tag)
{
    while (d != null)
    {
        int dIdx = dobjGlobal++;
        Console.WriteLine($"  DOBJ#{dIdx}");
        DumpMobj(d.Mobj, $"{tag}/DObj{dIdx}");
        d = d.Next;
    }
}

void WalkJobj(HSD_JOBJ j, int depth, string tag)
{
    while (j != null)
    {
        int jIdx = jobjGlobal++;
        string pad = new string(' ', depth * 2);
        Console.WriteLine($"{pad}JOBJ#{jIdx} flags={j.Flags}");
        if (j.Dobj != null) WalkDobj(j.Dobj, $"{tag}/JObj{jIdx}");
        if (j.Child != null) WalkJobj(j.Child, depth + 1, $"{tag}/JObj{jIdx}");
        j = j.Next;
    }
}

foreach (var root in file.Roots)
{
    if (filter != null && !root.Name.Contains(filter)) continue;
    Console.WriteLine($"\n=== Root '{root.Name}' data={root.Data?.GetType().Name} ===");
    if (root.Data is HSD_JOBJ rj) WalkJobj(rj, 0, root.Name);
    else if (root.Data is HSD_SOBJ sobj && sobj.JOBJDescs != null)
    {
        for (int i = 0; i < sobj.JOBJDescs.Length; i++)
        {
            Console.WriteLine($"  JObjDesc[{i}]:");
            var jd = sobj.JOBJDescs[i];
            if (jd?.RootJoint != null) WalkJobj(jd.RootJoint, 1, $"{root.Name}/JObjDesc{i}");
            else Console.WriteLine($"    (null RootJoint)");
        }
    }
    else Console.WriteLine($"  (unknown root type)");
}

Console.WriteLine($"\nUnique image hashes: {imageHash.Count}");
Console.WriteLine($"Unique palette hashes: {paletteHash.Count}");
Console.WriteLine($"Total TObj walked: {globalTobj}");
