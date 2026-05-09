// Dump scene_data full structure including raw refs at +0x00..+0x10
// (JObjDescs / CObjDescs / LObjDescs / FogDescs) plus first DObj's MObj/POBJ.
// usage: dotnet-script hsd_dump_sobj_full.csx -- <path>.dat
#r "C:\Users\naari\src\github.com\Ploaj\HSDLib\HSDRaw\bin\Release\netstandard2.0\HSDRaw.dll"

using System;
using System.Linq;
using HSDRaw;
using HSDRaw.Common;

if (Args.Count < 1) { Console.WriteLine("usage: <path>"); return; }

string path = Args[0];
var f = new HSDRawFile(path);
Console.WriteLine($"File: {path}");

void DumpRaw(string label, HSDStruct s, int n)
{
    if (s == null) { Console.WriteLine($"  {label}: (null)"); return; }
    var bytes = s.GetData() ?? new byte[0];
    Console.Write($"  {label} raw[{bytes.Length}] = ");
    for (int i = 0; i < Math.Min(n, bytes.Length); i++) Console.Write($"{bytes[i]:X2} ");
    Console.WriteLine();
}

foreach (var r in f.Roots) {
    Console.WriteLine($"\n=== Root '{r.Name}' type={r.Data?.GetType().Name} ===");
    if (!(r.Data is HSD_SOBJ sobj)) continue;

    var s = sobj._s;
    DumpRaw("SObj struct", s, 0x40);

    // SObj struct layout: each 4-byte slot is a reference (HSDStruct ptr).
    // Convention: +0x00=JObjDescs, +0x04=CObjDescs, +0x08=LObjDescs, +0x0C=FogDescs
    string[] names = { "JObjDescs", "CObjDescs", "LObjDescs", "FogDescs" };
    for (int off = 0; off < 0x10; off += 4) {
        var sub = s?.GetReference<HSDAccessor>(off);
        if (sub != null) {
            DumpRaw($"  [+0x{off:X2}={names[off/4]}]", sub._s, 0x40);
        } else {
            Console.WriteLine($"  [+0x{off:X2}={names[off/4]}]: (null)");
        }
    }

    // JObjDescs.RootJoint walk first DObj
    var rj = sobj.JOBJDescs?[0]?.RootJoint;
    if (rj == null) continue;
    var d = rj.Dobj;
    int dIdx = 0;
    while (d != null && dIdx < 5) {
        Console.WriteLine($"\n  --- DObj[{dIdx}] ---");
        DumpRaw("DObj struct", d._s, 0x18);
        var m = d.Mobj;
        if (m != null) {
            Console.WriteLine($"    MObj.RenderFlags = {m.RenderFlags} (0x{(uint)m.RenderFlags:X8})");
            DumpRaw("MObj struct", m._s, 0x20);
            if (m.Material != null) {
                var mat = m.Material;
                Console.WriteLine($"    Mat DIF=({mat.DIF_R},{mat.DIF_G},{mat.DIF_B},{mat.DIF_A}) Alpha={mat.Alpha} Shininess={mat.Shininess}");
                DumpRaw("Material struct", mat._s, 0x40);
            }
            if (m.PEDesc != null) {
                var pe = m.PEDesc;
                Console.WriteLine($"    PEDesc Flags={pe.Flags} BlendMode={pe.BlendMode} Src={pe.SrcFactor} Dst={pe.DstFactor}");
                DumpRaw("PEDesc struct", pe._s, 0x20);
            } else {
                Console.WriteLine($"    PEDesc: (null)");
            }
            if (m.Textures != null) {
                Console.WriteLine($"    has Textures (TObj)");
            }
        }
        var p = d.Pobj;
        if (p != null) {
            Console.WriteLine($"    POBJ flags={p.Flags} (0x{(uint)p.Flags:X8}) DLsize={p.DisplayListSize}");
            DumpRaw("POBJ struct", p._s, 0x20);
            // Attributes is HSDAccessor; iterate via reflection-free path
            // by reading raw bytes — vanilla csx handles this elsewhere.
            // skip in this dump.
        }
        d = d.Next; dIdx++;
    }
}
