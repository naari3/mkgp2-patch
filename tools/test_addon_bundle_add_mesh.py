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
     gets the BSDF Base Color baked into a 4x4 ad-hoc MObj (= the
     post-fix behavior; previously it silently dropped to grey).
  7) Verify a sibling cube whose fresh material has an Image Texture
     node feeding the BSDF Base Color: the image bytes must round-
     trip through `make_textured_mobj` -> `gx_encode` and end up in
     the output .dat as a TObj-attached Image of matching dimensions
     and pixel values.
  8) Verify the per-Material `mkgp2_target_format` UI prop actually
     drives encoder format selection: a cube whose material has the
     EnumProperty set to "CMP" must end up with a CMP-format Image
     in the output .dat (not RGBA8), and decoding it should give
     pixels approximately equal to the source orange (CMP is DXT1-
     style lossy so we tolerate per-channel deltas <= 32).

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


def _find_image_in_dat(out_dat, expected_w, expected_h, expected_pixel_rgba,
                       *, require_format=None, tolerance=0):
    """Walk every reachable TObj in `out_dat` and return True if at least
    one Image matches (`expected_w`, `expected_h`) AND every pixel
    decodes to `expected_pixel_rgba` (= 4-tuple of 0..255 ints) within
    ``tolerance`` per channel.

    `require_format` (int, e.g. `_TEXFMT["CMP"] = 14`) restricts the
    search to images whose `img.format == require_format`; pass None
    to accept any format.  Used by the CMP-target-format test to
    confirm the encoder honored the picker rather than silently
    falling back to RGBA8.

    Walking pattern mirrors `blender_import_hsd._collect_gx_bytes` so
    we hit every alias root + scene_data RootJoint without missing
    sub-trees."""
    import hsdraw
    dat = hsdraw.parse_dat(Path(out_dat).read_bytes())
    expected_pixel = bytes(expected_pixel_rgba)

    def walk_jobjs(jobj):
        while jobj is not None:
            yield jobj
            if jobj.child is not None:
                yield from walk_jobjs(jobj.child)
            jobj = jobj.next

    def all_root_jobjs():
        sd = dat.scene_data()
        if sd is not None:
            sobj = hsdraw.SObj.from_struct(sd.data)
            for jd in sobj.jobj_descs():
                rj = jd.root_joint
                if rj is not None:
                    yield rj

    def dobj_from(jobj):
        for off, s in jobj.as_struct().references():
            if off == 0x10:
                return hsdraw.DObj.from_struct(s)
        return None

    for root in all_root_jobjs():
        for jobj in walk_jobjs(root):
            d = dobj_from(jobj)
            while d is not None:
                m_raw = d.mobj
                if m_raw is not None:
                    m = hsdraw.MObj.from_struct(m_raw)
                    t = m.textures
                    while t is not None:
                        timg = t.image_data
                        if timg is not None:
                            img = (hsdraw.Image.from_struct(timg)
                                   if isinstance(timg, hsdraw.HsdStruct)
                                   else timg)
                            if (img.width == expected_w and
                                    img.height == expected_h and
                                    (require_format is None or
                                     img.format == require_format)):
                                gx = img.image_data()
                                # Decode using the image's actual format so
                                # CMP / RGB5A3 / RGBA8 each round-trip via
                                # the correct decoder.
                                rgba = bytes(hsdraw.gx_decode(
                                    img.format, expected_w, expected_h, gx))
                                ok = True
                                for i in range(0, len(rgba), 4):
                                    for ch in range(4):
                                        if abs(rgba[i + ch]
                                               - expected_pixel[ch]) > tolerance:
                                            ok = False
                                            break
                                    if not ok:
                                        break
                                if ok:
                                    return True
                        t = t.next
                d = d.next
    return False


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

            # Sanity-check the helper directly: the magenta material's
            # BSDF Base Color (1.0, 0.0, 1.0, 1.0) must come back as the
            # (255, 0, 255, 255) byte tuple the exporter will feed
            # `make_textured_mobj`.
            from blender_addon_mkgp2_course import _blender_material as bm
            color = bm.bsdf_base_color(fresh_mat)
            assert color == (255, 0, 255, 255), \
                f"bsdf_base_color magenta read mismatch: got {color}"
            assert bm.bsdf_image_texture(fresh_mat) is None, \
                "fresh material has no Image Texture node, but helper " \
                "returned non-None"
            print(f"[test] OK: bsdf_base_color extracts magenta as {color}")

            out_v3 = os.path.join(td, "v3.dat")
            stats_v3 = _export_stats(addon, bundle, out_v3)
            n_v3 = stats_v3["meshes"]
            assert n_v3 == n_v1 + 1, (
                f"fresh-material cube should land in the .dat now; "
                f"expected {n_v1 + 1} meshes, got {n_v3}")
            assert stats_v3["fresh_materials"] == 1, (
                f"exporter should report exactly 1 ad-hoc MObj built "
                f"from the BSDF; stats: {stats_v3}")
            # v1's baseline was un-edited (existing mat reuse only) -> 0 ad-hoc
            assert stats_v1["fresh_materials"] == 0
            print(f"[test] OK: fresh-material cube accepted "
                  f"(meshes={n_v3}, fresh_materials={stats_v3['fresh_materials']})")

            # ---- v4) Cube whose BSDF has an Image Texture node bound -----
            # Build a fresh 8x8 RGBA8 image, fill with `orange` (one solid
            # color so the row-flip in `bsdf_image_texture` is a no-op for
            # equality purposes), wire it through a new Principled BSDF.
            W, H = 8, 8
            orange = (255, 128, 0, 255)
            pix_floats = [c / 255.0 for c in orange] * (W * H)
            img_tex = bpy.data.images.new(
                name="orange_8x8", width=W, height=H, alpha=True)
            img_tex.pixels = pix_floats

            tex_mat = bpy.data.materials.new(name="img_tex_mat")
            tex_mat.use_nodes = True
            nt = tex_mat.node_tree
            bsdf2 = nt.nodes.get("Principled BSDF")
            assert bsdf2 is not None, "no Principled BSDF in fresh material"
            tex_node = nt.nodes.new(type='ShaderNodeTexImage')
            tex_node.image = img_tex
            nt.links.new(tex_node.outputs["Color"],
                         bsdf2.inputs["Base Color"])

            # Helper sanity: bsdf_image_texture should now return our 8x8
            assert bm.bsdf_image_texture(tex_mat) is not None, \
                "bsdf_image_texture didn't pick up the Image Texture node"
            w_h_raw = bm.bsdf_image_texture(tex_mat)
            assert w_h_raw[0] == W and w_h_raw[1] == H, \
                f"bsdf_image_texture wrong dims: {w_h_raw[:2]}"
            assert len(w_h_raw[2]) == W * H * 4, \
                f"bsdf_image_texture wrong byte length: {len(w_h_raw[2])}"

            bpy.ops.mesh.primitive_cube_add(size=5.0,
                                            location=(60.0, 0.0, 0.0))
            tex_cube = bpy.context.active_object
            tex_cube.name = "added_cube_image_texture"
            for c in list(tex_cube.users_collection):
                c.objects.unlink(tex_cube)
            bundle.objects.link(tex_cube)
            tex_cube.data.materials.clear()
            tex_cube.data.materials.append(tex_mat)
            tex_cube["mkgp2_joint_id"] = existing_jid
            tex_cube["mkgp2_cull"] = "NONE"

            out_v4 = os.path.join(td, "v4.dat")
            stats_v4 = _export_stats(addon, bundle, out_v4)
            n_v4 = stats_v4["meshes"]
            assert n_v4 == n_v3 + 1, (
                f"image-texture cube should land in the .dat; "
                f"expected {n_v3 + 1} meshes, got {n_v4}")
            assert stats_v4["fresh_materials"] == 2, (
                f"v4 should report 2 ad-hoc MObjs (v3 magenta + v4 "
                f"orange); stats: {stats_v4}")

            assert _find_image_in_dat(out_v4, W, H, orange), (
                f"output .dat does not contain an {W}x{H} Image filled "
                f"with {orange}; the BSDF Image Texture path is broken")
            print(f"[test] OK: BSDF Image Texture round-tripped: "
                  f"{W}x{H} RGBA8 = {orange} appears in v4.dat")

            # ---- v5) Cube whose material asks for CMP encoder format -----
            # Reuse the same orange image / BSDF wiring as v4, but on a
            # fresh material whose `mkgp2_target_format` EnumProperty is
            # set to "CMP".  Verify the encoder honored it: the output
            # .dat contains a CMP-format Image of the same dims, and
            # decoding that Image via gx_decode gives pixels close to
            # orange (CMP is DXT1-style lossy so we tolerate per-channel
            # delta <= 32).
            cmp_mat = bpy.data.materials.new(name="cmp_img_mat")
            cmp_mat.use_nodes = True
            nt2 = cmp_mat.node_tree
            bsdf3 = nt2.nodes.get("Principled BSDF")
            assert bsdf3 is not None
            tex_node2 = nt2.nodes.new(type='ShaderNodeTexImage')
            tex_node2.image = img_tex   # reuse the 8x8 orange Image
            nt2.links.new(tex_node2.outputs["Color"],
                          bsdf3.inputs["Base Color"])
            # Set the EnumProperty the addon registered. Attribute access
            # accepts the str identifier directly.
            cmp_mat.mkgp2_target_format = "CMP"

            # Helper sanity: material_target_format must report CMP now.
            fmt_name, fmt_int = bm.material_target_format(cmp_mat)
            assert fmt_name == "CMP" and fmt_int == bm._TEXFMT_FULL["CMP"], (
                f"material_target_format misread the prop: "
                f"got ({fmt_name!r}, {fmt_int})")

            bpy.ops.mesh.primitive_cube_add(size=5.0,
                                            location=(80.0, 0.0, 0.0))
            cmp_cube = bpy.context.active_object
            cmp_cube.name = "added_cube_cmp_format"
            for c in list(cmp_cube.users_collection):
                c.objects.unlink(cmp_cube)
            bundle.objects.link(cmp_cube)
            cmp_cube.data.materials.clear()
            cmp_cube.data.materials.append(cmp_mat)
            cmp_cube["mkgp2_joint_id"] = existing_jid
            cmp_cube["mkgp2_cull"] = "NONE"

            out_v5 = os.path.join(td, "v5.dat")
            stats_v5 = _export_stats(addon, bundle, out_v5)
            assert stats_v5["meshes"] == n_v4 + 1, (
                f"CMP-format cube should land in the .dat; "
                f"expected {n_v4 + 1} meshes, got {stats_v5['meshes']}")
            assert stats_v5["fresh_materials"] == 3, (
                f"v5 should report 3 ad-hoc MObjs (magenta + orange-RGBA8 + "
                f"orange-CMP); stats: {stats_v5}")

            cmp_int = bm._TEXFMT_FULL["CMP"]
            assert _find_image_in_dat(
                out_v5, W, H, orange,
                require_format=cmp_int, tolerance=32), (
                f"output .dat does not contain an {W}x{H} CMP-format "
                f"Image with pixels ~ {orange}; the target_format "
                f"plumbing is broken (or CMP encoder rejected the input)")
            print(f"[test] OK: target_format=CMP honored: "
                  f"{W}x{H} CMP image with pixels ~ {orange} appears in v5.dat")

        addon.unregister()
        print("[test] PASS")
    except Exception:
        traceback.print_exc()
        print("[test] FAIL")
        sys.exit(1)


main()
