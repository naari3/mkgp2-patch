"""Compare TObj/MObj/Image fields field-by-field between OURS and INU.
TObj layout per HSDLib HSD_TObj.cs (TrimmedSize=0x5C)."""
import sys, os, struct
sys.path.insert(0, os.path.join('tools', 'blender', 'blender_addon_mkgp2_course', 'vendor', 'windows_x86_64'))
import hsdraw

INU_PATH = r'C:\Users\naari\Documents\blender\MR_highway_short_A_inu_aliased.dat'
INU_ROOT = 'MR_highway_inu_joint'
OUR_PATH = r'features/cup_page3/files/my_course.dat'
OUR_ROOT = 'my_course_joint'


def first_textured(dat, want_root):
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
                                for moff, mref in mobj.as_struct().references():
                                    if moff == 8:
                                        return d, mobj, mref
                        except: pass
                        d = d.next
            try:
                if j.next is not None: stack.append(j.next)
                if j.child is not None: stack.append(j.child)
            except: pass


# TObj layout (HSDLib HSD_TObj.cs):
TOBJ_FIELDS = [
    ('id_str_ref', 'u32', 0x00),
    ('next_ref', 'u32', 0x04),
    ('TexMapID', 'u32', 0x08),
    ('GXTexGenSrc', 'u32', 0x0C),
    ('rot_x', 'f32', 0x10),
    ('rot_y', 'f32', 0x14),
    ('rot_z', 'f32', 0x18),
    ('scale_x', 'f32', 0x1C),
    ('scale_y', 'f32', 0x20),
    ('scale_z', 'f32', 0x24),
    ('tx', 'f32', 0x28),
    ('ty', 'f32', 0x2C),
    ('tz', 'f32', 0x30),
    ('wrap_s', 'u32', 0x34),
    ('wrap_t', 'u32', 0x38),
    ('repeat_s', 'u8', 0x3C),
    ('repeat_t', 'u8', 0x3D),
    ('flags', 'u32', 0x40),
    ('blending', 'f32', 0x44),
    ('mag_filter', 'u32', 0x48),
    ('image_ref', 'u32', 0x4C),
    ('tlut_ref', 'u32', 0x50),
    ('lod_ref', 'u32', 0x54),
    ('tev_ref', 'u32', 0x58),
]


def fmt_field(raw, ty, off):
    if ty == 'u32':
        v = int.from_bytes(raw[off:off+4], 'big')
        return f'0x{v:08x} ({v})'
    if ty == 'u8':
        v = raw[off]
        return f'0x{v:02x} ({v})'
    if ty == 'f32':
        v = struct.unpack('>f', raw[off:off+4])[0]
        return f'{v}'


def dump_tobj(label, ref):
    raw = ref.raw()
    print(f'\n--- {label} TObj ({len(raw)}B) ---')
    if len(raw) < 0x5C:
        print(f'  short')
        return
    flags_v = int.from_bytes(raw[0x40:0x44], 'big')
    coord_type = flags_v & 0xF
    colormap = (flags_v >> 16) & 0xF
    alphamap = (flags_v >> 20) & 0xF
    other_flags = flags_v & ~0x00FF000F
    for name, ty, off in TOBJ_FIELDS:
        v = fmt_field(raw, ty, off)
        extra = ''
        if name == 'flags':
            extra = f' [coord_type={coord_type} colormap={colormap} alphamap={alphamap} other=0x{other_flags:08x}]'
        if name == 'GXTexGenSrc':
            srcs = {0:'TG_POS',1:'TG_NRM',2:'TG_BINRM',3:'TG_TANGENT',4:'TG_TEX0',5:'TG_TEX1',6:'TG_TEX2',7:'TG_TEX3'}
            extra = f' [{srcs.get(int(v.split(" ")[1].rstrip(")").lstrip("(")), "?")}]'
        if name == 'wrap_s' or name == 'wrap_t':
            wraps = {0:'CLAMP',1:'REPEAT',2:'MIRROR'}
            extra = f' [{wraps.get(int(v.split(" ")[1].rstrip(")").lstrip("(")), "?")}]'
        if name == 'mag_filter':
            mags = {0:'NEAR',1:'LINEAR',2:'NEAR_MIP_NEAR',3:'LIN_MIP_NEAR',4:'NEAR_MIP_LIN',5:'LIN_MIP_LIN'}
            extra = f' [{mags.get(int(v.split(" ")[1].rstrip(")").lstrip("(")), "?")}]'
        print(f'    {name:14s}: {v}{extra}')
    print(f'  refs:')
    for off, sub in ref.references():
        sb = sub.raw()
        print(f'    off=0x{off:02x}: len={len(sb)}, head={sb[:16].hex()}')
        if off == 0x4C and len(sb) >= 0x18:
            iw = int.from_bytes(sb[0x04:0x06], 'big')
            ih = int.from_bytes(sb[0x06:0x08], 'big')
            ifmt = int.from_bytes(sb[0x08:0x0c], 'big')
            imip = int.from_bytes(sb[0x0c:0x10], 'big', signed=True)
            iminLOD = struct.unpack('>f', sb[0x10:0x14])[0]
            imaxLOD = struct.unpack('>f', sb[0x14:0x18])[0]
            fmts = {0:'I4',1:'I8',2:'IA4',3:'IA8',4:'RGB565',5:'RGB5A3',6:'RGBA8',8:'CI4',9:'CI8',10:'CI14X2',14:'CMP'}
            print(f'      Image: w={iw} h={ih} fmt={ifmt}({fmts.get(ifmt,"?")}) mip={imip} minLOD={iminLOD} maxLOD={imaxLOD}')
            # Image data ref?
            for ioff, isub in sub.references():
                isb = isub.raw()
                print(f'      img_ref off=0x{ioff:02x}: len={len(isb)}')


def dump_mobj(label, mobj):
    raw = mobj.as_struct().raw()
    print(f'\n--- {label} MObj ({len(raw)}B) ---')
    rf = int.from_bytes(raw[0x04:0x08], 'big')
    print(f'    render_flags  : 0x{rf:08x}')
    # MObj rendering flags (HSDLib HSD_MObj.cs):
    # bit 0: CONSTANT, bit 4: TEX0, bit 5: TEX1, ..., bit 12: ALPHA_MAT, bit 28-29-30: PE setup choice
    bits = []
    for i in range(32):
        if rf & (1 << i):
            bits.append(f'b{i}')
    print(f'    render_flags bits: {",".join(bits)}')
    print(f'  refs:')
    for off, sub in mobj.as_struct().references():
        sb = sub.raw()
        print(f'    off=0x{off:02x}: len={len(sb)}, head={sb[:16].hex()}')


def go(label, path, root):
    data = open(path, 'rb').read()
    dat = hsdraw.parse_dat(data)
    d, mobj, tref = first_textured(dat, root)
    print(f'\n=== {label}: {root} (textured DObj) ===')
    dump_mobj(label, mobj)
    dump_tobj(label, tref)


go('OURS', OUR_PATH, OUR_ROOT)
go('INU ', INU_PATH, INU_ROOT)
