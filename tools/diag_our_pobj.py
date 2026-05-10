"""Inspect OUR my_course.dat: list each textured POBJ + its attrs + UV sample."""
import sys, os, struct
sys.path.insert(0, os.path.join('tools', 'blender', 'blender_addon_mkgp2_course', 'vendor', 'windows_x86_64'))
import hsdraw

OUR_PATH = r'features/cup_page3/files/my_course.dat'

ATTR_NAMES = {0:"PNMTXIDX",9:"POS",10:"NRM",11:"CLR0",13:"TEX0",14:"TEX1",0xFF:"NULL"}

# vtxattr_t: u32 attr_id, u32 attr_type(direct/idx8/idx16), u32 comp_count, u32 comp_type, u8 frac, u8 pad, u16 stride, u32 ptr_or_offset
# Total = 0x18 bytes per entry.

def parse_attrs(buf):
    out = []
    i = 0
    while i + 4 <= len(buf):
        aid = int.from_bytes(buf[i:i+4], 'big')
        if aid == 0xFF: break
        attr_type = int.from_bytes(buf[i+4:i+8], 'big')
        comp_count = int.from_bytes(buf[i+8:i+0xc], 'big')
        comp_type = int.from_bytes(buf[i+0xc:i+0x10], 'big')
        frac = buf[i+0x10]
        stride = int.from_bytes(buf[i+0x12:i+0x14], 'big')
        # attr buffer ref is at offset i+0x14 within the parent (a relocation)
        out.append((ATTR_NAMES.get(aid, f'?{aid:x}'), attr_type, comp_count, comp_type, frac, stride))
        i += 0x18
    return out


def all_textured_pobjs(dat):
    visited = set()
    for r in dat.roots():
        if r.name == 'scene_data': continue
        rj = hsdraw.JObj.from_struct(r.data)
        stack = [rj]
        while stack:
            j = stack.pop()
            if j is None: continue
            jraw = bytes(j.as_struct().raw())
            if jraw in visited: continue
            visited.add(jraw)
            for off, ref in j.as_struct().references():
                if off == 16:
                    d = hsdraw.DObj.from_struct(ref)
                    while d is not None:
                        has_tex = False
                        try:
                            mobj_attr = d.mobj
                            if mobj_attr is not None:
                                mobj = mobj_attr if not isinstance(mobj_attr, hsdraw.HsdStruct) else hsdraw.MObj.from_struct(mobj_attr)
                                for moff, mref in mobj.as_struct().references():
                                    if moff == 8: has_tex = True; break
                        except: pass
                        if has_tex:
                            p = d.pobj
                            while p is not None:
                                pobj = p if not isinstance(p, hsdraw.HsdStruct) else hsdraw.Pobj.from_struct(p)
                                yield (r.name, pobj)
                                p = pobj.next
                        d = d.next
            try:
                if j.next is not None: stack.append(j.next)
                if j.child is not None: stack.append(j.child)
            except: pass


data = open(OUR_PATH, 'rb').read()
dat = hsdraw.parse_dat(data)

for root_name, pobj in all_textured_pobjs(dat):
    pstruct = pobj.as_struct()
    praw = pstruct.raw()
    flags = int.from_bytes(praw[0x0c:0x0e], 'big')
    dl_size_units = int.from_bytes(praw[0x0e:0x10], 'big', signed=True)
    dl_size = dl_size_units * 32
    # Attrs
    attrs = None
    attr_buf_ref = None
    attr_iter_ref = None
    for off, ref in pstruct.references():
        if off == 0x08:
            attr_buf = ref.raw()
            attrs = parse_attrs(attr_buf)
            attr_iter_ref = ref
            break
    # Find each attr's buffer (its sub-references at 0x14 within each entry)
    attr_buffers = {}
    if attr_iter_ref is not None:
        for soff, sref in attr_iter_ref.references():
            # sref is at sub-offset within the attrs blob; map sub-offset
            # to attr index = soff / 0x18
            idx = soff // 0x18
            if idx < len(attrs):
                attr_buffers[attrs[idx][0]] = sref.raw()
    print(f'\n--- root={root_name}  POBJ.flags=0x{flags:04x}  DL={dl_size}B ---')
    for a in attrs or []:
        print(f'    attr {a[0]:5s}: type={a[1]} comp_count={a[2]} comp_type={a[3]} frac={a[4]} stride={a[5]}')
    for an, ab in attr_buffers.items():
        print(f'    buf  {an:5s}: len={len(ab)}, head32B={ab[:32].hex()}')
    # decode TEX0 if present (indexed s16 components)
    if 'TEX0' in attr_buffers:
        tb = attr_buffers['TEX0']
        # find entry
        for a in attrs:
            if a[0] == 'TEX0':
                comp_type = a[3]; frac = a[4]; stride = a[5]; comp_count = a[2]
                # comp_type: 0=u8, 1=s8, 2=u16, 3=s16, 4=f32
                if comp_type == 4:  # f32
                    n = len(tb) // 4
                    vals = struct.unpack(f'>{n}f', tb)
                elif comp_type == 3:  # s16
                    n = len(tb) // 2
                    vals = [v / (1 << frac) for v in struct.unpack(f'>{n}h', tb)]
                elif comp_type == 2:  # u16
                    n = len(tb) // 2
                    vals = [v / (1 << frac) for v in struct.unpack(f'>{n}H', tb)]
                else:
                    vals = list(tb)
                # comp_count: 0=2 (S,T), 1=1, etc. Assume 2 (S,T pairs)
                pair = 2 if (comp_count + 1) >= 2 else 1
                print(f'    TEX0 decoded ({len(vals)//pair} verts, comp_type={comp_type} frac={frac}):')
                for i in range(0, min(len(vals), 16), pair):
                    print(f'      vert{i//pair}: ({vals[i]:.4f}, {vals[i+1]:.4f})')
                break
