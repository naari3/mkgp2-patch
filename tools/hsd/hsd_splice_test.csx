// Phase 0 sanity: programmatically splice a new HSD_JOBJ into scene_data
// hierarchy. Reproduces what HSDRawViewer GUI does when adding a child.
//   usage: dotnet-script hsd_splice_test.csx -- <input.dat> <output.dat>
//
// Splice plan (matching the user's _inu.dat layout):
//   - find scene_data.JObjDescs[0].RootJoint  (= scene root JOBJ)
//   - walk root.Child to find child[1] (the inu/opac branch)
//   - append a new HSD_JOBJ as the *last sibling* under child[1]
//
// The new JObj has no Dobj/children — pure marker. We just want to verify
// HSDLib serializes the splice correctly and the new node appears in the
// re-loaded tree.
#r "C:\Users\naari\src\github.com\Ploaj\HSDLib\HSDRaw\bin\Release\netstandard2.0\HSDRaw.dll"

using System;
using HSDRaw;
using HSDRaw.Common;

if (Args.Count < 2) { Console.WriteLine("usage: <in.dat> <out.dat>"); return; }
string inPath = Args[0];
string outPath = Args[1];

var file = new HSDRawFile(inPath);
var sceneRoot = file["scene_data"];
if (sceneRoot == null || !(sceneRoot.Data is HSD_SOBJ sobj)) {
    Console.WriteLine("no scene_data SOBJ"); return;
}
var jdescs = sobj.JOBJDescs;
if (jdescs == null || jdescs.Array.Length == 0) {
    Console.WriteLine("no JOBJDescs"); return;
}
var rootJoint = jdescs.Array[0].RootJoint;
Console.WriteLine($"scene root JOBJ flags={rootJoint.Flags}");

// walk to root.Child[1]
var c0 = rootJoint.Child;
if (c0 == null) { Console.WriteLine("scene root has no children"); return; }
var c1 = c0.Next;
if (c1 == null) { Console.WriteLine("scene root has no second child"); return; }
Console.WriteLine($"child[1] flags={c1.Flags} (expecting opac/inu branch)");

// walk to last sibling of c1.Child
var grandchild = c1.Child;
if (grandchild == null) { Console.WriteLine("child[1] has no children"); return; }
int gcCount = 1;
while (grandchild.Next != null) { grandchild = grandchild.Next; gcCount++; }
Console.WriteLine($"child[1] has {gcCount} grandchild(ren) currently");

// allocate new JOBJ leaf, default scale 1
var nj = new HSD_JOBJ();
nj.SX = 1.0f; nj.SY = 1.0f; nj.SZ = 1.0f;
nj.Flags = JOBJ_FLAG.NULL;  // empty marker
nj.ClassName = "test_splice";

// splice
grandchild.Next = nj;
gcCount++;
Console.WriteLine($"spliced new JOBJ as grandchild[{gcCount - 1}]");

// optionally call UpdateFlags to let HSDLib normalize ancestor flags
rootJoint.UpdateFlags();

file.Save(outPath);
Console.WriteLine($"saved: {outPath}");

// reload & verify
var verify = new HSDRawFile(outPath);
var vsobj = (HSD_SOBJ)verify["scene_data"].Data;
var vroot = vsobj.JOBJDescs.Array[0].RootJoint;
var vc1 = vroot.Child.Next;
int vgcCount = 0;
var vg = vc1.Child;
while (vg != null) { vgcCount++; vg = vg.Next; }
Console.WriteLine($"reloaded child[1] grandchild count: {vgcCount} (expected {gcCount})");
Console.WriteLine(vgcCount == gcCount ? "SPLICE OK" : "MISMATCH!");
