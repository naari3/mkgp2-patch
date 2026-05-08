// Diagnose: which JOBJs in scene_data share a struct with the public root
// joints? Tracks struct offset (writer-side identity) for each visited JOBJ
// and reports any matches against the public symbol table.
//   usage: dotnet-script hsd_dump_scene_tree.csx -- <path-to-dat>
#r "C:\Users\naari\src\github.com\Ploaj\HSDLib\HSDRaw\bin\Release\netstandard2.0\HSDRaw.dll"

using System;
using System.Collections.Generic;
using HSDRaw;
using HSDRaw.Common;

if (Args.Count < 1) { Console.WriteLine("usage: <path>"); return; }
string path = Args[0];
var file = new HSDRawFile(path);
file.Save(path + ".tmp"); // populate _structCacheToOffset
System.IO.File.Delete(path + ".tmp");

// build offset -> root name map
var offToRoot = new Dictionary<int, string>();
foreach (var r in file.Roots)
{
    int off = file.GetOffsetFromStruct(r.Data._s);
    offToRoot[off] = r.Name;
}

int idx = 0;
void Walk(HSD_JOBJ j, int depth)
{
    while (j != null)
    {
        int off = file.GetOffsetFromStruct(j._s);
        string label = offToRoot.TryGetValue(off, out var name) ? $"== alias root '{name}' ==" : "";
        int dobjCount = 0;
        var d = j.Dobj;
        while (d != null) { dobjCount++; d = d.Next; }
        string pad = new string(' ', depth * 2);
        Console.WriteLine($"{pad}JOBJ#{idx++} off=0x{off:X6} flags={j.Flags} dobj={dobjCount} {label}");
        if (j.Child != null) Walk(j.Child, depth + 1);
        j = j.Next;
    }
}

foreach (var root in file.Roots)
{
    Console.WriteLine($"\n=== Root '{root.Name}' ===");
    if (root.Data is HSD_SOBJ sobj)
    {
        if (sobj.JOBJDescs?.Array != null)
        for (int i = 0; i < sobj.JOBJDescs.Array.Length; i++)
        {
            Console.WriteLine($"  JObjDesc[{i}].RootJoint:");
            var rj = sobj.JOBJDescs.Array[i].RootJoint;
            if (rj != null) Walk(rj, 1);
        }
    }
    else if (root.Data is HSD_JOBJ rj)
    {
        Walk(rj, 1);
    }
    else
    {
        Console.WriteLine($"  (data type {root.Data?.GetType().Name})");
    }
}
