"""Headless Blender re-export of vis:my_course to my_course.dat.

Run via:
    "/c/Program Files/Blender Foundation/Blender 4.3/blender.exe" \
        --background \
        "C:\\Users\\naari\\Documents\\blender\\my_course\\my_course.blend" \
        --python tools/_blender_headless_promote.py \
        -- \
        "C:\\Users\\naari\\src\\github.com\\naari3\\mkgp2-patch\\features\\cup_page3\\files\\my_course.dat"

The vis: -> .dat pipeline is fully independent: no vanilla `.dat` is
read, scene_data is allocated from scratch via
`hsdraw.Dat.alloc_scene_data_minimal()`.

addon import path のセットアップは __init__.py 経由ではなく、_promote_vis_to_hsd
モジュールを直接 import して promote_vis_to_dat を呼ぶ (addon 登録不要)。
vendored hsdraw は addon の vendor 配下から sys.path で読む。
"""

import sys
import bpy
from pathlib import Path

# CLI 引数: blender 自身の引数を飛ばして "--" 以降だけ拾う
argv = sys.argv
if "--" not in argv:
    print("usage error: pass <output_dat> after '--'", file=sys.stderr)
    bpy.ops.wm.quit_blender()
args = argv[argv.index("--") + 1:]
if len(args) != 1:
    print(f"need 1 arg (output_dat) after '--', got: {args}", file=sys.stderr)
    bpy.ops.wm.quit_blender()
output_dat = args[0]

# addon の python module path を sys.path に通す
patch_root = Path(__file__).resolve().parent.parent
addon_dir = patch_root / "tools" / "blender" / "blender_addon_mkgp2_course"
vendor = addon_dir / "vendor" / "windows_x86_64"
sys.path.insert(0, str(addon_dir))
sys.path.insert(0, str(vendor))

# addon の __init__ を経由せず、純関数だけ import (bake + promote)
import importlib.util  # noqa: E402
def _load_module(name):
    spec = importlib.util.spec_from_file_location(name, str(addon_dir / f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

bake_mod = _load_module("_bake_vis_textures")
promote_mod = _load_module("_promote_vis_to_hsd")

# vis:* collection を探す
vis = None
for c in bpy.data.collections:
    if c.name.startswith("vis:"):
        vis = c
        break
if vis is None:
    print("ERROR: no vis:* collection found in this .blend", file=sys.stderr)
    bpy.ops.wm.quit_blender()
print(f"using collection: {vis.name}")

# Pass 1: bake — make sure each material has an Image Texture node attached
# (BSDF.Base Color が plain default_value のままだと export 時に Blender 側で
# 確認できないので、4x4 単色 PNG を生成して node 接続して .blend に保存する)。
print("=== bake pass ===")
bake_stats = bake_mod.bake_vis_collection_materials(vis, log_fn=print)
print(f"bake: {bake_stats}")

# Save .blend so the user can inspect the attached Image Texture nodes in
# Blender (open my_course.blend → Material editor → see ShaderNodeTexImage).
if bake_stats["attached"] > 0:
    bpy.ops.wm.save_mainfile()
    print(f"saved .blend with {bake_stats['attached']} new image-texture nodes")

# Pass 2: promote — read materials (now guaranteed to have Image Texture
# nodes via the bake pass) and synthesize the .dat from scratch.
print("=== promote pass ===")
stats = promote_mod.promote_vis_to_dat(
    vis,
    output_dat,
    log_fn=print,
)
print(f"done: {stats}")
