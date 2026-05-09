"""Verify _build_pobj_for_slot now writes per-face-corner UVs (not all (0,0)).

Run:
  blender --background --python tools/test_promote_vis_uvs.py

The pre-2026-05-10 implementation hardcoded `add_uv(0.0, 0.0)` for every
vertex, which made every fragment sample texel (0,0) of the texture and
gave the in-game billboards their "flat color" appearance. This test
synthesizes a vis: collection with a textured quad and asserts that the
emitted POBJ's UV array spans more than the (0,0) corner.
"""
from __future__ import annotations

import bpy
import os
import struct
import sys
import tempfile
import traceback
from pathlib import Path

ADDON_DIR = r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\tools\blender"
VANILLA_BIN = r"C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files"
COURSE_STEM = "uv_check"


def _make_textured_quad(name, location):
    """Quad in XY plane with an active UV layer mapping the unit square."""
    me = bpy.data.meshes.new(f"{name}_mesh")
    me.from_pydata(
        [(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)],
        [],
        [(0, 1, 2, 3)],
    )
    uv = me.uv_layers.new(name="UVMap")
    # Loop order matches face's vertex order (0, 1, 2, 3)
    uv.data[0].uv = (0.0, 0.0)
    uv.data[1].uv = (1.0, 0.0)
    uv.data[2].uv = (1.0, 1.0)
    uv.data[3].uv = (0.0, 1.0)
    obj = bpy.data.objects.new(name, me)
    obj.location = location
    mat = bpy.data.materials.new(f"{name}_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (0.2, 0.8, 0.3, 1.0)
    me.materials.append(mat)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _move_to_collection(obj, target):
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    target.objects.link(obj)


def _activate_layer_for(coll):
    def find(layer, name):
        if layer.collection.name == name:
            return layer
        for ch in layer.children:
            r = find(ch, name)
            if r is not None:
                return r
        return None
    L = find(bpy.context.view_layer.layer_collection, coll.name)
    if L is None:
        raise RuntimeError(f"layer collection for {coll.name} not found")
    bpy.context.view_layer.active_layer_collection = L


def main():
    sys.path.insert(0, ADDON_DIR)
    import blender_addon_mkgp2_course as addon
    addon.register()
    print("[test] addon registered")
    try:
        addon._vanilla_bin_dir = lambda: VANILLA_BIN
        with tempfile.TemporaryDirectory() as td:
            addon._output_bin_dir = lambda: td
            out_dat = os.path.join(td, f"{COURSE_STEM}.dat")
            vis = bpy.data.collections.new(f"vis:{COURSE_STEM}")
            bpy.context.scene.collection.children.link(vis)
            quad = _make_textured_quad("billboard", (0, 0, 0))
            _move_to_collection(quad, vis)
            _activate_layer_for(vis)
            print(f"[test] synthesized vis:{COURSE_STEM} (1 textured quad)")

            r = bpy.ops.export_scene.mkgp2_hsd_json(
                'EXEC_DEFAULT', filepath=out_dat)
            assert r == {'FINISHED'}, f"export: {r}"

            # Inspect the resulting .dat
            import hsdraw
            dat = hsdraw.parse_dat(Path(out_dat).read_bytes())
            jroot = next(rt for rt in dat.roots() if rt.name.endswith("_joint"))
            j = jroot.data
            jrefs = dict(j.references())
            assert 16 in jrefs, "JObj has no DObj reference"
            d = jrefs[16]

            # Walk DObj chain to find a POBJ with our quad's geometry.
            n_dobj = 0
            uv_arrays_found = []
            while d:
                n_dobj += 1
                drefs = dict(d.references())
                if 12 in drefs:
                    p = drefs[12]
                    while p:
                        prefs = dict(p.references())
                        if 8 in prefs:
                            va = prefs[8]
                            varaw = va.raw()
                            varefs = dict(va.references())
                            ne = len(varaw) // 24
                            for ei in range(ne):
                                e = varaw[ei*24:(ei+1)*24]
                                name = int.from_bytes(e[0:4], 'big')
                                if name == 0xff: break
                                if name == 13:  # TEX0/UV
                                    stride = int.from_bytes(e[16:20], 'big')
                                    sub_off = ei * 24 + 20
                                    if sub_off in varefs:
                                        arr = varefs[sub_off].raw()
                                        n_elems = len(arr) // stride
                                        uvs = []
                                        for k in range(n_elems):
                                            u, v = struct.unpack(
                                                ">ff",
                                                arr[k*stride:k*stride+8])
                                            uvs.append((u, v))
                                        uv_arrays_found.append(uvs)
                        p = prefs.get(4)
                d = drefs.get(4)
            print(f"[test] traversed {n_dobj} DObjs, found "
                  f"{len(uv_arrays_found)} UV arrays")
            assert uv_arrays_found, "no UV arrays found in any POBJ"

            # Strip trailing zero-padding (MeshBuilder pads to 32B chunks).
            uvs = uv_arrays_found[0]
            # The active corners we used are (0,0), (1,0), (1,1), (0,0),
            # (1,1), (0,1) for two triangles of a quad with V flipped:
            # Blender V flipped to GameCube V.
            #   Blender corner UVs: (0,0) (1,0) (1,1) (0,1)
            #   Flipped (1-v):      (0,1) (1,1) (1,0) (0,0)
            # Triangulation (0,1,2),(0,2,3) →
            #   (0,1) (1,1) (1,0) | (0,1) (1,0) (0,0)
            non_zero = [uv for uv in uvs if uv != (0.0, 0.0)]
            print(f"[test] UV array: {uvs}")
            print(f"[test] non-(0,0) UVs: {len(non_zero)} / {len(uvs)}")
            assert len(non_zero) >= 4, (
                f"expected at least 4 non-(0,0) UVs in the quad, got "
                f"{len(non_zero)}: {uvs}")
            print("[test] PASS")
        addon.unregister()
    except Exception:
        traceback.print_exc()
        print("[test] FAIL")
        sys.exit(1)


if __name__ == "__main__":
    main()
