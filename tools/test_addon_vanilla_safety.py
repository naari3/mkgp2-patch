"""Verify that the export-time guard refuses to write to the vanilla
bin directory. This is the regression test for the ROM-dump-overwrite
bug that prompted re-extraction.

Coverage:
  - course-level export refuses when bin_dir == vanilla
  - course-level export refuses when bin_dir is a descendant of vanilla
  - course-level export proceeds when bin_dir is unrelated to vanilla
  - per-asset Export Collision / Line / Auto refuse a vanilla filepath
  - Full Course Export refuses a vanilla bin_dir
  - vanilla guard helpers (_is_inside_vanilla) behave correctly

  blender --background --python tools/test_addon_vanilla_safety.py
"""

import bpy
import sys
import tempfile
import traceback
from pathlib import Path

ADDON_DIR = r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\tools\blender"
PATCH_DIR = Path(r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\features\cup_page3\files")


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
    assert lc is not None
    bpy.context.view_layer.active_layer_collection = lc


def main():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import blender_addon_mkgp2_course as addon
    addon.register()
    addon.reload_modules()
    print("[test] addon registered")

    try:
        # ---- Setup: pretend tmp dir is the vanilla bin dir, and
        # another tmp dir is the writable output. Inject directly via
        # monkey-patching the resolver helpers so we don't need a
        # configured Blender preference.
        van_tmp = tempfile.mkdtemp(prefix="mkgp2_test_vanilla_")
        out_tmp = tempfile.mkdtemp(prefix="mkgp2_test_output_")
        addon._vanilla_bin_dir = lambda: van_tmp
        addon._output_bin_dir = lambda: out_tmp
        # Keep the alias in sync so downstream code that still references
        # `_default_bin_dir` (back-compat) sees the same vanilla dir.
        addon._default_bin_dir = addon._vanilla_bin_dir
        print(f"[test] vanilla = {van_tmp}")
        print(f"[test] output  = {out_tmp}")

        # ---- A) helper sanity checks ---------------------------------
        assert addon._is_inside_vanilla(van_tmp) is True
        sub = Path(van_tmp) / "sub"
        sub.mkdir()
        assert addon._is_inside_vanilla(str(sub)) is True
        assert addon._is_inside_vanilla(out_tmp) is False
        assert addon._is_inside_vanilla("") is False
        print("[test] A _is_inside_vanilla classifies correctly")

        # ---- B) Course export refuses when bin_dir == vanilla -------
        bpy.ops.scene.mkgp2_import_course(
            'EXEC_DEFAULT',
            name="safety_short",
            collision_path=str(PATCH_DIR / "grd_short.bin"),
            line_path=str(PATCH_DIR / "test_course_short_line.bin"),
            auto_f_path="",
            auto_r_path="",
        )
        coll = bpy.data.collections["safety_short"]
        # Force the course's bin_dir to vanilla; this simulates the
        # broken pre-fix configuration.
        coll["mkgp2_bin_dir"] = van_tmp
        _activate_layer_for(coll)
        try:
            result = bpy.ops.scene.mkgp2_export_course('EXEC_DEFAULT')
        except RuntimeError:
            result = {'CANCELLED'}
        assert result == {'CANCELLED'}, \
            f"course export should refuse vanilla bin_dir, got {result}"
        # Verify nothing was written
        assert not list(Path(van_tmp).glob("*.bin")), \
            f"course export wrote to vanilla despite the guard: " \
            f"{list(Path(van_tmp).glob('*.bin'))}"
        print("[test] B course export refuses vanilla bin_dir")

        # ---- C) Course export refuses when bin_dir is a vanilla descendant
        coll["mkgp2_bin_dir"] = str(sub)
        try:
            result = bpy.ops.scene.mkgp2_export_course('EXEC_DEFAULT')
        except RuntimeError:
            result = {'CANCELLED'}
        assert result == {'CANCELLED'}, \
            "course export should refuse vanilla descendant"
        assert not list(sub.glob("*.bin"))
        print("[test] C course export refuses vanilla descendant")

        # ---- D) Course export ACCEPTS unrelated dir -----------------
        coll["mkgp2_bin_dir"] = out_tmp
        try:
            result = bpy.ops.scene.mkgp2_export_course('EXEC_DEFAULT')
        except RuntimeError as ex:
            print(f"[test] D unexpected RuntimeError: {ex}")
            result = {'CANCELLED'}
        assert result == {'FINISHED'}, \
            f"course export should accept output dir, got {result}"
        written = sorted(p.name for p in Path(out_tmp).glob("*.bin"))
        assert "grd_short.bin" in written
        assert "test_course_short_line.bin" in written
        print(f"[test] D course export to output OK ({len(written)} files)")

        # ---- E) Per-asset Collision export refuses vanilla filepath -
        col_obj = next(o for o in coll.all_objects
                       if o.name.startswith("CollisionMesh"))
        bpy.context.view_layer.objects.active = col_obj
        try:
            result = bpy.ops.export_mesh.mkgp2_collision_bin(
                'EXEC_DEFAULT',
                filepath=str(Path(van_tmp) / "evil.bin"),
            )
        except RuntimeError:
            result = {'CANCELLED'}
        assert result == {'CANCELLED'}, \
            "per-asset collision should refuse vanilla filepath"
        assert not (Path(van_tmp) / "evil.bin").exists()
        print("[test] E per-asset collision refuses vanilla filepath")

        # ---- F) Per-asset Line export refuses vanilla filepath ------
        line_root = next(o for o in coll.all_objects
                         if o.type == 'EMPTY' and o.name.endswith("_line"))
        bpy.context.view_layer.objects.active = line_root
        try:
            result = bpy.ops.export_scene.mkgp2_line_bin(
                'EXEC_DEFAULT',
                filepath=str(Path(van_tmp) / "evil_line.bin"),
            )
        except RuntimeError:
            result = {'CANCELLED'}
        assert result == {'CANCELLED'}
        assert not (Path(van_tmp) / "evil_line.bin").exists()
        print("[test] F per-asset line refuses vanilla filepath")

        # ---- G) Full-Course bulk export refuses vanilla bin_dir -----
        try:
            result = bpy.ops.export_scene.mkgp2_full_course(
                'EXEC_DEFAULT', bin_dir=van_tmp,
            )
        except RuntimeError:
            result = {'CANCELLED'}
        assert result == {'CANCELLED'}, \
            "full-course export should refuse vanilla bin_dir"
        assert not list(Path(van_tmp).glob("*.bin"))
        print("[test] G full-course export refuses vanilla bin_dir")

        # ---- H) Import file-set from vanilla leaves mkgp2_bin_dir empty
        # (so future export uses output preference, not vanilla)
        # Copy a .bin into the fake vanilla dir for this case.
        import shutil
        van_grd = Path(van_tmp) / "grd_short_vanillacopy.bin"
        shutil.copy2(str(PATCH_DIR / "grd_short.bin"), van_grd)
        bpy.ops.scene.mkgp2_import_course(
            'EXEC_DEFAULT',
            name="safety_from_vanilla",
            collision_path=str(van_grd),
            line_path="",
            auto_f_path="",
            auto_r_path="",
        )
        coll2 = bpy.data.collections["safety_from_vanilla"]
        # bin_dir should be blank because source was inside vanilla
        bd = coll2.get("mkgp2_bin_dir") or ""
        assert bd == "", \
            f"importing from vanilla should clear mkgp2_bin_dir, got {bd!r}"
        print("[test] H importing from vanilla clears mkgp2_bin_dir")

        # That cleared collection should now use the output preference
        # via the fallback path, and a Validate / Export should work.
        _activate_layer_for(coll2)
        result = bpy.ops.scene.mkgp2_export_course('EXEC_DEFAULT')
        assert result == {'FINISHED'}, \
            f"export with empty bin_dir + output pref should FINISH, got {result}"
        out_files = sorted(p.name for p in Path(out_tmp).glob("*.bin"))
        assert "grd_short_vanillacopy.bin" in out_files
        print("[test] H followup: export falls through to output dir")

        addon.unregister()
        print("[test] PASS")
    except Exception:
        traceback.print_exc()
        print("[test] FAIL")
        sys.exit(1)


main()
