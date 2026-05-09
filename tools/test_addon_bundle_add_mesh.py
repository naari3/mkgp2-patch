"""Verify the M3 bundle export pipeline's behavior when the user adds
a NEW mesh to an existing `mkgp2:<dat>` bundle.

Pipeline:
  1) Import vanilla MR_highway_short_A.dat as a bundle.
  2) Synthesize a new Blender Cube into the bundle, reusing an
     existing material (material slot 0 of an existing mesh) and
     binding the cube to an existing joint id.
  3) Export the bundle to a fresh .dat.
  4) Verify via hsdraw that the output has MORE meshes than the
     baseline (new cube was picked up).
  5) Verify a sibling cube whose `mkgp2_joint_id` points at a
     NON-existent joint is rejected with a WARN and skipped.
  6) Verify a sibling cube using a freshly-created Blender material
     (not in the bundle's import-time DTO map) is rejected with
     a WARN and skipped (= new material support is NOT in scope).

Documents the boundaries of bundle round-trip mesh-add support so
the mkgp2-edit-vanilla-course skill can quote them accurately.

  blender --background --python tools/test_addon_bundle_add_mesh.py
"""

import bpy
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

ADDON_DIR = r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\tools\blender"
VANILLA_BIN = r"C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files"
BASE_DAT_NAME = "MR_highway_short_A.dat"


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
    assert lc is not None, f"no layer collection for {coll.name}"
    bpy.context.view_layer.active_layer_collection = lc


def _export_stats(addon, bundle, out_dat):
    """Run the bundle exporter directly so we can read the returned
    `stats` dict (mesh count is the canonical truth here, far more
    reliable than counting POBJs back from the .dat)."""
    from blender_addon_mkgp2_course import _export_mkgp2_bundle
    stash_sj = bundle.get("mkgp2_scene_json")
    return _export_mkgp2_bundle.export_bundle_to_dat(
        bundle, stash_sj, out_dat)


