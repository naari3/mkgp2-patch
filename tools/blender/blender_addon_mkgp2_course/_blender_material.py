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
  * ``load_scene_template_dat(hsdraw, template_path)`` -> hsdraw.Dat
    seeded from a vanilla course .dat with non-`scene_data` roots
    stripped; preserves LObj (lights) and COBJ (camera) descriptors
    that `Dat.alloc_scene_data()` omits.

Coordinate transform belongs here too because every Blender-side mesh
exporter uses the same convention; centralizing it makes it harder to
drift the rule.
"""

from __future__ import annotations

from pathlib import Path


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
    """Build an MObj with TObj+Image attached, configured to match the
    vanilla MR_highway road's *lit + textured* pattern.

    The pattern, byte-for-byte from a vanilla road DObj's MObj/TObj:

      * MObj.render_flags = 0x2011 (CONSTANT | TEX0 | ALPHA_MAT)
      * MObj.material:
          amb_rgba = (128, 128, 128, 255) — standard ambient
          dif_rgba = ``color``             — user's BSDF base color
          spc_rgba = (255, 255, 255, 255) — standard specular
          shininess = 50, alpha = 1
      * TObj.flags = 0x40010
          = LIGHTMAP_DIFFUSE (bit 4 = 0x10)
          + COLORMAP_MODULATE (bits 16-19 = 4, value 0x40000)
        plus wrap_s=GX_REPEAT, wrap_t=GX_MIRROR, mag=GX_LINEAR.

    Why this ditched the previous `alloc_unlit_color` + REPLACE path:
    MKGP2's course renderer only samples TEX0 along its **lit-mesh
    TEV pipeline**.  TObjs that lack the LIGHTMAP_DIFFUSE bit fall
    through to a "no texture, output Material.dif" fallback — which is
    why every billboard collapsed to its BSDF Base Color default
    (#cccccc) regardless of the image we attached, and why solid-color
    course meshes also displayed only their Material.dif (which by
    coincidence carried the right color, masking the bug for solid
    meshes).  Switching to `MObj.alloc()` + manual lit Material +
    LIGHTMAP_DIFFUSE TObj routes the mesh through the path that
    actually samples the texel.

    The tradeoff: COLORMAP_MODULATE means ``final_color = texel * dif``,
    so the synth 4x4 path now fills the texel with **white** so
    Material.dif (= ``color``) survives the multiplication unchanged.
    Real Image Texture meshes get their pattern multiplied by Material.dif
    -- if the user wants the texture pattern to display at full
    saturation, set the BSDF Base Color to white (= no tint).

    Parameters
    ----------
    color : (R, G, B, A) byte tuple
        Material.dif_rgba.  For solid-color meshes the texel is white
        and this color shows through; for real-texture meshes this
        acts as a multiplicative tint.
    img_tuple : (w, h, raw_rgba_bytes) or None
        When None, a 4x4 white synth tile is generated.
    target_format : str | int | None
        GX target format ("RGBA8", "CMP", "RGB5A3", or the matching int).
        Falls back to RGBA8 if the requested format demands tile
        alignment this (w, h) pair can't satisfy (CMP needs 4×4).
    """
    if img_tuple is None:
        # Synth 4x4 WHITE tile.  Under COLORMAP_MODULATE the renderer
        # computes `texel * dif`; a white texel passes Material.dif
        # through verbatim, preserving the user's BSDF base color.
        # Earlier versions filled the synth tile with `color` and used
        # the REPLACE path -- that worked visually for solid meshes but
        # never displayed real Image Textures (see make_textured_mobj
        # docstring for the why).
        w, h = 4, 4
        pixel = b"\xff\xff\xff\xff"
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
    # format can't fit this (w, h) pair.
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

    # TObj configured to match vanilla road's 0x40010 pattern.  The
    # public hsdraw setters cover ColorMap (bits 16-19), AlphaMap
    # (bits 20-23), and CoordType (bits 0-3) — but NOT the
    # LIGHTMAP_DIFFUSE bit at 0x10, which we OR in by hand.
    tobj = hsdraw.TObj.alloc()
    tobj.tex_map_id = 0          # GX_TEXMAP0
    tobj.wrap_s = 0              # GX_REPEAT
    tobj.wrap_t = 1              # GX_MIRROR (vanilla road convention)
    tobj.set_image_data(img)
    tobj.set_color_operation(4)  # COLORMAP_MODULATE = texel * material color
    tobj.set_alpha_operation(0)  # ALPHAMAP_NONE
    tobj.set_coord_type(0)       # CoordType=UV (= bits 0-3 = 0)
    tobj.flags |= 0x10           # LIGHTMAP_DIFFUSE — no public setter
    tobj.blending = 1.0
    tobj.mag_filter = 1          # GX_LINEAR

    # Material with vanilla course conventions: ambient + spc non-zero
    # so the lit-mesh TEV pipeline (the one actually sampling our TObj)
    # has color sources to work with.
    mat = hsdraw.Material.alloc()
    mat.amb_rgba = (128, 128, 128, 255)
    mat.dif_rgba = tuple(color)
    mat.spc_rgba = (255, 255, 255, 255)
    mat.shininess = 50.0
    mat.alpha = 1.0

    # MObj.alloc() instead of alloc_unlit_color() -- the unlit preset
    # leaves amb/spc at zero which steers the renderer down the no-
    # texture fallback (the very bug this rewrite addresses).
    mobj = hsdraw.MObj.alloc()
    mobj.set_material(mat)
    mobj.render_flags = 0x2011   # CONSTANT|TEX0|ALPHA_MAT, vanilla road
    mobj.set_textures(tobj)
    return mobj


# -- Scene template loader -------------------------------------------------

def load_scene_template_dat(hsdraw, template_path):
    """Load a vanilla course `.dat` to use as a structural template,
    strip every root except `scene_data`, and return the resulting
    `hsdraw.Dat` ready for the caller to repoint
    ``scene_data.JOBJDescs[0].RootJoint`` and add their own alias roots.

    Why this exists: ``hsdraw.Dat.alloc_scene_data()`` produces an SObj
    that holds **only** the JObjDesc array.  A vanilla course SObj
    additionally carries pointers to LObj (lights) and COBJ (camera)
    descriptors — the in-game renderer reads both, and an SObj that
    lacks them leaves character meshes dark and texture sampling broken.
    The cheapest way to recover those without re-implementing the LObj
    / COBJ allocators in hsdraw is to load any vanilla course .dat
    (whose layout is uniform across the shipping set), strip everything
    that isn't `scene_data`, and reuse what's left.

    Parameters
    ----------
    hsdraw
        The vendored hsdraw module (passed in so this helper does not
        carry its own import; mirrors the convention used elsewhere
        in this package for callable helpers).
    template_path : str | os.PathLike
        Path to a vanilla course .dat.  Any non-shade course .dat from
        the dump works (e.g. ``MR_highway_long_A.dat``).  The function
        does not validate the source -- it just trusts that
        `scene_data` exists with at least one JObjDesc.

    Returns
    -------
    hsdraw.Dat
        A Dat whose only root is `scene_data`.  Caller must:
          * call ``scene_data().jobj_descs()[0].set_root_joint(rj)``
            with their synthesized root JObj, and
          * ``add_root(alias, rj)`` for the joint loader's alias.
        The Dat retains the template's LObj/COBJ descriptors verbatim;
        write() will serialize them alongside the new geometry.
    """
    template_path = Path(template_path)
    template_bytes = template_path.read_bytes()
    dat = hsdraw.parse_dat(template_bytes)
    sd = dat.scene_data()
    if sd is None:
        raise RuntimeError(
            f"scene template {template_path.name} has no scene_data root; "
            "cannot use as a structural template")
    sobj = hsdraw.SObj.from_struct(sd.data)
    descs = sobj.jobj_descs()
    if not descs:
        raise RuntimeError(
            f"scene template {template_path.name} scene_data has no "
            "JObjDescs; this template is too minimal")
    # Drop every root except scene_data.  HSDLib serializes only roots
    # in the table, so the orphaned joint trees no longer get written
    # out — the resulting Dat is genuinely small (hundreds of bytes
    # over the size needed for our own geometry).
    for rn in list(dat.root_names()):
        if rn != "scene_data":
            dat.remove_root(rn)
    return dat


# -- Coordinate transform --------------------------------------------------

def blender_to_hsd(co):
    """Map Blender world coords (Z up) to MKGP2 world frame (Y up).
    Same convention every Blender-side exporter (collision/auto/line)
    uses: ``(Bx, By, Bz)_blender -> (Bx, Bz, -By)_game``."""
    return (co.x, co.z, -co.y)
