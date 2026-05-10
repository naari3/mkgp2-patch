"""Vanilla-independent HSD .dat synthesis from a Blender `mkgp2:<dat>`
bundle (M3a).

Walks the bundle's stashed joint table + scene.json materials and
reconstructs an HSD .dat from scratch via the vendored `hsdraw` Rust
extension -- no vanilla `.dat` bytes are read.

For each texture, the writer picks between two paths based on whether
the user touched the Blender Image since import:

  * **Bypass**   PNG content hash matches `mkgp2_png_hash` AND
                 `Image.is_dirty` is False. Re-uses the raw GX-encoded
                 payload stashed at `mkgp2_gx_path` byte-for-byte
                 (including CMP, where re-encode would visibly degrade).
  * **Re-encode** Image was edited or hash mismatched. Reads
                 `Image.pixels` (RGBA8 float), packs to bytes, and runs
                 `hsdraw.gx_encode(format, w, h, rgba)` for the format
                 captured at import time.

Geometry is always rebuilt via `hsdraw.MeshBuilder`. Vertices arrive in
HSD world space (the importer baked them via JObj forward kinematics
+ SingleBoundJOBJ); we invert that bake per (joint, single_bind_joint)
to push back into JObj-local before the builder consumes them.

Joint TRS / flags / hierarchy / aliases come from the bundle's stashed
`mkgp2_joints` / `mkgp2_joint_aliases` props (synced from any Empty
parent edits at the top of the operator's execute path).

Limitations (call out in the operator UI when they bite):
  * Texture **dimension** changes are rejected (encoder requires the
    original w / h). M3 scope.
  * **New** Images that didn't exist at import (no `mkgp2_gx_*` props
    and no fallback format guess) are rejected. Use vanilla bypass
    until a follow-up adds new-tex authoring.
  * SingleBoundJOBJ is honored only as a transform source (vertices
    re-baked through it). Envelope / shape-set rigging is still out of
    scope.
"""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import _blender_material as bm


# ---------------------------------------------------------------------------
# HSDLib enum mirrors (string -> u32)
#
# csx export records these as the HSDLib enum's `.ToString()` form. The
# typed-view setters in the hsdraw binding take the numeric value, so we
# keep a plain dict here rather than reaching into HSDLib via dotnet.
# ---------------------------------------------------------------------------

_COLORMAP = {
    "NONE": 0, "COLORMAP_NONE": 0,
    "ALPHA_MASK": 1, "COLORMAP_ALPHA_MASK": 1,
    "RGB_MASK": 2, "COLORMAP_RGB_MASK": 2,
    "BLEND": 3, "COLORMAP_BLEND": 3,
    "MODULATE": 4, "COLORMAP_MODULATE": 4,
    "REPLACE": 5, "COLORMAP_REPLACE": 5,
    "PASS": 6, "COLORMAP_PASS": 6,
    "ADD": 7, "COLORMAP_ADD": 7,
    "SUB": 8, "COLORMAP_SUB": 8,
}
_ALPHAMAP = {
    "NONE": 0, "ALPHAMAP_NONE": 0,
    "ALPHA_MASK": 1, "ALPHAMAP_ALPHA_MASK": 1,
    "BLEND": 2, "ALPHAMAP_BLEND": 2,
    "MODULATE": 3, "ALPHAMAP_MODULATE": 3,
    "REPLACE": 4, "ALPHAMAP_REPLACE": 4,
    "PASS": 5, "ALPHAMAP_PASS": 5,
    "ADD": 6, "ALPHAMAP_ADD": 6,
    "SUB": 7, "ALPHAMAP_SUB": 7,
}
_WRAPMODE = {"CLAMP": 0, "REPEAT": 1, "MIRROR": 2}
_TEXMAPID = {
    "GX_TEXMAP0": 0, "GX_TEXMAP1": 1, "GX_TEXMAP2": 2, "GX_TEXMAP3": 3,
    "GX_TEXMAP4": 4, "GX_TEXMAP5": 5, "GX_TEXMAP6": 6, "GX_TEXMAP7": 7,
    "GX_MAX_TEXMAP": 8, "GX_TEXMAP_NULL": 9, "GX_TEXMAP_DISABLE": 10,
}
_TEXFILTER = {
    "GX_NEAR": 0, "GX_LINEAR": 1,
    "GX_NEAR_MIP_NEAR": 2, "GX_LIN_MIP_NEAR": 3,
    "GX_NEAR_MIP_LIN": 4, "GX_LIN_MIP_LIN": 5,
}
_TEXFMT = {
    "I4": 0, "I8": 1, "IA4": 2, "IA8": 3,
    "RGB565": 4, "RGB5A3": 5, "RGBA8": 6, "CMP": 14,
}

