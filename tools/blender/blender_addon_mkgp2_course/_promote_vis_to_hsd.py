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


def _bsdf_base_color(mat) -> tuple:
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


def _blender_to_hsd(co):
    """Map Blender world coords (Z up) to MKGP2 world frame (Y up).
    Same convention the collision/auto/line exporters use:
    (Bx, By, Bz)_blender -> (Bx, Bz, -By)_game."""
    return (co.x, co.z, -co.y)


def _bsdf_image_texture_node(mat):
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


def _build_pobj_for_slot(obj, slot_idx, hsdraw):
    """Build a POBJ from `obj`'s polygons whose material_index ==
    slot_idx.  Returns (Pobj, color_tuple, image_tuple, vert_count, tri_count) or
    None if the slot has no faces.

    `image_tuple` is (width, height, rgba_bytes) for the BSDF Image
    Texture if present, else None (caller will fall back to a synthetic
    solid texture from `color_tuple`).

    POBJ attributes: POS + NRM + TEX0 (UV) — vanilla course primary
    geometry compatible. Per-triangle face normal は flat shading 用、
    UV は (0,0) 固定 (テクスチャはどうせ単色 4x4 なので UV は意味を持たない)。
    """
    me = obj.data
    poly_idxs = [i for i, p in enumerate(me.polygons)
                 if p.material_index == slot_idx]
    if not poly_idxs:
        return None
    mat = obj.material_slots[slot_idx].material
    color = _bsdf_base_color(mat)
    img_tuple = _bsdf_image_texture_node(mat)

    # 実装方針: per-triangle の face normal を 3 vert 全部に同じ値で書き、
    # vert dedup は捨て、triangle ごとに 3 vert を独立 emit (normal 競合回避)。
    # UV は per-vertex で (0, 0) を渡す (どのみち単色 4x4 テクスチャなので
    # UV 座標は描画結果に影響しない)。
    positions: list = []
    normals:   list = []
    mb = hsdraw.MeshBuilder()
    # Blender CCW vs HSD CW で frontface が逆。CULLBACK で frontface 通過。
    mb.set_cull_back(True)

    def _face_normal(p0, p1, p2):
        ax = p1[0] - p0[0]; ay = p1[1] - p0[1]; az = p1[2] - p0[2]
        bx = p2[0] - p0[0]; by = p2[1] - p0[1]; bz = p2[2] - p0[2]
        nx = ay * bz - az * by
        ny = az * bx - ax * bz
        nz = ax * by - ay * bx
        ln = (nx * nx + ny * ny + nz * nz) ** 0.5
        if ln < 1e-9:
            return (0.0, 1.0, 0.0)
        return (nx / ln, ny / ln, nz / ln)

    tri_count = 0
    for pi in poly_idxs:
        p = me.polygons[pi]
        if len(p.vertices) == 3:
            tris = [tuple(p.vertices)]
        elif len(p.vertices) == 4:
            v0, v1, v2, v3 = p.vertices
            tris = [(v0, v1, v2), (v0, v2, v3)]
        else:
            vs = list(p.vertices)
            tris = [(vs[0], vs[k], vs[k + 1]) for k in range(1, len(vs) - 1)]
        for tri in tris:
            ws = [_blender_to_hsd(obj.matrix_world @ me.vertices[bv].co) for bv in tri]
            n = _face_normal(*ws)
            base = len(positions)
            for ws_i in ws:
                positions.append(ws_i)
                normals.append(n)
            mb.add_triangle(base, base + 1, base + 2)
            tri_count += 1

    for p in positions:
        mb.add_position(*p)
    for n in normals:
        mb.add_normal(*n)
    # UV all (0, 0) — 単色 4x4 texture 前提なので UV 値は不問。
    for _ in positions:
        mb.add_uv(0.0, 0.0)
    return mb.build(), color, img_tuple, len(positions), tri_count


def _make_textured_mobj(hsdraw, color, img_tuple):
    """Build an MObj with TObj+Image attached, RenderFlags configured
    vanilla-compatible (`CONSTANT|TEX0|ALPHA_MAT` = 0x2011).

    `color` is the (R,G,B,A) byte tuple used as Material.DIF (gets
    multiplied with the texture sample under CONSTANT mode).
    `img_tuple` = (w, h, raw_rgba_bytes); if None, synthesize a 4x4
    solid texture filled with `color`.
    """
    if img_tuple is None:
        # Synth 4x4 solid color (this is the fallback when the user hasn't
        # baked a texture into Blender; the bake helper should normally
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


