"""Survey vanilla course .dats: count POBJ flags by textured/textureless."""
import sys, os, time
sys.path.insert(0, os.path.join('tools', 'blender', 'blender_addon_mkgp2_course', 'vendor', 'windows_x86_64'))
import hsdraw

vanilla_files = [
    r'C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files\YI_land_long_a.dat',
    r'C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files\MR_highway_long_A.dat',
    r'C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files\PC_land_long_a.dat',
]


def walk(dat):
    """Find each POBJ in the .dat by walking the joint hierarchy.
    Cycle prevention by snapshotting the (struct.raw()) bytes - each
    HSD struct has a unique byte signature in normal .dats."""
    visited_jobj = set()
    for r in dat.roots():
        if r.name == 'scene_data':
            continue
        rj = hsdraw.JObj.from_struct(r.data)
        stack = [rj]
        while stack:
            j = stack.pop()
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
                            yield (flags, has_tobj)
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
                stack.append(nx)
            if ch is not None:
                stack.append(ch)


for path in vanilla_files:
    t0 = time.time()
    data = open(path, 'rb').read()
    dat = hsdraw.parse_dat(data)
    flags_with_tex = {}
    flags_no_tex = {}
    n = 0
    for flags, has_tex in walk(dat):
        d = flags_with_tex if has_tex else flags_no_tex
        d[flags] = d.get(flags, 0) + 1
        n += 1
    name = os.path.basename(path)
    print(f'{name}  ({n} POBJs, {time.time()-t0:.1f}s):')
    print(f'  textured  : {dict(sorted(flags_with_tex.items()))}')
    print(f'  textureless: {dict(sorted(flags_no_tex.items()))}')
    sys.stdout.flush()
