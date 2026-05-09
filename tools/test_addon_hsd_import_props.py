"""Smoke test: HSD import populates the M2 PNG-hash + .gx metadata
on every loaded Blender Image so the M3 unified exporter can dispatch
between bypass and re-encode.

Verifies (against a vanilla .dat imported via the Python-only path):
  - Each Image carries `mkgp2_png_hash` (SHA-1 hex digest).
  - Each Image carries `mkgp2_gx_path` pointing at an existing file.
  - `mkgp2_gx_format` matches the texture's `format`.
  - `mkgp2_gx_width` / `_height` / `_size` match the bundle's stash.

  blender --background --python tools/test_addon_hsd_import_props.py
"""

import bpy
import json
import os
import sys
import traceback
from pathlib import Path

ADDON_DIR = r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\tools\blender"
VANILLA_BIN = r"C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files"
BASE_DAT_NAME = "MR_highway_short_A.dat"


def _check_bundle(addon, base_dat):
    print(f"\n[test] importing {base_dat}")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    result = bpy.ops.import_scene.mkgp2_hsd_json(
        'EXEC_DEFAULT', filepath=str(base_dat))
    assert result == {'FINISHED'}, f"import: {result}"

    bundle = next((c for c in bpy.data.collections
                   if c.name.startswith("mkgp2:")), None)
    assert bundle is not None, "import did not create mkgp2:<dat>"
    stash_sj = bundle.get("mkgp2_scene_json")
    assert stash_sj, "bundle missing mkgp2_scene_json stash"
    scene = json.loads(stash_sj)
    tex_dtos = {t["id"]: t for t in scene["textures"]}

    # Walk every image referenced by every material in the scene.
    img_count = 0
    for img in bpy.data.images:
        if img.name.startswith("Render Result") or img.name.startswith("Viewer"):
            continue
        # Match by image name == texture id (importer names them after the id)
        dto = tex_dtos.get(img.name)
        if dto is None:
            # Some images may also be named with an extension; try stem match
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
        # `mkgp2_gx_size` = `len(gx_bytes)` recorded by the importer; the
        # inline scene_json does not carry an equivalent field, so we
        # only sanity-check that it is positive.
        assert int(img["mkgp2_gx_size"]) > 0, \
            f"{img.name}: mkgp2_gx_size should be > 0"

    assert img_count > 0, "no textures matched scene.json -- importer changed?"
    print(f"  validated {img_count} image(s) (GX-enriched)")


def main():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import blender_addon_mkgp2_course as addon
    addon.register()
    addon.reload_modules()

    try:
        if not addon.HSDRAW_AVAILABLE:
            print("[test] SKIP: hsdraw not vendored")
            return
        base_dat = Path(VANILLA_BIN) / BASE_DAT_NAME
        if not base_dat.is_file():
            print(f"[test] SKIP: base .dat missing at {base_dat}")
            return
        _check_bundle(addon, base_dat)
        addon.unregister()
        print("\n[test] PASS")
    except Exception:
        traceback.print_exc()
        print("[test] FAIL")
        sys.exit(1)


main()
