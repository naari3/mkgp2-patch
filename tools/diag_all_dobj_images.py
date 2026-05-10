"""List EVERY textured DObj in OURS my_course.dat with its Image dimensions/format."""
import sys, os, struct
sys.path.insert(0, os.path.join('tools', 'blender', 'blender_addon_mkgp2_course', 'vendor', 'windows_x86_64'))
import hsdraw

OUR_PATH = r'features/cup_page3/files/my_course.dat'
OUR_ROOT = 'my_course_joint'

FMTS = {0:'I4',1:'I8',2:'IA4',3:'IA8',4:'RGB565',5:'RGB5A3',6:'RGBA8',8:'CI4',9:'CI8',10:'CI14X2',14:'CMP'}


def all_textured(dat, want_root):
    visited = set()
    for r in dat.roots():
        if r.name != want_root: continue
        rj = hsdraw.JObj.from_struct(r.data)
        stack = [rj]
        idx = 0
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
                                rf = int.from_bytes(mobj.as_struct().raw()[0x04:0x08], 'big')
                                tref = None
                                for moff, mref in mobj.as_struct().references():
                                    if moff == 8: tref = mref; break
                                # Count verts on first POBJ
                                p = d.pobj
                                pobj = p if not isinstance(p, hsdraw.HsdStruct) else hsdraw.Pobj.from_struct(p)
                                praw = pobj.as_struct().raw()
                                dl_size = int.from_bytes(praw[0x0e:0x10], 'big', signed=True) * 32
                                # Get POS buffer length to estimate verts
                                vc = '?'
                                attr_buf_ref = None
                                for off2, ref2 in pobj.as_struct().references():
                                    if off2 == 0x08:
                                        for soff, sref in ref2.references():
                                            if soff == 0x14:  # first attr buf
                                                vc = len(sref.raw()) // 12
                                                break
                                        break
                                # Image
                                img_info = None
                                if tref is not None:
                                    for toff, sub in tref.references():
                                        if toff == 0x4C:
                                            sb = sub.raw()
                                            iw = int.from_bytes(sb[0x04:0x06], 'big')
                                            ih = int.from_bytes(sb[0x06:0x08], 'big')
                                            ifmt = int.from_bytes(sb[0x08:0x0c], 'big')
                                            img_info = (iw, ih, FMTS.get(ifmt, f'?{ifmt}'))
                                            break
                                yield (idx, vc, dl_size, rf, img_info)
                                idx += 1
                        except Exception as e:
                            print(f'  err: {e}')
                        d = d.next
            try:
                if j.next is not None: stack.append(j.next)
                if j.child is not None: stack.append(j.child)
            except: pass


data = open(OUR_PATH, 'rb').read()
dat = hsdraw.parse_dat(data)
print(f'{"idx":>3} {"verts":>6} {"DL":>5}B  {"rf":>10}  image')
for idx, vc, dl, rf, img in all_textured(dat, OUR_ROOT):
    img_s = f'{img[0]}x{img[1]} {img[2]}' if img else 'NONE'
    print(f'{idx:3d} {vc:6} {dl:5d}   0x{rf:08x}  {img_s}')
