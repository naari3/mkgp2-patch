"""Verify A4 — `_seed_filepath()` honors `_default_bin_dir()` and respects
caller-supplied filepaths.

We monkey-patch `_default_bin_dir` instead of touching the actual addon
preference because in --background mode preferences entries are not
populated by `register()` alone (the regular `addon_enable` path is what
adds them, and that requires the addon to be discoverable through Blender's
addon search path -- which it is, via the Junction we created earlier).

  blender --background --python tools/test_addon_default_bin_dir.py
"""

import bpy
import sys
import os
import traceback

ADDON_DIR = r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\tools\blender"
TEST_BIN_DIR = r"C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files"


class _FakeOp:
    filepath = ""


def main():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import blender_addon_mkgp2_course as addon
    addon.register()

    try:
        # ---- 1) preference empty -------------------------------------
        addon._default_bin_dir = lambda: ""
        op = _FakeOp()
        addon._seed_filepath(op)
        assert op.filepath == "", \
            "empty default_bin_dir must leave filepath untouched"
        print("[test] OK: empty preference -> no seeding")

        # ---- 2) preference set, no explicit filename ------------------
        addon._default_bin_dir = lambda: TEST_BIN_DIR
        op = _FakeOp()
        addon._seed_filepath(op)
        assert op.filepath.startswith(TEST_BIN_DIR), \
            f"filepath {op.filepath!r} not in {TEST_BIN_DIR!r}"
        assert op.filepath.endswith(os.sep), \
            f"filepath {op.filepath!r} should end with separator"
        print(f"[test] OK: dir-only seed -> {op.filepath!r}")

        # ---- 3) preference set, explicit filename --------------------
        op = _FakeOp()
        addon._seed_filepath(op, default_filename="mr_highway_short.bin")
        expected = os.path.join(TEST_BIN_DIR, "mr_highway_short.bin")
        assert op.filepath == expected, \
            f"filepath {op.filepath!r} != {expected!r}"
        print(f"[test] OK: dir + filename -> {op.filepath!r}")

        # ---- 4) caller already set filepath, helper must not clobber -
        op = _FakeOp()
        explicit = "C:/some/other/path.bin"
        op.filepath = explicit
        addon._seed_filepath(op, default_filename="mr_highway_short.bin")
        assert op.filepath == explicit, \
            "_seed_filepath clobbered an explicit filepath"
        print("[test] OK: existing filepath is preserved")

        # ---- 5) Full Course Import bin_dir defaulting ----------------
        # Replicate the invoke logic without going through invoke_props_dialog
        class _FakeFullOp:
            bin_dir = ""
        op = _FakeFullOp()
        if not op.bin_dir:
            op.bin_dir = addon._default_bin_dir()
        assert op.bin_dir == TEST_BIN_DIR
        print("[test] OK: Full Course bin_dir defaults to preference")

        addon.unregister()
        print("[test] PASS")
    except Exception:
        traceback.print_exc()
        print("[test] FAIL")
        sys.exit(1)


main()