# JOBJ_FLAG -- mirror from HSDLib HSDRaw/Common/HSD_JOBJ.cs. Identical
# to the table in `_hsd_writer.py`; duplicated locally so this module
# stays import-free when the writer is dropped.
_JOBJ_FLAG = {
    "SKELETON":              1 << 0,
    "SKELETON_ROOT":         1 << 1,
    "ENVELOPE_MODEL":        1 << 2,
    "CLASSICAL_SCALING":     1 << 3,
    "HIDDEN":                1 << 4,
    "PTCL":                  1 << 5,
    "MTX_DIRTY":             1 << 6,
    "LIGHTING":              1 << 7,
    "TEXGEN":                1 << 8,
    "BILLBOARD":             1 << 9,
    "VBILLBOARD":            2 << 9,
    "HBILLBOARD":            3 << 9,
    "RBILLBOARD":            4 << 9,
    "INSTANCE":              1 << 12,
    "PBILLBOARD":            1 << 13,
    "SPLINE":                1 << 14,
    "FLIP_IK":               1 << 15,
    "SPECULAR":              1 << 16,
    "USE_QUATERNION":        1 << 17,
    "OPA":                   1 << 18,
    "XLU":                   1 << 19,
    "TEXEDGE":               1 << 20,
    "NULL":                  0,
    "JOINT1":                1 << 21,
    "JOINT2":                2 << 21,
    "EFFECTOR":              3 << 21,
    "USER_DEFINED_MTX":      1 << 23,
    "MTX_INDEPEND_PARENT":   1 << 24,
    "MTX_INDEPEND_SRT":      1 << 25,
    "MTX_SCALE_COMPENSATE":  1 << 26,
    "ROOT_OPA":              1 << 28,
    "ROOT_XLU":              1 << 29,
    "ROOT_TEXEDGE":          1 << 30,
}

# POBJ_FLAG -- mirror from HSDLib HSDRaw/Common/HSD_POBJ.cs.
_POBJ_CULLBACK = 1 << 14
_POBJ_CULLFRONT = 1 << 15


# ---------------------------------------------------------------------------
# Coordinate transforms
# ---------------------------------------------------------------------------

def _blender_to_hsd_vec(co):
    """Inverse of `blender_import_hsd.py:game_to_blender`.

    Importer maps `(gx, gy, gz) -> (gx, -gz, gy)` (game Y-up -> Blender Z-up).
    Inverse: `(bx, by, bz) -> (bx, bz, -by)`.
    """
    return (co.x, co.z, -co.y)


def _flag_bits(names) -> int:
    bits = 0
    for n in names or []:
        if not n or n == "NULL":
            continue
        v = _JOBJ_FLAG.get(n)
        if v is None:
            print(f"  WARN: unknown JOBJ_FLAG {n!r}; skipping")
            continue
        bits |= v
    return bits


def _build_world_matrices(joints):
    """Forward-kinematics: return {joint_id -> mathutils.Matrix} in HSD
    world space.

    The csx exporter used Euler XYZ in the HSDRawViewer convention
    (`Scale * EulerXYZ * Translation`, row-vector). We reproduce that
    via `Matrix.Translation @ EulerXYZ.to_matrix() @ Matrix.Scale`
    (column-vector) which is the same composition.
    """
    import mathutils
    mat_by_id = {}
    parent_of = {j["id"]: j.get("parent") for j in joints}
    by_id = {j["id"]: j for j in joints}

    def world(jid):
        if jid in mat_by_id:
            return mat_by_id[jid]
        j = by_id.get(jid)
        if j is None:
            return mathutils.Matrix.Identity(4)
        t = j.get("translation") or [0.0, 0.0, 0.0]
        r = j.get("rotation") or [0.0, 0.0, 0.0]
        s = j.get("scale") or [1.0, 1.0, 1.0]
        local = (
            mathutils.Matrix.Translation(t)
            @ mathutils.Euler((r[0], r[1], r[2]), 'XYZ').to_matrix().to_4x4()
            @ mathutils.Matrix.Diagonal((s[0], s[1], s[2], 1.0))
        )
        parent = parent_of.get(jid)
        if parent is not None and parent in by_id:
            mat = world(parent) @ local
        else:
            mat = local
        mat_by_id[jid] = mat
        return mat

    for j in joints:
        world(j["id"])
    return mat_by_id


# ---------------------------------------------------------------------------
# Texture bypass / encode
# ---------------------------------------------------------------------------

class TextureBuildError(RuntimeError):
    pass


def _png_bytes_for_image(img) -> Optional[bytes]:
    """Return the file content of `img.filepath_raw` (or filepath fallback).
    Returns None if the file isn't reachable; the bypass path then
    has to fall back to re-encode.
    """
    fp = img.filepath_raw or img.filepath
    if not fp:
        return None
    try:
        # filepath_raw can carry Blender's `//` shorthand; resolve it
        # against the loaded .blend's directory.
        import bpy
        p = Path(bpy.path.abspath(fp))
        if not p.is_file():
            return None
        return p.read_bytes()
    except Exception:
        return None


