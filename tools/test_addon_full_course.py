"""End-to-end addon test:
register → invoke MKGP2_OT_ImportFullCourse via bpy.ops → save .blend.

  blender --background --python tools/test_addon_full_course.py
"""

import bpy
import sys
import traceback
from pathlib import Path

ADDON_DIR = r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\tools\blender"
SCENE_JSON = r"C:\Users\naari\Documents\blender\mr_highway_export\scene.json"
BIN_DIR = r"C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files"
PREFIX = "mr_highway"
SAVE_PATH = r"C:\Users\naari\Documents\blender\mr_highway_addon_test.blend"


def main():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import blender_addon_mkgp2_course as addon
    addon.register()
    print("[test] addon registered")

    try:
        result = bpy.ops.import_scene.mkgp2_full_course(
            'EXEC_DEFAULT',
            scene_json=SCENE_JSON,
            bin_dir=BIN_DIR,
            prefix=PREFIX,
        )
        print(f"[test] import result: {result}")
        assert result == {'FINISHED'}, f"unexpected result: {result}"

        # Sanity: the orchestrator should have created at least the HSD collection
        # and the collision/line/auto objects.
        n_objs = len(bpy.data.objects)
        n_colls = len(bpy.data.collections)
        print(f"[test] objects={n_objs}, collections={n_colls}")
        assert n_objs > 0, "no objects imported"

        # Save
        bpy.ops.wm.save_as_mainfile(filepath=SAVE_PATH)
        size = Path(SAVE_PATH).stat().st_size
        print(f"[test] saved {size} bytes -> {SAVE_PATH}")
        print("[test] PASS")
    except Exception:
        traceback.print_exc()
        print("[test] FAIL")
        sys.exit(1)


main()
