// dotnet-script to dump HSD DAT contents via HSDLib
// usage: dotnet-script hsd_dump.csx -- <path-to-dat>
#r "C:\Users\naari\src\github.com\Ploaj\HSDLib\HSDRaw\bin\Release\netstandard2.0\HSDRaw.dll"

using System;
using System.Linq;
using HSDRaw;
using HSDRaw.Common;

if (Args.Count < 1) { Console.WriteLine("usage: hsd_dump.csx <path>"); return; }

string path = Args[0];
var file = new HSDRawFile(path);

Console.WriteLine($"File: {path}");
Console.WriteLine($"Roots: {file.Roots.Count}, References: {file.References.Count}");

int jobjIdx = 0, dobjIdx = 0, mobjIdx = 0, pobjIdx = 0;

void WalkPobj(HSD_POBJ p)
{
    while (p != null)
    {
        int dlSize = p.DisplayListSize;
        byte[] dl = p.DisplayListBuffer ?? new byte[0];
        string head = string.Join(" ", dl.Take(Math.Min(16, dl.Length)).Select(b => b.ToString("X2")));
        Console.WriteLine($"    POBJ#{pobjIdx++} flags={p.Flags} DLsize={dlSize} DLbuf[{dl.Length}] head={head}");
        p = p.Next;
    }
}

void WalkDobj(HSD_DOBJ d)
{
    while (d != null)
    {
        Console.WriteLine($"  DOBJ#{dobjIdx++} class='{d.ClassName}'");
        var m = d.Mobj;
        if (m != null)
        {
            Console.WriteLine($"    MOBJ#{mobjIdx++} RenderFlags={m.RenderFlags} (0x{(int)m.RenderFlags:X8}) TexRef={(m.Textures != null)} MatRef={(m.Material != null)} PERef={(m.PEDesc != null)}");
            if (m.Material != null)
            {
                var mat = m.Material;
                Console.WriteLine($"      Mat DIF=({mat.DIF_R},{mat.DIF_G},{mat.DIF_B},{mat.DIF_A}) Alpha={mat.Alpha} Shininess={mat.Shininess}");
            }
            if (m.PEDesc != null)
            {
                var pe = m.PEDesc;
                Console.WriteLine($"      PE Flags={pe.Flags} BlendMode={pe.BlendMode} Src={pe.SrcFactor} Dst={pe.DstFactor} DepthFn={pe.DepthFunction}");
            }
        }
        else
        {
            Console.WriteLine($"    MOBJ: (null)");
        }
        if (d.Pobj != null) WalkPobj(d.Pobj);
        d = d.Next;
    }
}

void WalkJobj(HSD_JOBJ j, int depth)
{
    while (j != null)
    {
        string pad = new string(' ', depth * 2);
        Console.WriteLine($"{pad}JOBJ#{jobjIdx++} flags={j.Flags}");
        if (j.Dobj != null) WalkDobj(j.Dobj);
        if (j.Child != null) WalkJobj(j.Child, depth + 1);
        j = j.Next;
    }
}

foreach (var root in file.Roots)
{
    Console.WriteLine($"\n=== Root '{root.Name}' data={root.Data?.GetType().Name} ===");
    if (root.Data is HSD_JOBJ rj) WalkJobj(rj, 0);
    else if (root.Data is HSD_SOBJ sobj)
    {
        if (sobj.JOBJDescs != null)
        {
            for (int i = 0; i < sobj.JOBJDescs.Length; i++)
            {
                Console.WriteLine($"  JObjDesc[{i}]:");
                var jd = sobj.JOBJDescs[i];
                if (jd?.RootJoint != null) WalkJobj(jd.RootJoint, 1);
                else Console.WriteLine($"    (null RootJoint)");
            }
        }
    }
    else Console.WriteLine($"  (unknown root type, skipping)");
}

Console.WriteLine($"\nTotals: JOBJ={jobjIdx} DOBJ={dobjIdx} MOBJ={mobjIdx} POBJ={pobjIdx}");
