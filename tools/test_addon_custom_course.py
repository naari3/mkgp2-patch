"""Verify Phase B — custom-course New / Import / Export round-trip.

Pipeline:
  1) New Course "my_track" -> empty collection under MKGP2_Course/ with the
     four filename props initialized to <name>.bin etc.
  2) Import Course from the vanilla mr_highway_short file-set -> one
     collection holding 1 collision pair + 1 line root + 2 auto meshes,
     correctly tagged with mkgp2_kind / mkgp2_*_bin / mkgp2_bin_dir.
  3) Export Course of (2) into a temp directory -> 4 files appear and the
     collision counts match the source.

  blender --background --python tools/test_addon_custom_course.py
"""

import bpy
import sys
import tempfile
import traceback
from pathlib import Path

ADDON_DIR = r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\tools\blender"
BIN_DIR = Path(r"C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files")
SCENE_JSON = Path(r"C:\Users\naari\Documents\blender\mr_highway_export\scene.json")


def _collision_counts(path):
    import blender_import_collision as col_imp
    _hdr, tris, walls = col_imp.parse_collision_bin(str(path))
    return len(tris), len(walls)


def main():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import blender_addon_mkgp2_course as addon
    addon.register()
    addon.reload_modules()
    print("[test] addon registered")

    try:
        # ---- B1: New Course --------------------------------------------
        result = bpy.ops.scene.mkgp2_new_course(
            'EXEC_DEFAULT',
            name="my_track",
            bin_dir=str(BIN_DIR),
        )
        assert result == {'FINISHED'}, f"new_course result: {result}"
        coll = bpy.data.collections.get("my_track")
        assert coll is not None
        assert coll.get("mkgp2_kind") == "course"
        assert coll.get("mkgp2_collision_bin") == "my_track.bin"
        assert coll.get("mkgp2_line_bin") == "my_track_line.bin"
        assert coll.get("mkgp2_auto_f_bin") == "my_track_Auto.bin"
        assert coll.get("mkgp2_auto_r_bin") == "my_track_Auto_R.bin"
        assert coll.get("mkgp2_bin_dir") == str(BIN_DIR)
        # Confirm it lives under MKGP2_Course/
        root = bpy.data.collections.get("MKGP2_Course")
        assert root is not None
        assert any(c.name == "my_track" for c in root.children)
        # No objects yet
        assert len(coll.all_objects) == 0
        print("[test] B1 New Course: empty collection with templated props OK")

        # Duplicate name should be rejected
        try:
            bpy.ops.scene.mkgp2_new_course('EXEC_DEFAULT', name="my_track")
            print("[test] WARN: duplicate name was not rejected")
        except RuntimeError as ex:
            print(f"[test] B1 duplicate name rejected: {ex}")

        # ---- B2: Import Course (file-set) -----------------------------
        result = bpy.ops.scene.mkgp2_import_course(
            'EXEC_DEFAULT',
            name="vanilla_short",
            collision_path=str(BIN_DIR / "mr_highway_short.bin"),
            line_path=str(BIN_DIR / "mr_highway_short_line.bin"),
            auto_f_path=str(BIN_DIR / "mr_highway_short_Auto.bin"),
            auto_r_path=str(BIN_DIR / "mr_highway_short_Auto_R.bin"),
        )
        assert result == {'FINISHED'}, f"import_course result: {result}"
        course = bpy.data.collections.get("vanilla_short")
        assert course is not None
        assert course.get("mkgp2_kind") == "course"
        assert course.get("mkgp2_collision_bin") == "mr_highway_short.bin"
        assert course.get("mkgp2_line_bin") == "mr_highway_short_line.bin"
        assert course.get("mkgp2_auto_f_bin") == "mr_highway_short_Auto.bin"
        assert course.get("mkgp2_auto_r_bin") == "mr_highway_short_Auto_R.bin"
        assert course.get("mkgp2_bin_dir") == str(BIN_DIR)

        names = [o.name for o in course.all_objects]
        # 1 CollisionMesh + 1 WallSegments + 1 line root + 7 line variants + 2 auto meshes
        col_obj = next((o for o in course.all_objects if o.name.startswith("CollisionMesh")), None)
        wall_obj = next((o for o in course.all_objects if o.name.startswith("WallSegments")), None)
        line_root = next((o for o in course.all_objects if o.type == 'EMPTY' and o.name.endswith("_line")), None)
        auto_f = next((o for o in course.all_objects if o.get("mkgp2_auto_role") == "F"), None)
        auto_r = next((o for o in course.all_objects if o.get("mkgp2_auto_role") == "R"), None)
        assert col_obj is not None, f"no CollisionMesh in collection; have {names}"
        assert wall_obj is not None
        assert line_root is not None
        assert auto_f is not None
        assert auto_r is not None
        # Auto F / R roles tagged
        assert auto_f.get("mkgp2_auto_role") == "F"
        assert auto_r.get("mkgp2_auto_role") == "R"
        print(f"[test] B2 Import Course: {len(course.all_objects)} objects, all tagged correctly")

        # ---- B3: Export Course ----------------------------------------
        # Make sure the active layer collection points at our course.
        # Find the layer-collection that wraps our `course`.
        def _find_layer_coll(layer_root, target):
            if layer_root.collection is target:
                return layer_root
            for child in layer_root.children:
                hit = _find_layer_coll(child, target)
                if hit is not None:
                    return hit
            return None

        lc = _find_layer_coll(bpy.context.view_layer.layer_collection, course)
        assert lc is not None, "could not locate layer collection for course"
        bpy.context.view_layer.active_layer_collection = lc

        with tempfile.TemporaryDirectory() as td:
            # Override the bin_dir prop so output goes to the temp dir
            course["mkgp2_bin_dir"] = td
            result = bpy.ops.scene.mkgp2_export_course('EXEC_DEFAULT')
            assert result == {'FINISHED'}, f"export_course result: {result}"
            written = sorted(p.name for p in Path(td).glob("*.bin"))
            expected = sorted([
                "mr_highway_short.bin",
                "mr_highway_short_line.bin",
                "mr_highway_short_Auto.bin",
                "mr_highway_short_Auto_R.bin",
            ])
            assert written == expected, f"unexpected written set: {written}"
            print(f"[test] B3 Export Course: 4 files written: {written}")

            # Round-trip collision counts
            src = _collision_counts(BIN_DIR / "mr_highway_short.bin")
            out = _collision_counts(Path(td) / "mr_highway_short.bin")
            assert src == out, f"collision counts diverged src={src} out={out}"
            print(f"[test] collision counts {src} match")

        # ---- Detect: an active member of the course collection should
        #              suggest the course exporter -----------------------
        bpy.context.view_layer.objects.active = col_obj
        hint, op_id, _icon = addon._detect_export_target(col_obj)
        assert op_id == "scene.mkgp2_export_course", \
            f"detect should route course members to course export, got {op_id}"
        assert "course:" in hint
        print(f"[test] _detect_export_target picks course exporter: {hint}")

        # ---- B4: Import Course with HSD slot --------------------------
        # Confirms an HSD scene.json bundle nests inside the course
        # collection and the .dat name lands on mkgp2_hsd_dat.
        if not SCENE_JSON.exists():
            print(f"[test] B4 SKIP: scene.json bundle not found at {SCENE_JSON}")
        else:
            colls_before = set(bpy.data.collections)
            result = bpy.ops.scene.mkgp2_import_course(
                'EXEC_DEFAULT',
                name="vanilla_with_hsd",
                collision_path=str(BIN_DIR / "mr_highway_short.bin"),
                line_path="",
                auto_f_path="",
                auto_r_path="",
                hsd_path=str(SCENE_JSON),
            )
            assert result == {'FINISHED'}, f"hsd import_course result: {result}"
            course_h = bpy.data.collections.get("vanilla_with_hsd")
            assert course_h is not None
            dat_name = course_h.get("mkgp2_hsd_dat") or ""
            assert dat_name, "mkgp2_hsd_dat should be populated from scene.json source_dat"
            print(f"[test] B4 mkgp2_hsd_dat = {dat_name!r}")
            new_colls = [c for c in bpy.data.collections
                         if c not in colls_before and c is not course_h]
            assert any(c.name in [cc.name for cc in course_h.children]
                       for c in new_colls), \
                "imported HSD collection should be nested under course collection"
            # New HSD collection should be the source of dat_name (mkgp2_source_dat)
            hsd_child = next((c for c in course_h.children
                              if c.get("mkgp2_source_dat")), None)
            assert hsd_child is not None, \
                "no nested HSD collection with mkgp2_source_dat"
            assert hsd_child.get("mkgp2_source_dat") == dat_name
            n_hsd = sum(1 for o in hsd_child.all_objects if o.type == 'MESH')
            print(f"[test] B4 HSD nest OK: {hsd_child.name} ({n_hsd} meshes)")

        addon.unregister()
        print("[test] PASS")
    except Exception:
        traceback.print_exc()
        print("[test] FAIL")
        sys.exit(1)


main()
