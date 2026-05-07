"""Verify T1b — visualization operators and overlay toggles.

Operators tested:
  - mkgp2.show_only_variant / show_all_variants / hide_all_variants
  - mkgp2.show_collision_material
  - mkgp2.add_origin_marker

Toggles tested:
  - WindowManager.mkgp2_show_arrows  (handler attached/detached)
  - WindowManager.mkgp2_show_waypoint_ids

Draw handler *output* cannot be visually verified in --background mode,
so we only confirm the handler registration changes when the toggle
flips and that handler callbacks don't throw on a synthetic draw.

  blender --background --python tools/test_addon_visualization.py
"""

import bpy
import sys
import traceback
from pathlib import Path

ADDON_DIR = r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\tools\blender"
PATCH_DIR = Path(r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\features\cup_page3\files")


def main():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import blender_addon_mkgp2_course as addon
    addon.register()
    addon.reload_modules()
    print("[test] addon registered")

    try:
        # ---- Setup: a clean course with line variants ----------------
        bpy.ops.scene.mkgp2_import_course(
            'EXEC_DEFAULT',
            name="vis_course",
            collision_path=str(PATCH_DIR / "grd_short.bin"),
            line_path=str(PATCH_DIR / "test_course_short_line.bin"),
            auto_f_path="",
            auto_r_path="",
        )
        coll = bpy.data.collections["vis_course"]
        line_root = next(o for o in coll.all_objects
                         if o.type == 'EMPTY' and o.name.endswith("_line"))
        variants = sorted(
            (c for c in line_root.children
             if c.type == 'MESH' and c.name.startswith("LineVariant_")),
            key=lambda o: int(o.name.split("_")[1]))
        n_variants = len(variants)
        assert n_variants >= 2, "need >=2 variants to test visibility"
        print(f"[test] setup: {n_variants} line variants under {line_root.name}")

        # Activate course in layer collection
        def find_lc(layer_root, target):
            if layer_root.collection is target:
                return layer_root
            for ch in layer_root.children:
                hit = find_lc(ch, target)
                if hit is not None:
                    return hit
            return None
        lc = find_lc(bpy.context.view_layer.layer_collection, coll)
        bpy.context.view_layer.active_layer_collection = lc

        # Make the line root active so _resolve_line_root resolves it
        # via the active-object branch (background-mode safe).
        bpy.context.view_layer.objects.active = line_root

        # ---- T1b-1: Show only one variant ----------------------------
        result = bpy.ops.mkgp2.show_only_variant(variant_index=0)
        assert result == {'FINISHED'}
        for v in variants:
            idx = int(v.name.split("_")[1])
            expect = (idx != 0)
            assert v.hide_viewport == expect, \
                f"variant {idx} hide={v.hide_viewport}, expected {expect}"
        print("[test] V1b-1a: show_only_variant(0) hides v1..vN")

        result = bpy.ops.mkgp2.show_only_variant(variant_index=3)
        assert result == {'FINISHED'}
        for v in variants:
            idx = int(v.name.split("_")[1])
            expect = (idx != 3)
            assert v.hide_viewport == expect, \
                f"variant {idx} hide={v.hide_viewport}, expected {expect}"
        print("[test] V1b-1b: show_only_variant(3) re-targets")

        # ---- T1b-1: Show all + hide all ------------------------------
        bpy.ops.mkgp2.show_all_variants()
        assert all(not v.hide_viewport for v in variants), \
            "show_all should make every variant visible"
        bpy.ops.mkgp2.hide_all_variants()
        assert all(v.hide_viewport for v in variants), \
            "hide_all should make every variant hidden"
        print("[test] V1b-1c: show_all / hide_all")

        # ---- T1b-2 + T1b-3: Overlay toggles -------------------------
        wm = bpy.context.window_manager
        # Initially false; flipping should attach a handler.
        assert wm.mkgp2_show_arrows is False
        assert wm.mkgp2_show_waypoint_ids is False
        wm.mkgp2_show_arrows = True
        assert addon._draw_handles["arrows"] is not None, \
            "arrows handler should be attached after toggle on"
        wm.mkgp2_show_arrows = False
        assert addon._draw_handles["arrows"] is None, \
            "arrows handler should be detached after toggle off"
        wm.mkgp2_show_waypoint_ids = True
        assert addon._draw_handles["waypoints"] is not None
        wm.mkgp2_show_waypoint_ids = False
        assert addon._draw_handles["waypoints"] is None
        print("[test] V1b-2/3: overlay toggles attach/detach draw handlers")

        # Smoke-test the arrow callback runs without exceptions on a
        # populated scene (the GPU draw will silently no-op outside a
        # 3D Viewport but the iteration code must not crash).
        try:
            addon._draw_arrows_callback()
        except Exception as ex:
            # Expected: in background mode there's no shader context;
            # we accept any RuntimeError, but reject AttributeError /
            # TypeError which would indicate an addon bug.
            if isinstance(ex, (AttributeError, TypeError)):
                raise
            print(f"[test] arrows callback raised expected GPU error: "
                  f"{type(ex).__name__}")
        else:
            print("[test] arrows callback ran cleanly")

        # ---- T1b-4: Show collision material -------------------------
        col_obj = next(o for o in coll.all_objects
                       if o.name.startswith("CollisionMesh"))
        # Confirm MaterialID color attribute is present (importer wrote it)
        ml = col_obj.data.color_attributes.get("MaterialID")
        assert ml is not None, \
            "importer should populate MaterialID color attribute"
        # Run operator -- in --background there might be no 3D area, so
        # we accept either FINISHED (with a viewport) or the explicit
        # CANCELLED report.
        result = bpy.ops.mkgp2.show_collision_material()
        if result == {'FINISHED'}:
            print("[test] V1b-4: show_collision_material activated viewport mode")
        else:
            print(f"[test] V1b-4: show_collision_material: {result} "
                  f"(no 3D viewport in --background)")

        # Active color attribute should now point at MaterialID regardless
        ca = col_obj.data.color_attributes
        assert ca.active_color is not None and \
               ca.active_color.name == "MaterialID", \
            f"active color attribute is {ca.active_color and ca.active_color.name!r}"
        print("[test] V1b-4: active color attribute = MaterialID")

        # ---- T1b-5: Origin marker -----------------------------------
        result = bpy.ops.mkgp2.add_origin_marker()
        assert result == {'FINISHED'}
        marker = bpy.data.objects.get(addon.ORIGIN_MARKER_NAME)
        assert marker is not None
        assert tuple(marker.location) == (0.0, 0.0, 0.0)
        assert marker in [o for o in coll.objects], \
            f"marker should be linked under course collection (got {marker.users_collection})"
        # Re-running should re-use the existing marker, not create a
        # second one.
        result = bpy.ops.mkgp2.add_origin_marker()
        assert result == {'FINISHED'}
        markers = [o for o in bpy.data.objects
                   if o.name.startswith(addon.ORIGIN_MARKER_NAME)]
        assert len(markers) == 1, f"expected 1 marker, got {len(markers)}"
        print("[test] V1b-5: origin marker idempotent")

        addon.unregister()
        # After unregister, draw handler state must be cleared.
        assert addon._draw_handles["arrows"] is None
        assert addon._draw_handles["waypoints"] is None
        print("[test] PASS")
    except Exception:
        traceback.print_exc()
        print("[test] FAIL")
        sys.exit(1)


main()
