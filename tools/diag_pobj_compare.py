"""Diagnostic: walk every JObj→DObj→POBJ chain in a .dat and dump the
first 32 bytes + decoded fields of each POBJ. Use to compare our
generated POBJ structure against vanilla.

Usage:
  python tools/diag_pobj_compare.py <path.dat> [--first-n N]

POBJ struct layout (HSDLib, big-endian):
  0x00-0x03  next ptr  (relocation, raw int)
  0x04-0x07  vtx_attr_grp ptr
  0x08-0x09  flags (u16)
  0x0A-0x0B  n_vertices (u16)
  0x0C-0x0F  display_list_ptr
  0x10-0x13  display_list_size (u32, in 32-byte chunks)
  0x14-0x17  references[]  envelope/blend ptr / matrix ref
                           (struct varies by flags)

Wait the offsets above don't match what patch_pobj_flags uses (0x0c).
Reading hsdraw source… actually the in-file POBJ layout differs from
what hsdraw exposes. Let's just dump raw bytes and explore.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'blender', 'blender_addon_mkgp2_course', 'vendor', 'win_amd64'))
import hsdraw

def walk(dat):
    """Yield (root_name, dobj_idx, pobj_idx, pobj, file_offset_or_None) per POBJ."""
    for r in dat.roots():
        if r.name == 'scene_data':
            continue
        rj = hsdraw.JObj.from_struct(r.data)
        stack = [rj]
        while stack:
            j = stack.pop()
            if j is None:
                continue
            for off, ref in j.as_struct().references():
                if off == 16:  # DObj head
                    d = hsdraw.DObj.from_struct(ref)
                    didx = 0
                    while d is not None:
                        pobj_attr = d.pobj
                        pidx = 0
                        while pobj_attr is not None:
                            if isinstance(pobj_attr, hsdraw.HsdStruct):
                                pobj = hsdraw.Pobj.from_struct(pobj_attr)
                            else:
                                pobj = pobj_attr
                            yield (r.name, didx, pidx, pobj, d)
                            pobj_attr = pobj.next
                            pidx += 1
                        d = d.next
                        didx += 1
            try:
                if j.next is not None: stack.append(j.next)
            except Exception: pass
            try:
                if j.child is not None: stack.append(j.child)
            except Exception: pass


def dump_pobj(pobj, file_bytes, label):
    raw = pobj.as_struct().raw()
    off = file_bytes.find(raw)
    print(f'  {label}: file_offset=0x{off:x}, raw_len={len(raw)}')
    print(f'    bytes[0:32]: {raw[:32].hex()}')
    # Try to decode common fields
    flags = int.from_bytes(raw[0x0c:0x0e], 'big')
    nverts = int.from_bytes(raw[0x0e:0x10], 'big')
    print(f'    flags=0x{flags:04x}  nverts={nverts}  display_list_size={pobj.display_list_size}')
    # Walk references()
    for ref_off, ref_struct in pobj.as_struct().references():
        ref_raw = ref_struct.raw()
        print(f'    ref off=0x{ref_off:x}: struct len={len(ref_raw)}, raw[:24]={ref_raw[:24].hex()}')
        if ref_off == 0x8:
            # Decode VtxAttr table (each entry = 24 bytes: name, type, count, format, stride, offset)
            print(f'      VtxAttr full raw: {ref_raw.hex()}')
            for i in range(0, len(ref_raw), 24):
                if i + 24 > len(ref_raw): break
                e = ref_raw[i:i+24]
                name   = int.from_bytes(e[0:4], 'big')
                typ    = int.from_bytes(e[4:8], 'big')
                cnt    = int.from_bytes(e[8:12], 'big')
                fmt    = int.from_bytes(e[12:16], 'big')
                stride = int.from_bytes(e[16:20], 'big')
                off    = int.from_bytes(e[20:24], 'big')
                if name == 0xff or name == 0:
                    print(f'      VtxAttr[{i//24}]: TERMINATOR / END (name={name})')
                    break
                print(f'      VtxAttr[{i//24}]: name={name} type={typ} cnt={cnt} fmt={fmt} stride={stride} offset=0x{off:x}')
        if ref_off == 0x10:
            # Display list: try to decode opcode + count
            opcode = ref_raw[0]
            count = int.from_bytes(ref_raw[1:3], 'big')
            print(f'      DList: opcode=0x{opcode:02x} count={count} dlist_bytes_per_vert=(displaylist_size_minus_3)/count={(len(ref_raw)-3)/max(count,1):.2f}')
    # Get DObj's MObj for context
    return raw, off


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    path = sys.argv[1]
    first_n = 3
    if '--first-n' in sys.argv:
        first_n = int(sys.argv[sys.argv.index('--first-n') + 1])

    with open(path, 'rb') as f:
        data = f.read()
    dat = hsdraw.parse_dat(data)
    print(f'=== {path} ({len(data)} bytes) ===')
    print(f'roots: {[r.name for r in dat.roots()]}')

    count = 0
    for root_name, didx, pidx, pobj, dobj in walk(dat):
        if count >= first_n:
            break
        count += 1
        label = f'{root_name}.dobj{didx}.pobj{pidx}'
        dump_pobj(pobj, data, label)
        # Also dump MObj.TObj.flags for the same DObj
        try:
            mobj_attr = dobj.mobj
            if mobj_attr is not None:
                mobj = mobj_attr if not isinstance(mobj_attr, hsdraw.HsdStruct) else hsdraw.MObj.from_struct(mobj_attr)
                m_raw = mobj.as_struct().raw()
                m_off = data.find(m_raw)
                print(f'    MObj: file_off=0x{m_off:x}, render_flags=0x{mobj.render_flags:08x}')
                # MObj layout (HSDLib): 0x04 = render_flags ptr-or-int, 0x08 = TObj_ptr, 0x0c = MAT_ptr
                # Walk references
                for ref_off, ref_struct in mobj.as_struct().references():
                    print(f'      m_ref off=0x{ref_off:x}: len={len(ref_struct.raw())}')
                    if ref_off == 8:  # TObj head
                        try:
                            tobj = hsdraw.TObj.from_struct(ref_struct)
                            t_raw = tobj.as_struct().raw()
                            print(f'      TObj: tex_map_id={tobj.tex_map_id}, wrap_s={tobj.wrap_s}, wrap_t={tobj.wrap_t}')
                            # tobj.flags is at offset 0x40 (per existing addon docstring)
                            tflags = int.from_bytes(t_raw[0x40:0x44], 'big')
                            print(f'      TObj.flags=0x{tflags:08x}, raw[0x40:0x44]={t_raw[0x40:0x44].hex()}, raw[0x0c:0x10]={t_raw[0x0c:0x10].hex()}')
                        except Exception as ex:
                            print(f'      TObj decode error: {ex}')
        except Exception as ex:
            print(f'    MObj walk error: {ex}')


if __name__ == '__main__':
    main()
