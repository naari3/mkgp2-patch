"""Reproduces the actual install path: addon loaded from
`%APPDATA%\\Blender Foundation\\Blender\\<ver>\\scripts\\addons\\mkgp2_course`
(an NTFS junction). Verifies _resolve_source_path() resolves the junction
to the real repo location, so blender_import_* modules can be imported.

  blender --background --python tools/test_addon_via_junction.py
"""

import bpy
import sys
import importlib
import traceback

ADDONS = r"C:\Users\naari\AppData\Roaming\Blender Foundation\Blender\4.3\scripts\addons"
ADDON_NAME = "mkgp2_course"  # the junction directory name


def main():
    if ADDONS not in sys.path:
        sys.path.insert(0, ADDONS)

    # Wipe any cached imports so we exercise the cold path
    for name in (
        ADDON_NAME,
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
        addon = importlib.import_module(ADDON_NAME)
        print(f"[test] addon imported: {addon.__file__}")
        print(f"[test] _resolve_source_path() = {addon._resolve_source_path()}")
        addon.register()
        ok, err = addon.reload_modules()
        print(f"[test] reload_modules() ok={ok}  err={err}")
        assert ok, f"reload_modules failed: {err}"
        # Sanity: the importer modules are reachable
        assert addon.hsd_imp is not None
        assert addon.col_imp is not None
        assert addon.line_imp is not None
        assert addon.auto_imp is not None
        addon.unregister()
        print("[test] PASS")
    except Exception:
        traceback.print_exc()
        print("[test] FAIL")
        sys.exit(1)


main()
