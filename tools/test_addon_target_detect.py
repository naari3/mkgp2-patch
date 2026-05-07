"""Verify A2 — the panel target detector classifies each Outliner object
imported by Full Course back to the right export operator.

Inputs (after `import_scene.mkgp2_full_course`):
  CollisionMesh_<stem> + WallSegments_<stem>     -> collision exporter
  <stem>_line empty root                         -> line exporter
  LineVariant_<i>_<stem>_line mesh               -> line exporter
  Auto_<stem> mesh                               -> auto exporter
  HSD mesh / random object                       -> no exporter
  None                                           -> no active

  blender --background --python tools/test_addon_target_detect.py
"""

import bpy
import sys
import traceback
from pathlib import Path

ADDON_DIR = r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\tools\blender"
SCENE_JSON = r"C:\Users\naari\Documents\blender\mr_highway_export\scene.json"
BIN_DIR = r"C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files"
PREFIX = "mr_highway"


def main():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import blender_addon_mkgp2_course as addon
    addon.register()
    addon.reload_modules()

    try:
        bpy.ops.import_scene.mkgp2_full_course(
            'EXEC_DEFAULT',
            scene_json=SCENE_JSON,
            bin_dir=BIN_DIR,
            prefix=PREFIX,
        )
        detect = addon._detect_export_target

        cases = [
            (bpy.data.objects.get(f"CollisionMesh_{PREFIX}_short"),
             "collision",
             "export_mesh.mkgp2_collision_bin"),
            (bpy.data.objects.get(f"WallSegments_{PREFIX}_short"),
             "collision",
             "export_mesh.mkgp2_collision_bin"),
            # importer doubles "_line": stem = "mr_highway_short_line", root = "<stem>_line"
            (bpy.data.objects.get(f"{PREFIX}_short_line_line"),
             "line root",
             "export_scene.mkgp2_line_bin"),
            (bpy.data.objects.get(f"LineVariant_0_{PREFIX}_short_line"),
             "line variant",
             "export_scene.mkgp2_line_bin"),
            (bpy.data.objects.get(f"Auto_{PREFIX}_short_Auto"),
             "auto path",
             "export_scene.mkgp2_auto_bin"),
        ]

        for obj, expect_substr, expect_op in cases:
            assert obj is not None, f"missing test fixture object"
            hint, op_id, icon = detect(obj)
            print(f"  {obj.name}: hint={hint!r} op={op_id!r} icon={icon!r}")
            assert expect_substr in hint, \
                f"{obj.name}: expected '{expect_substr}' in hint, got {hint!r}"
            assert op_id == expect_op, \
                f"{obj.name}: expected op {expect_op!r}, got {op_id!r}"

        # Object that doesn't fit any category (an HSD mesh from the bundle)
        hsd_obj = next((o for o in bpy.data.objects
                        if o.type == 'MESH'
                        and not o.name.startswith(("CollisionMesh_", "WallSegments_",
                                                   "LineVariant_", "Auto_"))),
                       None)
        assert hsd_obj is not None, "no HSD mesh in scene to test the unknown case"
        hint, op_id, icon = detect(hsd_obj)
        print(f"  {hsd_obj.name}: hint={hint!r} op={op_id!r}")
        assert "not a known" in hint
        assert op_id is None

        # No active object
        hint, op_id, icon = detect(None)
        print(f"  None: hint={hint!r} op={op_id!r}")
        assert "no active" in hint
        assert op_id is None

        addon.unregister()
        print("[test] PASS")
    except Exception:
        traceback.print_exc()
        print("[test] FAIL")
        sys.exit(1)


main()
