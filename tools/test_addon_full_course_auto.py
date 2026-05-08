"""Verify Vanilla Full Course auto-discovery flow.

Default invocation now takes only `bin_dir` + `prefix`. The addon:
  1. Locates <Prefix>_short_A.dat and <Prefix>_long_A.dat (case
     insensitive) in bin_dir.
  2. Runs hsd_export_for_blender.csx on each via dotnet-script.
  3. Imports the produced scene.json bundles into Blender.
  4. Imports collision / line / Auto F/R for both rounds.

This test SKIPs if dotnet-script or the .dat files are not present so
the test suite still passes on machines that haven't set up the HSD
toolchain.

  blender --background --python tools/test_addon_full_course_auto.py
"""

import bpy
import sys
import traceback
from pathlib import Path

ADDON_DIR = r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\tools\blender"
BIN_DIR = Path(r"C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files")
PREFIX = "mr_highway"


def main():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import blender_addon_mkgp2_course as addon
    addon.register()
    addon.reload_modules()
    print("[test] addon registered")

    try:
        # Skip-on-missing-environment guards so this runs cleanly in CI
        # / on machines without the HSD toolchain.
        if addon._resolve_dotnet_script() is None:
            print("[test] SKIP: dotnet-script not found")
            return
        if not BIN_DIR.is_dir():
            print(f"[test] SKIP: bin_dir not present: {BIN_DIR}")
            return
        short_dat = addon._find_vanilla_dat(BIN_DIR, PREFIX, "short")
        long_dat = addon._find_vanilla_dat(BIN_DIR, PREFIX, "long")
        if short_dat is None and long_dat is None:
            print(f"[test] SKIP: no <{PREFIX}>_short_A.dat / _long_A.dat")
            return
        print(f"[test] dotnet-script: {addon._resolve_dotnet_script()}")
        print(f"[test] short.dat: {short_dat}")
        print(f"[test] long.dat:  {long_dat}")

        # Execute with scene_json blank -- triggers the auto path
        result = bpy.ops.import_scene.mkgp2_full_course(
            'EXEC_DEFAULT',
            scene_json="",
            bin_dir=str(BIN_DIR),
            prefix=PREFIX,
        )
        assert result == {'FINISHED'}, f"auto full course: {result}"
        print("[test] auto full course returned FINISHED")

        # Expectations:
        # - Both rounds' HSD bundles are at scene root (if .dat existed)
        # - collision / wall / line root / auto F/R are at scene root
        bundles = [c.name for c in bpy.data.collections
                   if c.name.startswith("mkgp2:")]
        print(f"[test] HSD bundles at scene root: {bundles}")
        if short_dat:
            assert any("short" in n.lower() for n in bundles), \
                f"short HSD bundle missing; have {bundles}"
        if long_dat:
            assert any("long" in n.lower() for n in bundles), \
                f"long HSD bundle missing; have {bundles}"

        for round_label in ("short", "long"):
            for kind, name in (
                ("collision", f"CollisionMesh_{PREFIX}_{round_label}"),
                ("wall", f"WallSegments_{PREFIX}_{round_label}"),
                ("line root", f"{PREFIX}_{round_label}_line_line"),
                ("auto F", f"Auto_{PREFIX}_{round_label}_Auto"),
                ("auto R", f"Auto_{PREFIX}_{round_label}_Auto_R"),
            ):
                obj = bpy.data.objects.get(name)
                assert obj is not None, \
                    f"{kind} '{name}' missing after auto import"
            print(f"[test] {round_label} round: collision/wall/line/auto OK")

        # Promote should pick up both rounds and nest the matching HSD
        # bundles automatically (this is the original problem we set
        # out to fix).
        result = bpy.ops.scene.mkgp2_promote_vanilla()
        assert result == {'FINISHED'}, f"promote: {result}"
        short_coll = bpy.data.collections.get(f"{PREFIX}_short")
        long_coll = bpy.data.collections.get(f"{PREFIX}_long")
        assert short_coll is not None and long_coll is not None
        if short_dat:
            short_hsd = next((c for c in short_coll.children
                              if c.name.startswith("mkgp2:")), None)
            assert short_hsd is not None, \
                "short course should have HSD nested after auto+promote"
            assert short_coll.get("mkgp2_hsd_dat"), \
                "short course mkgp2_hsd_dat empty"
        if long_dat:
            long_hsd = next((c for c in long_coll.children
                             if c.name.startswith("mkgp2:")), None)
            assert long_hsd is not None, \
                "long course should have HSD nested after auto+promote"
            assert long_coll.get("mkgp2_hsd_dat"), \
                "long course mkgp2_hsd_dat empty (the original bug)"
        print(f"[test] auto+promote: short hsd_dat="
              f"{short_coll.get('mkgp2_hsd_dat')!r} / "
              f"long hsd_dat={long_coll.get('mkgp2_hsd_dat')!r}")

        addon.unregister()
        print("[test] PASS")
    except Exception:
        traceback.print_exc()
        print("[test] FAIL")
        sys.exit(1)


main()
