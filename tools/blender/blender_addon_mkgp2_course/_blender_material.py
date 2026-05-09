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


# -- GX texture format table -----------------------------------------------
#
# GxTexFmt name -> integer enum (mirrors HSDLib HSDRaw/GX/GXEnums.cs).
# Kept in sync with `blender_import_hsd._TEXFMT` (the importer carries
# the full table for round-trip purposes; the exporter only needs the
# subset users actually pick from when constructing a fresh material).
_TEXFMT_FULL = {
    "I4": 0, "I8": 1, "IA4": 2, "IA8": 3,
    "RGB565": 4, "RGB5A3": 5, "RGBA8": 6,
    "CI4": 8, "CI8": 9, "CI14X2": 10,
    "CMP": 14,
}

# Subset exposed via the addon UI (Material -> Target texture format).
# Keep this list short on purpose:
#   * RGBA8  = lossless, the default; matches the byte-equiv path.
#   * CMP    = compact lossy (DXT1-style, 8x smaller than RGBA8).
#   * RGB5A3 = compact with alpha (4x smaller, 16-bit quantized).
# The remaining vanilla formats are still picked up on import (= bypass
# path) but cannot be selected for fresh materials from the addon.
TARGET_FORMAT_CHOICES = ("RGBA8", "CMP", "RGB5A3")
DEFAULT_TARGET_FORMAT = "RGBA8"


def material_target_format(mat) -> tuple[str, int]:
    """Resolve a Blender Material's GX target format for fresh-material
    export.  Returns ``(name, gx_int)``.

    Looks for the format in three places, in order:
      1. ``mat.mkgp2_target_format`` -- the EnumProperty attribute the
         addon registers on `bpy.types.Material`.  Returns the str
         identifier (= "RGBA8" / "CMP" / "RGB5A3"); only present when
         the addon is loaded.
      2. ``mat["mkgp2_target_format"]`` -- the raw ID-property form,
         in case the user (or a script that runs without the addon
         registered) set the property by hand as a string.  Tolerates
         int values as well by reverse-looking-up `_TEXFMT_FULL`.
      3. Fallback to ``DEFAULT_TARGET_FORMAT``.

    Unknown / out-of-range values are silently coerced to the default
    rather than raising, so a bad property doesn't kill the whole
    export.
    """
    name = DEFAULT_TARGET_FORMAT
    if mat is not None:
        # 1) Registered EnumProperty attribute -- always returns a str
        #    identifier when present.
        attr_val = getattr(mat, "mkgp2_target_format", None)
        if isinstance(attr_val, str) and attr_val in TARGET_FORMAT_CHOICES:
            name = attr_val
        else:
            # 2) Raw custom-property form (string or int).
            raw = mat.get("mkgp2_target_format")
            if isinstance(raw, str) and raw in TARGET_FORMAT_CHOICES:
                name = raw
            elif isinstance(raw, int):
                # Reverse lookup: if `raw` matches a known fmt int and
                # also belongs to the UI's allowed subset, accept it.
                for k, v in _TEXFMT_FULL.items():
                    if v == raw and k in TARGET_FORMAT_CHOICES:
                        name = k
                        break
    return name, _TEXFMT_FULL[name]


def _format_alignment_ok(fmt_name: str, w: int, h: int) -> bool:
    """Compressed formats need power-of-2 / 4-aligned dimensions; the
    GX hardware tile size is format-specific.  CMP demands 4x4 tiles.
    RGB5A3 / RGBA8 etc. are 4x4 tiles too but tolerate non-multiples
    of 4 via padding inside hsdraw's encoder.

    Returns True if the (w, h) pair is safe for the named format,
    False if the encoder would either reject or silently pad.  Callers
    use this to decide whether to fall back to RGBA8.
    """
    if fmt_name == "CMP":
        return (w % 4 == 0) and (h % 4 == 0)
    return True


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

def make_textured_mobj(hsdraw, color, img_tuple, target_format=None):
    """Build an MObj with TObj+Image attached, RenderFlags configured
    vanilla-compatible (`CONSTANT|TEX0|ALPHA_MAT` = 0x2011).

    `color` is the (R,G,B,A) byte tuple used as Material.DIF (gets
    multiplied with the texture sample under CONSTANT mode).
    `img_tuple` = (w, h, raw_rgba_bytes); if None, synthesize a 4x4
    solid texture filled with `color`.

    `target_format` selects the GX texture format the encoder writes:
      * None / "RGBA8" -> 6  (lossless, default; ~ 4 bytes/pixel)
      * "CMP"          -> 14 (DXT1-style, ~ 0.5 bytes/pixel; lossy)
      * "RGB5A3"       -> 5  (16-bit; ~ 2 bytes/pixel; quantized)
    Pass either the str name (looked up in `_TEXFMT_FULL`) or an int
    enum value directly.  Callers building from `material_target_format`
    pass the int; tests / standalone callers can pass the name.

    If the requested format demands tile alignment that this `(w, h)`
    pair fails (e.g. CMP needs both dims divisible by 4), the function
    silently falls back to RGBA8 rather than raising — so a 5x7 image
    on a CMP-tagged material still exports, just in RGBA8.
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

    # Resolve target_format -> (name, int).  None -> default RGBA8.
    if target_format is None:
        fmt_name, fmt_int = DEFAULT_TARGET_FORMAT, _TEXFMT_FULL[DEFAULT_TARGET_FORMAT]
    elif isinstance(target_format, str):
        fmt_name = target_format
        fmt_int = _TEXFMT_FULL.get(fmt_name, _TEXFMT_FULL[DEFAULT_TARGET_FORMAT])
    else:
        fmt_int = int(target_format)
        fmt_name = next(
            (k for k, v in _TEXFMT_FULL.items() if v == fmt_int),
            DEFAULT_TARGET_FORMAT,
        )

    # Alignment guard -- silently downgrade to RGBA8 if the requested
    # format can't fit this (w, h) pair.  hsdraw's encoder might pad
    # internally but the guarantees there aren't strong; safer to fall
    # back than to ship a misaligned CMP payload.
    if not _format_alignment_ok(fmt_name, w, h):
        fmt_name = DEFAULT_TARGET_FORMAT
        fmt_int = _TEXFMT_FULL[DEFAULT_TARGET_FORMAT]

    # GX-encode.  hsdraw handles tile alignment + 32-byte padding
    # internally (`gx_encode` wraps `gx_image::encode_image`).
    gx_bytes = hsdraw.gx_encode(fmt_int, w, h, raw)

    # Image alloc + populate.
    img = hsdraw.Image.alloc()
    img.width = w
    img.height = h
    img.format = fmt_int
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
