"""For each vanilla SHAPE-flagged textured POBJ, walk back to find the
owning JObj and check if it's HIDDEN / part of an alpha pass / etc."""
import sys, os
sys.path.insert(0, os.path.join('tools', 'blender', 'blender_addon_mkgp2_course', 'vendor', 'windows_x86_64'))
import hsdraw

# JObj flags
JOBJ_HIDDEN = 1 << 4
JOBJ_OPA = 1 << 18
JOBJ_XLU = 1 << 19
JOBJ_TEXEDGE = 1 << 20
JOBJ_ROOT_OPA = 1 << 28
JOBJ_ROOT_XLU = 1 << 29
JOBJ_ROOT_TEXEDGE = 1 << 30
JOBJ_LIGHTING = 1 << 7

paths = [
    r'C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files\YI_land_long_a.dat',
    r'C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files\MR_highway_long_A.dat',
    r'C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files\PC_land_long_a.dat',
]


def walk(dat, file_bytes):
    visited_jobj = set()
    for r in dat.roots():
        if r.name == 'scene_data':
            continue
        rj = hsdraw.JObj.from_struct(r.data)
        # We track parent chain by passing it in stack
        stack = [(rj, r.name, [])]
        while stack:
            j, root_name, parents = stack.pop()
            if j is None:
                continue
            jraw = bytes(j.as_struct().raw())
            if jraw in visited_jobj:
                continue
            visited_jobj.add(jraw)
            for off, ref in j.as_struct().references():
                if off == 16:
                    visited_dobj = set()
                    d = hsdraw.DObj.from_struct(ref)
                    while d is not None:
                        draw = bytes(d.as_struct().raw())
                        if draw in visited_dobj:
                            break
                        visited_dobj.add(draw)
                        has_tobj = False
                        try:
                            mobj_attr = d.mobj
                            if mobj_attr is not None:
                                mobj = mobj_attr if not isinstance(mobj_attr, hsdraw.HsdStruct) else hsdraw.MObj.from_struct(mobj_attr)
                                for moff, mref in mobj.as_struct().references():
                                    if moff == 8:
                                        has_tobj = True
                                        break
                        except Exception:
                            pass
                        visited_pobj = set()
                        p = d.pobj
                        while p is not None:
                            pobj = p if not isinstance(p, hsdraw.HsdStruct) else hsdraw.Pobj.from_struct(p)
                            praw = bytes(pobj.as_struct().raw())
                            if praw in visited_pobj:
                                break
                            visited_pobj.add(praw)
                            flags = int.from_bytes(praw[0x0c:0x0e], 'big')
                            yield (root_name, parents + [j], j, flags, has_tobj, mobj if has_tobj else None)
                            p = pobj.next
                        d = d.next
            try:
                nx = j.next
            except Exception:
                nx = None
            try:
                ch = j.child
            except Exception:
                ch = None
            if nx is not None:
                stack.append((nx, root_name, parents))
            if ch is not None:
                stack.append((ch, root_name, parents + [j]))


SHOW_FLAGS = int(os.environ.get('POBJ_FLAGS', '0'), 0)  # SHAPE=0, CULLBACK=0x8000
for path in paths:
    data = open(path, 'rb').read()
    dat = hsdraw.parse_dat(data)
    print(f'\n=== {os.path.basename(path)} (POBJ_FLAGS=0x{SHOW_FLAGS:04x}) ===')
    seen_combo = {}
    for root_name, parent_chain, jobj, flags, has_tex, mobj in walk(dat, data):
        if not has_tex or flags != SHOW_FLAGS:
            continue
        jflags = jobj.flags
        rf = mobj.render_flags if mobj else 0
        # Decode JObj flags
        j_decode = []
        if jflags & JOBJ_HIDDEN: j_decode.append('HIDDEN')
        if jflags & JOBJ_OPA: j_decode.append('OPA')
        if jflags & JOBJ_XLU: j_decode.append('XLU')
        if jflags & JOBJ_TEXEDGE: j_decode.append('TEXEDGE')
        if jflags & JOBJ_ROOT_OPA: j_decode.append('ROOT_OPA')
        if jflags & JOBJ_ROOT_XLU: j_decode.append('ROOT_XLU')
        if jflags & JOBJ_LIGHTING: j_decode.append('LIGHTING')
        # parent root info
        root_jobj = parent_chain[0] if parent_chain else jobj
        root_flags = root_jobj.flags
        rj_decode = []
        if root_flags & JOBJ_HIDDEN: rj_decode.append('HIDDEN')
        if root_flags & JOBJ_OPA: rj_decode.append('OPA')
        if root_flags & JOBJ_XLU: rj_decode.append('XLU')
        if root_flags & JOBJ_ROOT_OPA: rj_decode.append('ROOT_OPA')
        if root_flags & JOBJ_ROOT_XLU: rj_decode.append('ROOT_XLU')
        # Aggregate (root_name, jflags, mobj.rf) -> count
        key = (root_name, jflags, root_flags, rf)
        seen_combo[key] = seen_combo.get(key, 0) + 1
    # Print top combos
    for (root_name, jflags, root_flags, rf), cnt in sorted(seen_combo.items(), key=lambda x: -x[1])[:8]:
        j_decode = []
        if jflags & JOBJ_HIDDEN: j_decode.append('HIDDEN')
        if jflags & JOBJ_OPA: j_decode.append('OPA')
        if jflags & JOBJ_XLU: j_decode.append('XLU')
        if jflags & JOBJ_TEXEDGE: j_decode.append('TEXEDGE')
        if jflags & JOBJ_ROOT_OPA: j_decode.append('ROOT_OPA')
        if jflags & JOBJ_ROOT_XLU: j_decode.append('ROOT_XLU')
        if jflags & JOBJ_LIGHTING: j_decode.append('LIGHTING')
        rj_decode = []
        if root_flags & JOBJ_OPA: rj_decode.append('OPA')
        if root_flags & JOBJ_XLU: rj_decode.append('XLU')
        if root_flags & JOBJ_ROOT_OPA: rj_decode.append('ROOT_OPA')
        if root_flags & JOBJ_ROOT_XLU: rj_decode.append('ROOT_XLU')
        print(f'  [{cnt:4}] {root_name}: JObj=0x{jflags:08x}({",".join(j_decode)}) root=0x{root_flags:08x}({",".join(rj_decode)}) MObj.rf=0x{rf:08x}')
    if False:
        print(f'  {root_name} depth={len(parent_chain)} JObj.flags=0x{jflags:08x} ({",".join(j_decode)}) root.flags=0x{root_flags:08x} ({",".join(rj_decode)}) MObj.rf=0x{rf:08x}')
