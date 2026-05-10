"""Dump POS + TEX0 + DL of each fmt_test_plane."""
import sys, os, struct
sys.path.insert(0, os.path.join('tools', 'blender', 'blender_addon_mkgp2_course', 'vendor', 'windows_x86_64'))
import hsdraw

OUR_PATH = r'features/cup_page3/files/my_course.dat'
OUR_ROOT = 'my_course_joint'

def all_textured(dat, want_root):
    visited = set()
    for r in dat.roots():
        if r.name != want_root: continue
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
                        try:
                            mobj_attr = d.mobj
                            if mobj_attr is not None:
                                mobj = mobj_attr if not isinstance(mobj_attr, hsdraw.HsdStruct) else hsdraw.MObj.from_struct(mobj_attr)
                                p = d.pobj
                                if p is not None:
                                    pobj = p if not isinstance(p, hsdraw.HsdStruct) else hsdraw.Pobj.from_struct(p)
                                    yield pobj
                        except: pass
                        d = d.next
            try:
                if j.next is not None: stack.append(j.next)
                if j.child is not None: stack.append(j.child)
            except: pass


data = open(OUR_PATH, 'rb').read()
dat = hsdraw.parse_dat(data)
pobjs = list(all_textured(dat, OUR_ROOT))
print(f'total POBJs: {len(pobjs)}')

# Last 3 are fmt_test_planes
for i, pobj in enumerate(pobjs[-3:]):
    pstruct = pobj.as_struct()
    praw = pstruct.raw()
    flags = int.from_bytes(praw[0x0c:0x0e], 'big')
    dl_size = int.from_bytes(praw[0x0e:0x10], 'big', signed=True) * 32
    dl_buf = praw[0x10:0x10+dl_size]
    print(f'\n--- POBJ #{len(pobjs)-3+i} (fmt_test_plane) flags=0x{flags:04x} DL={dl_size}B ---')
    print(f'  DL hex: {dl_buf.hex()}')
    # Extract POS + TEX0 buffers
    for off, ref in pstruct.references():
        if off == 0x08:
            for soff, sref in ref.references():
                buf = sref.raw()
                idx = soff // 0x18
                # peek into attr id
                aid = int.from_bytes(ref.raw()[idx*0x18:idx*0x18+4], 'big')
                names = {0:"PNMTXIDX",9:"POS",10:"NRM",11:"CLR0",13:"TEX0"}
                aname = names.get(aid, f'a{aid}')
                if aname == 'POS':
                    # 3 f32 per vert, stride 12
                    n = len(buf) // 12
                    print(f'  POS ({n} verts):')
                    for k in range(n):
                        x, y, z = struct.unpack('>fff', buf[k*12:k*12+12])
                        print(f'    v{k}: ({x:8.2f}, {y:8.2f}, {z:8.2f})')
                elif aname == 'TEX0':
                    n = len(buf) // 8
                    print(f'  TEX0 ({n} verts):')
                    for k in range(n):
                        u, v = struct.unpack('>ff', buf[k*8:k*8+8])
                        print(f'    uv{k}: ({u:.4f}, {v:.4f})')