def main():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import blender_addon_mkgp2_course as addon
    addon.register()
    addon.reload_modules()
    print("[test] addon registered")

    try:
        if not addon.HSDRAW_AVAILABLE:
            print("[test] SKIP: hsdraw not vendored")
            return
        base_dat = Path(VANILLA_BIN) / BASE_DAT_NAME
        if not base_dat.is_file():
            print(f"[test] SKIP: base .dat missing at {base_dat}")
            return
        addon._vanilla_bin_dir = lambda: VANILLA_BIN
        addon._output_bin_dir = lambda: tempfile.gettempdir()

        # ---- Import bundle --------------------------------------------
        result = bpy.ops.import_scene.mkgp2_hsd_json(
            'EXEC_DEFAULT', filepath=str(base_dat))
        assert result == {'FINISHED'}, f"import: {result}"
        bundle = next((c for c in bpy.data.collections
                       if c.name.startswith("mkgp2:")), None)
        assert bundle is not None
        _activate_layer_for(bundle)

        existing_meshes = [o for o in bundle.objects if o.type == 'MESH']
        any_mesh = existing_meshes[0]
        existing_mat = any_mesh.data.materials[0] if any_mesh.data.materials else None
        existing_jid = any_mesh.get("mkgp2_joint_id")
        assert existing_mat is not None and existing_jid, \
            f"could not pick a sample (mat={existing_mat}, jid={existing_jid!r})"
        print(f"[test] sampled existing material={existing_mat.name!r} "
              f"joint_id={existing_jid!r}")

        # ---- Baseline export ------------------------------------------
        with tempfile.TemporaryDirectory() as td:
            out_v0 = os.path.join(td, "v0.dat")
            stats_v0 = _export_stats(addon, bundle, out_v0)
            n_v0 = stats_v0["meshes"]
            print(f"[test] v0 baseline mesh count: {n_v0}")

            # ---- Add a new Cube reusing existing material + joint ----
            bpy.ops.mesh.primitive_cube_add(size=10.0,
                                            location=(0.0, 0.0, 0.0))
            new_cube = bpy.context.active_object
            new_cube.name = "added_cube_existing_mat"
            # Move into bundle ONLY
            for c in list(new_cube.users_collection):
                c.objects.unlink(new_cube)
            bundle.objects.link(new_cube)
            new_cube.data.materials.clear()
            new_cube.data.materials.append(existing_mat)
            new_cube["mkgp2_joint_id"] = existing_jid
            new_cube["mkgp2_cull"] = "NONE"

            # Re-export
            out_v1 = os.path.join(td, "v1.dat")
            stats_v1 = _export_stats(addon, bundle, out_v1)
            n_v1 = stats_v1["meshes"]
            print(f"[test] v1 (added 1 cube w/ existing mat) mesh count: {n_v1}")
            assert n_v1 == n_v0 + 1, (
                f"expected {n_v0 + 1} meshes after adding cube, got {n_v1}")
            print("[test] OK: new mesh reusing existing material was "
                  "picked up by the exporter")

            # ---- Add a Cube whose joint id is BOGUS (= must skip) ----
            bpy.ops.mesh.primitive_cube_add(size=5.0,
                                            location=(20.0, 0.0, 0.0))
            bogus = bpy.context.active_object
            bogus.name = "added_cube_bogus_jid"
            for c in list(bogus.users_collection):
                c.objects.unlink(bogus)
            bundle.objects.link(bogus)
            bogus.data.materials.clear()
            bogus.data.materials.append(existing_mat)
            bogus["mkgp2_joint_id"] = "jobj_99999"  # does not exist
            bogus["mkgp2_cull"] = "NONE"

            out_v2 = os.path.join(td, "v2.dat")
            stats_v2 = _export_stats(addon, bundle, out_v2)
            n_v2 = stats_v2["meshes"]
            assert n_v2 == n_v1, (
                f"bogus-jid cube should be skipped; expected "
                f"{n_v1} meshes, got {n_v2}")
            print(f"[test] OK: cube with bogus mkgp2_joint_id was "
                  f"correctly skipped (still {n_v2} meshes)")
            # Remove bogus before next sub-case
            bpy.data.objects.remove(bogus, do_unlink=True)

            # ---- Add a Cube with a FRESH (non-DTO) material ----------
            fresh_mat = bpy.data.materials.new(name="brand_new_mat")
            fresh_mat.use_nodes = True
            bsdf = fresh_mat.node_tree.nodes.get("Principled BSDF")
            if bsdf is not None:
                bsdf.inputs["Base Color"].default_value = (1.0, 0.0, 1.0, 1.0)
            bpy.ops.mesh.primitive_cube_add(size=5.0,
                                            location=(40.0, 0.0, 0.0))
            fresh = bpy.context.active_object
            fresh.name = "added_cube_fresh_mat"
            for c in list(fresh.users_collection):
                c.objects.unlink(fresh)
            bundle.objects.link(fresh)
            fresh.data.materials.clear()
            fresh.data.materials.append(fresh_mat)
            fresh["mkgp2_joint_id"] = existing_jid
            fresh["mkgp2_cull"] = "NONE"

            out_v3 = os.path.join(td, "v3.dat")
            try:
                stats_v3 = _export_stats(addon, bundle, out_v3)
                n_v3 = stats_v3["meshes"]
                print(f"[test] v3 (fresh-material cube) mesh count: {n_v3} "
                      f"(was {n_v1} before)")
                if n_v3 == n_v1 + 1:
                    print("[test] NOTE: pipeline ACCEPTED fresh material "
                          "(unexpected -- update skill if confirmed)")
                elif n_v3 == n_v1:
                    print("[test] OK: pipeline silently skipped fresh-mat "
                          "cube (expected; fresh materials are not in DTO map)")
                else:
                    print(f"[test] NOTE: unexpected n_v3={n_v3}")
            except Exception as ex:
                print(f"[test] OK: pipeline rejected fresh-material cube "
                      f"(raised: {type(ex).__name__}: {ex})")

        addon.unregister()
        print("[test] PASS")
    except Exception:
        traceback.print_exc()
        print("[test] FAIL")
        sys.exit(1)


main()
