"""Verify the Promote vis: -> HSD .dat operator.

Pipeline:
  1) Synthesize a small `vis:test_oval` collection (2 cubes, 2 colored
     materials) at the scene root.
  2) Activate the vis: layer collection and invoke
     `scene.mkgp2_promote_vis_to_hsd` (EXEC_DEFAULT).
  3) Verify the output .dat exists at the expected path with a sane
     size (markedly smaller than the structural base .dat -- the bulk
     of MR_highway_short_A is GC'd once non-`scene_data` roots are
     stripped).
  4) Parse the result via the vendored `hsdraw` and verify:
        * scene_data root present
        * `<stem>_joint` alias root present
        * scene_data.JOBJDescs[0].RootJoint identical to the
          `<stem>_joint` root (i.e. RootJoint was repointed at the
          synthesized JObj)
        * No other roots remain
  5) Vanilla guard: refuse to write into the configured vanilla bin.

Skips cleanly when:
  * `hsdraw` is not vendored for the host platform.
  * `MR_highway_short_A.dat` is missing from the vanilla bin dir.

  blender --background --python tools/test_addon_promote_vis_to_hsd.py
"""

import bpy
import os
import sys
import tempfile
import traceback
from pathlib import Path

ADDON_DIR = r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\tools\blender"
VANILLA_BIN = r"C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files"
BASE_DAT_NAME = "MR_highway_short_A.dat"
COURSE_STEM = "test_oval"


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


def _make_colored_mesh(name, color_rgba, location):
    """Create a small cube mesh with one Principled BSDF material whose
    Base Color is `color_rgba` (floats 0..1). Returns the new object."""
    bpy.ops.mesh.primitive_cube_add(size=10.0, location=location)
    obj = bpy.context.active_object
    obj.name = name
    mat = bpy.data.materials.new(name=f"{name}_mat")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    assert bsdf is not None
    bsdf.inputs["Base Color"].default_value = color_rgba
    obj.data.materials.append(mat)
    return obj


def _move_to_collection(obj, dst):
    """Unlink `obj` from every collection currently holding it and link
    it under `dst` only."""
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    dst.objects.link(obj)


def main():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import blender_addon_mkgp2_course as addon
    addon.register()
    addon.reload_modules()
    print("[test] addon registered")

    try:
        if not addon.HSDRAW_AVAILABLE:
            print("[test] SKIP: hsdraw not vendored for this platform")
            return
        base_dat = Path(VANILLA_BIN) / BASE_DAT_NAME
        if not base_dat.is_file():
            print(f"[test] SKIP: base .dat not found at {base_dat}")
            return

        # ---- Pref wiring (EXEC_DEFAULT bypasses invoke, but the
        #      operator still calls _vanilla_bin_dir / _is_inside_vanilla
        #      from execute() for the safety guard.) -------------------
        addon._vanilla_bin_dir = lambda: VANILLA_BIN

        with tempfile.TemporaryDirectory() as td:
            addon._output_bin_dir = lambda: td
            out_dat = os.path.join(td, f"{COURSE_STEM}.dat")

            # ---- 1) Synthesize a vis: collection -------------------
            vis = bpy.data.collections.new(f"vis:{COURSE_STEM}")
            bpy.context.scene.collection.children.link(vis)

            # Two cubes with distinct material colors so we can verify
            # multi-DObj chaining (one DObj per (mesh, material slot)).
            red = _make_colored_mesh("oval_inner", (0.9, 0.1, 0.1, 1.0),
                                     location=(0.0, 0.0, 0.0))
            blu = _make_colored_mesh("oval_outer", (0.1, 0.4, 0.9, 1.0),
                                     location=(20.0, 0.0, 0.0))
            _move_to_collection(red, vis)
            _move_to_collection(blu, vis)
            print(f"[test] synthesized {vis.name}: 2 meshes, 2 materials")

            _activate_layer_for(vis)

            # ---- 2) Invoke operator --------------------------------
            result = bpy.ops.scene.mkgp2_promote_vis_to_hsd(
                'EXEC_DEFAULT',
                filepath=out_dat,
                base_dat=str(base_dat),
            )
            assert result == {'FINISHED'}, f"promote: {result}"
            print(f"[test] operator returned FINISHED")

            # ---- 3) File exists + size sanity ----------------------
            assert os.path.isfile(out_dat), f"writer did not produce {out_dat}"
            out_size = os.path.getsize(out_dat)
            base_size = base_dat.stat().st_size
            print(f"[test] wrote {Path(out_dat).name}: {out_size} bytes "
                  f"(base {base_size} bytes)")
            # Promoted .dat carries only scene_data + our new root, so it
            # should be DRASTICALLY smaller than the full MR_highway base
            # (~2.8MB). Allow plenty of headroom in case the structural
            # base shrinks one day, but assert at least a 5x reduction.
            assert out_size < base_size // 5, (
                f"output {out_size} should be << base {base_size}; "
                "non-scene_data roots may not have been stripped")

            # ---- 4) Parse via hsdraw and verify ---------------------
            import hsdraw
            dat = hsdraw.parse_dat(Path(out_dat).read_bytes())
            roots = list(dat.root_names())
            print(f"[test] roots: {roots}")

            assert "scene_data" in roots, "scene_data root missing"
            alias = f"{COURSE_STEM}_joint"
            assert alias in roots, f"{alias} alias root missing"
            # Every other root must have been stripped.
            extras = [r for r in roots if r not in ("scene_data", alias)]
            assert not extras, f"unexpected extra roots: {extras}"

            # scene_data.JOBJDescs[0].RootJoint must match our alias root.
            sd = dat.scene_data()
            assert sd is not None, "scene_data() returned None"
            container = sd.data.get_reference(0x00)
            assert container is not None, \
                "scene_data has no JOBJDescs container"
            descs = container.references()
            assert descs, "JOBJDescs container is empty"
            first_desc = descs[0][1]
            root_joint = first_desc.get_reference(0x00)
            assert root_joint is not None, \
                "first JOBJDesc.RootJoint is NULL"
            # Cross-check via Dat.find_root_for(struct): the struct
            # reached from scene_data.JOBJDescs[0].RootJoint must be
            # registered as the `<stem>_joint` alias root.
            found = dat.find_root_for(root_joint)
            assert found is not None, (
                "scene_data.JOBJDescs[0].RootJoint is not registered as "
                "any root -- repointing failed")
            assert found.name == alias, (
                f"scene_data.JOBJDescs[0].RootJoint resolves to root "
                f"{found.name!r}, expected {alias!r} -- repointing failed")
            print(f"[test] scene_data RootJoint -> {alias} alias  OK")

        # ---- 5) Vanilla guard ------------------------------------------
        # Re-create the vis: collection (it was inside the tempdir-scoped
        # block above, but the collection survives -- only the .dat is
        # gone). Re-activate it for a fresh invocation.
        _activate_layer_for(vis)
        evil = str(Path(VANILLA_BIN) / "evil.dat")
        try:
            result = bpy.ops.scene.mkgp2_promote_vis_to_hsd(
                'EXEC_DEFAULT',
                filepath=evil,
                base_dat=str(base_dat),
            )
        except RuntimeError:
            result = {'CANCELLED'}
        assert result == {'CANCELLED'}, \
            "operator must refuse to write inside vanilla bin dir"
        assert not Path(evil).exists(), \
            "operator wrote into vanilla bin despite refusing"
        print("[test] vanilla guard refused write")

        addon.unregister()
        print("[test] PASS")
    except Exception:
        traceback.print_exc()
        print("[test] FAIL")
        sys.exit(1)


main()
