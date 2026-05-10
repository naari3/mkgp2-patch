"""Properly read DL buffer of fmt_test_planes."""
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
                            if d.mobj is not None:
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

for i, pobj in enumerate(pobjs[-3:]):
    pstruct = pobj.as_struct()
    praw = pstruct.raw()
    flags = int.from_bytes(praw[0x0c:0x0e], 'big')
    dl_size = int.from_bytes(praw[0x0e:0x10], 'big', signed=True) * 32
    print(f'\n--- POBJ #{len(pobjs)-3+i} (fmt_test_plane) flags=0x{flags:04x} DL={dl_size}B ---')
    print(f'  references:')
    for off, ref in pstruct.references():
        sb = ref.raw()
        print(f'    off=0x{off:02x}: len={len(sb)}, head32B={sb[:32].hex()}')
    # DL buffer is at off=0x10
    for off, ref in pstruct.references():
        if off == 0x10:
            dl = ref.raw()
            print(f'  DL ({len(dl)} bytes):')
            print(f'    full hex: {dl.hex()}')
            # Decode GX primitive opcode
            # Layout: opcode (1B) + count (2B) + indices...
            # opcode 0x80=quads,0x90=triangles,0x98=tristrip,0xA0=trifan
            # primitives can repeat
            i = 0
            while i < len(dl):
                op = dl[i]
                if op == 0x00:  # NOP / padding
                    i += 1
                    continue
                count = int.from_bytes(dl[i+1:i+3], 'big')
                op_names = {0x80:'QUADS', 0x88:'?88', 0x90:'TRIANGLES', 0x98:'TRISTRIP', 0xA0:'TRIFAN', 0xA8:'LINES'}
                op_name = op_names.get(op & 0xF8, f'UNK_{op:02x}')
                print(f'    opcode=0x{op:02x} ({op_name}) count={count}')
                # Skip indices: count * (vat-stride)
                # We don't know VAT format, but typical with POS+TEX0 INDEX16:
                # 2 INDEX16 attrs = 4 bytes per vert
                stride = 4
                for k in range(count):
                    vidx_pos = int.from_bytes(dl[i+3+k*stride:i+3+k*stride+2], 'big')
                    vidx_tex = int.from_bytes(dl[i+3+k*stride+2:i+3+k*stride+4], 'big')
                    print(f'      vert{k}: POS_idx={vidx_pos}, TEX0_idx={vidx_tex}')
                i = i + 3 + count * stride
