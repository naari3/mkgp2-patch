"""inu (modded MR_highway) と our my_course を joint 単位で structural diff。
inu は user 確認済み「modding でテクスチャ追加成功した既存例」、ours は flat color 失敗中。"""
import sys, os, struct
sys.path.insert(0, os.path.join('tools', 'blender', 'blender_addon_mkgp2_course', 'vendor', 'windows_x86_64'))
import hsdraw

INU_PATH = r'C:\Users\naari\Documents\blender\MR_highway_short_A_inu_aliased.dat'
INU_ROOT = 'MR_highway_inu_joint'
OUR_PATH = r'features/cup_page3/files/my_course.dat'
OUR_ROOT = 'my_course_joint'


def walk_textured_pobj(dat, file_bytes, want_root):
    """Yield first textured POBJ under want_root."""
    visited = set()
    for r in dat.roots():
        if r.name != want_root:
            continue
        rj = hsdraw.JObj.from_struct(r.data)
        stack = [rj]
        while stack:
            j = stack.pop()
            if j is None:
                continue
            jraw = bytes(j.as_struct().raw())
            if jraw in visited:
                continue
            visited.add(jraw)
            for off, ref in j.as_struct().references():
                if off == 16:
                    d = hsdraw.DObj.from_struct(ref)
                    while d is not None:
                        has_tex = False
                        mobj = None
                        try:
                            mobj_attr = d.mobj
                            if mobj_attr is not None:
                                mobj = mobj_attr if not isinstance(mobj_attr, hsdraw.HsdStruct) else hsdraw.MObj.from_struct(mobj_attr)
                                for moff, mref in mobj.as_struct().references():
                                    if moff == 8: has_tex = True; break
                        except: pass
                        if has_tex:
                            p = d.pobj
                            pobj = p if not isinstance(p, hsdraw.HsdStruct) else hsdraw.Pobj.from_struct(p)
                            yield (j, d, mobj, pobj, file_bytes)
                            return
                        d = d.next
            try:
                if j.next is not None: stack.append(j.next)
                if j.child is not None: stack.append(j.child)
            except: pass


def hex_dump(label, raw, offsets_meaning=None):
    print(f'  {label}: len={len(raw)}')
    for i in range(0, min(len(raw), 96), 16):
        chunk = raw[i:i+16]
        print(f'    +{i:02x}: {chunk.hex(" ")}')


def dump_jobj_chain(label, path, root_name):
    data = open(path, 'rb').read()
    dat = hsdraw.parse_dat(data)
    print(f'\n=== {label}: {root_name} ===')
    # Get the root JObj's flags + walk chain to first textured POBJ
    for r in dat.roots():
        if r.name == root_name:
            rj = hsdraw.JObj.from_struct(r.data)
            print(f'  Root JObj: flags=0x{rj.flags:08x}, raw_len={len(rj.as_struct().raw())}')
            j_raw = rj.as_struct().raw()
            hex_dump('Root JObj raw', j_raw)
            print(f'  Root JObj refs:')
            for off, ref in rj.as_struct().references():
                print(f'    off=0x{off:x}: len={len(ref.raw())}, head={ref.raw()[:24].hex()}')
            break
    # First textured POBJ + parent chain
    for j, d, mobj, pobj, fb in walk_textured_pobj(dat, data, root_name):
        print(f'  --- First textured DObj/POBJ ---')
        d_raw = d.as_struct().raw()
        hex_dump('DObj raw', d_raw)
        print(f'  DObj refs:')
        for off, ref in d.as_struct().references():
            rr = ref.raw()
            if len(rr) > 32:
                print(f'    off=0x{off:x}: len={len(rr)}, head={rr[:24].hex()}')
            else:
                print(f'    off=0x{off:x}: len={len(rr)}, raw={rr.hex()}')
        m_raw = mobj.as_struct().raw()
        hex_dump('MObj raw', m_raw)
        print(f'  MObj refs:')
        for off, ref in mobj.as_struct().references():
            rr = ref.raw()
            print(f'    off=0x{off:x}: len={len(rr)}, head={rr[:32].hex()}')
        p_raw = pobj.as_struct().raw()
        hex_dump('POBJ raw', p_raw)
        flags = int.from_bytes(p_raw[0x0c:0x0e], 'big')
        print(f'  POBJ.flags = 0x{flags:04x}')
        # TObj raw
        for moff, mref in mobj.as_struct().references():
            if moff == 8:
                t_raw = mref.raw()
                hex_dump('TObj raw [0x40:0x60]', t_raw[0x40:0x60])
                # TObj flags + GXTexGenSrc
                tflags = int.from_bytes(t_raw[0x40:0x44], 'big')
                src    = int.from_bytes(t_raw[0x0c:0x10], 'big')
                print(f'  TObj.flags=0x{tflags:08x}  GXTexGenSrc=0x{src:08x}')
                # TObj refs
                print(f'  TObj refs:')
                for toff, tref in mref.references():
                    print(f'    off=0x{toff:x}: len={len(tref.raw())}')
                break
        break


dump_jobj_chain('OURS', OUR_PATH, OUR_ROOT)
dump_jobj_chain('INU', INU_PATH, INU_ROOT)
