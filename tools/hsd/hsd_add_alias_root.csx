// dotnet-script: add an aliased public symbol (Root) to an existing JObj
// usage: dotnet-script hsd_add_alias_root.csx -- <in.dat> <out.dat> <existing_root_name> <child_index> <new_symbol_name>
// - walks scene_data.RootJoint -> .Child then .Next repeatedly, picks the Nth child
// - then adds a new HSDRootNode with the same HSDAccessor (same underlying HSDStruct)
//   so both public symbols point to identical bytes in the output file.
#r "C:\Users\naari\src\github.com\Ploaj\HSDLib\HSDRaw\bin\Release\netstandard2.0\HSDRaw.dll"

using System;
using HSDRaw;
using HSDRaw.Common;

if (Args.Count < 5)
{
    Console.WriteLine("usage: hsd_add_alias_root.csx <in.dat> <out.dat> <root_name> <child_index> <new_symbol>");
    return;
}

string inPath  = Args[0];
string outPath = Args[1];
string rootName = Args[2];
int childIdx  = int.Parse(Args[3]);
string newSym = Args[4];

var file = new HSDRawFile(inPath);

Console.WriteLine($"Opened: {inPath}");
Console.WriteLine($"Roots before: {file.Roots.Count}");

var root = file[rootName];
if (root == null) { Console.WriteLine($"root '{rootName}' not found"); return; }

HSD_JOBJ rootJoint = null;
if (root.Data is HSD_SOBJ sobj)
{
    // SOBJ -> JOBJDescs[0] -> RootJoint
    var descs = sobj.JOBJDescs;
    if (descs == null || descs.Array.Length == 0) { Console.WriteLine("no JOBJDescs"); return; }
    rootJoint = descs.Array[0].RootJoint;
}
else if (root.Data is HSD_JOBJ j)
{
    rootJoint = j;
}
else
{
    Console.WriteLine($"root accessor type {root.Data.GetType().Name} not supported");
    return;
}

if (rootJoint == null) { Console.WriteLine("rootJoint null"); return; }

// walk .Child then .Next chain, pick Nth
HSD_JOBJ target = rootJoint.Child;
for (int i = 0; i < childIdx && target != null; i++)
    target = target.Next;

if (target == null) { Console.WriteLine($"child[{childIdx}] not found"); return; }

Console.WriteLine($"Target JObj: struct len={target._s.Length}, refs={target._s.References.Count}");

// create aliased root - same accessor, so same HSDStruct, so same bytes in output
file.Roots.Add(new HSDRootNode { Name = newSym, Data = target });

Console.WriteLine($"Added root '{newSym}' aliased to existing JObj");
Console.WriteLine($"Roots after: {file.Roots.Count}");

file.Save(outPath);

Console.WriteLine($"Saved: {outPath}");

// re-open and verify both roots point to the same struct offset
var verify = new HSDRawFile(outPath);
var a = verify[rootName];
var b = verify[newSym];
Console.WriteLine($"Verify: '{rootName}' struct offset = 0x{verify.GetOffsetFromStruct(a.Data._s):X}");
// walk to the same child in the re-opened file for fair compare
HSD_JOBJ aRoot = (a.Data is HSD_SOBJ s2) ? s2.JOBJDescs.Array[0].RootJoint : (HSD_JOBJ)a.Data;
HSD_JOBJ aTarget = aRoot.Child;
for (int i = 0; i < childIdx && aTarget != null; i++) aTarget = aTarget.Next;
int aOff = verify.GetOffsetFromStruct(aTarget._s);
int bOff = verify.GetOffsetFromStruct(b.Data._s);
Console.WriteLine($"child[{childIdx}] via root : 0x{aOff:X}");
Console.WriteLine($"new symbol '{newSym}'    : 0x{bOff:X}");
Console.WriteLine(aOff == bOff ? "ALIAS OK (same offset)" : "MISMATCH!");
