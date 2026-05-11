"""Promote a `vis:<name>` editor-only collection to a fresh HSD .dat.

Builds one POBJ per (mesh, material slot) via the vendored hsdraw
MeshBuilder, packs the chain under a single root JObj, repoints
`scene_data.JOBJDescs[0].RootJoint` at the new joint and strips every
non-`scene_data` root from the base .dat.  The output is a self-
contained course .dat the size of just the synthesized geometry.

Convention: a `vis:<name>` collection is a sibling of the addon's
HSD bundle collections (`mkgp2:<dat>`).  It is editor-only until
promoted -- meshes carry no `mkgp2_*` HSD metadata, materials are
plain Principled BSDF.  The promote pass reads BSDF base color as
the unlit MObj diffuse, ignores normals / UVs / textures (Phase 1
scope of hsdraw POBJ writer matches: F32x3 position + RGBA8 color
attribute group, TRIANGLE / TRIANGLE_STRIP primitives).  Geometry
edits in Blender survive the round-trip.
"""

from __future__ import annotations

from pathlib import Path

import bpy

from . import _blender_material as bm

# Re-export the helpers under their historical underscore names so any
# in-tree caller / docstring reference keeps resolving. The actual
# implementations live in `_blender_material` (shared with the bundle
# exporter); these aliases are pure forwarding.
_bsdf_base_color = bm.bsdf_base_color
_bsdf_image_texture_node = bm.bsdf_image_texture
_blender_to_hsd = bm.blender_to_hsd


