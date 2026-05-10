"""Find vanilla textured POBJs with flags=0x0000 (SHAPE) and dump structure."""
import sys, os
sys.path.insert(0, os.path.join('tools', 'blender', 'blender_addon_mkgp2_course', 'vendor', 'windows_x86_64'))
import hsdraw

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
        stack = [(rj, r.name)]
        while stack:
            j, root_name = stack.pop()
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
                    didx = 0
                    while d is not None:
                        draw = bytes(d.as_struct().raw())
                        if draw in visited_dobj:
                            break
                        visited_dobj.add(draw)
                        # MObj/TObj presence
                        has_tobj = False
                        mobj = None
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
                        pidx = 0
                        while p is not None:
                            pobj = p if not isinstance(p, hsdraw.HsdStruct) else hsdraw.Pobj.from_struct(p)
                            praw = bytes(pobj.as_struct().raw())
                            if praw in visited_pobj:
                                break
                            visited_pobj.add(praw)
                            flags = int.from_bytes(praw[0x0c:0x0e], 'big')
                            yield (root_name, didx, pidx, pobj, dobj_obj := d, mobj_obj := mobj, has_tobj, flags, file_bytes.find(praw))
                            p = pobj.next
                            pidx += 1
                        d = d.next
                        didx += 1
            try:
                nx = j.next
            except Exception:
                nx = None
            try:
                ch = j.child
            except Exception:
                ch = None
            if nx is not None:
                stack.append((nx, root_name))
            if ch is not None:
                stack.append((ch, root_name))


for path in paths:
    data = open(path, 'rb').read()
    dat = hsdraw.parse_dat(data)
    print(f'\n=== {os.path.basename(path)} ===')
    for root_name, didx, pidx, pobj, dobj, mobj, has_tex, flags, off in walk(dat, data):
        if not has_tex or flags != 0:
            continue
        praw = pobj.as_struct().raw()
        nverts = int.from_bytes(praw[0x0e:0x10], 'big')
        # MObj render_flags
        rf = mobj.render_flags if mobj else None
        # TObj.flags
        tflags = None
        for moff, mref in mobj.as_struct().references():
            if moff == 8:
                t_raw = mref.raw()
                tflags = int.from_bytes(t_raw[0x40:0x44], 'big')
                break
        # POBJ refs
        ref_offs = [r[0] for r in pobj.as_struct().references()]
        # VtxAttr decode
        vtx_attr = []
        for ref_off, ref_struct in pobj.as_struct().references():
            if ref_off == 8:
                vbytes = ref_struct.raw()
                for i in range(0, len(vbytes), 24):
                    if i + 24 > len(vbytes): break
                    name = int.from_bytes(vbytes[i:i+4], 'big')
                    if name == 255 or name == 0:
                        break
                    typ = int.from_bytes(vbytes[i+4:i+8], 'big')
                    cnt = int.from_bytes(vbytes[i+8:i+12], 'big')
                    fmt = int.from_bytes(vbytes[i+12:i+16], 'big')
                    stride = int.from_bytes(vbytes[i+16:i+20], 'big')
                    vtx_attr.append((name, typ, cnt, fmt, stride))
        print(f'  {root_name}.d{didx}.p{pidx}  nverts={nverts}  MObj.rf=0x{rf:08x}  TObj.flags=0x{tflags:08x}  POBJrefs={ref_offs}  VtxAttr={vtx_attr}')
        print(f'    POBJ raw:  {praw.hex()}')
