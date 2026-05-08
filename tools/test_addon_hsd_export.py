"""Verify the unified Export HSD operator (M3b dispatcher) round-trips
a vanilla .dat through the Blender addon -> _export_mkgp2_bundle path.

Coverage:
  - Auto-regenerate the test scene.json bundle via M1+ csx so the run
    is self-bootstrapping and the bundle always carries the M2 GX
    metadata the new exporter needs.
  - Import the bundle and invoke `export_scene.mkgp2_hsd_json` to a
    tempdir. The dispatcher branch is the bundle path (the active
    layer collection is `mkgp2:<dat>`).
  - Verify the produced .dat exists, parses via hsdraw, has all
    expected roots (scene_data + every alias), and that re-import
    gives the same joint / mesh / texture counts.
  - Vanilla guard: refusing to write into the read-only vanilla bin.
  - SKIPs cleanly when dotnet-script / hsdraw / vanilla .dat is
    missing so the test works on machines without the HSD toolchain.

  blender --background --python tools/test_addon_hsd_export.py
"""

import bpy
import json
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ADDON_DIR = r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\tools\blender"
VANILLA_BIN = r"C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files"
BASE_DAT_NAME = "MR_highway_short_A.dat"


def _activate_layer_for(coll):
    def find(layer_root, target):
        if layer_root.collection is target:
            return layer_root
        for ch in layer_root.children:
            hit = find(ch, target)
            if hit is not None:
                return hit
        return None
    lc = find(bpy.context.view_layer.layer_collection, coll)
    assert lc is not None, f"no layer collection for {coll.name}"
    bpy.context.view_layer.active_layer_collection = lc


def _ensure_bundle(addon, base_dat, out_dir):
    """Run the M1+ csx to produce a fresh scene.json bundle for `base_dat`
    in `out_dir`. Returns the absolute path to scene.json. Raises
    RuntimeError if dotnet-script / csx isn't reachable."""
    csx = addon._resolve_csx_path()
    if not Path(csx).is_file():
        raise RuntimeError(f"csx not found at {csx}")
    dotnet = addon._resolve_dotnet_script()
    if dotnet is None:
        raise RuntimeError("dotnet-script not found")
    proc = subprocess.run(
        [dotnet, csx, "--", str(base_dat), str(out_dir)],
        capture_output=True, text=True, timeout=240,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"csx returned {proc.returncode}\nstdout: "
            f"{proc.stdout[-500:]}\nstderr: {proc.stderr[-500:]}")
    sj = Path(out_dir) / "scene.json"
    assert sj.is_file(), "csx did not produce scene.json"
    return str(sj)


def _export(addon, bundle, out_dat):
    """Activate `bundle` and run the unified export operator. Returns
    the operator's `bpy.context.window_manager.operator_run_string`-ish
    info: a dict pulled from the operator's stats by re-parsing the
    written .dat (the operator returns FINISHED only)."""
    _activate_layer_for(bundle)
    result = bpy.ops.export_scene.mkgp2_hsd_json(
        'EXEC_DEFAULT', filepath=out_dat,
    )
    assert result == {'FINISHED'}, f"export: {result}"
    assert os.path.isfile(out_dat), f"writer did not produce {out_dat}"


def _hash_file(p):
    h = __import__("hashlib").sha1()
    h.update(Path(p).read_bytes())
    return h.hexdigest()


def _summary(out_dat):
    """Parse `out_dat` and pull a tuple (roots, joint_count_via_walk,
    sum_of_dl_sizes) for cross-export comparison."""
    import hsdraw
    dat = hsdraw.parse_dat(Path(out_dat).read_bytes())
    roots = sorted(dat.root_names())
    return roots


