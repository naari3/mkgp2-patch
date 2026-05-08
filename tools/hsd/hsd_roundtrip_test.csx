// Phase 0 sanity: open vanilla .dat and immediately Save() as new file.
// Diff dump (scene tree + roots) to confirm HSDLib writer doesn't mangle
// hierarchy on a no-op round-trip. Bytes may differ (struct reordering,
// dedup) but semantic dump should match.
//   usage: dotnet-script hsd_roundtrip_test.csx -- <input.dat> <output.dat>
#r "C:\Users\naari\src\github.com\Ploaj\HSDLib\HSDRaw\bin\Release\netstandard2.0\HSDRaw.dll"

using System;
using HSDRaw;

if (Args.Count < 2) { Console.WriteLine("usage: <in.dat> <out.dat>"); return; }
string inPath = Args[0];
string outPath = Args[1];

var file = new HSDRawFile(inPath);
Console.WriteLine($"loaded: {inPath}  roots={file.Roots.Count}");

file.Save(outPath);
Console.WriteLine($"saved : {outPath}");

var verify = new HSDRawFile(outPath);
Console.WriteLine($"reload: roots={verify.Roots.Count}");

var inSize = new System.IO.FileInfo(inPath).Length;
var outSize = new System.IO.FileInfo(outPath).Length;
Console.WriteLine($"sizes : in={inSize} out={outSize} delta={outSize - inSize}");
