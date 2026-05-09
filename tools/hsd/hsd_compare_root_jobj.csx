// Detailed root-JObj diff for two .dat files.
// usage: dotnet-script hsd_compare_root_jobj.csx -- <fileA> <rootnameA> <fileB> <rootnameB>
//
// Prints, for each side: root JObj's TRS, flags, child count, dobj count,
// inverse-bind ptr, robj ptr, particle joint ptr, raw 64-byte struct dump,
// and the *first* DObj's MObj/POBJ summary. Aimed at "why does inu render
// while my_course doesn't" forensics.
#r "C:\Users\naari\src\github.com\Ploaj\HSDLib\HSDRaw\bin\Release\netstandard2.0\HSDRaw.dll"

using System;
using System.Linq;
using HSDRaw;
using HSDRaw.Common;

if (Args.Count < 4) {
    Console.WriteLine("usage: hsd_compare_root_jobj.csx <fileA> <rootnameA> <fileB> <rootnameB>");
    return;
}

void DumpJobj(string label, HSD_JOBJ j, HSDRawFile file)
{
    Console.WriteLine($"--- {label} ---");
    if (j == null) { Console.WriteLine("  (null)"); return; }
    Console.WriteLine($"  flags     = {j.Flags}  (0x{(uint)j.Flags:X8})");
    Console.WriteLine($"  TX,TY,TZ  = {j.TX,12:F4} {j.TY,12:F4} {j.TZ,12:F4}");
    Console.WriteLine($"  RX,RY,RZ  = {j.RX,12:F4} {j.RY,12:F4} {j.RZ,12:F4}");
    Console.WriteLine($"  SX,SY,SZ  = {j.SX,12:F4} {j.SY,12:F4} {j.SZ,12:F4}");
    Console.WriteLine($"  child?    = {(j.Child != null ? "yes" : "no")}  next? {(j.Next != null ? "yes" : "no")}");
    Console.WriteLine($"  inv-bind? = {(j.InverseWorldTransform != null ? "yes" : "no")}");
    Console.WriteLine($"  robj?     = {(j.RobjOrParticleJoint != null ? "yes" : "no")}");
    int dobjCount = 0;
    var d = j.Dobj;
    HSD_POBJ firstP = null; HSD_MOBJ firstM = null;
    while (d != null) {
        if (dobjCount == 0) { firstM = d.Mobj; firstP = d.Pobj; }
        dobjCount++; d = d.Next;
    }
    Console.WriteLine($"  dobj_count = {dobjCount}");
    if (firstM != null) {
        Console.WriteLine($"  dobj0.MObj.RenderFlags = {firstM.RenderFlags} (0x{(int)firstM.RenderFlags:X8})");
        if (firstM.Material != null) {
            var mat = firstM.Material;
            Console.WriteLine($"    mat DIF=({mat.DIF_R},{mat.DIF_G},{mat.DIF_B},{mat.DIF_A}) AMB=({mat.AMB_R},{mat.AMB_G},{mat.AMB_B},{mat.AMB_A}) SPC=({mat.SPC_R},{mat.SPC_G},{mat.SPC_B},{mat.SPC_A})");
            Console.WriteLine($"    mat Alpha={mat.Alpha} Shininess={mat.Shininess}");
        }
    }
    if (firstP != null) {
        Console.WriteLine($"  dobj0.POBJ.flags  = {firstP.Flags}  (0x{(uint)firstP.Flags:X8})");
        Console.WriteLine($"  dobj0.POBJ.DLsize = {firstP.DisplayListSize}");
        Console.WriteLine($"  dobj0.POBJ.NAttrs = {firstP.Attributes?.Length ?? 0}");
        if (firstP.Attributes != null) {
            foreach (var a in firstP.Attributes) {
                Console.WriteLine($"    attr name={a.AttributeName} attr_type={a.AttributeType} comp_count={a.CompCount} comp_type={a.CompType} scale={a.Scale} stride={a.Stride}");
            }
        }
    }
    // Raw struct bytes (the JOBJ struct itself, 0x40 bytes)
    var s = j._s;
    if (s != null) {
        var bytes = s.GetBytes() ?? new byte[0];
        Console.Write("  raw[0x{0:X}] = ", bytes.Length);
        for (int i = 0; i < Math.Min(0x40, bytes.Length); i++)
            Console.Write($"{bytes[i]:X2} ");
        Console.WriteLine();
    }
}

HSD_JOBJ Resolve(HSDRawFile f, string name)
{
    foreach (var r in f.Roots) {
        if (r.Name != name) continue;
        if (r.Data is HSD_JOBJ rj) return rj;
        if (r.Data is HSD_SOBJ sobj && sobj.JOBJDescs != null && sobj.JOBJDescs.Length > 0)
            return sobj.JOBJDescs[0]?.RootJoint;
    }
    return null;
}

string fa = Args[0], ra = Args[1], fb = Args[2], rb = Args[3];
var fileA = new HSDRawFile(fa);
var fileB = new HSDRawFile(fb);
Console.WriteLine($"A = {fa} root={ra}");
Console.WriteLine($"B = {fb} root={rb}");
DumpJobj($"A root  ({ra})", Resolve(fileA, ra), fileA);
DumpJobj($"B root  ({rb})", Resolve(fileB, rb), fileB);