def _image_pixels_rgba8(img) -> bytes:
    """Pack `Image.pixels` (float32 RGBA, 0..1) into bytes (u8 RGBA).
    Length = 4 * width * height. Raises TextureBuildError on Image
    state we can't handle (no pixels loaded, channels != 4)."""
    w, h = int(img.size[0]), int(img.size[1])
    if w <= 0 or h <= 0:
        raise TextureBuildError(
            f"Image '{img.name}': zero dimensions ({w}x{h}); not loaded?")
    if img.channels != 4:
        raise TextureBuildError(
            f"Image '{img.name}': channels={img.channels}, expected 4 (RGBA)")
    pixels = list(img.pixels)
    # Pixels arrive bottom-to-top in Blender; PNG / GX want top-to-bottom.
    # Flip rows here so the encoded GX bytes line up with the on-disk PNG.
    out = bytearray(w * h * 4)
    row_bytes = w * 4
    for y in range(h):
        src_off = (h - 1 - y) * row_bytes
        dst_off = y * row_bytes
        for i in range(row_bytes):
            v = pixels[src_off + i]
            # Clamp + quantize
            if v < 0.0:
                v = 0.0
            elif v > 1.0:
                v = 1.0
            out[dst_off + i] = int(v * 255.0 + 0.5)
    return bytes(out)


def _build_image_struct(blender_img, hsdraw, *, log):
    """Allocate an `Image` struct from a Blender Image, dispatching
    bypass-vs-reencode based on the M2 stashed metadata.

    Returns (Image, decision_str) for logging.
    """
    if blender_img is None:
        raise TextureBuildError("texture has no Blender Image bound")

    name = blender_img.name
    gx_path_raw = blender_img.get("mkgp2_gx_path")
    gx_format = blender_img.get("mkgp2_gx_format")
    gx_w = blender_img.get("mkgp2_gx_width")
    gx_h = blender_img.get("mkgp2_gx_height")
    png_hash = blender_img.get("mkgp2_png_hash")

    if not gx_format or gx_w is None or gx_h is None:
        raise TextureBuildError(
            f"Image '{name}' lacks M2 metadata (mkgp2_gx_format / "
            "mkgp2_gx_width / mkgp2_gx_height); was the bundle imported "
            "by an M2-capable importer?")
    fmt_int = _TEXFMT.get(gx_format)
    if fmt_int is None:
        raise TextureBuildError(
            f"Image '{name}': unknown GX format {gx_format!r}")
    cur_w = int(blender_img.size[0])
    cur_h = int(blender_img.size[1])
    if cur_w != int(gx_w) or cur_h != int(gx_h):
        raise TextureBuildError(
            f"Image '{name}': dimensions changed in Blender "
            f"({cur_w}x{cur_h} vs original {gx_w}x{gx_h}). "
            "Resize is not supported in M3.")

    img_struct = hsdraw.Image.alloc()
    img_struct.format = fmt_int
    img_struct.width = int(gx_w)
    img_struct.height = int(gx_h)
    # mipmap / min_lod / max_lod left at zero (vanilla course .dat
    # textures don't carry mips on the corpus we've seen).

    # ---- Bypass dispatch ---------------------------------------------------
    bypass_ok = False
    if gx_path_raw and png_hash and not blender_img.is_dirty:
        gx_path = Path(gx_path_raw)
        png_now = _png_bytes_for_image(blender_img)
        if (png_now is not None
                and hashlib.sha1(png_now).hexdigest() == str(png_hash)
                and gx_path.is_file()):
            payload = gx_path.read_bytes()
            img_struct.set_image_data_bytes(payload)
            log(f"  tex {name} ({gx_format} {gx_w}x{gx_h}): "
                f"BYPASS ({len(payload)} bytes from .gx)")
            return img_struct, "bypass"
        bypass_ok = False

    # ---- Re-encode path ----------------------------------------------------
    rgba = _image_pixels_rgba8(blender_img)
    payload = hsdraw.gx_encode(fmt_int, gx_w, gx_h, rgba)
    img_struct.set_image_data_bytes(payload)
    log(f"  tex {name} ({gx_format} {gx_w}x{gx_h}): "
        f"REENCODE ({len(payload)} bytes from gx_encode)")
    return img_struct, "reencode"


# ---------------------------------------------------------------------------
# MObj / TObj construction
# ---------------------------------------------------------------------------

