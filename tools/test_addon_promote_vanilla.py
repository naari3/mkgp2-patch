"""Verify the Promote Vanilla flow.

Pipeline:
  1) Run Vanilla Full Course on mr_highway -> scene root holds an HSD
     bundle + collision/line/auto for both short and long.
  2) Run scene.mkgp2_promote_vanilla.
  3) Verify two `mkgp2_kind=course` collections exist
     (mr_highway_short, mr_highway_long), each with the right members
     and bin filename props. Short carries the HSD bundle nested under
     it; long carries no HSD nest because the user only generated
     scene.json for short.
  4) Re-run promote -> idempotent (no errors, no new collections).
  5) Validate Course on each promoted collection -> OK.

  blender --background --python tools/test_addon_promote_vanilla.py
"""

import bpy
import sys
import traceback
from pathlib import Path

ADDON_DIR = r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\tools\blender"
SCENE_JSON = r"C:\Users\naari\Documents\blender\mr_highway_export\scene.json"
BIN_DIR = r"C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files"
PREFIX = "mr_highway"


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
    assert lc is not None, f"could not locate layer collection for {coll.name}"
    bpy.context.view_layer.active_layer_collection = lc


def main():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import blender_addon_mkgp2_course as addon
    addon.register()
    addon.reload_modules()
    print("[test] addon registered")

    try:
        # ---- 1) Vanilla full course --------------------------------
        if not Path(SCENE_JSON).exists():
            print(f"[test] SKIP: scene.json not found at {SCENE_JSON}")
            return
        result = bpy.ops.import_scene.mkgp2_full_course(
            'EXEC_DEFAULT',
            scene_json=SCENE_JSON,
            bin_dir=BIN_DIR,
            prefix=PREFIX,
        )
        assert result == {'FINISHED'}
        print("[test] V1 vanilla import done")

        # Sanity: at this point no `mkgp2_kind=course` collection exists
        course_colls = [c for c in bpy.data.collections
                        if c.get("mkgp2_kind") == "course"]
        assert len(course_colls) == 0, \
            f"vanilla import should NOT create course collections, got {[c.name for c in course_colls]}"
        # Confirm the canonical members are sitting at scene root
        scene_master = bpy.context.scene.collection
        names_at_root = {o.name for o in scene_master.all_objects}
        assert any("CollisionMesh_mr_highway_short" in n for n in names_at_root)
        assert any("CollisionMesh_mr_highway_long" in n for n in names_at_root)
        print(f"[test] scene root has {len(names_at_root)} objects pre-promote")

        # ---- 2) Promote --------------------------------------------
        result = bpy.ops.scene.mkgp2_promote_vanilla()
        assert result == {'FINISHED'}, f"promote: {result}"
        print("[test] V2 promote returned FINISHED")

        # ---- 3) Verify -----------------------------------------------
        short = bpy.data.collections.get("mr_highway_short")
        long_ = bpy.data.collections.get("mr_highway_long")
        assert short is not None, "mr_highway_short course collection missing"
        assert long_ is not None, "mr_highway_long course collection missing"
        for c, label in ((short, "short"), (long_, "long")):
            assert c.get("mkgp2_kind") == "course"
            assert c.get("mkgp2_collision_bin") == f"mr_highway_{label}.bin"
            assert c.get("mkgp2_line_bin") == f"mr_highway_{label}_line.bin"
            assert c.get("mkgp2_auto_f_bin") == f"mr_highway_{label}_Auto.bin"
            assert c.get("mkgp2_auto_r_bin") == f"mr_highway_{label}_Auto_R.bin"
            # Members
            members = list(c.objects)
            names = sorted(m.name for m in members)
            print(f"[test] {label} members: {names}")
            for tag, expect in (
                ("collision", f"CollisionMesh_mr_highway_{label}"),
                ("walls", f"WallSegments_mr_highway_{label}"),
                ("line root", f"mr_highway_{label}_line_line"),
                ("auto F", f"Auto_mr_highway_{label}_Auto"),
                ("auto R", f"Auto_mr_highway_{label}_Auto_R"),
            ):
                assert any(m.name == expect for m in members), \
                    f"{label}: {tag} '{expect}' missing"
            # Auto roles tagged?
            auto_f = next(m for m in members if m.name == f"Auto_mr_highway_{label}_Auto")
            auto_r = next(m for m in members if m.name == f"Auto_mr_highway_{label}_Auto_R")
            assert auto_f.get("mkgp2_auto_role") == "F", \
                f"{label} auto F not tagged"
            assert auto_r.get("mkgp2_auto_role") == "R", \
                f"{label} auto R not tagged"

        # HSD bundle: short should have it nested, long should NOT
        # (only one scene.json was supplied, for short)
        short_hsd = next((c for c in short.children
                          if c.name.startswith("mkgp2:")), None)
        assert short_hsd is not None, \
            "short should have HSD bundle nested"
        long_hsd = next((c for c in long_.children
                         if c.name.startswith("mkgp2:")), None)
        assert long_hsd is None, \
            f"long should not have an HSD bundle, got {long_hsd}"
        # mkgp2_hsd_dat reflects the source_dat
        assert short.get("mkgp2_hsd_dat"), \
            "short.mkgp2_hsd_dat should match the bundle source_dat"
        print(f"[test] HSD nest: short={short_hsd.name} ({short.get('mkgp2_hsd_dat')}); "
              f"long has none (expected)")

        # ---- 4) Idempotence ----------------------------------------
        before = sum(1 for c in bpy.data.collections
                     if c.get("mkgp2_kind") == "course")
        # Now everything is in courses, second invocation should report
        # WARNING + CANCELLED (no new candidates).
        try:
            result = bpy.ops.scene.mkgp2_promote_vanilla()
        except RuntimeError as ex:
            # bpy.ops surfaces ERROR reports as RuntimeError
            print(f"[test] V4 second run raised RuntimeError as expected: {ex}")
            result = {'CANCELLED'}
        assert result == {'CANCELLED'}, \
            f"idempotent run should CANCEL, got {result}"
        after = sum(1 for c in bpy.data.collections
                    if c.get("mkgp2_kind") == "course")
        assert before == after, \
            f"second promote should not create new collections " \
            f"({before} -> {after})"
        print(f"[test] V4 idempotent: {before} course(s) before/after")

        # ---- 5) Validate each promoted course ---------------------
        for c in (short, long_):
            _activate_layer_for(c)
            try:
                result = bpy.ops.scene.mkgp2_validate_course('EXEC_DEFAULT')
            except RuntimeError as ex:
                # validate operator never raises -- it surfaces issues
                # as Warnings. Promote it back if so.
                raise
            assert result == {'FINISHED'}, f"validate {c.name}: {result}"
            # Re-run validate directly to inspect issues
            import blender_validate as val
            import blender_import_line as li
            import blender_export_line as le
            import blender_import_auto as ai
            import blender_export_auto as ae
            issues = val.validate_course(
                c, line_imp=li, line_exp=le, auto_imp=ai, auto_exp=ae)
            # We *expect* a naming warning here only if the HSD bundle
            # is co-resident. The HSD child collection's meshes don't
            # show up in `coll.objects` (only in `coll.all_objects`),
            # so they don't trigger naming check. Should be clean.
            assert not issues, f"validate {c.name} flagged: {issues}"
            print(f"[test] V5 validate {c.name}: OK")

        addon.unregister()
        print("[test] PASS")
    except Exception:
        traceback.print_exc()
        print("[test] FAIL")
        sys.exit(1)


main()
