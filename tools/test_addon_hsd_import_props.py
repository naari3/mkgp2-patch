"""Smoke test: HSD import populates the M2 PNG-hash + .gx metadata
on every loaded Blender Image so the M3 unified exporter can dispatch
between bypass and re-encode.

Verifies (against an M1+ bundle):
  - Each Image carries `mkgp2_png_hash` (SHA-1 hex digest).
  - Each Image carries `mkgp2_gx_path` pointing at an existing file.
  - `mkgp2_gx_format` matches the scene.json texture's `format`.
  - `mkgp2_gx_width` / `_height` / `_size` match scene.json.

Optional legacy regression: if a `--legacy <scene.json>` arg is passed
(an old bundle that lacks `gx_file`) the test asserts import still
succeeds and Images get the PNG hash but no .gx props.

Usage:
  blender --background --python tools/test_addon_hsd_import_props.py \
      -- <m1_bundle_scene.json> [legacy_bundle_scene.json]
"""

import bpy
import json
import os
import sys
import traceback
from pathlib import Path

ADDON_DIR = r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\tools\blender"


def _load_scene_textures(scene_json):
    with open(scene_json, "r", encoding="utf-8") as f:
        s = json.load(f)
    return {t["id"]: t for t in s["textures"]}


def _check_bundle(addon, scene_json, *, expect_gx):
    print(f"\n[test] importing {scene_json}")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon.hsd_imp.import_scene(scene_json)

    tex_dtos = _load_scene_textures(scene_json)
    bundle = next((c for c in bpy.data.collections
                   if c.name.startswith("mkgp2:")), None)
    assert bundle is not None, "import did not create mkgp2:<dat>"

    # Walk every image referenced by every material in the scene.
    img_count = 0
    for img in bpy.data.images:
        if img.name.startswith("Render Result") or img.name.startswith("Viewer"):
            continue
        # Match by filename stem == texture id
        stem = Path(img.filepath).stem if img.filepath else img.name
        dto = tex_dtos.get(stem)
        if dto is None:
            continue
        img_count += 1
        assert "mkgp2_png_hash" in img.keys(), \
            f"{img.name}: mkgp2_png_hash missing"
        h = img["mkgp2_png_hash"]
        assert isinstance(h, str) and len(h) == 40, \
            f"{img.name}: mkgp2_png_hash should be 40-hex SHA-1, got {h!r}"

        if expect_gx:
            for k in ("mkgp2_gx_path", "mkgp2_gx_format",
                      "mkgp2_gx_width", "mkgp2_gx_height", "mkgp2_gx_size"):
                assert k in img.keys(), f"{img.name}: {k} missing"
            assert os.path.isfile(img["mkgp2_gx_path"]), \
                f"{img.name}: gx_path does not exist: {img['mkgp2_gx_path']}"
            assert img["mkgp2_gx_format"] == dto["format"], \
                (f"{img.name}: gx_format mismatch "
                 f"({img['mkgp2_gx_format']} vs {dto['format']})")
            assert img["mkgp2_gx_width"] == dto["width"]
            assert img["mkgp2_gx_height"] == dto["height"]
            assert img["mkgp2_gx_size"] == dto["gx_size"]
        else:
            # Legacy mode: gx props absent (still acceptable)
            assert "mkgp2_gx_path" not in img.keys(), \
                f"{img.name}: legacy bundle should have no mkgp2_gx_path"

    assert img_count > 0, "no textures matched scene.json -- importer changed?"
    print(f"  validated {img_count} image(s) "
          f"({'GX-enriched' if expect_gx else 'legacy mode'})")


def main():
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = []
    if not argv:
        print("[test] usage: ... -- <m1_bundle_scene.json> [legacy.json]")
        sys.exit(2)

    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import blender_addon_mkgp2_course as addon
    addon.register()
    addon.reload_modules()

    try:
        _check_bundle(addon, argv[0], expect_gx=True)
        if len(argv) >= 2:
            _check_bundle(addon, argv[1], expect_gx=False)
        addon.unregister()
        print("\n[test] PASS")
    except Exception:
        traceback.print_exc()
        print("[test] FAIL")
        sys.exit(1)


main()
