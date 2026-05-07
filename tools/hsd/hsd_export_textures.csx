// dotnet-script: extract all unique textures from a HSD DAT to PNG.
// usage: dotnet script hsd_export_textures.csx -- <path-to-dat> <out-dir> [root-substr]
// Output: <out-dir>/<sha1>.png   (one PNG per unique image, deduped by sha1)
//         <out-dir>/_index.txt   (sha1 → root path / format / wxh)
#r "C:\Users\naari\src\github.com\Ploaj\HSDLib\HSDRaw\bin\Release\netstandard2.0\HSDRaw.dll"
#r "nuget: SixLabors.ImageSharp, 3.1.5"

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using HSDRaw;
using HSDRaw.Common;
using SixLabors.ImageSharp;
using SixLabors.ImageSharp.PixelFormats;

if (Args.Count < 2) { Console.WriteLine("usage: hsd_export_textures.csx <dat-path> <out-dir> [root-substr]"); return; }

string path = Args[0];
string outDir = Args[1];
string filter = Args.Count >= 3 ? Args[2] : null;

Directory.CreateDirectory(outDir);
var file = new HSDRawFile(path);

var seen = new Dictionary<string, string>(); // sha1 → "rootpath  WxH  fmt  bytes"
int written = 0, skipped = 0, failed = 0;

string Sha1(byte[] b) {
    using var sha = SHA1.Create();
    return BitConverter.ToString(sha.ComputeHash(b)).Replace("-", "").Substring(0, 12);
}

void HandleTobj(HSD_TOBJ t, string tag) {
    int idx = 0;
    while (t != null) {
        var img = t.ImageData;
        if (img != null && img.ImageData != null) {
            string h = Sha1(img.ImageData);
            string descTag = $"{tag}/Tobj{idx}";
            string desc = $"{descTag}  W={img.Width} H={img.Height} fmt={img.Format} src_bytes={img.ImageData.Length}";
            if (!seen.ContainsKey(h)) {
                seen[h] = desc;
                try {
                    byte[] rgba = t.GetDecodedImageData(0);
                    int w = img.Width, ht = img.Height;
                    if (rgba == null || rgba.Length != w * ht * 4) {
                        Console.WriteLine($"  WARN: {h} unexpected decoded length {rgba?.Length ?? -1} (expected {w*ht*4})  [{desc}]");
                        failed++;
                    } else {
                        // HSDLib quirk: CMP and RGBA8 decoders pack BGRA, others pack RGBA.
                        // Normalize to RGBA8 for ImageSharp.
                        if (img.Format == HSDRaw.GX.GXTexFmt.CMP || img.Format == HSDRaw.GX.GXTexFmt.RGBA8)
                            for (int i = 0; i + 2 < rgba.Length; i += 4) { var tmp = rgba[i]; rgba[i] = rgba[i+2]; rgba[i+2] = tmp; }
                        using var bmp = SixLabors.ImageSharp.Image.LoadPixelData<Rgba32>(rgba, w, ht);
                        string outPath = Path.Combine(outDir, $"{h}.png");
                        bmp.SaveAsPng(outPath);
                        written++;
                        Console.WriteLine($"  WROTE {h}.png  {desc}");
                    }
                } catch (Exception ex) {
                    Console.WriteLine($"  ERR  {h} decode failed: {ex.Message}  [{desc}]");
                    failed++;
                }
            } else {
                skipped++;
            }
        }
        idx++;
        t = t.Next;
    }
}

void WalkDobj(HSD_DOBJ d, string tag) {
    int i = 0;
    while (d != null) {
        if (d.Mobj != null && d.Mobj.Textures != null)
            HandleTobj(d.Mobj.Textures, $"{tag}/DObj{i}");
        i++; d = d.Next;
    }
}

void WalkJobj(HSD_JOBJ j, string tag) {
    int i = 0;
    while (j != null) {
        string subTag = $"{tag}/JObj{i}";
        if (j.Dobj != null) WalkDobj(j.Dobj, subTag);
        if (j.Child != null) WalkJobj(j.Child, subTag);
        i++; j = j.Next;
    }
}

foreach (var root in file.Roots) {
    if (filter != null && !root.Name.Contains(filter)) continue;
    if (root.Data is HSD_JOBJ rj) WalkJobj(rj, root.Name);
    else if (root.Data is HSD_SOBJ sobj && sobj.JOBJDescs != null) {
        for (int i = 0; i < sobj.JOBJDescs.Length; i++) {
            var jd = sobj.JOBJDescs[i];
            if (jd?.RootJoint != null) WalkJobj(jd.RootJoint, $"{root.Name}/JObjDesc{i}");
        }
    }
}

File.WriteAllLines(
    Path.Combine(outDir, "_index.txt"),
    seen.OrderBy(kv => kv.Key).Select(kv => $"{kv.Key}\t{kv.Value}")
);
Console.WriteLine($"\nTotal unique: {seen.Count}, wrote: {written}, dedup-skipped: {skipped}, failed: {failed}");
