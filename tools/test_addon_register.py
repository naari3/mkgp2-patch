"""Run inside `blender --background --python` to verify the MKGP2 addon
can be imported, registered and unregistered without raising.

  blender --background --python tools/test_addon_register.py
"""

import sys
import importlib
import traceback

ADDON_DIR = r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\tools\blender"


def main():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)

    # Force-reload importer/exporter modules so we exercise reload_modules() path
    for name in (
        "blender_addon_mkgp2_course",
        "blender_import_hsd",
        "blender_import_collision",
        "blender_import_line",
        "blender_import_auto",
        "blender_import_course_all",
        "blender_export_line",
        "blender_export_auto",
        "blender_export_collision",
    ):
        sys.modules.pop(name, None)

    try:
        addon = importlib.import_module("blender_addon_mkgp2_course")
        print("[test] import OK")
        addon.register()
        print("[test] register OK")

        # Confirm operators are present
        import bpy
        ops = [
            "import_scene.mkgp2_hsd_json",
            "import_mesh.mkgp2_collision_bin",
            "import_mesh.mkgp2_line_bin",
            "import_mesh.mkgp2_auto_bin",
            "import_scene.mkgp2_full_course",
            "export_scene.mkgp2_line_bin",
            "export_scene.mkgp2_auto_bin",
            "export_mesh.mkgp2_collision_bin",
            "mkgp2.reload_modules",
        ]
        for op in ops:
            mod, name = op.split(".")
            ns = getattr(bpy.ops, mod)
            assert hasattr(ns, name), f"missing operator: {op}"
        print(f"[test] operators registered: {len(ops)}")

        # Confirm panel is present
        assert hasattr(bpy.types, "MKGP2_PT_course_panel"), "panel missing"
        print("[test] panel registered")

        addon.unregister()
        print("[test] unregister OK")
        print("[test] PASS")
    except Exception:
        traceback.print_exc()
        print("[test] FAIL")
        sys.exit(1)


main()
