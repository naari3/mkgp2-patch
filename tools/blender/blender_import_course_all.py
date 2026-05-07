"""
MKGP2 Course Unified Importer (PoC orchestrator)

Imports HSD mesh + collision + line + auto for one course into a single
Blender scene, all sharing the same Game-Y-up → Blender-Z-up coordinate
transform (X=GX, Y=-GZ, Z=GY).

Usage:
  blender --background --python blender_import_course_all.py -- \
    <scene-json> <bin-dir> <prefix> [save-blend]

  Example:
    blender --background --python blender_import_course_all.py -- \
      C:/Users/naari/Documents/blender/mr_highway_export/scene.json \
      "C:/Users/naari/Documents/Dolphin ROMs/Triforce/mkgp2/files" \
      mr_highway \
      C:/Users/naari/Documents/blender/mr_highway_full.blend
"""

import bpy
import os
import sys
from pathlib import Path

# Add mkgp2docs to sys.path so the per-asset importers can be imported.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import blender_import_hsd as hsd_imp
import blender_import_collision as col_imp
import blender_import_line as line_imp
import blender_import_auto as auto_imp


def try_import(label, fn, path):
    p = Path(path)
    if not p.exists():
        print(f"  SKIP {label}: missing {p}")
        return
    print(f"\n>>> {label}: {p.name}")
    try:
        fn(str(p))
    except Exception as ex:
        print(f"  ERR {label}: {ex}")


def import_course(scene_json, bin_dir, prefix):
    bin_dir = Path(bin_dir)

    # 1. HSD mesh (= visual reference)
    print(f"\n=== HSD mesh ===")
    hsd_imp.import_scene(scene_json)

    # 2. Collision
    print(f"\n=== Collision ===")
    try_import("collision_short", col_imp.import_collision, bin_dir / f"{prefix}_short.bin")
    try_import("collision_long",  col_imp.import_collision, bin_dir / f"{prefix}_long.bin")

    # 3. Line waypoints
    print(f"\n=== Line ===")
    try_import("line_short", line_imp.import_line, bin_dir / f"{prefix}_short_line.bin")
    try_import("line_long",  line_imp.import_line, bin_dir / f"{prefix}_long_line.bin")

    # 4. Auto AI paths
    print(f"\n=== Auto ===")
    try_import("auto_short_F", auto_imp.import_auto, bin_dir / f"{prefix}_short_Auto.bin")
    try_import("auto_short_R", auto_imp.import_auto, bin_dir / f"{prefix}_short_Auto_R.bin")
    try_import("auto_long_F",  auto_imp.import_auto, bin_dir / f"{prefix}_long_Auto.bin")
    try_import("auto_long_R",  auto_imp.import_auto, bin_dir / f"{prefix}_long_Auto_R.bin")

    print(f"\n=== Done ===")


if __name__ == "__main__":
    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    if len(argv) < 3:
        print("usage: ... -- <scene-json> <bin-dir> <prefix> [save-blend]")
        sys.exit(1)
    scene_json, bin_dir, prefix = argv[0], argv[1], argv[2]
    import_course(scene_json, bin_dir, prefix)
    if len(argv) >= 4:
        save_path = argv[3]
        bpy.ops.wm.save_as_mainfile(filepath=save_path)
        print(f"  saved: {save_path}")
