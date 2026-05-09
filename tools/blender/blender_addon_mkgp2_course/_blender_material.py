"""Shared Blender-material -> HSD MObj helpers.

Both the vis: -> .dat promote pipeline (`_promote_vis_to_hsd`) and the
mkgp2: bundle export pipeline (`_export_mkgp2_bundle`) hit the same
problem: a Blender Principled BSDF needs to be turned into a vanilla-
style course MObj (`CONSTANT|TEX0|ALPHA_MAT` = 0x2011) backed by a TObj
+ Image with either a 4x4 solid color (single-color BSDF) or the BSDF's
Image Texture node payload (per-pixel pattern).

These helpers were originally inlined in `_promote_vis_to_hsd` because
the bundle exporter only ever emitted a hard-coded grey fallback for
fresh materials. After the M3 round of work it became clear the bundle
path needs the same vis:-style construction so that a user editing
vanilla can add a new Blender material and have its color/texture
actually land in the .dat (instead of silently turning grey). Hoisted
here so both pipelines reuse the same logic.

Stable surface (no leading underscores -- callers are sibling modules
in the same `blender_addon_mkgp2_course` package):

  * ``bsdf_base_color(mat)`` -> ``(r, g, b, a)`` byte tuple
  * ``bsdf_image_texture(mat)`` -> ``(w, h, rgba_bytes)`` or ``None``
  * ``make_textured_mobj(hsdraw, color, img_tuple)`` -> hsdraw.MObj
  * ``blender_to_hsd(co)`` -> ``(x, y, z)`` in MKGP2 world frame

Coordinate transform belongs here too because every Blender-side mesh
exporter uses the same convention; centralizing it makes it harder to
drift the rule.
"""

from __future__ import annotations


# -- BSDF readers ----------------------------------------------------------

def bsdf_base_color(mat) -> tuple:
    """Read Principled BSDF base color as (r, g, b, a) bytes, with a
    grey fallback for un-noded / un-set materials."""
    if mat is None:
        return (200, 200, 200, 255)
    if mat.use_nodes and mat.node_tree:
        for n in mat.node_tree.nodes:
            if n.type == 'BSDF_PRINCIPLED':
                r, g, b, a = n.inputs["Base Color"].default_value
                return (int(r * 255), int(g * 255), int(b * 255), int(a * 255))
    r, g, b, a = mat.diffuse_color
    return (int(r * 255), int(g * 255), int(b * 255), int(a * 255))


def bsdf_image_texture(mat):
    """Find the Image Texture node feeding mat's BSDF Base Color.
    Returns the image as (width, height, raw_rgba_bytes) or None."""
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return None
    bsdf = None
    for n in mat.node_tree.nodes:
        if n.type == 'BSDF_PRINCIPLED':
            bsdf = n
            break
    if bsdf is None:
        return None
    bc = bsdf.inputs.get("Base Color")
    if bc is None or not bc.is_linked:
        return None
    src = bc.links[0].from_node
    if src is None or src.type != 'TEX_IMAGE' or src.image is None:
        return None
    img = src.image
    w, h = img.size
    if w <= 0 or h <= 0:
        return None
    # Blender stores pixels as flat float RGBA (row-major, bottom-up).
    # GX expects top-down, so flip rows.
    px = list(img.pixels)
    raw = bytearray(w * h * 4)
    for y in range(h):
        src_row = (h - 1 - y) * w * 4
        dst_row = y * w * 4
        for i in range(w * 4):
            v = px[src_row + i]
            if v < 0.0: v = 0.0
            elif v > 1.0: v = 1.0
            raw[dst_row + i] = int(round(v * 255))
    return (w, h, bytes(raw))


# -- HSD MObj/TObj/Image construction --------------------------------------

def make_textured_mobj(hsdraw, color, img_tuple):
    """Build an MObj with TObj+Image attached, RenderFlags configured
    vanilla-compatible (`CONSTANT|TEX0|ALPHA_MAT` = 0x2011).

    `color` is the (R,G,B,A) byte tuple used as Material.DIF (gets
    multiplied with the texture sample under CONSTANT mode).
    `img_tuple` = (w, h, raw_rgba_bytes); if None, synthesize a 4x4
    solid texture filled with `color`.
    """
    if img_tuple is None:
        # Synth 4x4 solid color (this is the fallback when the BSDF has
        # no Image Texture node bound; bake helpers should normally
        # populate the BSDF before we get here).
        w, h = 4, 4
        pixel = bytes(color)
        raw = pixel * (w * h)
    else:
        w, h, raw = img_tuple

    # GX-encode RGBA8 (format=6). hsdraw handles tile alignment + 32-byte
    # padding internally (`gx_encode` wraps `gx_image::encode_image`).
    gx_bytes = hsdraw.gx_encode(6, w, h, raw)

    # Image alloc + populate.
    img = hsdraw.Image.alloc()
    img.width = w
    img.height = h
    img.format = 6  # GxTexFmt::RGBA8
    img.set_image_data_bytes(gx_bytes)

    # TObj alloc + populate. Vanilla road MObj's TObj uses these defaults
    # (per dump tools/hsd/dump_tobj_raw.csx on test_course_road.dat).
    tobj = hsdraw.TObj.alloc()
    tobj.tex_map_id = 0          # GX_TEXMAP0
    tobj.wrap_s = 0              # GX_REPEAT
    tobj.wrap_t = 0
    tobj.set_image_data(img)
    # color/alpha operations: REPLACE (texel becomes the final color).
    tobj.set_color_operation(5)  # ColorMap::REPLACE
    tobj.set_alpha_operation(4)  # AlphaMap::REPLACE
    tobj.set_coord_type(0)       # CoordType::UV
    tobj.blending = 1.0
    tobj.mag_filter = 1          # GX_LINEAR

    # MObj: alloc with vanilla course-style render flags.
    mobj = hsdraw.MObj.alloc_unlit_color(*color)
    # CONSTANT|TEX0|ALPHA_MAT = 0x2011, vanilla road MObj 互換。
    mobj.render_flags = 0x2011
    mobj.set_textures(tobj)
    return mobj


# -- Coordinate transform --------------------------------------------------

def blender_to_hsd(co):
    """Map Blender world coords (Z up) to MKGP2 world frame (Y up).
    Same convention every Blender-side exporter (collision/auto/line)
    uses: ``(Bx, By, Bz)_blender -> (Bx, Bz, -By)_game``."""
    return (co.x, co.z, -co.y)
