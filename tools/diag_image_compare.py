"""Compare Image struct + raw GX texture bytes between INU and OURS."""
import sys, os
sys.path.insert(0, os.path.join('tools', 'blender', 'blender_addon_mkgp2_course', 'vendor', 'windows_x86_64'))
import hsdraw

INU_PATH = r'C:\Users\naari\Documents\blender\MR_highway_short_A_inu_aliased.dat'
INU_ROOT = 'MR_highway_inu_joint'
OUR_PATH = r'features/cup_page3/files/my_course.dat'
OUR_ROOT = 'my_course_joint'


def all_textured_images(dat, file_bytes, want_root, min_size=64):
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
                                for moff, mref in mobj.as_struct().references():
                                    if moff == 8:
                                        for toff, tref in mref.references():
                                            traw = tref.raw()
                                            if len(traw) >= 8:
                                                w = int.from_bytes(traw[0x04:0x06], 'big')
                                                if w >= min_size:
                                                    yield (idx, tref)
                                                    idx += 1
                        except: pass
                        d = d.next
            try:
                if j.next is not None: stack.append(j.next)
                if j.child is not None: stack.append(j.child)
            except: pass


def first_textured_image(dat, file_bytes, want_root):
    for i, ref in all_textured_images(dat, file_bytes, want_root, min_size=32):
        return ref
    return None


def dump_image(label, path, root):
    data = open(path, 'rb').read()
    dat = hsdraw.parse_dat(data)
    img_struct = first_textured_image(dat, data, root)
    if img_struct is None:
        print(f'{label}: no textured image found')
        return
    raw = img_struct.raw()
    print(f'\n{label}: Image struct len={len(raw)}, head 32B = {raw[:32].hex()}')
    # Image struct (HSDLib HSD_Image): TrimmedSize=0x18
    #   0x00: data ptr (relocation)
    #   0x04: width  (u16)
    #   0x06: height (u16)
    #   0x08: format (u32)
    #   0x0c: mipmap (u32)
    #   0x10: minLOD (f32)
    #   0x14: maxLOD (f32)
    if len(raw) >= 0x18:
        w = int.from_bytes(raw[0x04:0x06], 'big')
        h = int.from_bytes(raw[0x06:0x08], 'big')
        fmt = int.from_bytes(raw[0x08:0x0c], 'big')
        mip = int.from_bytes(raw[0x0c:0x10], 'big')
        print(f'  parsed: w={w}, h={h}, fmt={fmt}, mip={mip}')
    # data ref
    refs = list(img_struct.references())
    print(f'  refs: {len(refs)}')
    for ref_off, ref in refs:
        rr = ref.raw()
        print(f'    off=0x{ref_off:x}: data len={len(rr)}, head 32B = {rr[:32].hex()}')


dump_image('OURS  ', OUR_PATH, OUR_ROOT)
dump_image('INU   ', INU_PATH, INU_ROOT)