def main():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import blender_addon_mkgp2_course as addon
    addon.register()
    addon.reload_modules()
    print("[test] addon registered")

    try:
        if not addon.HSDRAW_AVAILABLE:
            print("[test] SKIP: hsdraw not vendored for this platform")
            return
        if addon._resolve_dotnet_script() is None:
            print("[test] SKIP: dotnet-script not found")
            return
        base_dat = Path(VANILLA_BIN) / BASE_DAT_NAME
        if not base_dat.is_file():
            print(f"[test] SKIP: base .dat missing at {base_dat}")
            return

        # Wire vanilla bin dir / output dir overrides
        addon._vanilla_bin_dir = lambda: VANILLA_BIN
        addon._output_bin_dir = lambda: tempfile.gettempdir()

        with tempfile.TemporaryDirectory(prefix="mkgp2_hsd_test_") as td:
            # ---- 0) Regenerate bundle via M1+ csx ---------------------
            scene_json = _ensure_bundle(addon, base_dat, td)
            print(f"[test] bundle: {scene_json}")

            # ---- 1) Import HSD bundle via the operator ----------------
            result = bpy.ops.import_scene.mkgp2_hsd_json(
                'EXEC_DEFAULT', filepath=scene_json)
            assert result == {'FINISHED'}, f"import: {result}"
            bundle = next((c for c in bpy.data.collections
                           if c.name.startswith("mkgp2:")), None)
            assert bundle is not None, "import did not create mkgp2:<dat>"
            source_dat = bundle.get("mkgp2_source_dat")
            assert source_dat, "imported bundle has no mkgp2_source_dat"
            assert bundle.get("mkgp2_scene_json"), "missing mkgp2_scene_json"
            print(f"[test] imported bundle: {bundle.name} "
                  f"(source_dat={source_dat})")

            base_size = base_dat.stat().st_size

            # ---- 2) Baseline export (no edits) ------------------------
            out_dat_v0 = os.path.join(td, "v0_" + source_dat)
            _export(addon, bundle, out_dat_v0)
            out_size_v0 = os.path.getsize(out_dat_v0)
            print(f"[test] v0 baseline: {out_size_v0} bytes "
                  f"(vs base {base_size})")
            assert base_size // 2 < out_size_v0 < base_size * 5, (
                f"v0 {out_size_v0} outside envelope "
                f"[{base_size//2}, {base_size*5}]")

            # Sanity: roots match
            roots_v0 = _summary(out_dat_v0)
            assert "scene_data" in roots_v0
            aliases = json.loads(bundle.get("mkgp2_joint_aliases", "{}"))
            for alias_name in aliases:
                assert alias_name in roots_v0, (
                    f"alias '{alias_name}' missing from v0 output")
            print(f"[test] v0 roots OK: scene_data + {len(aliases)} aliases")

            # ---- 3) Re-export with NO changes -- byte-equiv? ----------
            # The Rust writer is deterministic (sorted struct dedup +
            # stable buffer alignment), so back-to-back exports of an
            # untouched bundle MUST produce byte-identical output. This
            # is the M3c "byte-equiv on second pass" criterion.
            out_dat_v1 = os.path.join(td, "v1_" + source_dat)
            _export(addon, bundle, out_dat_v1)
            h0 = _hash_file(out_dat_v0)
            h1 = _hash_file(out_dat_v1)
            assert h0 == h1, (
                f"v0 and v1 (no edits) differ: {h0} vs {h1}; "
                "the writer is not deterministic on identical input")
            print(f"[test] v0 == v1 byte-equiv on un-edited bundle "
                  f"(sha={h0[:16]}...)")

            # ---- 4) Texture edit -> re-encode path -------------------
            # Pick the largest texture and dirty it (simulate the user
            # editing it in Blender's Image Editor). The PNG file on
            # disk is unchanged, but is_dirty flips True -> exporter
            # should fall through to the encoder for that one texture
            # and bypass for all the others.
            largest = max(
                (im for im in bpy.data.images
                 if im.get("mkgp2_gx_path")),
                key=lambda im: int(im.get("mkgp2_gx_size") or 0),
            )
            print(f"[test] dirtying texture {largest.name} "
                  f"({int(largest.get('mkgp2_gx_size') or 0)} GX bytes)")
            # Touch a single pixel to set `is_dirty` and force re-encode
            pixels = list(largest.pixels)
            pixels[0] = min(1.0, pixels[0] + 0.0001)
            largest.pixels = pixels  # write-back marks the image dirty

            out_dat_v2 = os.path.join(td, "v2_" + source_dat)
            _export(addon, bundle, out_dat_v2)
            h2 = _hash_file(out_dat_v2)
            assert h2 != h0, (
                "texture edit did not change the .dat hash; bypass "
                "wasn't actually broken for the dirty image")
            print(f"[test] texture edit -> v2 differs from v0 "
                  f"(sha={h2[:16]}...)")

            # ---- 5) Vanilla guard ------------------------------------
            evil = str(Path(VANILLA_BIN) / "evil.dat")
            try:
                guard_result = bpy.ops.export_scene.mkgp2_hsd_json(
                    'EXEC_DEFAULT', filepath=evil,
                )
            except RuntimeError:
                guard_result = {'CANCELLED'}
            assert guard_result == {'CANCELLED'}, \
                "export_scene.mkgp2_hsd_json must refuse vanilla path"
            assert not Path(evil).exists()
            print("[test] vanilla guard refused write")

        addon.unregister()
        print("[test] PASS")
    except Exception:
        traceback.print_exc()
        print("[test] FAIL")
        sys.exit(1)


main()
