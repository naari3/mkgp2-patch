"""Sanity-check that the test_course assets shipped in this repo
(features/cup_page3/files/) can be imported as a custom course.

  blender --background --python tools/test_addon_patch_test_course.py
"""

import bpy
import sys
import traceback
from pathlib import Path

ADDON_DIR = r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\tools\blender"
PATCH_DIR = Path(r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\features\cup_page3\files")


def main():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import blender_addon_mkgp2_course as addon
    addon.register()
    addon.reload_modules()

    try:
        # ---- short variant: collision + line, no auto, no HSD ----------
        result = bpy.ops.scene.mkgp2_import_course(
            'EXEC_DEFAULT',
            name="test_course_short",
            collision_path=str(PATCH_DIR / "grd_short.bin"),
            line_path=str(PATCH_DIR / "test_course_short_line.bin"),
            auto_f_path="",
            auto_r_path="",
        )
        assert result == {'FINISHED'}, f"short import: {result}"
        c = bpy.data.collections.get("test_course_short")
        assert c is not None
        col_obj = next((o for o in c.all_objects if o.name.startswith("CollisionMesh")), None)
        wall_obj = next((o for o in c.all_objects if o.name.startswith("WallSegments")), None)
        line_root = next((o for o in c.all_objects if o.type == 'EMPTY' and o.name.endswith("_line")), None)
        n_variants = sum(1 for o in c.all_objects if o.type == 'MESH' and o.name.startswith("LineVariant_"))
        autos = [o for o in c.all_objects if o.get("mkgp2_auto_role")]
        assert col_obj is not None and wall_obj is not None
        assert line_root is not None
        assert n_variants > 0
        assert len(autos) == 0, f"expected no autos but got {[o.name for o in autos]}"
        print(f"[test] test_course_short OK: "
              f"{len(col_obj.data.polygons)} tris / "
              f"{len(wall_obj.data.edges)} walls / "
              f"{n_variants} line variants / 0 autos")
        # Custom props
        assert c.get("mkgp2_collision_bin") == "grd_short.bin"
        assert c.get("mkgp2_line_bin") == "test_course_short_line.bin"
        assert c.get("mkgp2_auto_f_bin") == ""
        assert c.get("mkgp2_auto_r_bin") == ""

        # ---- long variant: collision only -----------------------------
        result = bpy.ops.scene.mkgp2_import_course(
            'EXEC_DEFAULT',
            name="test_course_long",
            collision_path=str(PATCH_DIR / "grd_long.bin"),
            line_path="",  # patch repo has no long-line variant
            auto_f_path="",
            auto_r_path="",
        )
        assert result == {'FINISHED'}, f"long import: {result}"
        c = bpy.data.collections.get("test_course_long")
        assert c is not None
        col_obj = next((o for o in c.all_objects if o.name.startswith("CollisionMesh")), None)
        wall_obj = next((o for o in c.all_objects if o.name.startswith("WallSegments")), None)
        assert col_obj is not None and wall_obj is not None
        n_lines = sum(1 for o in c.all_objects if o.type == 'EMPTY' and o.name.endswith("_line"))
        assert n_lines == 0, f"expected no line root, got {n_lines}"
        print(f"[test] test_course_long OK: "
              f"{len(col_obj.data.polygons)} tris / "
              f"{len(wall_obj.data.edges)} walls / 0 lines / 0 autos")

        addon.unregister()
        print("[test] PASS")
    except Exception:
        traceback.print_exc()
        print("[test] FAIL")
        sys.exit(1)


main()
