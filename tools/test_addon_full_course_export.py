"""Verify A3 — Full Course Export operator emits 8 .bin files matching
the Full Course Import set (collision short/long + line short/long +
auto short F/R + long F/R).

Pipeline:
  Full Course import (mr_highway) -> Full Course export to tmpdir ->
  filename / count assertions + cheap round-trip check (collision triangle
  / wall counts equal source).

  blender --background --python tools/test_addon_full_course_export.py
"""

import bpy
import sys
import tempfile
import traceback
from pathlib import Path

ADDON_DIR = r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\tools\blender"
SCENE_JSON = r"C:\Users\naari\Documents\blender\mr_highway_export\scene.json"
BIN_DIR = Path(r"C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files")
PREFIX = "mr_highway"

EXPECTED = {
    f"{PREFIX}_short.bin",
    f"{PREFIX}_long.bin",
    f"{PREFIX}_short_line.bin",
    f"{PREFIX}_long_line.bin",
    f"{PREFIX}_short_Auto.bin",
    f"{PREFIX}_short_Auto_R.bin",
    f"{PREFIX}_long_Auto.bin",
    f"{PREFIX}_long_Auto_R.bin",
}


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
        result = bpy.ops.import_scene.mkgp2_full_course(
            'EXEC_DEFAULT',
            scene_json=SCENE_JSON,
            bin_dir=str(BIN_DIR),
            prefix=PREFIX,
        )
        assert result == {'FINISHED'}
        print("[test] full course import OK")

        with tempfile.TemporaryDirectory() as td:
            result = bpy.ops.export_scene.mkgp2_full_course(
                'EXEC_DEFAULT',
                bin_dir=td,
                overwrite=True,
            )
            assert result == {'FINISHED'}, f"export op result: {result}"
            print(f"[test] full course export to {td} -> FINISHED")

            actual = {f.name for f in Path(td).glob("*.bin")}
            missing = EXPECTED - actual
            extra = actual - EXPECTED
            assert not missing, f"missing files: {sorted(missing)}"
            assert not extra, f"unexpected extra files: {sorted(extra)}"
            print(f"[test] OK: 8 files written exactly matching expected set")

            for name in sorted(EXPECTED):
                size = (Path(td) / name).stat().st_size
                assert size > 0, f"{name} is empty"
                print(f"  {name}: {size} bytes")

            # ---- collision round-trip ------------------------------------
            for stem in (f"{PREFIX}_short", f"{PREFIX}_long"):
                src = _collision_counts(BIN_DIR / f"{stem}.bin")
                out = _collision_counts(Path(td) / f"{stem}.bin")
                assert src == out, \
                    f"{stem}: counts diverge src={src} out={out}"
                print(f"[test] {stem}: collision counts {src} match")

        # ---- error case: missing bin_dir parameter ---------------------
        try:
            result = bpy.ops.export_scene.mkgp2_full_course(
                'EXEC_DEFAULT',
                bin_dir="",
            )
            print(f"[test] empty bin_dir -> result={result!r}")
            assert result == {'CANCELLED'}, f"expected CANCELLED, got {result!r}"
            print("[test] OK: empty bin_dir is rejected")
        except RuntimeError as ex:
            # bpy.ops can also raise RuntimeError when an operator reports
            # an ERROR; that's still a valid rejection signal.
            print(f"[test] empty bin_dir raised RuntimeError as expected: {ex}")

        addon.unregister()
        print("[test] PASS")
    except Exception:
        traceback.print_exc()
        print("[test] FAIL")
        sys.exit(1)


main()
