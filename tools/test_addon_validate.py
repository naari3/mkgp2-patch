"""Verify T1a — Validate Course operator surfaces issues for broken
state and reports OK for clean assets.

Pipeline:
  1) Import the in-repo grd_short / test_course_short_line bundle as a
     custom course.
  2) Validate it -- expected to be issue-free.
  3) Mutate one CollisionMesh vertex outside the grid AABB, validate
     again -- expected to surface the out-of-bounds warning.
  4) Move one wall vertex off Z=0 -- expected to surface the wall
     plane warning.
  5) Add an unrelated cube to the course collection -- expected to
     surface the naming warning.

  blender --background --python tools/test_addon_validate.py
"""

import bpy
import sys
import traceback
from pathlib import Path

ADDON_DIR = r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\tools\blender"
PATCH_DIR = Path(r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\features\cup_page3\files")


def _import_clean_course(name="vc_clean"):
    bpy.ops.scene.mkgp2_import_course(
        'EXEC_DEFAULT',
        name=name,
        collision_path=str(PATCH_DIR / "grd_short.bin"),
        line_path=str(PATCH_DIR / "test_course_short_line.bin"),
        auto_f_path="",
        auto_r_path="",
    )
    return bpy.data.collections[name]


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
    assert lc is not None, "could not locate layer collection"
    bpy.context.view_layer.active_layer_collection = lc


def _run_validate():
    """Run the operator, capture WARNING reports."""
    # Operator pushes WARNINGs via self.report -- those are visible in
    # the Info editor in interactive Blender, but in --background mode
    # we need to call validate.validate_course() directly to inspect
    # the issue list. Easier: invoke the operator and rely on its
    # printed lines (captured by stdout).
    # For asserts we re-import the validate module and call directly.
    import blender_validate as val
    import blender_import_line as li
    import blender_export_line as le
    import blender_import_auto as ai
    import blender_export_auto as ae
    coll = bpy.context.view_layer.active_layer_collection.collection
    return val.validate_course(coll, line_imp=li, line_exp=le,
                               auto_imp=ai, auto_exp=ae)


def main():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import blender_addon_mkgp2_course as addon
    addon.register()
    addon.reload_modules()
    print("[test] addon registered")

    try:
        # ---- 1) Import + validate clean ------------------------------
        coll = _import_clean_course()
        _activate_layer_for(coll)
        issues = _run_validate()
        assert not issues, f"clean course flagged {len(issues)} issue(s): {issues}"
        print("[test] V1 clean course validates OK")

        # Operator path: should report INFO 'OK', return FINISHED.
        result = bpy.ops.scene.mkgp2_validate_course('EXEC_DEFAULT')
        assert result == {'FINISHED'}
        print("[test] V1 operator FINISHED")

        # ---- 2) Out-of-bounds triangle -------------------------------
        col_obj = next(o for o in coll.all_objects
                       if o.name.startswith("CollisionMesh"))
        # Move one vertex far outside grid -- guaranteed to fail
        # (grid origins/cells are stored on the object).
        col_obj.data.vertices[0].co.x += 100000.0
        issues = _run_validate()
        assert any("outside grid bounds" in s for s in issues), \
            f"expected grid-bounds issue, got: {issues}"
        print(f"[test] V2 grid-bounds issue detected: "
              f"{next(s for s in issues if 'outside' in s)[:80]}")
        col_obj.data.vertices[0].co.x -= 100000.0  # revert

        # ---- 3) Off-plane wall vertex --------------------------------
        wall_obj = next(o for o in coll.all_objects
                        if o.name.startswith("WallSegments"))
        wall_obj.data.vertices[0].co.z += 5.0
        issues = _run_validate()
        assert any("off Z=0" in s for s in issues), \
            f"expected wall-plane issue, got: {issues}"
        print(f"[test] V3 wall-plane issue detected: "
              f"{next(s for s in issues if 'off Z=0' in s)[:80]}")
        wall_obj.data.vertices[0].co.z -= 5.0  # revert

        # ---- 4) Naming convention violation -------------------------
        cube_mesh = bpy.data.meshes.new("ValidatorCube")
        cube_mesh.from_pydata([(0,0,0), (1,0,0), (1,1,0), (0,1,0)],
                              [], [(0,1,2,3)])
        cube_obj = bpy.data.objects.new("StrayCube", cube_mesh)
        coll.objects.link(cube_obj)
        issues = _run_validate()
        assert any("convention" in s for s in issues), \
            f"expected naming issue, got: {issues}"
        print(f"[test] V4 naming issue detected: "
              f"{next(s for s in issues if 'convention' in s)[:80]}")
        coll.objects.unlink(cube_obj)
        bpy.data.objects.remove(cube_obj)
        bpy.data.meshes.remove(cube_mesh)

        # ---- 5) Re-validate clean again -----------------------------
        issues = _run_validate()
        assert not issues, f"course should be clean again, got: {issues}"
        print("[test] V5 reverted course validates OK again")

        addon.unregister()
        print("[test] PASS")
    except Exception:
        traceback.print_exc()
        print("[test] FAIL")
        sys.exit(1)


main()
