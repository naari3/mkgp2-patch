"""Dump every textured TObj from vanilla MR_highway and ours, group by to_dict signature."""
import sys, os, json
from collections import Counter, defaultdict
sys.path.insert(0, os.path.join('tools','blender','blender_addon_mkgp2_course','vendor','windows_x86_64'))
import hsdraw

VANILLA = r'C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files\MR_highway_long_A.dat'
OURS = r'features/cup_page3/files/my_course.dat'


def all_textured_tobjs(dat):
    """Yield (root_name, tobj_dict, image_dict) for every textured POBJ's TObj."""
    visited = set()
    for r in dat.roots():
        if r.name == 'scene_data':
            continue
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
                                rf = int.from_bytes(mobj.as_struct().raw()[0x04:0x08], 'big')
                                for moff, mref in mobj.as_struct().references():
                                    if moff == 8:
                                        tobj = hsdraw.TObj.from_struct(mref)
                                        td = tobj.to_dict()
                                        # pull image fields too
                                        img_d = {}
                                        for toff, sub in mref.references():
                                            if toff == 0x4C:
                                                sb = sub.raw()
                                                img_d['w'] = int.from_bytes(sb[0x04:0x06], 'big')
                                                img_d['h'] = int.from_bytes(sb[0x06:0x08], 'big')
                                                img_d['fmt'] = int.from_bytes(sb[0x08:0x0c], 'big')
                                                img_d['mip'] = int.from_bytes(sb[0x0c:0x10], 'big', signed=True)
                                                break
                                        yield (r.name, rf, td, img_d)
                                        break
                        except: pass
                        d = d.next
            try:
                if j.next is not None: stack.append(j.next)
                if j.child is not None: stack.append(j.child)
            except: pass


def signature(td, mobj_rf, img_d):
    """Reduce TObj+MObj to a comparable signature (omit volatile addr)."""
    keys = ['tex_map_id','tex_gen_src','wrap_s','wrap_t','repeat_s','repeat_t',
            'rotation','scale','translation','flags','blending','mag_filter',
            'coord_type','color_op','alpha_op',
            'is_lightmap_diffuse','is_lightmap_ambient','is_lightmap_specular',
            'is_bump','image_data_present','tlut_data_present','lod_data_present',
            'tev_data_present','next_present']
    sig = {k: td.get(k) for k in keys}
    sig['mobj_render_flags'] = mobj_rf
    sig['img_fmt'] = img_d.get('fmt')
    sig['img_mip'] = img_d.get('mip')
    return sig


def fmt_sig(s):
    parts = []
    for k, v in s.items():
        parts.append(f'{k}={v}')
    return '\n  '.join(parts)


def go(label, path):
    data = open(path, 'rb').read()
    dat = hsdraw.parse_dat(data)
    sigs = Counter()
    for root_name, mobj_rf, td, img_d in all_textured_tobjs(dat):
        sig = signature(td, mobj_rf, img_d)
        # use frozen dict as key
        key = tuple(sorted(sig.items(), key=lambda x: x[0]))
        sigs[key] += 1
    print(f'\n=== {label} ({sum(sigs.values())} textured TObjs, {len(sigs)} unique signatures) ===')
    for i, (key, count) in enumerate(sigs.most_common()):
        sig = dict(key)
        print(f'  --- sig #{i} ({count}x) ---')
        print('   ', fmt_sig(sig))
    return sigs

vanilla_sigs = go('VANILLA MR_highway_long_A', VANILLA)
ours_sigs    = go('OURS my_course', OURS)

# Show keys present in OURS but not in any vanilla (= probably the cause)
print('\n\n=== diff: OURS signatures NOT seen in vanilla ===')
for key, count in ours_sigs.most_common():
    if key not in vanilla_sigs:
        sig = dict(key)
        print(f'  -- ours sig ({count}x) NOT in vanilla --')
        # find closest vanilla sig
        best_match = None
        best_diff = None
        for vkey in vanilla_sigs:
            vsig = dict(vkey)
            diffs = [(k, sig.get(k), vsig.get(k)) for k in set(sig.keys())|set(vsig.keys()) if sig.get(k) != vsig.get(k)]
            if best_diff is None or len(diffs) < len(best_diff):
                best_diff = diffs
                best_match = vsig
        print(f'    closest vanilla sig differs in {len(best_diff)} fields:')
        for k, ours_v, van_v in best_diff:
            print(f'      {k}: OURS={ours_v}  VANILLA={van_v}')