def _build_pobj_for_slot(obj, me, slot_idx, hsdraw):
    """Build a POBJ from `obj`'s polygons whose material_index ==
    slot_idx.  Returns (Pobj, color_tuple, image_tuple, vert_count, tri_count) or
    None if the slot has no faces.

    Parameters:
      obj  -- Blender Object (caller passes the depsgraph-evaluated copy
              so material slots stay consistent with `me`).  Used for
              material_slots and name only -- geometry comes from `me`.
      me   -- Mesh to read geometry/UV from.  Usually
              ``eval_obj.to_mesh()``; passing the evaluated form means
              modifier stacks (Subdivision Surface, Solidify, Geometry
              Nodes scatter etc.) get baked transparently at export
              time without `Apply Modifier` on the source.

    `image_tuple` is (width, height, rgba_bytes) for the BSDF Image
    Texture if present, else None (caller will fall back to a synthetic
    solid texture from `color_tuple`).

    POBJ attributes: POS + TEX0 (UV) — vanilla textured-POBJ pattern
    (every textured POBJ in vanilla YI_land_long_a uses POS+TEX0 with
    no NRM; lighting is handled per-vertex via CLR0 or omitted entirely
    for unlit scenery).  Including NRM here -- the pre-2026-05-10 path
    -- triggers an MKGP2 HSD renderer code path that collapses the TEX0
    pipeline to a single texel sample, producing the flat-color render
    we observed on the 3 fmt_test_planes.

    UV は Blender の active UV layer から per-loop で読み出す。UV が無い
    場合は polygon の corner index に基づく fallback (tri/quad は
    natural unit square mapping、n-gon は (0,0) flat)。
    """
    poly_idxs = [i for i, p in enumerate(me.polygons)
                 if p.material_index == slot_idx]
    if not poly_idxs:
        return None
    mat = obj.material_slots[slot_idx].material
    color = _bsdf_base_color(mat)
    img_tuple = _bsdf_image_texture_node(mat)

    uv_layer = me.uv_layers.active

    # 実装方針: 各 vert を triangle ごとに独立 emit (vert dedup なし)。
    # UV は active UV layer の per-loop データを採用; 無ければ corner index
    # から (0,0)/(1,0)/(1,1)/(0,1) を生成。GameCube/HSD は V が下向きの
    # convention なので Blender V を 1.0 - v で反転。NRM は emit しない:
    # vanilla の textured POBJ (= POS+TEX0) と同 layout に揃えると
    # MKGP2 renderer の TEX0 + IDENTITY matrix path がちゃんと UV を
    # interpolate するようになる (NRM 含めると flat-color バグ再発)。
    # Geometry buffers — collected per-triangle, then bulk-pushed via
    # MeshBuilder.from_arrays at the end (= per-vertex add_position /
    # add_uv loops would be ~3 Python→Rust crossings per vertex; bulk
    # path is one crossing total, ≥1.3x faster on small mesh and
    # ≥4.8x on 1000-vert smoke per hsdraw maintainer's measurement).
    positions: list = []   # flat list[float]: x0,y0,z0, x1,y1,z1, ...
    uvs:       list = []   # flat list[float]: u0,v0, u1,v1, ...
    triangles: list = []   # flat list[int]:   i0,i1,i2, i0,i1,i2, ...
    # Note: cull-mode is NOT configured via mb.set_cull_back(True) — that
    # toggles POBJ.flags bit 0x4000 which the game's POBJ_FLAG enum
    # treats as an unknown POBJ type (sampler skips TEX0; my_course
    # billboards rendered as Material.DIF only on 2026-05-09).  We set
    # `pobj.flags = 0x8000` (= CULLBACK) explicitly after build instead.
    # Vanilla MR_highway POBJs are `pf=0x0000` or `pf=0x8000` only.

    # Fallback per-corner UVs, used when the mesh has no active UV layer.
    # tri: standard right-triangle covering half the texture; quad: full
    # unit square; n-gon: degenerates to (0, 0) (flat color, same as the
    # pre-2026-05-10 behaviour for textureless faces).
    _fallback_uvs_tri  = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
    _fallback_uvs_quad = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]

    tri_count = 0
    for pi in poly_idxs:
        p = me.polygons[pi]
        verts = list(p.vertices)
        loops = list(p.loop_indices)  # parallel to `verts`, one per corner
        nv = len(verts)
        # Resolve per-corner UV in Blender (u, v) space.  Flip V so the
        # GX texture sampler reads the same orientation Blender's UV
        # editor displays (Blender V grows up; GameCube V grows down).
        if uv_layer is not None:
            corner_uvs = [
                (
                    float(uv_layer.data[li].uv[0]),
                    1.0 - float(uv_layer.data[li].uv[1]),
                )
                for li in loops
            ]
        elif nv == 3:
            corner_uvs = _fallback_uvs_tri
        elif nv == 4:
            corner_uvs = _fallback_uvs_quad
        else:
            corner_uvs = [(0.0, 0.0)] * nv
        # Triangulate as corner-index lists into `verts`/`corner_uvs`.
        # Winding: bisect 2026-05-10 step 4 で REVERSED に切替。
        # Blender CCW (front face = counter-clockwise from outside) ↔
        # GameCube GX hardware の front face = CW のため、Blender 順をそのまま
        # emit すると全 face が back-facing 判定 → POBJ.flags=0x8000 (CULLBACK)
        # と組合せると全面 cull → 真っ黒/透明。reverse して CW 順で emit。
        if nv == 3:
            tri_corners = [(0, 2, 1)]
        elif nv == 4:
            tri_corners = [(0, 2, 1), (0, 3, 2)]
        else:
            tri_corners = [(0, k + 1, k) for k in range(1, nv - 1)]
        for tc in tri_corners:
            base = len(positions) // 3
            for c in tc:
                x, y, z = _blender_to_hsd(obj.matrix_world @ me.vertices[verts[c]].co)
                positions.append(x); positions.append(y); positions.append(z)
                u, v = corner_uvs[c]
                uvs.append(u); uvs.append(v)
            triangles.append(base);     triangles.append(base + 1)
            triangles.append(base + 2)
            tri_count += 1

    mb = hsdraw.MeshBuilder.from_arrays(
        positions=positions,
        triangles=triangles,
        uvs=uvs,
    )
    pobj = mb.build()
    # POBJ.flags=0x8000 (= CULLBACK; vanilla 94-97% の primary mesh が使う)
    # は hsdraw 2026-05-11 wheel から writable property 化、直 setter で。
    # 過去は post-write の bm.patch_pobj_flags() byte patch を使っていた。
    # winding は CCW→CW reversed 済み (`tri_corners` 逆順) なので CULLBACK ON
    # で正しい face が表に出る。
    pobj.flags = 0x8000
    # `len(positions) // 3` because positions is a flat list of x/y/z floats.
    return pobj, color, img_tuple, len(positions) // 3, tri_count


# `_make_textured_mobj` lives in `_blender_material` now; keep an alias
# so existing call sites in this module don't have to change.
_make_textured_mobj = bm.make_textured_mobj


