"""Dump raw image bytes of fmt_test_plane (last 3 textured DObjs) and compute color distribution."""
import sys, os
sys.path.insert(0, os.path.join('tools', 'blender', 'blender_addon_mkgp2_course', 'vendor', 'windows_x86_64'))
import hsdraw
from collections import Counter

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
                                tref = None
                                for moff, mref in mobj.as_struct().references():
                                    if moff == 8: tref = mref; break
                                yield tref
                        except: pass
                        d = d.next
            try:
                if j.next is not None: stack.append(j.next)
                if j.child is not None: stack.append(j.child)
            except: pass


data = open(OUR_PATH, 'rb').read()
dat = hsdraw.parse_dat(data)
trefs = list(all_textured(dat, OUR_ROOT))
print(f'total textured DObjs: {len(trefs)}')

# Last 3 = fmt_test_plane RGBA8/CMP/RGB5A3
for i, tref in enumerate(trefs[-3:]):
    print(f'\n--- DObj #{len(trefs)-3+i} (fmt_test_plane candidate) ---')
    for toff, sub in tref.references():
        if toff == 0x4C:
            sb = sub.raw()
            iw = int.from_bytes(sb[0x04:0x06], 'big')
            ih = int.from_bytes(sb[0x06:0x08], 'big')
            ifmt = int.from_bytes(sb[0x08:0x0c], 'big')
            print(f'  Image: {iw}x{ih} fmt={ifmt}')
            # The Image's data ref is at offset 0x00 within Image struct
            for ioff, isub in sub.references():
                if ioff == 0x00:
                    pix = isub.raw()
                    print(f'  pixel data: {len(pix)} bytes')
                    # For 64x64 RGBA8 it's GX format (tiled). But to detect checker vs flat we just count unique 4-byte tuples.
                    quads = [pix[k:k+4] for k in range(0, len(pix), 4)]
                    uniq = Counter(quads)
                    print(f'  unique 4-byte tuples: {len(uniq)}')
                    most = uniq.most_common(8)
                    for v, c in most:
                        print(f'    {v.hex()} : {c}')
                    break
            break
