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
    that `Dat.alloc_scene_data_minimal()` omits.

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


# -- Texture size clamp for GX hardware ------------------------------------

# GX texture wrap registers cap dimensions at 1024.  Source: libogc gx.h
# (`#define GX_MAX_TEX_WIDTH 1024`); sysdolphin / Smash / vanilla MKGP2
# course .dats all stay within this.  Anything larger silently breaks --
# in practice the dolphin emulator either refuses to bind the texture
# (= mesh renders as the magenta default) or wraps the dimensions back
# under 1024 and samples garbage (= garbled stripes).  Clamping here
# means the user can drop any high-res Image Texture into Blender and
# the pipeline will downscale just before encoding.
GX_MAX_TEXTURE_DIM = 1024


def _clamp_texture_size_for_gx(img, max_dim: int = GX_MAX_TEXTURE_DIM):
    """Read pixels from `img`, downscaling to <= max_dim if needed and
    aligning both dims to multiples of 4 (= CMP tile size; harmless for
    other formats).

    Returns (out_w, out_h, raw_rgba_bytes) where rows are top-down (GX
    convention; Blender stores bottom-up so we flip).  Source image is
    not mutated -- when resampling is needed, a temporary `img.copy()`
    is scaled and then removed from `bpy.data.images`.

    Edge cases:
      * w/h already <= max_dim AND already 4-aligned -> direct read,
        no copy / scale.
      * Aspect-preserving scale that lands on a non-/4 dim -> rounded
        DOWN to nearest /4 (worst case 3 pixel crop per side).  Keeps
        CMP / non-CMP encoding paths identical so format choice is not
        silently downgraded by the alignment guard in make_textured_mobj.
      * `img.copy()` raising (image deleted mid-call etc.) -> falls back
        to direct read of the source at original size; caller's existing
        alignment guard kicks in if the format demands /4.
    """
    w, h = img.size
    if w <= 0 or h <= 0:
        return None

    # Decide target dimensions.
    needs_scale = max(w, h) > max_dim or (w % 4) != 0 or (h % 4) != 0
    if needs_scale:
        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
        else:
            scale = 1.0
        nw = max(4, (round(w * scale) // 4) * 4)
        nh = max(4, (round(h * scale) // 4) * 4)
    else:
        nw, nh = w, h

    if (nw, nh) != (w, h):
        try:
            scaled = img.copy()
        except Exception as ex:
            print(f"  texture clamp: img.copy() failed for {img.name!r}: "
                  f"{type(ex).__name__}: {ex}; using source dims {w}x{h}")
            scaled = None

        if scaled is not None:
            try:
                scaled.scale(nw, nh)
                px = list(scaled.pixels)
                print(f"  texture auto-clamp: {img.name!r} {w}x{h} -> "
                      f"{nw}x{nh} (GX max={max_dim}, /4 aligned)")
                w, h = nw, nh
            finally:
                # Best-effort cleanup; bpy.data.images.remove() can raise
                # in odd contexts (image still referenced, etc.).
                try:
                    import bpy
                    bpy.data.images.remove(scaled)
                except Exception:
                    pass
        else:
            px = list(img.pixels)
    else:
        px = list(img.pixels)

    # Blender stores pixels as flat float RGBA (row-major, bottom-up).
    # GX expects top-down, so flip rows.
    raw = bytearray(w * h * 4)
    for y in range(h):
        src_row = (h - 1 - y) * w * 4
        dst_row = y * w * 4
        for i in range(w * 4):
            v = px[src_row + i]
            if v < 0.0:
                v = 0.0
            elif v > 1.0:
                v = 1.0
            raw[dst_row + i] = int(round(v * 255))
    return w, h, bytes(raw)


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
    Returns the image as (width, height, raw_rgba_bytes) or None.

    Image is auto-clamped to GX hardware max (= ``GX_MAX_TEXTURE_DIM``,
    1024) and 4-pixel-aligned.  See ``_clamp_texture_size_for_gx``.
    """
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
    return _clamp_texture_size_for_gx(src.image)


# -- HSD MObj/TObj/Image construction --------------------------------------

def make_textured_mobj(hsdraw, color, img_tuple, target_format=None):
    """Build an MObj with TObj+Image attached, configured to match the
    vanilla MR_highway road's *lit + textured* pattern.

    The result, byte-for-byte from a vanilla road DObj's MObj/TObj:

      * MObj.render_flags = 0x2011 (CONSTANT | TEX0 | ALPHA_MAT)
      * MObj.material via Material.new(...): amb=128,128,128 / dif=WHITE
        / spc=WHITE / shininess=50 / alpha=1
      * TObj.flags = 0x40010 (LIGHTMAP_DIFFUSE 0x10 + COLORMAP_MODULATE
        in bits 16-19), wrap_s/t=CLAMP, mag=LINEAR, repeat_s/t=1,
        scale=(1,1,1) identity, tex_gen_src=TG_TEX0.

    Construction goes through ``hsdraw.MObj.alloc_textured(material,
    image, **kwargs)`` (added in the 2026-05-11 hsdraw wheel,
    de-coupling action item #5).  The hsdraw library deliberately does
    NOT ship a course-genre wrapper -- the kwargs explicitly capture
    every value the MKGP2 course renderer expects, with rationale in
    the inline comments around the call.

    Why we use lit + textured (not unlit): MKGP2's course renderer only
    samples TEX0 along its lit-mesh TEV pipeline.  TObjs that lack the
    LIGHTMAP_DIFFUSE bit fall through to a "no texture, output
    Material.dif" fallback -- every billboard collapsed to its BSDF
    Base Color default (#cccccc) regardless of the image we attached,
    until we routed the mesh through the lit pipeline.  COLORMAP_MODULATE
    means ``final_color = texel * dif``, so the synth 4x4 path fills
    the texel with the BSDF base color and Material.dif stays WHITE so
    the per-mesh color survives the multiplication unchanged.  Real
    Image Texture meshes get their pattern multiplied by Material.dif
    -- set BSDF Base Color = white (= no tint) for full saturation.

    Parameters
    ----------
    color : (R, G, B, A) byte tuple
        Used for the synth 4x4 tile (= img_tuple is None) so the per-
        mesh BSDF color survives MODULATE with Material.dif=WHITE.
    img_tuple : (w, h, raw_rgba_bytes) or None
        Fully assembled top-down RGBA8 raster from the BSDF Image
        Texture node (already auto-clamped to <=1024 dim and 4-aligned;
        see ``_clamp_texture_size_for_gx``).  None -> synth 4x4.
    target_format : str | int | None
        GX target format ("RGBA8" / "CMP" / "RGB5A3" or the matching
        int).  Silently downgrades to RGBA8 if the requested format
        demands tile alignment this (w, h) pair can't satisfy.
    """
    if img_tuple is None:
        # Synth 4x4 tile filled with the BSDF base color.  Under
        # COLORMAP_BLEND with Material.dif=WHITE and tobj.blending=1.0
        # the renderer outputs `texel` directly, so the per-mesh BSDF
        # color survives by being baked into the synth tile (instead of
        # being multiplied by a white texel).
        w, h = 4, 4
        pixel = bytes(color)  # (R, G, B, A) per BSDF base color
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

    # Channel-order workaround for hsdraw's RGB5A3 encoder: the in-game
    # sample displays the encoded image with R and B swapped vs the raw
    # we feed in (verified empirically — RGBA8 and CMP both round-trip
    # cleanly, only RGB5A3 inverts).  Pre-swap R<->B so the displayed
    # color matches the source PNG.  TODO: fix in hsdraw upstream
    # (`gx_image::encode_image` for the RGB5A3 path) and remove this.
    if fmt_name == "RGB5A3":
        swapped = bytearray(raw)
        for i in range(0, len(swapped), 4):
            swapped[i], swapped[i + 2] = swapped[i + 2], swapped[i]
        raw = bytes(swapped)

    # GX-encode.  hsdraw handles tile alignment + 32-byte padding
    # internally (`gx_encode` wraps `gx_image::encode_image`).
    gx_bytes = hsdraw.gx_encode(fmt_int, w, h, raw)

    # Image alloc + populate.
    img = hsdraw.Image.alloc()
    img.width = w
    img.height = h
    img.format = fmt_int
    img.set_image_data_bytes(gx_bytes)

    # Material with vanilla YI_land_long_a textured-POBJ values: amb /
    # spc non-zero so the lit-mesh TEV pipeline has color sources, dif
    # WHITE so COLORMAP_MODULATE with blending=1.0 yields `texel` exactly
    # (the per-mesh color is carried in the texel itself, see the
    # synth-4x4 fallback above).
    mat = hsdraw.Material.new(
        amb=(128, 128, 128, 255),
        dif=(255, 255, 255, 255),
        spc=(255, 255, 255, 255),
        shininess=50.0,
        alpha=1.0,
    )

    # Build the MObj+TObj chain in one call via the hsdraw 2026-05-11
    # `MObj.alloc_textured(material, image, **kwargs)` preset (handoff
    # `_for_course_mesh` 不採用、caller が kwargs で course-genre 寄せ
    # 設定を明示する責務)。byte-equivalent to the prior 25-line explicit
    # setter sequence: render_flags / wrap / alpha_op / lightmap_diffuse /
    # repeat / scale / mag_filter / color_op / blending / tex_gen_src /
    # tex_map_id all match vanilla course textured POBJ pattern.
    #
    # Rationale per kwarg (= the comment trail from the explicit
    # version, condensed):
    #   render_flags=0x2011  CONSTANT|TEX0|ALPHA_MAT, vanilla CULLBACK
    #     road. 0x40002011 (TEXEDGE) caused every my_course mesh to go
    #     black on bisect 2026-05-10 step 3 (= GXCompare NEVER discards
    #     every pixel when bit 28 is off).
    #   tex_gen_src=4 (TG_TEX0)  use vertex TEX0 attribute as UV input.
    #   repeat_s/t=1  vanilla MR_highway_long_A.dat の全 275 textured
    #     TObj が 1。hsdraw default の 0 だと MKGP2 runtime が
    #     TObj_RebuildTransformMtx で Scale(0/SX,...) を slot 60 に load
    #     して UV collapse (memory project_repeat_st_fixes_uv_collapse.md /
    #     docs/hsd_research/ghidra_gx_matrix_init_trace_part2.md)。
    #   wrap_s/t=0 (GX_CLAMP)  bisect 2026-05-10 step 6: REPEAT を選ぶと
    #     UV (0,0) sampling 時に bilinear footprint が opposite cell まで
    #     広がって 2-cell 平均色が出る; CLAMP で同 cell 内に収まる。inu
    #     aliased もすべて CLAMP/CLAMP。
    #   color_op=4 (COLORMAP_MODULATE)  texel × Material.dif。dif WHITE
    #     なので texel そのまま出る。
    #   alpha_op=0 (ALPHAMAP_NONE)  texel.A は素通し (bisect で MODULATE
    #     にしたら一部 alpha 描画が崩れた)。
    #   mag_filter=1 (GX_LINEAR)  inu_aliased pattern。
    #   lightmap_diffuse=True  TObj.flags bit 0x10。これが無いと MKGP2
    #     course renderer が「no texture, output Material.dif」 fallback
    #     に落ちて texture が出ない (memory
    #     project_alloc_unlit_color_no_tex_sampling.md)。
    #   scale=(1,1,1)  identity UV transform; default の 0 だと
    #     UV × 0 = 0 で全 fragment が texel(0,0) 単色 (= UV collapse)。
    mobj = hsdraw.MObj.alloc_textured(
        mat, img,
        render_flags=0x2011,
        tex_map_id=0,
        tex_gen_src=4,
        scale=(1.0, 1.0, 1.0),
        wrap_s=0, wrap_t=0,
        repeat_s=1, repeat_t=1,
        mag_filter=1,
        color_op=4, alpha_op=0,
        blending=1.0,
        lightmap_diffuse=True,
    )
    return mobj


# -- Scene template loader -------------------------------------------------

def load_scene_template_dat(hsdraw, template_path):
    """Load a vanilla course `.dat` to use as a structural template,
    strip every root except `scene_data`, and return the resulting
    `hsdraw.Dat` ready for the caller to repoint
    ``scene_data.JOBJDescs[0].RootJoint`` and add their own alias roots.

    Why this exists: ``hsdraw.Dat.alloc_scene_data_minimal()`` produces an SObj
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
