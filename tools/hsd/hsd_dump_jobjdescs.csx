// List scene_data.JObjDescs in detail (length, each desc -> RootJoint flags / dobj count)
// usage: dotnet-script hsd_dump_jobjdescs.csx -- <path>.dat
#r "C:\Users\naari\src\github.com\Ploaj\HSDLib\HSDRaw\bin\Release\netstandard2.0\HSDRaw.dll"

using System;
using HSDRaw;
using HSDRaw.Common;

if (Args.Count < 1) { Console.WriteLine("usage: hsd_dump_jobjdescs.csx <path>"); return; }

string path = Args[0];
var f = new HSDRawFile(path);
Console.WriteLine($"File: {path}");
foreach (var r in f.Roots) {
    if (r.Data is HSD_SOBJ sobj) {
        Console.WriteLine($"Root '{r.Name}'  type=SObj");
        var arr = sobj.JOBJDescs;
        Console.WriteLine($"  JOBJDescs.Length = {arr?.Length ?? 0}");
        if (arr != null) {
            for (int i = 0; i < arr.Length; i++) {
                var jd = arr[i];
                if (jd == null) {
                    Console.WriteLine($"  [{i}] (null entry)");
                    continue;
                }
                var rj = jd.RootJoint;
                int dcount = 0;
                if (rj != null) {
                    var d = rj.Dobj; while (d != null) { dcount++; d = d.Next; }
                }
                Console.WriteLine($"  [{i}] RootJoint: {(rj == null ? "(null)" : $"flags={rj.Flags} dobj={dcount}")}");
            }
        }
        // Also show the raw scene_data top: HSD_SOBJ struct typically has
        // JObjDescs at +0x00, CObjDescs at +0x04, LObjDescs at +0x08, FogDescs at +0x0C, ...
        var s = sobj._s;
        if (s != null) {
            var bytes = s.GetData() ?? new byte[0];
            Console.Write($"  raw[{bytes.Length}] = ");
            for (int i = 0; i < Math.Min(0x20, bytes.Length); i++) Console.Write($"{bytes[i]:X2} ");
            Console.WriteLine();
        }
    } else {
        Console.WriteLine($"Root '{r.Name}'  type={r.Data?.GetType().Name}");
    }
}
