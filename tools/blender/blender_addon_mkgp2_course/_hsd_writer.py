"""Python port of `tools/hsd/hsd_import_from_blender.csx` Pass 0-4.

Reads `<bundle_dir>/scene.json` produced by the Blender addon's HSD
exporter, applies structural mutations to `<base.dat>` via the
vendored `hsdraw` Rust library, writes the result to `<output.dat>`.

Drop-in replacement for the dotnet-script + HSDLib path: same input /
output contract, same Pass 0-4 semantics, same JOBJ_FLAG name set.
Mesh / material / texture content is preserved from the base; only
joint TRS / flags / hierarchy / aliases / new joints are written.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


# JOBJ_FLAG enum mirror from HSDLib `HSDRaw/Common/HSD_JOBJ.cs`.
# scene.json's `flags: [...]` values are these enum names; we OR the bits.
# `NULL` is intentionally 0 (csx ParseFlagList skips both empty and "NULL").
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


def _flag_bits(names, *, log) -> int:
    bits = 0
    for n in names or []:
        if not n or n == "NULL":
            continue
        v = _JOBJ_FLAG.get(n)
        if v is None:
            log(f"  WARN: unknown JOBJ_FLAG {n!r}, skipping")
            continue
        bits |= v
    return bits


def _walk_jobj(jobj, id_by_struct, jobj_by_id):
    """DFS visit; assigns `jobj_<N>` ids in DFS order. Mirrors the
    `EmitJoint()` recursion in `hsd_export_for_blender.csx`. Aliased
    structs (already in `id_by_struct`) keep their existing id."""
    s = jobj.as_struct()
    if s in id_by_struct:
        return id_by_struct[s]
    new_id = f"jobj_{len(jobj_by_id)}"
    id_by_struct[s] = new_id
    jobj_by_id[new_id] = jobj
    c = jobj.child
    while c is not None:
        _walk_jobj(c, id_by_struct, jobj_by_id)
        c = c.next
    return new_id


def _build_id_maps(dat):
    """Pass 0: id_by_struct + jobj_by_id by walking
       (a) `scene_data.JOBJDescs[i].RootJoint` for each entry of the
           `HSDNullPointerArrayAccessor<HSD_JOBJDesc>` container at
           SOBJ offset 0x00, then
       (b) any remaining `*_joint` roots whose data struct hasn't been
           seen (rare; usually all `*_joint` roots are aliases of the
           JOBJDescs[i].RootJoint structs).

    Order matches the csx reader (`hsd_export_for_blender.csx`) so the
    `jobj_<N>` ids in scene.json line up with the structs we resolve
    here. SObj layout reference: HSDLib `HSDRaw/Common/HSD_SOBJ.cs`."""
    import hsdraw

    id_by_struct: dict = {}
    jobj_by_id: dict = {}

    sd_root = dat.scene_data()
    if sd_root is not None:
        sd_struct = sd_root.data
        # HSD_SOBJ[0x00] -> HSDNullPointerArrayAccessor<HSD_JOBJDesc>
        container = sd_struct.get_reference(0x00)
        if container is not None:
            # Each ref in the container = one HSD_JOBJDesc, in array
            # index order. JOBJDesc[0x00] = RootJoint.
            for _off, jd in container.references():
                rj_struct = jd.get_reference(0x00)
                if rj_struct is None:
                    continue
                rj = hsdraw.JObj.from_struct(rj_struct)
                _walk_jobj(rj, id_by_struct, jobj_by_id)

    for r in dat.roots():
        if r.name == "scene_data":
            continue
        if r.data in id_by_struct:
            continue
        rj = hsdraw.JObj.from_struct(r.data)
        _walk_jobj(rj, id_by_struct, jobj_by_id)

    return id_by_struct, jobj_by_id


def import_from_scene_json(
    base_dat,
    bundle_dir,
    output_dat,
    *,
    verbose: bool = True,
    log_fn=None,
) -> dict:
    """Apply Pass 0-4 mutations to `base_dat` from `bundle_dir/scene.json`,
    write to `output_dat`. Returns a stats dict (counts per pass).

    Phase 1 scope (matches the csx): geometry / material / texture
    content is preserved from the base.dat. Joint TRS, flags, hierarchy,
    aliases and brand-new (DObj-less) joints are the supported edits."""

    import hsdraw

    base_dat = Path(base_dat)
    bundle_dir = Path(bundle_dir)
    output_dat = Path(output_dat)
    scene_json = bundle_dir / "scene.json"

    if not base_dat.is_file():
        raise FileNotFoundError(f"base.dat not found: {base_dat}")
    if not scene_json.is_file():
        raise FileNotFoundError(f"scene.json not found: {scene_json}")

    log = log_fn if log_fn is not None else (print if verbose else (lambda *_: None))

    sj = json.loads(scene_json.read_text(encoding="utf-8"))
    j_aliases: dict = sj.get("joint_aliases") or {}
    j_joints: list = sj.get("joints") or []

    raw = base_dat.read_bytes()
    dat = hsdraw.parse_dat(raw)
    log(f"base    : {base_dat.name}  roots={len(list(dat.roots()))}")
    log(f"json    : aliases={len(j_aliases)} joints={len(j_joints)}")

    # ---- Pass 0: id maps from base tree --------------------------------
    id_by_struct, jobj_by_id = _build_id_maps(dat)
    log(f"base    : walked joints={len(jobj_by_id)}")
    base_walked = len(jobj_by_id)

    # ---- Pass 1: alloc new HSD_JOBJ for unknown JSON joint ids ---------
    new_alloc = 0
    for jdto in j_joints:
        jid = jdto.get("id")
        if not jid or jid in jobj_by_id:
            continue
        nj = hsdraw.JObj.alloc()  # identity scale (1,1,1), zero T/R, flags=0
        jobj_by_id[jid] = nj
        id_by_struct[nj.as_struct()] = jid
        new_alloc += 1
    if new_alloc:
        log(f"new     : allocated {new_alloc} new HSD_JOBJ(s)")

    # ---- Pass 2: alias add / repoint / remove --------------------------
    existing_by_name = {r.name: r for r in dat.roots()}
    added = repointed = removed = 0
    for alias_name, jobj_id in j_aliases.items():
        target = jobj_by_id.get(jobj_id)
        if target is None:
            log(f"  WARN: alias {alias_name!r} -> unknown {jobj_id}, skipping")
            continue
        if alias_name in existing_by_name:
            existing = existing_by_name[alias_name]
            if existing.data != target.as_struct():
                dat.repoint_root(alias_name, target)
                repointed += 1
        else:
            dat.add_root(alias_name, target)
            added += 1

    # Remove file.Roots entries that are *_joint and NOT in jsonAliases.
    # We can't query "data is HSD_JOBJ" without a typed accessor; use the
    # approximation "data is in id_by_struct" (= a struct we walked as a
    # JObj during Pass 0/1, which is the same set csx's `r.Data is
    # HSD_JOBJ` filter would catch).
    jobj_structs = set(id_by_struct.keys())
    to_remove = []
    for r in list(dat.roots()):
        if r.name == "scene_data":
            continue
        if r.data not in jobj_structs:
            continue
        if r.name in j_aliases:
            continue
        to_remove.append(r.name)
    for nm in to_remove:
        dat.remove_root(nm)
        removed += 1
    log(f"aliases : added={added} repointed={repointed} removed={removed}")
    log(f"final   : roots={len(list(dat.roots()))}")

    # ---- Pass 3: TRS / flag sync ---------------------------------------
    trs_changed = flags_changed = 0
    for jdto in j_joints:
        jid = jdto.get("id")
        j = jobj_by_id.get(jid)
        if j is None:
            continue
        new_flags = _flag_bits(jdto.get("flags"), log=log)
        if j.flags != new_flags:
            j.flags = new_flags
            flags_changed += 1
        t = jdto.get("translation") or [0.0, 0.0, 0.0]
        r = jdto.get("rotation") or [0.0, 0.0, 0.0]
        s = jdto.get("scale") or [1.0, 1.0, 1.0]
        moved = (
            j.tx != t[0] or j.ty != t[1] or j.tz != t[2]
            or j.rx != r[0] or j.ry != r[1] or j.rz != r[2]
            or j.sx != s[0] or j.sy != s[1] or j.sz != s[2]
        )
        if moved:
            j.set_local_trs(t[0], t[1], t[2], r[0], r[1], r[2], s[0], s[1], s[2])
            trs_changed += 1
    log(f"joints  : flags-changed={flags_changed} trs-changed={trs_changed}")

    # ---- Pass 4: hierarchy rewire (Child / Next chain) -----------------
    hier_changed = 0
    for jdto in j_joints:
        jid = jdto.get("id")
        parent = jobj_by_id.get(jid)
        if parent is None:
            continue
        ch_ids = jdto.get("children") or []
        resolved = []
        bad = False
        for i, cid in enumerate(ch_ids):
            c = jobj_by_id.get(cid)
            if c is None:
                log(f"  WARN: {jid} child[{i}]={cid} unknown; "
                    "hierarchy sync skipped for this parent")
                bad = True
                break
            resolved.append(c)
        if bad:
            continue

        desired_child = resolved[0] if resolved else None
        cur_child = parent.child
        cur_struct = cur_child.as_struct() if cur_child is not None else None
        des_struct = desired_child.as_struct() if desired_child is not None else None
        changed = False
        if cur_struct != des_struct:
            parent.set_child(desired_child)
            changed = True
        for i, c in enumerate(resolved):
            desired_next = resolved[i + 1] if i + 1 < len(resolved) else None
            cur_next = c.next
            cur_n = cur_next.as_struct() if cur_next is not None else None
            des_n = desired_next.as_struct() if desired_next is not None else None
            if cur_n != des_n:
                c.set_next(desired_next)
                changed = True
        if changed:
            hier_changed += 1
    log(f"hierarchy: parents-rewired={hier_changed}")

    # ---- Save ----------------------------------------------------------
    out_bytes = bytes(dat.write())
    output_dat.write_bytes(out_bytes)
    log(f"wrote   : {output_dat.name}  size={len(out_bytes)}")

    # ---- Verify roundtrip ---------------------------------------------
    verify = hsdraw.parse_dat(out_bytes)
    log(f"reload  : roots={len(list(verify.roots()))}")
    for r in verify.roots():
        log(f"  - {r.name}")

    return {
        "base_walked": base_walked,
        "new_alloc": new_alloc,
        "aliases_added": added,
        "aliases_repointed": repointed,
        "aliases_removed": removed,
        "trs_changed": trs_changed,
        "flags_changed": flags_changed,
        "hier_changed": hier_changed,
        "output_size": len(out_bytes),
    }


# ---------------------------------------------------------------------------
# CLI entry — convenient for subprocess invocation / debugging
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    if len(sys.argv) != 4:
        print("usage: python -m _hsd_writer <base.dat> <bundle_dir> <out.dat>")
        sys.exit(2)
    stats = import_from_scene_json(sys.argv[1], sys.argv[2], sys.argv[3])
    print(f"\nstats: {stats}")
