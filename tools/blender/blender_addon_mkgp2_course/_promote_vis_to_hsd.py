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


def _build_pobj_for_slot(obj, slot_idx, hsdraw):
    """Build a POBJ from `obj`'s polygons whose material_index ==
    slot_idx.  Returns (Pobj, color_tuple, vert_count, tri_count) or
    None if the slot has no faces.

    Polygons are triangulated on the fly (3-, 4-, n-gons all OK; n>4 is
    fan-triangulated).  Vertices are deduplicated per slot so each POBJ
    has the minimum vertex stream."""
    me = obj.data
    poly_idxs = [i for i, p in enumerate(me.polygons)
                 if p.material_index == slot_idx]
    if not poly_idxs:
        return None
    mat = obj.material_slots[slot_idx].material
    color = _bsdf_base_color(mat)

    vert_remap: dict = {}     # blender vert idx -> local POBJ idx
    positions: list = []      # in HSD world frame
    colors: list = []
    mb = hsdraw.MeshBuilder()

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
            local = []
            for bv in tri:
                if bv not in vert_remap:
                    co = obj.matrix_world @ me.vertices[bv].co
                    vert_remap[bv] = len(positions)
                    positions.append(_blender_to_hsd(co))
                    colors.append(color)
                local.append(vert_remap[bv])
            mb.add_triangle(*local)

    for p in positions:
        mb.add_position(*p)
    for c in colors:
        mb.add_color(*c)
    return mb.build(), color, len(positions), len(poly_idxs)


def promote_vis_to_dat(
    vis_collection,
    base_dat,
    output_dat,
    *,
    course_name: str | None = None,
    log_fn=None,
) -> dict:
    """Build a fresh `<course>.dat` from `vis_collection`'s meshes.

    Parameters:
      vis_collection -- the `vis:<name>` Collection to promote.
      base_dat       -- vanilla .dat to use as the structural base.
                        scene_data is preserved (modulo RootJoint
                        repoint); every other root is stripped.
      output_dat     -- destination .dat path.
      course_name    -- stem used for the new `<stem>_joint` alias.
                        Defaults to the collection's name minus the
                        leading `vis:`.

    Returns a stats dict (dobj_count, total_verts, total_tris,
    output_size).  Raises RuntimeError on any consistency failure
    (no faces in the collection, base .dat parse error, ...).
    """
    import hsdraw

    base_dat = Path(base_dat)
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
            pobj, color, nv, nt = r
            mat_name = slot.material.name if slot.material else "?"
            log(f"  built {obj.name}.{mat_name}: {nv}v / {nt}t color={color}")
            mobj = hsdraw.MObj.alloc_unlit_color(*color)
            d = hsdraw.DObj.alloc()
            d.set_mobj(mobj)
            d.set_pobj(pobj)
            dobjs.append(d)
            total_verts += nv
            total_tris += nt
        # Also handle objects with zero material slots (rare; fall back
        # to a default grey material for those)
        if not obj.material_slots:
            r = _build_pobj_for_slot(obj, 0, hsdraw)  # slot 0 even if empty
            if r is not None:
                pobj, color, nv, nt = r
                log(f"  built {obj.name}.<no-slot>: {nv}v / {nt}t (default grey)")
                mobj = hsdraw.MObj.alloc_unlit_color(200, 200, 200, 255)
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

    # Root JObj housing the chain
    root_jobj = hsdraw.JObj.alloc()
    root_jobj.set_dobj(dobjs[0])

    # ---- Splice into the base .dat -------------------------------------
    if not base_dat.is_file():
        raise FileNotFoundError(f"base .dat not found: {base_dat}")
    dat = hsdraw.parse_dat(base_dat.read_bytes())

    sd_root = dat.scene_data()
    if sd_root is None:
        raise RuntimeError(
            f"base .dat {base_dat.name} has no scene_data root; cannot promote")

    container = sd_root.data.get_reference(0x00)
    if container is None or not container.references():
        raise RuntimeError(
            f"base .dat {base_dat.name} scene_data has no JOBJDescs; "
            "this base is too minimal to use as a structural template")

    # Repoint scene_data.JOBJDescs[0].RootJoint at our new JObj
    first_desc = container.references()[0][1]
    first_desc.set_reference(0x00, root_jobj)

    alias_name = f"{name}_joint"
    dat.add_root(alias_name, root_jobj)

    # Strip every other root (leave only scene_data + our new alias)
    for r_name in list(dat.root_names()):
        if r_name == "scene_data" or r_name == alias_name:
            continue
        dat.remove_root(r_name)

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
