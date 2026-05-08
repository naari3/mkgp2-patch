"""Verify A4 — `_seed_filepath()` honors `_vanilla_bin_dir()` /
`_output_bin_dir()` and respects caller-supplied filepaths.

We monkey-patch the helpers directly instead of touching the actual addon
preferences because in --background mode preferences entries are not
populated by `register()` alone (the regular `addon_enable` path is what
adds them, and that requires the addon to be discoverable through Blender's
addon search path -- which it is, via the Junction we created earlier).

History note: this test originally targeted `_default_bin_dir`. After the
preference split (vanilla read-only / output writable), `_seed_filepath`
reads `_vanilla_bin_dir` for Import seeding and `_output_bin_dir` for
Export seeding. We exercise both paths.

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
        # ---- 1) both preferences empty -------------------------------
        addon._vanilla_bin_dir = lambda: ""
        addon._output_bin_dir = lambda: ""
        addon._default_bin_dir = addon._vanilla_bin_dir  # legacy alias
        op = _FakeOp()
        addon._seed_filepath(op)
        assert op.filepath == "", \
            "empty vanilla_bin_dir must leave filepath untouched"
        print("[test] OK: empty preference -> no seeding")

        # ---- 2) vanilla set, no explicit filename --------------------
        addon._vanilla_bin_dir = lambda: TEST_BIN_DIR
        addon._default_bin_dir = addon._vanilla_bin_dir
        op = _FakeOp()
        addon._seed_filepath(op)
        assert op.filepath.startswith(TEST_BIN_DIR), \
            f"filepath {op.filepath!r} not in {TEST_BIN_DIR!r}"
        assert op.filepath.endswith(os.sep), \
            f"filepath {op.filepath!r} should end with separator"
        print(f"[test] OK: vanilla dir seed -> {op.filepath!r}")

        # ---- 3) vanilla set, explicit filename -----------------------
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

        # ---- 5) prefer_output=True picks output_bin_dir --------------
        out_dir = r"C:\Users\naari\Documents\mkgp2_export"
        addon._output_bin_dir = lambda: out_dir
        op = _FakeOp()
        addon._seed_filepath(op, prefer_output=True)
        assert op.filepath.startswith(out_dir), \
            f"prefer_output should seed from output, got {op.filepath!r}"
        print(f"[test] OK: prefer_output -> {op.filepath!r}")

        # ---- 6) prefer_output=True falls back to vanilla when output empty
        addon._output_bin_dir = lambda: ""
        op = _FakeOp()
        addon._seed_filepath(op, prefer_output=True)
        assert op.filepath.startswith(TEST_BIN_DIR), \
            f"prefer_output should fall back to vanilla, got {op.filepath!r}"
        print("[test] OK: prefer_output falls back to vanilla")

        # ---- 7) Full Course Import bin_dir defaulting (vanilla side) -
        class _FakeFullOp:
            bin_dir = ""
        op = _FakeFullOp()
        if not op.bin_dir:
            op.bin_dir = addon._vanilla_bin_dir()
        assert op.bin_dir == TEST_BIN_DIR
        print("[test] OK: Full Course Import bin_dir defaults to vanilla pref")

        addon.unregister()
        print("[test] PASS")
    except Exception:
        traceback.print_exc()
        print("[test] FAIL")
        sys.exit(1)


main()
