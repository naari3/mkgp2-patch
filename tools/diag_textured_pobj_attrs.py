"""Survey vanilla textured POBJs: which GX attributes are emitted?"""
import sys, os
sys.path.insert(0, os.path.join('tools', 'blender', 'blender_addon_mkgp2_course', 'vendor', 'windows_x86_64'))
import hsdraw

vanilla_files = [
    r'C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files\YI_land_long_a.dat',
    r'C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files\MR_highway_long_A.dat',
]


# vtxattr_t in HSDLib HSD_POBJ.cs has format: u32 attr_id, u32 attr_type, u32 comp_count, ...
# Let's parse the Attributes buffer and report which attrs (POS=9, NRM=10, CLR0=11, TEX0=13)
# are present.  Each attr entry is 0x18 bytes; terminator has attr_id == 0xFF.
ATTR_NAMES = {
    0: "PNMTXIDX", 1: "T0MIDX", 9: "POS", 10: "NRM", 11: "CLR0", 12: "CLR1",
    13: "TEX0", 14: "TEX1", 15: "TEX2", 16: "TEX3",
    25: "NBT",
    0xFF: "NULL",
}


def attrs_of_pobj(pobj_struct):
    # Find ref at off=0x08 = Attributes buffer (usually a HSDStruct of bytes)
    for off, ref in pobj_struct.references():
        if off == 0x08:
            buf = ref.raw()
            i = 0
            present = []
            while i + 4 <= len(buf):
                aid = int.from_bytes(buf[i:i+4], 'big')
                if aid == 0xFF:
                    break
                present.append(ATTR_NAMES.get(aid, f'?{aid:x}'))
                i += 0x18
            return tuple(present)
    return None


def walk(dat):
    visited_jobj = set()
    for r in dat.roots():
        if r.name == 'scene_data':
            continue
        rj = hsdraw.JObj.from_struct(r.data)
        stack = [rj]
        while stack:
            j = stack.pop()
            if j is None: continue
            jraw = bytes(j.as_struct().raw())
            if jraw in visited_jobj: continue
            visited_jobj.add(jraw)
            for off, ref in j.as_struct().references():
                if off == 16:
                    visited_dobj = set()
                    d = hsdraw.DObj.from_struct(ref)
                    while d is not None:
                        draw = bytes(d.as_struct().raw())
                        if draw in visited_dobj: break
                        visited_dobj.add(draw)
                        has_tobj = False
                        try:
                            mobj_attr = d.mobj
                            if mobj_attr is not None:
                                mobj = mobj_attr if not isinstance(mobj_attr, hsdraw.HsdStruct) else hsdraw.MObj.from_struct(mobj_attr)
                                for moff, mref in mobj.as_struct().references():
                                    if moff == 8: has_tobj = True; break
                        except: pass
                        visited_pobj = set()
                        p = d.pobj
                        while p is not None:
                            pobj = p if not isinstance(p, hsdraw.HsdStruct) else hsdraw.Pobj.from_struct(p)
                            praw = bytes(pobj.as_struct().raw())
                            if praw in visited_pobj: break
                            visited_pobj.add(praw)
                            attrs = attrs_of_pobj(pobj.as_struct())
                            yield (has_tobj, attrs)
                            p = pobj.next
                        d = d.next
            try:
                if j.next is not None: stack.append(j.next)
                if j.child is not None: stack.append(j.child)
            except: pass


for path in vanilla_files:
    print(f'\n=== {os.path.basename(path)} ===')
    data = open(path, 'rb').read()
    dat = hsdraw.parse_dat(data)
    tex_attr_count = {}
    notex_attr_count = {}
    for has_tex, attrs in walk(dat):
        d = tex_attr_count if has_tex else notex_attr_count
        d[attrs] = d.get(attrs, 0) + 1
    print(f'  textured  : {dict(sorted(tex_attr_count.items(), key=lambda x: -x[1]))}')
    print(f'  textureless: {dict(sorted(notex_attr_count.items(), key=lambda x: -x[1]))}')