def promote_vis_to_dat(
    vis_collection,
    output_dat,
    *,
    course_name: str | None = None,
    log_fn=None,
    template_dat=None,
) -> dict:
    """Build a fresh `<course>.dat` from `vis_collection`'s meshes.

    Scene template
    --------------
    When ``template_dat`` is provided, the resulting Dat is seeded from
    that vanilla course .dat (everything except `scene_data` is stripped)
    so the SObj retains LObj (lights) and COBJ (camera) descriptors.
    The in-game renderer reads both: a Dat without them (the case when
    we fall back to ``hsdraw.Dat.alloc_scene_data_minimal()``) leaves
    character meshes dark and breaks texture sampling on our own
    geometry.  Pass ``template_dat=None`` only for byte-equivalence
    tests / structural asserts where the absence of light/camera data
    is acceptable.

    Parameters:
      vis_collection -- the `vis:<name>` Collection to promote.
      output_dat     -- destination .dat path.
      course_name    -- stem used for the new `<stem>_joint` alias.
                        Defaults to the collection's name minus the
                        leading `vis:`.
      template_dat   -- optional path to a vanilla course .dat used as
                        the scene template.  ``None`` falls back to
                        ``hsdraw.Dat.alloc_scene_data_minimal()`` and
                        emits a loud warning -- the output is unsafe
                        to ship.

    Returns a stats dict (dobj_count, total_verts, total_tris,
    output_size).  Raises RuntimeError on any consistency failure
    (no faces in the collection, hsdraw alloc shape change, ...).
    """
    import hsdraw

    output_dat = Path(output_dat)
    log = log_fn if log_fn is not None else print

    name = course_name
    if name is None:
        cn = vis_collection.name
        name = cn[len("vis:"):] if cn.startswith("vis:") else cn
    if not name:
        raise RuntimeError("could not derive a course stem from the collection name")

    # ---- Build the DObj chain ------------------------------------------
    # Read geometry through the depsgraph so modifier stacks (Subdivision
    # Surface, Solidify, Mirror, Array, Bevel, Geometry Nodes scatter /
    # instance, etc.) are baked at export without requiring `Apply
    # Modifier` on the source.  This matches the convention used by
    # Blender's built-in FBX / glTF / OBJ / USD exporters.
    #
    # Object-type filter: every Blender data type that supports
    # ``Object.to_mesh()`` -- i.e. MESH plus the non-mesh types whose
    # depsgraph evaluation produces a Mesh (FONT/TextCurve, CURVE,
    # SURFACE, META).  Restricting to MESH alone silently dropped Text
    # objects, NURBS curves with bevel, metaball clusters, etc., even
    # though they all evaluate to renderable geometry the moment you
    # save / export from Blender's built-in exporters.  to_mesh() may
    # still return None for the degenerate case (empty curve, font with
    # no body), in which case we skip with a log line rather than
    # raise.
    _MESHABLE_TYPES = ('MESH', 'FONT', 'CURVE', 'SURFACE', 'META')
    depsgraph = bpy.context.evaluated_depsgraph_get()
    dobjs = []
    total_verts = 0
    total_tris = 0
    for obj in [o for o in vis_collection.objects if o.type in _MESHABLE_TYPES]:
        eval_obj = obj.evaluated_get(depsgraph)
        eval_me = eval_obj.to_mesh()
        if eval_me is None:
            log(f"  skip {obj.name}: to_mesh() returned None (empty curve / no body?)")
            continue
        try:
            slots = eval_obj.material_slots
            for slot_idx, slot in enumerate(slots):
                r = _build_pobj_for_slot(eval_obj, eval_me, slot_idx, hsdraw)
                if r is None:
                    continue
                pobj, color, img_tuple, nv, nt = r
                mat_name = slot.material.name if slot.material else "?"
                tex_info = (f"img={img_tuple[0]}x{img_tuple[1]}"
                            if img_tuple else "synth-4x4")
                fmt_name, fmt_int = bm.material_target_format(slot.material)
                log(f"  built {obj.name}.{mat_name}: {nv}v / {nt}t "
                    f"color={color} {tex_info} format={fmt_name}")
                mobj = _make_textured_mobj(
                    hsdraw, color, img_tuple, target_format=fmt_int)
                d = hsdraw.DObj.alloc()
                d.set_mobj(mobj)
                d.set_pobj(pobj)
                dobjs.append(d)
                total_verts += nv
                total_tris += nt
            if not slots:
                r = _build_pobj_for_slot(eval_obj, eval_me, 0, hsdraw)
                if r is not None:
                    pobj, color, img_tuple, nv, nt = r
                    log(f"  built {obj.name}.<no-slot>: {nv}v / {nt}t (default grey)")
                    # No material -> no per-material format prop; default RGBA8.
                    mobj = _make_textured_mobj(
                        hsdraw, (200, 200, 200, 255), img_tuple)
                    d = hsdraw.DObj.alloc()
                    d.set_mobj(mobj)
                    d.set_pobj(pobj)
                    dobjs.append(d)
                    total_verts += nv
                    total_tris += nt
        finally:
            eval_obj.to_mesh_clear()

    if not dobjs:
        raise RuntimeError(
            f"vis collection {vis_collection.name!r} contained no faces; "
            "nothing to promote")

    # Chain DObjs via set_next so the JObj has a single linked list head
    for i in range(len(dobjs) - 1):
        dobjs[i].set_next(dobjs[i + 1])

    # Root JObj housing the chain.
    #
    # bisect 2026-05-10 step 5:
    # 動く例 `MR_highway_short_A_inu_aliased.dat` の `MR_highway_inu_joint`
    # と structural diff を取った結果、INU は JObj の 3 pass bit
    # (OPA|XLU|TEXEDGE + ROOT_OPA|ROOT_XLU|ROOT_TEXEDGE = 0x701c0000)
    # を立てて 3 pass 全部に出席している。我々は OPA-only だったため、
    # OPA pass の TEV state setup が SHAPE textured POBJ を正しく描画でき
    # ていなかった疑い。INU 構造は container-root + child JObj だが、ここでは
    # root に直接 3 pass bit を立てる (我々は flat tree)。
    _JOBJ_OPA          = 1 << 18  # 0x00040000
    _JOBJ_XLU          = 1 << 19  # 0x00080000
    _JOBJ_TEXEDGE      = 1 << 20  # 0x00100000
    _JOBJ_ROOT_OPA     = 1 << 28  # 0x10000000
    _JOBJ_ROOT_XLU     = 1 << 29  # 0x20000000
    _JOBJ_ROOT_TEXEDGE = 1 << 30  # 0x40000000
    root_jobj = hsdraw.JObj.alloc()
    root_jobj.flags = (
        _JOBJ_OPA | _JOBJ_XLU | _JOBJ_TEXEDGE
        | _JOBJ_ROOT_OPA | _JOBJ_ROOT_XLU | _JOBJ_ROOT_TEXEDGE
    )  # = 0x701c0000 — match INU pattern
    root_jobj.set_dobj(dobjs[0])

    # ---- Build the .dat from a scene template --------------------------
    # The renderer needs the SObj to carry LObj (lights) + COBJ (camera)
    # descriptors.  `Dat.alloc_scene_data_minimal()` only allocates the
    # JObjDesc array, so its output leaves characters dark and breaks
    # texture sampling.  `bm.load_scene_template_dat()` reads any vanilla
    # course .dat, drops every root except `scene_data`, and hands us a
    # Dat whose SObj keeps the template's LObj/COBJ verbatim — only the
    # JObjDesc[0].RootJoint and the alias roots need swapping in.
    if template_dat is not None:
        dat = bm.load_scene_template_dat(hsdraw, template_dat)
        log(f"  scene template: {Path(template_dat).name}")
    else:
        log("  WARN: no scene template; falling back to "
            "Dat.alloc_scene_data_minimal() — output will be missing "
            "LObj/COBJ and will render incorrectly in-game (characters "
            "dark, course textures collapsed). Use only for byte-"
            "equivalence tests, NEVER for shipping.")
        dat = hsdraw.Dat.alloc_scene_data_minimal()
    sd = dat.scene_data()
    if sd is None:
        raise RuntimeError(
            "scene template / alloc_scene_data_minimal() did not produce "
            "a scene_data root; hsdraw bug?")
    sobj = hsdraw.SObj.from_struct(sd.data)
    descs = sobj.jobj_descs()
    if not descs:
        raise RuntimeError(
            "scene_data has no JOBJDesc; hsdraw alloc shape changed?")
    descs[0].set_root_joint(root_jobj)

    alias_name = f"{name}_joint"
    dat.add_root(alias_name, root_jobj)

    # GXTexGenSrc / repeat_s/t / POBJ.flags はすべて hsdraw 2026-05-11 wheel の
    # writable property を経由して構築段階で設定済 (= 過去の post-write byte
    # patch 経路 `bm.patch_pobj_flags` / `bm.patch_tobj_tex_gen_src` は撤去)。
    out_bytes = bytes(dat.write())
    output_dat.write_bytes(out_bytes)
    log(f"  wrote {output_dat.name}: {len(out_bytes)} bytes "
        f"({len(dobjs)} DObjs, {total_verts} verts, {total_tris} tris)")

    return {
        "dobj_count": len(dobjs),
        "total_verts": total_verts,
        "total_tris": total_tris,
        "output_size": len(out_bytes),
        "alias_name": alias_name,
    }
