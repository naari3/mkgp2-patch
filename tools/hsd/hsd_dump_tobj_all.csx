#r "C:\Users\naari\src\github.com\Ploaj\HSDLib\HSDRaw\bin\Release\netstandard2.0\HSDRaw.dll"
using System;
using HSDRaw;
using HSDRaw.Common;

if (Args.Count < 1) { Console.WriteLine("usage: dump_tobj_all.csx <file.dat>"); return; }
var f = new HSDRawFile(Args[0]);
HSD_JOBJ root = null;
foreach (var r in f.Roots)
{
    if (r.Data is HSD_JOBJ rj) { root = rj; break; }
    if (r.Data is HSD_SOBJ sobj && sobj.JOBJDescs?.Length > 0)
    {
        root = sobj.JOBJDescs[0]?.RootJoint;
        if (root != null) break;
    }
}
if (root == null) { Console.WriteLine("no JObj root"); return; }

int jobjIdx = 0;
foreach (var jobj in WalkJObjs(root))
{
    int dobjIdx = 0;
    foreach (var dobj in EnumerateDObjs(jobj))
    {
        var mobj = dobj.Mobj;
        if (mobj == null) { dobjIdx++; continue; }
        int tobjIdx = 0;
        foreach (var tobj in EnumerateTObjs(mobj))
        {
            int flags = (int)tobj.Flags;
            int coordType = flags & 0xF;
            string coordName = coordType switch {
                0 => "UV", 1 => "REFLECTION", 2 => "HILIGHT", 3 => "SHADOW", 4 => "TOON", 5 => "GRADATION", _ => $"?{coordType}"
            };
            bool bump = (flags & 0x1000000) != 0;
            string lights = "";
            if ((flags & (1<<4)) != 0) lights += "DIFFUSE,";
            if ((flags & (1<<5)) != 0) lights += "SPECULAR,";
            if ((flags & (1<<6)) != 0) lights += "AMBIENT,";
            if ((flags & (1<<7)) != 0) lights += "EXT,";
            if ((flags & (1<<8)) != 0) lights += "SHADOW,";
            int colorOp = (flags >> 16) & 0xF;
            int alphaOp = (flags >> 20) & 0xF;
            Console.WriteLine($"[J{jobjIdx} D{dobjIdx} T{tobjIdx}] flags=0x{flags:X8} coord={coordName} bump={bump} lights={lights} colorOp={colorOp} alphaOp={alphaOp}");
            Console.WriteLine($"    repeat_s={tobj.RepeatS} repeat_t={tobj.RepeatT} wrap_s={tobj.WrapS} wrap_t={tobj.WrapT} tex_gen_src={tobj.GXTexGenSrc} mag={tobj.MagFilter}");
            Console.WriteLine($"    R=({tobj.RX},{tobj.RY},{tobj.RZ}) S=({tobj.SX},{tobj.SY},{tobj.SZ}) T=({tobj.TX},{tobj.TY},{tobj.TZ})");
            tobjIdx++;
        }
        dobjIdx++;
    }
    jobjIdx++;
}

IEnumerable<HSD_JOBJ> WalkJObjs(HSD_JOBJ j)
{
    if (j == null) yield break;
    yield return j;
    foreach (var c in WalkJObjs(j.Child)) yield return c;
    foreach (var n in WalkJObjs(j.Next)) yield return n;
}
IEnumerable<HSD_DOBJ> EnumerateDObjs(HSD_JOBJ j)
{
    var d = j.Dobj;
    while (d != null) { yield return d; d = d.Next; }
}
IEnumerable<HSD_TOBJ> EnumerateTObjs(HSD_MOBJ m)
{
    var t = m.Textures;
    while (t != null) { yield return t; t = t.Next; }
}