def promote_vis_to_dat(
    vis_collection,
    output_dat,
    *,
    course_name: str | None = None,
    log_fn=None,
) -> dict:
    """Build a fresh `<course>.dat` from `vis_collection`'s meshes.

    No vanilla `.dat` is read. The scene_data SObj (with a single
    JOBJDesc whose RootJoint we set to our synthesized JObj) is
    fully allocated from scratch via `hsdraw.Dat.alloc_scene_data()`.

    Parameters:
      vis_collection -- the `vis:<name>` Collection to promote.
      output_dat     -- destination .dat path.
      course_name    -- stem used for the new `<stem>_joint` alias.
                        Defaults to the collection's name minus the
                        leading `vis:`.

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
    dobjs = []
    total_verts = 0
    total_tris = 0
    for obj in [o for o in vis_collection.objects if o.type == 'MESH']:
        for slot_idx, slot in enumerate(obj.material_slots):
            r = _build_pobj_for_slot(obj, slot_idx, hsdraw)
            if r is None:
                continue
            pobj, color, img_tuple, nv, nt = r
            mat_name = slot.material.name if slot.material else "?"
            tex_info = (f"img={img_tuple[0]}x{img_tuple[1]}"
                        if img_tuple else "synth-4x4")
            log(f"  built {obj.name}.{mat_name}: {nv}v / {nt}t color={color} {tex_info}")
            mobj = _make_textured_mobj(hsdraw, color, img_tuple)
            d = hsdraw.DObj.alloc()
            d.set_mobj(mobj)
            d.set_pobj(pobj)
            dobjs.append(d)
            total_verts += nv
            total_tris += nt
        if not obj.material_slots:
            r = _build_pobj_for_slot(obj, 0, hsdraw)
            if r is not None:
                pobj, color, img_tuple, nv, nt = r
                log(f"  built {obj.name}.<no-slot>: {nv}v / {nt}t (default grey)")
                mobj = _make_textured_mobj(hsdraw, (200, 200, 200, 255), img_tuple)
                d = hsdraw.DObj.alloc()
                d.set_mobj(mobj)
                d.set_pobj(pobj)
                dobjs.append(d)
                total_verts += nv
                total_tris += nt

    if not dobjs:
        raise RuntimeError(
            f"vis collection {vis_collection.name!r} contained no faces; "
            "nothing to promote")

    # Chain DObjs via set_next so the JObj has a single linked list head
    for i in range(len(dobjs) - 1):
        dobjs[i].set_next(dobjs[i + 1])

    # Root JObj housing the chain.
    # HSD renderer は ROOT_OPA / ROOT_XLU / ROOT_TEXEDGE が立った tree しか
    # OPA/XLU pass scan で traverse しないので OPA | ROOT_OPA を立てる。
    #
    # **LIGHTING bit (1<<7) は立てない**。vanilla の textured course primary
    # mesh (test_course_road, MR_highway road など) も LIGHTING bit 無しで
    # `OPA, ROOT_OPA` (= 0x10040000) で書かれている。textured mesh は
    # texture sample が色を提供するので光源計算は不要。LIGHTING bit を
    # 立てると scene の LObj (= game-side で実体化される default light)
    # が mesh に specular highlight を乗せて、camera 移動で highlights が
    # スライド = 「光源反射のような動き」の opacity 点滅を起こす (実機で
    # 確認済み)。
    # LIGHTING を立てるのは vanilla `DN_stadium_shade_al.dat` 等の
    # **textureless shade object** だけ (texture 無しなので光源計算で色を出す)。
    _JOBJ_OPA      = 1 << 18  # 0x00040000
    _JOBJ_ROOT_OPA = 1 << 28  # 0x10000000
    root_jobj = hsdraw.JObj.alloc()
    root_jobj.flags = _JOBJ_OPA | _JOBJ_ROOT_OPA
    root_jobj.set_dobj(dobjs[0])

    # ---- Build the .dat from scratch -----------------------------------
    # Fully independent: no vanilla .dat is read. `Dat.alloc_scene_data()`
    # produces a Dat whose only root is `scene_data`, an SObj carrying
    # one JOBJDesc with its RootJoint reference NULL. We set it to the
    # synthesized root JObj and add an alias root for the joint loader.
    dat = hsdraw.Dat.alloc_scene_data()
    sd = dat.scene_data()
    if sd is None:
        raise RuntimeError(
            "Dat.alloc_scene_data() did not produce a scene_data root; "
            "hsdraw bug?")
    sobj = hsdraw.SObj.from_struct(sd.data)
    descs = sobj.jobj_descs()
    if not descs:
        raise RuntimeError(
            "freshly allocated scene_data has no JOBJDesc; "
            "hsdraw alloc_scene_data() shape changed?")
    descs[0].set_root_joint(root_jobj)

    alias_name = f"{name}_joint"
    dat.add_root(alias_name, root_jobj)

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
