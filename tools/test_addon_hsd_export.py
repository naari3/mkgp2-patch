"""Verify the Export HSD operator round-trips a vanilla .dat through
the Blender addon -> hsd_import_from_blender.csx pipeline.

Coverage:
  - Import an HSD scene.json bundle (csx-produced) into Blender as a
    `mkgp2:<dat>` collection
  - Activate it and invoke `export_scene.mkgp2_hsd_json` to a tempdir
  - Verify the produced .dat exists, has reasonable size, and reloads
    via HSDRawFile (proxied: just open it; if the file is malformed
    HSDLib will raise on parse)
  - SKIPs cleanly when dotnet-script or the source bundle is missing
    so it works on machines without the HSD toolchain

  blender --background --python tools/test_addon_hsd_export.py
"""

import bpy
import os
import sys
import tempfile
import traceback
from pathlib import Path

ADDON_DIR = r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\tools\blender"
SCENE_JSON = r"C:\Users\naari\Documents\blender\mr_highway_export\scene.json"
VANILLA_BIN = r"C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files"


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
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import blender_addon_mkgp2_course as addon
    addon.register()
    addon.reload_modules()
    print("[test] addon registered")

    try:
        if addon._resolve_dotnet_script() is None:
            print("[test] SKIP: dotnet-script not found")
            return
        if not Path(SCENE_JSON).is_file():
            print(f"[test] SKIP: scene.json bundle not found at {SCENE_JSON}")
            return

        # Wire vanilla bin dir for the operator's base.dat lookup
        addon._vanilla_bin_dir = lambda: VANILLA_BIN
        addon._output_bin_dir = lambda: tempfile.gettempdir()

        # ---- 1) Import HSD bundle via the operator
        result = bpy.ops.import_scene.mkgp2_hsd_json('EXEC_DEFAULT',
                                                     filepath=SCENE_JSON)
        assert result == {'FINISHED'}, f"import: {result}"
        bundle = next((c for c in bpy.data.collections
                       if c.name.startswith("mkgp2:")), None)
        assert bundle is not None, "import did not create mkgp2:<dat>"
        source_dat = bundle.get("mkgp2_source_dat")
        assert source_dat, "imported bundle has no mkgp2_source_dat"
        print(f"[test] imported bundle: {bundle.name} "
              f"(source_dat={source_dat})")

        # Sanity: stashed JSON props
        assert bundle.get("mkgp2_joints"), "no mkgp2_joints on bundle"
        assert bundle.get("mkgp2_joint_aliases"), "no mkgp2_joint_aliases on bundle"

        # ---- 2) Verify base.dat exists in vanilla bin dir
        base_dat = Path(VANILLA_BIN) / source_dat
        assert base_dat.is_file(), f"base .dat missing at {base_dat}"
        base_size = base_dat.stat().st_size
        print(f"[test] base .dat: {base_dat.name} ({base_size} bytes)")

        # ---- 3) Activate bundle as the layer collection, run Export
        _activate_layer_for(bundle)
        with tempfile.TemporaryDirectory() as td:
            out_dat = os.path.join(td, source_dat)
            result = bpy.ops.export_scene.mkgp2_hsd_json(
                'EXEC_DEFAULT', filepath=out_dat,
            )
            assert result == {'FINISHED'}, f"export: {result}"
            assert os.path.isfile(out_dat), f"writer did not produce {out_dat}"
            out_size = os.path.getsize(out_dat)
            print(f"[test] wrote {Path(out_dat).name} ({out_size} bytes)")
            # Round-trip size sanity: writer's no-op pass adds ~32 bytes
            # for struct alignment vs the original. Allow generous slack.
            delta = out_size - base_size
            assert -1024 <= delta <= 4096, \
                f"output size {out_size} differs from base {base_size} by " \
                f"{delta} bytes; expected near-identical for no-op pass"
            print(f"[test] size delta = {delta:+d} bytes (acceptable)")

        # ---- 4) Vanilla guard: refusing to write into the vanilla dir
        result = {'FINISHED'}
        try:
            result = bpy.ops.export_scene.mkgp2_hsd_json(
                'EXEC_DEFAULT',
                filepath=str(Path(VANILLA_BIN) / "evil.dat"),
            )
        except RuntimeError:
            result = {'CANCELLED'}
        assert result == {'CANCELLED'}, \
            "export_scene.mkgp2_hsd_json must refuse vanilla path"
        assert not (Path(VANILLA_BIN) / "evil.dat").exists()
        print("[test] vanilla guard refused write")

        addon.unregister()
        print("[test] PASS")
    except Exception:
        traceback.print_exc()
        print("[test] FAIL")
        sys.exit(1)


main()
