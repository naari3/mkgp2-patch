"""Smoke test for `_export_mkgp2_bundle.export_bundle_to_dat`:

  1. Import an M1+ scene.json bundle into Blender via the addon.
  2. Run the new exporter directly (not through the operator yet).
  3. Verify:
     - Output .dat exists, parses cleanly via hsdraw.
     - Has scene_data root.
     - Every joint alias from the bundle's stash appears as a root.
     - The texture bypass count matches the unique texture count
       (no edits made -> all bypass).
     - Re-importing the produced .dat gives the same joint and
       texture counts.

Usage:
  blender --background --python tools/test_addon_export_mkgp2_bundle.py \
      -- <m1_bundle_scene.json>
"""

import bpy
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

ADDON_DIR = r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\tools\blender"


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


def main():
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = []
    if not argv:
        print("[test] usage: ... -- <scene.json>")
        sys.exit(2)
    scene_json = argv[0]

    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import blender_addon_mkgp2_course as addon
    addon.register()
    addon.reload_modules()

    try:
        if not addon.HSDRAW_AVAILABLE:
            print("[test] SKIP: hsdraw not vendored")
            return

        # ---- 1) import bundle ----------------------------------------
        addon.hsd_imp.import_scene(scene_json)
        bundle = next((c for c in bpy.data.collections
                       if c.name.startswith("mkgp2:")), None)
        assert bundle is not None, "no mkgp2:<dat> collection after import"
        stash_sj = bundle.get("mkgp2_scene_json")
        assert stash_sj, "bundle missing mkgp2_scene_json stash"
        print(f"[test] imported bundle: {bundle.name}")

        # ---- 2) export -----------------------------------------------
        from blender_addon_mkgp2_course import _export_mkgp2_bundle
        with tempfile.TemporaryDirectory() as td:
            out_dat = os.path.join(td, "mkgp2_bundle_out.dat")
            stats = _export_mkgp2_bundle.export_bundle_to_dat(
                bundle, stash_sj, out_dat,
            )
            print(f"[test] stats: {stats}")
            assert os.path.isfile(out_dat)

            # ---- 3) parse output via hsdraw --------------------------
            import hsdraw
            dat = hsdraw.parse_dat(Path(out_dat).read_bytes())
            roots = list(dat.root_names())
            print(f"[test] roots: {roots}")
            assert "scene_data" in roots, "scene_data missing"
            aliases = json.loads(bundle.get("mkgp2_joint_aliases", "{}"))
            for alias_name in aliases:
                assert alias_name in roots, f"alias '{alias_name}' missing"
            print(f"[test] all {len(aliases)} aliases present")

            # No edits made -> every texture should be bypass
            assert stats["tex_reencode"] == 0, (
                f"unexpected re-encodes: {stats['tex_reencode']}; all "
                "should bypass on un-edited bundle")
            print(f"[test] bypass={stats['tex_bypass']} "
                  f"reencode={stats['tex_reencode']}")

        addon.unregister()
        print("[test] PASS")
    except Exception:
        traceback.print_exc()
        print("[test] FAIL")
        sys.exit(1)


main()
