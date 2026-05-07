"""Verify A1 — collision name conflict resolution.

After Full Course import of mr_highway, scene must contain both:
  CollisionMesh_mr_highway_short / WallSegments_mr_highway_short
  CollisionMesh_mr_highway_long  / WallSegments_mr_highway_long

Each pair carries a matching `mkgp2_collision_stem` custom property and
the exporter resolves the pair from any active member.

Round-trip: export each pair to a temp file and diff against the source
.bin. Byte-identical is a stretch goal (depends on exporter's grid
ordering); we settle for "exported file parses to the same triangle /
wall counts as the source".

  blender --background --python tools/test_addon_collision_naming.py
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


def _read_counts(path):
    """Reuse the importer's parser to count triangles and walls.

    Avoids drift between test and shipped format spec.
    """
    import blender_import_collision as col_imp
    _hdr, tris, walls = col_imp.parse_collision_bin(str(path))
    return len(tris), len(walls)


def main():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import blender_addon_mkgp2_course as addon
    addon.register()
    addon.reload_modules()
    print("[test] addon registered + modules loaded")

    try:
        result = bpy.ops.import_scene.mkgp2_full_course(
            'EXEC_DEFAULT',
            scene_json=SCENE_JSON,
            bin_dir=str(BIN_DIR),
            prefix=PREFIX,
        )
        assert result == {'FINISHED'}, f"import failed: {result}"

        # ---- Naming check ---------------------------------------------------
        for stem in (f"{PREFIX}_short", f"{PREFIX}_long"):
            cm = bpy.data.objects.get(f"CollisionMesh_{stem}")
            ws = bpy.data.objects.get(f"WallSegments_{stem}")
            assert cm is not None, f"missing CollisionMesh_{stem}"
            assert ws is not None, f"missing WallSegments_{stem}"
            assert cm.get("mkgp2_collision_stem") == stem, \
                f"CollisionMesh_{stem} stem prop wrong: {cm.get('mkgp2_collision_stem')}"
            assert ws.get("mkgp2_collision_stem") == stem
            print(f"[test] OK: CollisionMesh_{stem} ({len(cm.data.polygons)} tris) + "
                  f"WallSegments_{stem} ({len(ws.data.edges)} edges)")

        # ---- No legacy fixed names left over -------------------------------
        assert bpy.data.objects.get("CollisionMesh") is None, \
            "legacy 'CollisionMesh' (no suffix) leaked into scene"
        assert bpy.data.objects.get("WallSegments") is None, \
            "legacy 'WallSegments' (no suffix) leaked into scene"
        print("[test] OK: no legacy unsuffixed names remain")

        # ---- Round-trip both pairs -----------------------------------------
        with tempfile.TemporaryDirectory() as td:
            for stem in (f"{PREFIX}_short", f"{PREFIX}_long"):
                # Set active to the CollisionMesh half and let resolver find
                # the matching WallSegments via stem prop.
                cm = bpy.data.objects[f"CollisionMesh_{stem}"]
                bpy.context.view_layer.objects.active = cm
                out = Path(td) / f"{stem}.bin"
                result = bpy.ops.export_mesh.mkgp2_collision_bin(
                    'EXEC_DEFAULT', filepath=str(out),
                )
                assert result == {'FINISHED'}, f"{stem} export failed: {result}"

                src_tris, src_walls = _read_counts(BIN_DIR / f"{stem}.bin")
                out_tris, out_walls = _read_counts(out)
                print(f"[test] {stem}: src=(tris={src_tris}, walls={src_walls})  "
                      f"out=(tris={out_tris}, walls={out_walls})")
                assert src_tris == out_tris, f"{stem}: triangle count mismatch"
                assert src_walls == out_walls, f"{stem}: wall count mismatch"

                # Now drive the resolver from the WallSegments half too,
                # to confirm pair resolution from either member.
                ws = bpy.data.objects[f"WallSegments_{stem}"]
                bpy.context.view_layer.objects.active = ws
                out2 = Path(td) / f"{stem}_via_walls.bin"
                result = bpy.ops.export_mesh.mkgp2_collision_bin(
                    'EXEC_DEFAULT', filepath=str(out2),
                )
                assert result == {'FINISHED'}
                t2, w2 = _read_counts(out2)
                assert (t2, w2) == (out_tris, out_walls), \
                    f"{stem}: walls-driven export differs from mesh-driven"
                print(f"[test] {stem}: pair resolution from WallSegments half OK")

        addon.unregister()
        print("[test] PASS")
    except Exception:
        traceback.print_exc()
        print("[test] FAIL")
        sys.exit(1)


main()