def _build_tobj_chain(material_dto, image_cache, hsdraw, *, log):
    """Walk a scene.json material's `textures` list and produce a TObj
    chain head (None if the material has no textures). image_cache is a
    {tex_id -> (Image_struct, decision)} memo so identical textures get
    the same allocated Image (csx-style dedup)."""
    refs = material_dto.get("textures") or []
    if not refs:
        return None

    head = None
    prev = None
    for ref in refs:
        tex_id = ref["tex_id"]
        cache_entry = image_cache.get(tex_id)
        if cache_entry is None:
            # Image_struct is built per-bundle by the caller; if the
            # caller didn't pre-populate, this is a programmer bug.
            raise TextureBuildError(
                f"texture id {tex_id!r} referenced by material "
                f"{material_dto.get('id')!r} was not pre-built in image_cache")
        img_struct, _decision = cache_entry

        tobj = hsdraw.TObj.alloc()
        tobj.set_image_data(img_struct)

        # tex_map_id
        tmid = _TEXMAPID.get(ref.get("tex_map_id", "GX_TEXMAP0"), 0)
        tobj.tex_map_id = tmid
        # GXTexGenSrc=4 (TG_TEX0) — vertex の TEX0 attribute から UV を取る。
        # csx export は今のところ gen_src を記録していないが vanilla 全件
        # TG_TEX0 なのでハードコード。hsdraw 2026-05-11 wheel から property
        # 化済 (それ以前は post-write byte patch でしか設定できなかった)。
        tobj.tex_gen_src = 4

        # wrap / repeat
        ws = _WRAPMODE.get(ref.get("wrap_s", "CLAMP"), 0)
        wt = _WRAPMODE.get(ref.get("wrap_t", "CLAMP"), 0)
        tobj.wrap_s = ws
        tobj.wrap_t = wt
        tobj.repeat_s = int(ref.get("repeat_s", 1))
        tobj.repeat_t = int(ref.get("repeat_t", 1))

        # filter / blending
        mf = _TEXFILTER.get(ref.get("mag_filter", "GX_LINEAR"), 1)
        tobj.mag_filter = mf
        tobj.blending = float(ref.get("blending", 1.0))

        # color / alpha operation -- packed into TObj.flags via setters
        tobj.set_color_operation(_COLORMAP.get(ref.get("color_op", "MODULATE"), 4))
        tobj.set_alpha_operation(_ALPHAMAP.get(ref.get("alpha_op", "MODULATE"), 3))
        # coord_type defaults to UV (0); the csx export records it via the
        # implicit Flags low nibble. The stashed scene.json doesn't carry
        # the field today; default UV matches the entire vanilla corpus.
        tobj.set_coord_type(0)

        # Identity scale (csx export doesn't record TObj transform; the
        # field exists in HSD but every vanilla course .dat we've seen
        # leaves it at scale=1, R/T=0).
        tobj.set_scale(1.0, 1.0, 1.0)

        if head is None:
            head = tobj
        else:
            prev.set_next(tobj)
        prev = tobj
    return head


def _build_mobj(material_dto, image_cache, hsdraw, *, log):
    """Allocate an MObj reflecting `material_dto` (one entry from
    scene.json `materials[]`). Material struct gets the diffuse RGBA8 +
    alpha. RenderFlags are set from the raw u32 (preserves the original
    bit layout including obscure flags we don't have a name for)."""
    mobj = hsdraw.MObj.alloc()
    mobj.render_flags = int(material_dto.get("render_flags_raw", 0))

    dif = material_dto.get("diffuse_rgba") or [255, 255, 255, 255]
    mat = hsdraw.Material.new(
        dif=(int(dif[0]) & 0xFF, int(dif[1]) & 0xFF,
             int(dif[2]) & 0xFF, int(dif[3]) & 0xFF),
        amb=(255, 255, 255, 255),
        spc=(255, 255, 255, 255),
        alpha=float(material_dto.get("alpha", 1.0)),
        shininess=50.0,
    )
    mobj.set_material(mat)

    head = _build_tobj_chain(material_dto, image_cache, hsdraw, log=log)
    if head is not None:
        mobj.set_textures(head)
    return mobj


# ---------------------------------------------------------------------------
# Mesh -> POBJ
# ---------------------------------------------------------------------------

