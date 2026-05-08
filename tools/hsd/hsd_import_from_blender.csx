// dotnet-script: write a JSON+PNG bundle (produced by
// hsd_export_for_blender.csx) back into a .dat using HSDLib.
//
// Phase 1 MVP: structural-only writer.
//   - Reads `<base.dat>` as the canonical source of mesh/material/texture
//     content. The base must be the same .dat (or a known-identical one)
//     that the bundle was originally exported from. Mesh DL bytes are
//     **not** re-encoded from JSON in this phase.
//   - Applies these structural edits expressed in scene.json:
//     1. `joint_aliases` -> file.Roots additions (and removal of stale
//        non-`scene_data` roots not present in the JSON map)
//     2. (deferred) joint hierarchy parent/children rewiring
//     3. (deferred) joint flag / TRS overrides
//   - Saves the result to <out.dat>.
//
// Geometry / material / texture edits are NOT yet supported. A future
// phase will (a) re-encode POBJ DL bytes from JSON-resident vertex /
// primitive arrays and (b) repack textures from PNG via HSDLib's GX
// encoders.
//
// usage: dotnet-script hsd_import_from_blender.csx -- <base.dat> <bundle.dir> <out.dat>
//   bundle.dir = directory containing scene.json (and tex/*.png)

#r "C:\Users\naari\src\github.com\Ploaj\HSDLib\HSDRaw\bin\Release\netstandard2.0\HSDRaw.dll"

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using HSDRaw;
using HSDRaw.Common;

if (Args.Count < 3)
{
    Console.WriteLine("usage: hsd_import_from_blender.csx <base.dat> <bundle.dir> <out.dat>");
    return;
}
string basePath = Args[0];
string bundleDir = Args[1];
string outPath = Args[2];

string scenePath = Path.Combine(bundleDir, "scene.json");
if (!File.Exists(scenePath))
{
    Console.WriteLine($"scene.json not found at {scenePath}");
    return;
}

// ---- Load base + JSON ------------------------------------------------
var file = new HSDRawFile(basePath);
Console.WriteLine($"base    : {basePath}  roots={file.Roots.Count}");

var sceneJsonText = File.ReadAllText(scenePath);
var sceneDoc = JsonDocument.Parse(sceneJsonText);
var sceneJson = sceneDoc.RootElement;

var jsonAliases = new Dictionary<string, string>();
JsonElement aliasesElem;
if (sceneJson.TryGetProperty("joint_aliases", out aliasesElem))
{
    foreach (var p in aliasesElem.EnumerateObject())
        jsonAliases[p.Name] = p.Value.GetString();
}
Console.WriteLine($"json    : aliases={jsonAliases.Count}");

// ---- Build jobj_id -> HSD_JOBJ map by walking base scene tree --------
// MUST match the DFS order used by hsd_export_for_blender.csx so that
// the IDs we assign here line up with the IDs in scene.json.
var jobjById = new Dictionary<string, HSD_JOBJ>();
var idByStruct = new Dictionary<HSDStruct, string>();
int counter = 0;

string EmitJoint(HSD_JOBJ j, string parentId)
{
    if (j._s != null && idByStruct.TryGetValue(j._s, out var existing))
        return existing;
    string id = $"jobj_{counter++}";
    if (j._s != null) idByStruct[j._s] = id;
    jobjById[id] = j;
    if (j.Child != null)
    {
        var c = j.Child;
        while (c != null) { EmitJoint(c, id); c = c.Next; }
    }
    return id;
}

HSDRootNode sceneDataRoot = null;
foreach (var r in file.Roots) if (r.Name == "scene_data") { sceneDataRoot = r; break; }
if (sceneDataRoot != null && sceneDataRoot.Data is HSD_SOBJ sobj && sobj.JOBJDescs != null)
{
    for (int i = 0; i < sobj.JOBJDescs.Length; i++)
    {
        var jd = sobj.JOBJDescs[i];
        if (jd?.RootJoint != null) EmitJoint(jd.RootJoint, null);
    }
}
foreach (var r in file.Roots)
{
    if (r.Name == "scene_data") continue;
    if (r.Data is HSD_JOBJ rj)
    {
        if (rj._s != null && idByStruct.ContainsKey(rj._s)) continue; // alias of already-walked
        EmitJoint(rj, null);
    }
}
Console.WriteLine($"base    : walked joints={counter}");

// ---- Apply alias additions / removals --------------------------------
// `file.Roots` is the canonical alias list. We want it to contain:
//   - scene_data (always present)
//   - one root per (name -> id) entry in jsonAliases, where Data points
//     at the HSD_JOBJ identified by the id
//   - and nothing else (stale aliases get pruned)

var existingByName = new Dictionary<string, HSDRootNode>();
foreach (var r in file.Roots) existingByName[r.Name] = r;

int added = 0, repointed = 0, removed = 0;

foreach (var (aliasName, jobjId) in jsonAliases.Select(kv => (kv.Key, kv.Value)))
{
    if (!jobjById.TryGetValue(jobjId, out var targetJobj))
    {
        Console.WriteLine($"  WARN: alias '{aliasName}' references unknown {jobjId}, skipping");
        continue;
    }

    if (existingByName.TryGetValue(aliasName, out var existing))
    {
        // already present — verify it points at the right struct
        if (existing.Data._s != targetJobj._s)
        {
            existing.Data = targetJobj;
            repointed++;
        }
    }
    else
    {
        file.Roots.Add(new HSDRootNode { Name = aliasName, Data = targetJobj });
        added++;
    }
}

// remove file.Roots entries that are JOBJ-data and NOT in jsonAliases
// (don't touch scene_data or anything that isn't a JOBJ)
var toRemove = new List<HSDRootNode>();
foreach (var r in file.Roots)
{
    if (r.Name == "scene_data") continue;
    if (!(r.Data is HSD_JOBJ)) continue;
    if (!jsonAliases.ContainsKey(r.Name)) toRemove.Add(r);
}
foreach (var r in toRemove)
{
    file.Roots.Remove(r);
    removed++;
}

Console.WriteLine($"aliases : added={added} repointed={repointed} removed={removed}");
Console.WriteLine($"final   : roots={file.Roots.Count}");

// ---- Save -------------------------------------------------------------
file.Save(outPath);
Console.WriteLine($"wrote   : {outPath}  size={new FileInfo(outPath).Length}");

// ---- Verify roundtrip --------------------------------------------------
var verify = new HSDRawFile(outPath);
Console.WriteLine($"reload  : roots={verify.Roots.Count}");
foreach (var r in verify.Roots) Console.WriteLine($"  - {r.Name}  ({r.Data?.GetType().Name})");
