"""Verify scene.mkgp2_attach_hsd nests an unhosted HSD bundle into
the active course collection.

We don't have a long-side scene.json on disk, so this test fakes one:
after Promote Vanilla, we create an empty `mkgp2:MR_highway_long_A.dat`
collection at the scene root with a `mkgp2_source_dat` prop and 1
placeholder mesh, then run attach against the long course.

Coverage:
  - Long course gets the matching bundle nested
  - mkgp2_hsd_dat custom prop reflects the bundle's source_dat
  - Re-running on a course that already has a nested bundle cancels
  - Active course with no matching bundle reports ERROR

  blender --background --python tools/test_addon_attach_hsd.py
"""

import bpy
import sys
import traceback
from pathlib import Path

ADDON_DIR = r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\tools\blender"
SCENE_JSON = r"C:\Users\naari\Documents\blender\mr_highway_export\scene.json"
BIN_DIR = r"C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files"


def _activate_layer_for(coll):
    def find(layer_root, target):
        if layer_root.collection is target:
            return layer_root
        for ch in layer_root.children:
            hit = find(ch, target)
            if hit is not None:
                return hit
        return None
    lc = find(bpy.context.view_layer.layer_collection, coll)
    assert lc is not None
    bpy.context.view_layer.active_layer_collection = lc


def _make_mock_bundle(dat_name):
    """Replicate what blender_import_hsd would produce: a collection
    named `mkgp2:<dat_name>` at the scene root with a mkgp2_source_dat
    prop and at least one mesh inside."""
    coll = bpy.data.collections.new(f"mkgp2:{dat_name}")
    bpy.context.scene.collection.children.link(coll)
    coll["mkgp2_source_dat"] = dat_name
    mesh = bpy.data.meshes.new(f"{dat_name}_placeholder")
    mesh.from_pydata([(0,0,0), (1,0,0), (0,1,0)], [], [(0,1,2)])
    obj = bpy.data.objects.new(f"{dat_name}_placeholder", mesh)
    coll.objects.link(obj)
    return coll


def main():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import blender_addon_mkgp2_course as addon
    addon.register()
    addon.reload_modules()
    print("[test] addon registered")

    try:
        if not Path(SCENE_JSON).exists():
            print(f"[test] SKIP: scene.json not found at {SCENE_JSON}")
            return

        # ---- Setup: vanilla import + promote (short HSD nests on
        # short course; long is HSD-less, mkgp2_hsd_dat empty)
        bpy.ops.import_scene.mkgp2_full_course(
            'EXEC_DEFAULT',
            scene_json=SCENE_JSON,
            bin_dir=BIN_DIR,
            prefix="mr_highway",
        )
        bpy.ops.scene.mkgp2_promote_vanilla()
        long_coll = bpy.data.collections.get("mr_highway_long")
        short_coll = bpy.data.collections.get("mr_highway_short")
        assert long_coll is not None and short_coll is not None
        assert long_coll.get("mkgp2_hsd_dat") == "", \
            "long course should start without an HSD bundle"
        long_hsd_pre = next((c for c in long_coll.children
                             if c.name.startswith("mkgp2:")), None)
        assert long_hsd_pre is None
        print("[test] V1 setup: long has no HSD, short has one")

        # ---- A1: Drop a fake long bundle at scene root, attach -----
        bundle = _make_mock_bundle("MR_highway_long_A.dat")
        assert addon._find_parent_collection(bundle) is bpy.context.scene.collection

        _activate_layer_for(long_coll)
        result = bpy.ops.scene.mkgp2_attach_hsd()
        assert result == {'FINISHED'}, f"attach: {result}"

        nested = next((c for c in long_coll.children
                       if c.name.startswith("mkgp2:")), None)
        assert nested is not None, "bundle did not nest under long course"
        assert nested.name == "mkgp2:MR_highway_long_A.dat"
        assert long_coll.get("mkgp2_hsd_dat") == "MR_highway_long_A.dat"
        print(f"[test] A1 long course nests {nested.name}, "
              f"hsd_dat={long_coll.get('mkgp2_hsd_dat')}")

        # ---- A2: Re-running on a course that already has a nest
        #          should cancel cleanly (no overwrite).
        try:
            result = bpy.ops.scene.mkgp2_attach_hsd()
        except RuntimeError:
            result = {'CANCELLED'}
        assert result == {'CANCELLED'}, \
            f"re-attach should CANCEL, got {result}"
        # Still only 1 nested bundle
        nested_after = [c for c in long_coll.children
                        if c.name.startswith("mkgp2:")]
        assert len(nested_after) == 1
        print("[test] A2 re-attach refuses overwrite")

        # ---- A3: Course whose name has no matching unhosted bundle.
        # We add a brand-new mock course "test_unmatched" and try
        # attaching -- there's no mkgp2:*test_unmatched* bundle.
        unmatched = bpy.data.collections.new("test_unmatched")
        bpy.context.scene.collection.children.link(unmatched)
        unmatched["mkgp2_kind"] = "course"
        unmatched["mkgp2_course_name"] = "test_unmatched"
        _activate_layer_for(unmatched)
        try:
            result = bpy.ops.scene.mkgp2_attach_hsd()
        except RuntimeError:
            result = {'CANCELLED'}
        assert result == {'CANCELLED'}, \
            f"unmatched should CANCEL, got {result}"
        print("[test] A3 unmatched course CANCELs with diagnostic")

        # ---- A4: Promote re-run with the now-attached bundle should
        # not re-link or duplicate it.
        try:
            result = bpy.ops.scene.mkgp2_promote_vanilla()
        except RuntimeError:
            result = {'CANCELLED'}
        nested_final = [c for c in long_coll.children
                        if c.name.startswith("mkgp2:")]
        assert len(nested_final) == 1
        print("[test] A4 promote idempotent w/ already-attached bundle")

        addon.unregister()
        print("[test] PASS")
    except Exception:
        traceback.print_exc()
        print("[test] FAIL")
        sys.exit(1)


main()