def _build_pobj_for_mesh(obj, joint_world, sb_world, cull, hsdraw, *, log,
                          want_normals=True, want_colors=True, want_uvs=True):
    """Build one POBJ from a Blender mesh object.

    `joint_world` and `sb_world` are mathutils.Matrix instances in HSD
    world space (already converted from any Blender frame). The vertex
    bake at import was `pos_blender = blender_from_game(joint_world *
    sb_world * pos_local)`, so to invert we apply
    `inv(joint_world * sb_world)` after converting back from Blender to
    HSD world.

    Triangulation: walk `mesh.calc_loop_triangles()` so n-gons get split
    cleanly. Vertices are deduplicated by (vertex_index, uv, normal,
    color) tuple to keep POBJ stream tight while still letting Blender
    UV / normal seams produce distinct stream entries.
    """
    import mathutils
    me = obj.data
    if not me.polygons:
        return None, 0, 0

    final = joint_world @ sb_world
    inv_final = final.inverted_safe()
    rot_inv = inv_final.to_3x3()

    me.calc_loop_triangles()

    # Read the active UV / vertex color layers if present (gated by
    # caller's want_* flags so we emit only what scene.json originally
    # carried -- the importer triangulates everything and Blender will
    # auto-compute normals/UVs even when the source had none).
    uv_layer = me.uv_layers.active.data if (want_uvs and me.uv_layers) else None
    color_attr = None
    if want_colors and me.color_attributes:
        ca = me.color_attributes.active_color
        if ca is not None and ca.domain == 'CORNER':
            color_attr = ca.data

    has_normals = bool(want_normals)
    if has_normals:
        # Per-loop split normals: Blender 4.1+ removed
        # `calc_normals_split()`; `corner_normals` is auto-populated.
        # Older builds (pre-4.1) still expose the explicit recompute --
        # run both so the writer works on either.
        if hasattr(me, "calc_normals_split"):
            me.calc_normals_split()
        corner_normals = getattr(me, "corner_normals", None)
    else:
        corner_normals = None

    # Stream dedup table keyed by (vert_idx, u, v, nx, ny, nz, r, g, b, a)
    stream: Dict[tuple, int] = {}
    positions: List[Tuple[float, float, float]] = []
    uvs: List[Tuple[float, float]] = []
    normals: List[Tuple[float, float, float]] = []
    colors: List[Tuple[int, int, int, int]] = []
    triangles: List[Tuple[int, int, int]] = []

    have_uv = uv_layer is not None
    have_color = color_attr is not None

    for tri in me.loop_triangles:
        local_idx = []
        for li in tri.loops:
            loop = me.loops[li]
            vi = loop.vertex_index
            v_world = obj.matrix_world @ me.vertices[vi].co
            v_hsd_world = mathutils.Vector(_blender_to_hsd_vec(v_world))
            v_local = inv_final @ v_hsd_world
            pos_key = (round(v_local.x, 5), round(v_local.y, 5), round(v_local.z, 5))

            uv_key = None
            if have_uv:
                u, v = uv_layer[li].uv
                uv_key = (round(u, 6), round(1.0 - v, 6))

            nrm_key = None
            if has_normals:
                if corner_normals is not None:
                    n_local_blender = mathutils.Vector(corner_normals[li].vector)
                else:
                    n_local_blender = mathutils.Vector(loop.normal)
                n_world = (obj.matrix_world.to_3x3() @ n_local_blender).normalized()
                n_hsd_world = mathutils.Vector(_blender_to_hsd_vec(n_world))
                n_local = (rot_inv @ n_hsd_world).normalized()
                nrm_key = (round(n_local.x, 4), round(n_local.y, 4), round(n_local.z, 4))

            col_key = None
            if have_color:
                c = color_attr[li].color
                col_key = (
                    max(0, min(255, int(c[0] * 255 + 0.5))),
                    max(0, min(255, int(c[1] * 255 + 0.5))),
                    max(0, min(255, int(c[2] * 255 + 0.5))),
                    max(0, min(255, int(c[3] * 255 + 0.5))),
                )

            key = (vi, pos_key, uv_key, nrm_key, col_key)
            idx = stream.get(key)
            if idx is None:
                idx = len(positions)
                stream[key] = idx
                positions.append(pos_key)
                if have_uv:
                    uvs.append(uv_key)
                if has_normals:
                    normals.append(nrm_key)
                if have_color:
                    colors.append(col_key)
            local_idx.append(idx)
        triangles.append(tuple(local_idx))

    if not triangles:
        return None, 0, 0

    # Bulk-push via MeshBuilder.from_arrays (= one Python→Rust crossing per
    # attribute instead of N per-vertex add_*).  positions/normals are
    # 3-tuples of float, uvs 2-tuples of float, colors 4-tuples of byte,
    # triangles 3-tuples of vert index — flatten all into 1-D arrays for
    # the new ctor.
    kwargs = {
        "positions":  [c for p in positions for c in p],
        "triangles":  [c for t in triangles for c in t],
    }
    if has_normals:
        kwargs["normals"] = [c for n in normals for c in n]
    if have_color:
        # `colors` is bytes-like per-vertex; from_arrays accepts list/bytes
        kwargs["colors"] = bytes(c for col in colors for c in col)
    if have_uv:
        kwargs["uvs"] = [c for u in uvs for c in u]

    # Cull-mode is NOT configured via mb.set_cull_back/_front — those
    # toggle POBJ.flags bits 0x4000 / 0x2000 which the game's POBJ_FLAG
    # enum treats as unknown POBJ types (TEX0 sampling stops working).
    # Cull mode belongs in PE_DESC, not POBJ.flags; skipped until hsdraw
    # exposes that surface.
    mb = hsdraw.MeshBuilder.from_arrays(**kwargs)
    pobj = mb.build()
    return pobj, len(positions), len(triangles)


# ---------------------------------------------------------------------------
# Top-level export
# ---------------------------------------------------------------------------

