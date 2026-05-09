"""Verify the unified Export HSD operator (M3b dispatcher) round-trips
a vanilla .dat through the Blender addon -> _export_mkgp2_bundle path.

Coverage:
  - Import a vanilla .dat directly via the Python-only ImportHSD path
    (`hsdraw.export_scene_json` + `hsdraw.gx_decode`, no csx).
  - Invoke `export_scene.mkgp2_hsd_json` against the resulting bundle.
    `mkgp2_scene_json` is now an inline JSON string rather than a path.
  - Verify the produced .dat exists, parses via hsdraw, has all
    expected roots (scene_data + every alias), and that the byte size
    is in a sensible envelope vs the vanilla source.
  - Vanilla guard: refusing to write into the read-only vanilla bin.
  - SKIPs cleanly when hsdraw / vanilla .dat is missing.

  blender --background --python tools/test_addon_hsd_export.py
"""

import bpy
import json
import os
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


def _import_dat_via_operator(base_dat):
    """Import `base_dat` via ImportHSD (= Python direct .dat read).
    Returns the new mkgp2:<dat> Collection."""
    pre = set(c.name for c in bpy.data.collections)
    result = bpy.ops.import_scene.mkgp2_hsd_json(
        'EXEC_DEFAULT', filepath=str(base_dat))
    assert result == {'FINISHED'}, f"import: {result}"
    post = [c for c in bpy.data.collections if c.name not in pre]
    assert post, "ImportHSD did not create a new collection"
    return post[0]


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
        base_dat = Path(VANILLA_BIN) / BASE_DAT_NAME
        if not base_dat.is_file():
            print(f"[test] SKIP: base .dat missing at {base_dat}")
            return

        # Wire vanilla bin dir / output dir overrides
        addon._vanilla_bin_dir = lambda: VANILLA_BIN
        addon._output_bin_dir = lambda: tempfile.gettempdir()

        with tempfile.TemporaryDirectory(prefix="mkgp2_hsd_test_") as td:
            # ---- 1) Import vanilla .dat via the operator (Python only) -
            bundle = _import_dat_via_operator(base_dat)
            source_dat = bundle.get("mkgp2_source_dat")
            assert source_dat, "imported bundle has no mkgp2_source_dat"
            sj = bundle.get("mkgp2_scene_json")
            assert sj, "missing mkgp2_scene_json"
            assert sj.lstrip().startswith('{'), (
                "mkgp2_scene_json should be inline JSON now, got: "
                f"{sj[:80]!r}")
            print(f"[test] imported bundle: {bundle.name} "
                  f"(source_dat={source_dat}, scene_json {len(sj)} chars)")

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