def export_bundle_to_dat(
    bundle,
    scene_json,
    output_dat,
    *,
    log_fn=None,
    template_dat=None,
) -> dict:
    """Write `bundle` (a Blender `mkgp2:<dat>` collection) out as a
    fresh HSD .dat at `output_dat`.

    Parameters:
      bundle      Blender Collection. Must carry the M2 stashed props
                  (`mkgp2_joints`, `mkgp2_joint_aliases`).
      scene_json  Either an inline JSON string (new-style bundles, kept
                  on the collection's `mkgp2_scene_json` custom prop as
                  a literal string) or a filesystem path to a scene.json
                  file (legacy csx-era bundles). Auto-detected: a string
                  starting with `{` is treated as inline JSON, anything
                  else is parsed as a path.
      output_dat  Destination .dat path.
      template_dat  Optional path to a vanilla course .dat used as the
                  scene template — see ``_promote_vis_to_hsd`` for the
                  rationale.  ``None`` falls back to
                  ``hsdraw.Dat.alloc_scene_data()`` (no LObj/COBJ;
                  unsafe to ship, kept only for byte-equiv tests).

    Returns a stats dict. Raises TextureBuildError / RuntimeError on
    fatal inconsistencies (missing stash, malformed material reference,
    etc.) so the operator can surface a clear error.
    """
    import bpy
    import hsdraw

    log = log_fn if log_fn is not None else print
    output_dat = Path(output_dat)

    if isinstance(scene_json, str) and scene_json.lstrip().startswith('{'):
        scene = json.loads(scene_json)
    else:
        scene_json_path = Path(scene_json)
        if not scene_json_path.is_file():
            raise FileNotFoundError(f"scene.json not found: {scene_json_path}")
        scene = json.loads(scene_json_path.read_text(encoding="utf-8"))
    materials_by_id = {m["id"]: m for m in scene.get("materials", [])}
    texture_dtos = {t["id"]: t for t in scene.get("textures", [])}
    # Per-mesh DTOs let us know which attributes were present in the
    # original .dat (csx only emits the attribute slots that had data).
    # Keyed by `mesh_<n>` (the source path prefix the importer stamps as
    # `mkgp2_source_path`) so we can re-derive the mesh's attribute set
    # at export time without re-walking the .dat.
    mesh_dtos = {m["id"]: m for m in scene.get("meshes", [])}

    raw_joints = bundle.get("mkgp2_joints")
    raw_aliases = bundle.get("mkgp2_joint_aliases")
    if not raw_joints:
        raise RuntimeError(f"bundle '{bundle.name}' has no mkgp2_joints stash")
    joints: list = json.loads(raw_joints) if isinstance(raw_joints, str) else list(raw_joints)
    aliases: dict = json.loads(raw_aliases) if isinstance(raw_aliases, str) else dict(raw_aliases or {})
    log(f"bundle: {bundle.name}  joints={len(joints)}  aliases={len(aliases)}")

    if not joints:
        raise RuntimeError(f"bundle '{bundle.name}' has zero joints stashed")

    # ---- Pass 0: per-joint world matrix --------------------------------
    world_by_id = _build_world_matrices(joints)

    # ---- Pass 1: allocate JObj for every joint id ----------------------
    jobj_by_id: Dict[str, "hsdraw.JObj"] = {}
    for j in joints:
        jid = j["id"]
        nj = hsdraw.JObj.alloc()
        nj.set_local_trs(
            *(j.get("translation") or [0, 0, 0]),
            *(j.get("rotation") or [0, 0, 0]),
            *(j.get("scale") or [1, 1, 1]),
        )
        nj.flags = _flag_bits(j.get("flags"))
        jobj_by_id[jid] = nj
    log(f"alloc   : {len(jobj_by_id)} JObjs")

    # ---- Pass 2: wire child / next chain -------------------------------
    for j in joints:
        parent = jobj_by_id[j["id"]]
        ch_ids = j.get("children") or []
        kids = [jobj_by_id[c] for c in ch_ids if c in jobj_by_id]
        if kids:
            parent.set_child(kids[0])
            for i, k in enumerate(kids[:-1]):
                k.set_next(kids[i + 1])

    # ---- Pass 3: pre-build Image structs per unique tex_id -------------
    image_cache: Dict[str, Tuple[object, str]] = {}
    # Group meshes by mkgp2_joint_id; collect referenced material ids
    referenced_tex_ids: set = set()
    mesh_objs = [o for o in bundle.objects if o.type == 'MESH']
    for mo in mesh_objs:
        if mo.data.materials:
            mat = mo.data.materials[0]
            if mat is not None:
                # The Blender material name == scene.json material id (set
                # by importer's `make_material(mat_dto, ...)`)
                mid = mat.name
                # Strip Blender's `.001` disambiguation suffix
                base_mid = mid.split(".", 1)[0]
                m_dto = materials_by_id.get(base_mid)
                if m_dto is not None:
                    for ref in m_dto.get("textures") or []:
                        referenced_tex_ids.add(ref["tex_id"])

    bypass_count = reencode_count = 0
    bpy_images = bpy.data.images
    for tex_id in referenced_tex_ids:
        # Find the Blender Image whose loaded PNG stem == tex_id (this
        # is how the importer keys them; PNGs are named `<sha>.png`).
        bimg = None
        for img in bpy_images:
            stem = Path(img.filepath).stem if img.filepath else img.name
            if stem == tex_id:
                bimg = img
                break
        if bimg is None:
            raise TextureBuildError(
                f"texture id {tex_id!r} referenced by a material but no "
                "Blender Image found with matching stem; was the bundle "
                "fully imported?")
        img_struct, decision = _build_image_struct(bimg, hsdraw, log=log)
        image_cache[tex_id] = (img_struct, decision)
        if decision == "bypass":
            bypass_count += 1
        else:
            reencode_count += 1
    log(f"textures: {len(image_cache)} unique  "
        f"(bypass={bypass_count}, reencode={reencode_count})")

    # ---- Pass 4: build MObj per material id (deduped) ------------------
    mobj_by_mid: Dict[str, object] = {}
    for mid, m_dto in materials_by_id.items():
        mobj_by_mid[mid] = _build_mobj(m_dto, image_cache, hsdraw, log=log)
    log(f"materials: {len(mobj_by_mid)} MObjs built")

    # ---- Pass 5: per joint, build DObj chain ---------------------------
    # For each Blender mesh, attach a DObj to its `mkgp2_joint_id` joint.
    dobj_chain_by_jid: Dict[str, list] = {}
    total_verts = total_tris = 0
    skipped = 0
    fresh_materials = 0   # ad-hoc MObjs built from non-DTO Blender materials
    for mo in mesh_objs:
        jid = mo.get("mkgp2_joint_id")
        if not jid or jid not in jobj_by_id:
            print(f"  WARN: mesh '{mo.name}' has no mkgp2_joint_id -> "
                  "{jid!r}; skipping")
            skipped += 1
            continue
        sb_id = mo.get("mkgp2_single_bind_joint")
        cull = mo.get("mkgp2_cull", "NONE")
        joint_world = world_by_id.get(jid)
        sb_world = world_by_id.get(sb_id) if sb_id else None
        if sb_world is None:
            import mathutils
            sb_world = mathutils.Matrix.Identity(4)
        # Pull attribute presence from the source mesh DTO when we can
        # match the Blender object back to its `mesh_<n>` id. Object name
        # carries Blender's `.001` disambiguator after re-import / dupes;
        # split it off before lookup.
        mesh_id = mo.name.split(".", 1)[0]
        m_dto = mesh_dtos.get(mesh_id)
        want_uvs = bool(m_dto.get("uvs")) if m_dto else True
        want_normals = bool(m_dto.get("normals")) if m_dto else True
        want_colors = bool(m_dto.get("colors")) if m_dto else True
        try:
            pobj, nv, nt = _build_pobj_for_mesh(
                mo, joint_world, sb_world, cull, hsdraw, log=log,
                want_uvs=want_uvs, want_normals=want_normals,
                want_colors=want_colors,
            )
        except Exception as ex:
            raise RuntimeError(f"failed to build POBJ for mesh '{mo.name}': {ex}")
        if pobj is None:
            skipped += 1
            continue
        # Material lookup by Blender material name (matches scene.json id)
        mat_name = "mat_0"
        if mo.data.materials and mo.data.materials[0] is not None:
            mat_name = mo.data.materials[0].name.split(".", 1)[0]
        mobj = mobj_by_mid.get(mat_name)
        if mobj is None:
            # Fresh material the bundle didn't import (= user added a
            # new Blender material to color a hand-added mesh). Build
            # a vis:-style course-compatible MObj on the fly from the
            # BSDF: Base Color drives a 4x4 RGBA8 fallback texture,
            # or an Image Texture node feeding Base Color is used as
            # the per-pixel pattern. Either way the result has the
            # vanilla `CONSTANT|TEX0|ALPHA_MAT` (= 0x2011) render
            # flags set, so the new mesh actually shows the user's
            # color instead of silently going grey.
            fresh_mat = (mo.data.materials[0]
                         if mo.data.materials else None)
            color = bm.bsdf_base_color(fresh_mat)
            img_tuple = bm.bsdf_image_texture(fresh_mat)
            fmt_name, fmt_int = bm.material_target_format(fresh_mat)
            mobj = bm.make_textured_mobj(
                hsdraw, color, img_tuple, target_format=fmt_int)
            fresh_materials += 1
            log(f"  INFO: mesh '{mo.name}' uses fresh material "
                f"'{mat_name}'; built ad-hoc MObj from BSDF "
                f"(color={color}, img="
                f"{f'{img_tuple[0]}x{img_tuple[1]}' if img_tuple else 'solid 4x4'}, "
                f"format={fmt_name})")

        d = hsdraw.DObj.alloc()
        d.set_mobj(mobj)
        d.set_pobj(pobj)
        dobj_chain_by_jid.setdefault(jid, []).append(d)
        total_verts += nv
        total_tris += nt

    # Chain DObjs and attach the head to the joint
    for jid, dobjs in dobj_chain_by_jid.items():
        for i in range(len(dobjs) - 1):
            dobjs[i].set_next(dobjs[i + 1])
        jobj_by_id[jid].set_dobj(dobjs[0])
    log(f"meshes  : built={len(mesh_objs)-skipped}  skipped={skipped}  "
        f"verts={total_verts}  tris={total_tris}")

    # ---- Pass 5b: rendering-flag autocomplete --------------------------
    # vis: bundle (from-scratch course) は flags 列が空のままなことが多い。
    # HSD renderer は ROOT_OPA / ROOT_XLU / ROOT_TEXEDGE のいずれも立っていない
    # JObj tree を traverse skip するので、画面が真っ黒になる
    # (test_course=`OPA, ROOT_OPA`、MR_highway=`ROOT_OPA, ROOT_XLU, ROOT_TEXEDGE`
    #  に対し my_course=`NULL` だった原因)。
    #
    # 補完ルール (元の flags が完全に空ビットだった joint だけ上書き):
    #   - DObj を持つ joint              → OPA を立てる
    #   - parent を持たない root joint   → ROOT_OPA を追加
    # 明示的に flags が指定済みの joint には触らない (vanilla 再 export 経路の
    # XLU や TEXEDGE 等の保持を壊さないため)。
    autofill = 0
    parents = {j["id"]: j.get("parent") for j in joints}
    for j in joints:
        jid = j["id"]
        nj = jobj_by_id[jid]
        if nj.flags != 0:
            continue  # source had explicit flags -> preserve
        new_flags = 0
        if jid in dobj_chain_by_jid:
            new_flags |= _JOBJ_FLAG["OPA"]
        if not parents.get(jid):
            new_flags |= _JOBJ_FLAG["ROOT_OPA"]
        if new_flags:
            nj.flags = new_flags
            autofill += 1
    if autofill:
        log(f"flagfill: auto-set OPA/ROOT_OPA on {autofill} JObj "
            f"(source had no rendering flags)")

    # ---- Pass 6: emit Dat with scene_data + alias roots ----------------
    # Identify the root joint -- the one with no parent and (by csv
    # convention) the first one in the joints list. If multiple roots
    # exist, only the first lands under scene_data; the rest become
    # alias roots only.
    root_id = None
    for j in joints:
        if not j.get("parent"):
            root_id = j["id"]
            break
    if root_id is None:
        raise RuntimeError(
            f"bundle '{bundle.name}' has no parent-less joint; cannot "
            "pick a scene_data RootJoint")
    root_jobj = jobj_by_id[root_id]

    # Seed the Dat from a vanilla scene template when available -- see
    # `_promote_vis_to_hsd.promote_vis_to_dat` for the LObj/COBJ rationale.
    if template_dat is not None:
        dat = bm.load_scene_template_dat(hsdraw, template_dat)
        log(f"scene template: {Path(template_dat).name}")
    else:
        log("WARN: no scene template; falling back to "
            "Dat.alloc_scene_data() — output will be missing LObj/COBJ "
            "and will render incorrectly in-game (characters dark, "
            "course textures collapsed). Use only for byte-equivalence "
            "tests, NEVER for shipping.")
        dat = hsdraw.Dat.alloc_scene_data()
    sd = dat.scene_data()
    if sd is None:
        raise RuntimeError("scene template / alloc_scene_data() did not "
                           "produce a scene_data root; hsdraw bug?")
    sobj = hsdraw.SObj.from_struct(sd.data)
    descs = sobj.jobj_descs()
    if not descs:
        raise RuntimeError("scene_data has no JObjDesc; hsdraw alloc "
                           "shape changed?")
    descs[0].set_root_joint(root_jobj)

    # Add every alias from the bundle's stash. Aliases pointing at a
    # joint we don't have allocated (stale stash) are skipped with a
    # warning so the operator doesn't crash.
    for alias_name, alias_target_id in aliases.items():
        target = jobj_by_id.get(alias_target_id)
        if target is None:
            print(f"  WARN: alias {alias_name!r} -> unknown joint id "
                  f"{alias_target_id!r}; skipping")
            continue
        # If the target joint id == root_id, the alias should still be
        # added so external code can lookup by name -- this matches the
        # vanilla pattern where MR_highway has an alias for the root.
        dat.add_root(alias_name, target)

    out_bytes = bytes(dat.write())
    # GXTexGenSrc=4 (TG_TEX0) は _build_tobj_chain 内で `tobj.tex_gen_src = 4`
    # 直 setter (hsdraw 2026-05-11 wheel から property 化) で設定済み。
    # 過去の post-write byte-patch (`bm.patch_tobj_tex_gen_src`) は撤去。
    #
    # POBJ.flags=0x8000 patch は bundle 経路では引き続き未適用 (vis: 経路の
    # 床真っ黒 / billboard 透明 regression が出た 2026-05-10 を踏まえ、
    # bundle (= 既存 mod 編集) では vanilla bytes をなるべく弄らない方針)。
    output_dat.write_bytes(out_bytes)
    log(f"wrote   : {output_dat.name}  size={len(out_bytes)}")

    return {
        "joints": len(jobj_by_id),
        "aliases": sum(1 for a in aliases.values() if a in jobj_by_id),
        "materials": len(mobj_by_mid),
        "fresh_materials": fresh_materials,
        "textures": len(image_cache),
        "tex_bypass": bypass_count,
        "tex_reencode": reencode_count,
        "meshes": len(mesh_objs) - skipped,
        "verts": total_verts,
        "tris": total_tris,
        "output_size": len(out_bytes),
    }
