"""Parse a Dolphin FifoPlayer `.dff` file and decode its GX command stream.

Format reference (verbatim from
`Source/Core/Core/FifoPlayer/FifoDataFile.cpp` of dolphin-emu/dolphin):

    constexpr u32 FILE_ID = 0x0d01f1f0;
    constexpr u32 VERSION_NUMBER = 6;

    struct FileHeader {        // 128 bytes, packed
        u32 fileId;
        u32 file_version;
        u32 min_loader_version;
        u64 bpMemOffset;
        u32 bpMemSize;
        u64 cpMemOffset;
        u32 cpMemSize;
        u64 xfMemOffset;
        u32 xfMemSize;
        u64 xfRegsOffset;
        u32 xfRegsSize;
        u64 frameListOffset;
        u32 frameCount;
        u32 flags;
        u64 texMemOffset;
        u32 texMemSize;
        u32 mem1_size;
        u32 mem2_size;
        char gameid[8];
        u8 reserved[24];
    };

    struct FileFrameInfo {     // 64 bytes, packed
        u64 fifoDataOffset;
        u32 fifoDataSize;
        u32 fifoStart;
        u32 fifoEnd;
        u64 memoryUpdatesOffset;
        u32 numMemoryUpdates;
        u8 reserved[32];
    };

GX opcode decoding follows
`Source/Core/VideoCommon/OpcodeDecoding.h::detail::RunCommand`.

Usage:
    python tools/dff_parse.py <path.dff> [--frame N] [--around-vertex-count N]

The `--around-vertex-count` flag scans every primitive draw call and,
for each one with that exact `num_vertices`, prints the preceding
TexCoordGen / TEV / TexObj loads — the registers we need to inspect
to debug texture sampling.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path
from typing import Iterator, Optional


# ---------------------------------------------------------------------------
# Header / frame info parsing
# ---------------------------------------------------------------------------

FILE_ID = 0x0d01f1f0

_HEADER_FMT = "<3I Q I Q I Q I Q I Q I I Q I I I 8s 24s"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)
assert _HEADER_SIZE == 128, f"FileHeader expected 128 bytes, got {_HEADER_SIZE}"

_FRAME_FMT = "<Q I I I Q I 32s"
_FRAME_SIZE = struct.calcsize(_FRAME_FMT)
assert _FRAME_SIZE == 64, f"FileFrameInfo expected 64 bytes, got {_FRAME_SIZE}"


def parse_header(buf: bytes) -> dict:
    fields = struct.unpack(_HEADER_FMT, buf[:_HEADER_SIZE])
    (file_id, file_version, min_loader_version,
     bpMemOffset, bpMemSize,
     cpMemOffset, cpMemSize,
     xfMemOffset, xfMemSize,
     xfRegsOffset, xfRegsSize,
     frameListOffset, frameCount, flags,
     texMemOffset, texMemSize,
     mem1_size, mem2_size,
     gameid, reserved) = fields
    if file_id != FILE_ID:
        raise ValueError(
            f"not a Dolphin FifoLog .dff: file_id=0x{file_id:08x} "
            f"!= expected 0x{FILE_ID:08x}")
    return {
        "file_version": file_version,
        "min_loader_version": min_loader_version,
        "bpMemOffset": bpMemOffset, "bpMemSize": bpMemSize,
        "cpMemOffset": cpMemOffset, "cpMemSize": cpMemSize,
        "xfMemOffset": xfMemOffset, "xfMemSize": xfMemSize,
        "xfRegsOffset": xfRegsOffset, "xfRegsSize": xfRegsSize,
        "frameListOffset": frameListOffset, "frameCount": frameCount,
        "flags": flags,
        "texMemOffset": texMemOffset, "texMemSize": texMemSize,
        "mem1_size": mem1_size, "mem2_size": mem2_size,
        "gameid": gameid.rstrip(b"\x00").decode("ascii", "replace"),
    }


def parse_frame_list(buf: bytes, list_offset: int, count: int) -> list[dict]:
    out = []
    for i in range(count):
        f = struct.unpack(
            _FRAME_FMT, buf[list_offset + i * _FRAME_SIZE :
                            list_offset + (i + 1) * _FRAME_SIZE])
        out.append({
            "fifoDataOffset": f[0],
            "fifoDataSize":   f[1],
            "fifoStart":      f[2],
            "fifoEnd":        f[3],
            "memoryUpdatesOffset": f[4],
            "numMemoryUpdates":    f[5],
        })
    return out


# ---------------------------------------------------------------------------
# GX opcode decoding (faithful to OpcodeDecoding.h::RunCommand)
# ---------------------------------------------------------------------------

OP_NOP            = 0x00
OP_LOAD_CP_REG    = 0x08
OP_LOAD_XF_REG    = 0x10
OP_LOAD_INDX_A    = 0x20
OP_LOAD_INDX_B    = 0x28
OP_LOAD_INDX_C    = 0x30
OP_LOAD_INDX_D    = 0x38
OP_CMD_CALL_DL    = 0x40
OP_CMD_UNK_METRICS = 0x44
OP_CMD_INVL_VC    = 0x48
OP_LOAD_BP_REG    = 0x61
OP_PRIMITIVE_LO   = 0x80
OP_PRIMITIVE_HI   = 0xbf

PRIMITIVE_NAMES = {
    0x0: "QUADS",
    0x1: "QUADS_2",
    0x2: "TRIANGLES",
    0x3: "TRIANGLE_STRIP",
    0x4: "TRIANGLE_FAN",
    0x5: "LINES",
    0x6: "LINE_STRIP",
    0x7: "POINTS",
}

# Vertex size cache per VAT — populated by CP register loads.  We need
# this to know how much to advance past a primitive opcode.  Without
# replaying CP state we can't compute vertex_size per-VAT, so the
# parser tracks CP register writes and uses a heuristic for vertex
# size (sum of attribute sizes from VAT definitions).  See cp_state.

class CPState:
    """Minimal CPState mirror: tracks vtx_desc + vtx_attr across CP
    register writes, just enough to compute vertex size per VAT.

    We only need the size, not the full layout, so we approximate via
    the byte_count fields documented in CPMemory.h.  Good enough for
    decoding past primitives; not faithful enough to actually load
    vertex data.
    """
    def __init__(self):
        # vtx_desc: 2 × u32 (lo / hi) per global, applies to all VATs
        self.vtx_desc_lo = 0
        self.vtx_desc_hi = 0
        # vtx_attr: per-VAT 3 × u32 (a / b / c)
        self.vtx_attr = [[0, 0, 0] for _ in range(8)]
        # INDEX-mode array base + stride per attribute slot.
        # CP 0xa0..0xab = ARRAY_BASE for slots 0..11
        #   (0=POS_MTX_DATA, 1..7=TEX_MTX_DATA, 8=POS, 9=NRM,
        #    10=COLOR0, 11=COLOR1)
        # CP 0xac..0xaf = ARRAY_BASE for TEX0..TEX3
        # CP 0xb0..0xbb / 0xbc..0xbf = ARRAY_STRIDE for the same slots
        # (Reference: VideoCommon/CPMemory.h ARRAY_xxxx + dolphin
        # OpcodeDecoder switch on cmd2.)
        self.array_base = [0] * 16
        self.array_stride = [0] * 16

    def on_cp(self, cmd: int, value: int):
        if cmd == 0x50:
            self.vtx_desc_lo = value
        elif cmd == 0x60:
            self.vtx_desc_hi = value
        elif 0x70 <= cmd <= 0x77:
            self.vtx_attr[cmd - 0x70][0] = value
        elif 0x80 <= cmd <= 0x87:
            self.vtx_attr[cmd - 0x80][1] = value
        elif 0x90 <= cmd <= 0x97:
            self.vtx_attr[cmd - 0x90][2] = value
        elif 0xa0 <= cmd <= 0xaf:
            self.array_base[cmd - 0xa0] = value
        elif 0xb0 <= cmd <= 0xbf:
            self.array_stride[cmd - 0xb0] = value

    def vertex_size(self, vat: int) -> int:
        """Compute byte size of one vertex under the given VAT.

        VertexLoaderBase::GetVertexSize is the authoritative reference.
        We approximate by walking the standard attribute order and
        summing per-attribute sizes for DIRECT mode, plus 1 / 2 bytes
        for INDEX8 / INDEX16 modes.
        """
        # vtx_desc bits (lo, then hi):
        #   lo bits 0:    pos_mat_idx (0/1)
        #   lo bits 1-8:  texN_mat_idx[0..7] (one bit each)
        #   lo bits 9-10: position    (0=none, 1=direct, 2=idx8, 3=idx16)
        #   lo bits 11-12: normal
        #   lo bits 13-14: color0
        #   lo bits 15-16: color1
        #   hi bits 0-1, 2-3, ..., 14-15: tex0..tex7
        # See VideoCommon/CPMemory.h: TVtxDesc Hex / VtxAttr layouts.

        lo = self.vtx_desc_lo
        hi = self.vtx_desc_hi
        attr_a = self.vtx_attr[vat][0]
        attr_b = self.vtx_attr[vat][1]
        attr_c = self.vtx_attr[vat][2]

        size = 0

        # Position matrix index
        if lo & 0x1:
            size += 1
        # Texture matrix indices
        for i in range(8):
            if (lo >> (1 + i)) & 0x1:
                size += 1

        # --- helper to compute attribute size per (cnt, fmt) ---
        # cnt = 0 (single component) or 1 (multi). fmt encodes sub-types.
        # For our purposes here we only need the byte count for DIRECT
        # mode.  INDEX8 and INDEX16 always 1 / 2 bytes.
        def attr_size(cnt: int, fmt: int, is_pos_or_tex: bool) -> int:
            # cnt: number of components (varies by attribute)
            # fmt: 0=u8, 1=s8, 2=u16, 3=s16, 4=f32 (positions/normals/tex)
            #      colors use a different table (RGB565=0, RGB888=1, etc.)
            sizeof_fmt = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4}.get(fmt, 0)
            return cnt * sizeof_fmt

        # Position: lo bits 9-10 = mode, attr_a bits 0-3 = (cnt, fmt)
        pos_mode = (lo >> 9) & 0x3
        if pos_mode == 1:  # DIRECT
            pos_cnt_bit = attr_a & 0x1
            pos_cnt = 3 if pos_cnt_bit else 2  # 1 = XYZ (3), 0 = XY (2)
            pos_fmt = (attr_a >> 1) & 0x7
            # frac = bits 4-8 (no size impact)
            sizeof = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4}.get(pos_fmt, 0)
            size += pos_cnt * sizeof
        elif pos_mode == 2:  # INDEX8
            size += 1
        elif pos_mode == 3:  # INDEX16
            size += 2

        # Normal: lo bits 11-12 = mode, attr_a bits 9-10 = format
        nrm_mode = (lo >> 11) & 0x3
        if nrm_mode == 1:
            nrm_cnt_bit = (attr_a >> 9) & 0x1   # 0 = NRM, 1 = NBT (3 normals)
            nrm_fmt = (attr_a >> 10) & 0x7
            n_normals = 9 if nrm_cnt_bit else 3  # 3 components per normal × 3 if NBT
            sizeof = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4}.get(nrm_fmt, 0)
            size += n_normals * sizeof
        elif nrm_mode == 2:
            size += 1
        elif nrm_mode == 3:
            size += 2

        # Color0/Color1: lo bits 13-14 / 15-16
        for ci, shift in enumerate((13, 15)):
            mode = (lo >> shift) & 0x3
            if mode == 1:
                # color components stored per format:
                # 0=RGB565 (2B), 1=RGB888 (3B), 2=RGB888x (4B),
                # 3=RGBA4444 (2B), 4=RGBA6666 (3B), 5=RGBA8888 (4B)
                col_attr_shift = 13 + ci * 4   # color0 at attr_a bit 13, color1 at 17
                col_fmt_bits = (attr_a >> col_attr_shift) & 0x7
                col_byte = {0: 2, 1: 3, 2: 4, 3: 2, 4: 3, 5: 4}.get(col_fmt_bits, 0)
                size += col_byte
            elif mode == 2:
                size += 1
            elif mode == 3:
                size += 2

        # Tex0..Tex7: hi bits in pairs
        for ti in range(8):
            mode = (hi >> (ti * 2)) & 0x3
            if mode == 1:
                # tex coord: cnt bit + fmt 3 bits per tex
                # attr_b layout: tex0_cnt at bit 0, tex0_fmt at bits 1-3,
                #                tex0_frac at bits 4-8 (no size impact)
                # Then 9 bits per tex × 5 tex in attr_b, rest in attr_c.
                if ti < 5:
                    base = ti * 9
                    src = attr_b
                else:
                    base = (ti - 5) * 9
                    src = attr_c
                cnt_bit = (src >> base) & 0x1
                fmt = (src >> (base + 1)) & 0x7
                t_cnt = 2 if cnt_bit else 1  # ST = 2, S = 1
                sizeof = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4}.get(fmt, 0)
                size += t_cnt * sizeof
            elif mode == 2:
                size += 1
            elif mode == 3:
                size += 2

        return size


def _swap32(b: bytes, off: int) -> int:
    return struct.unpack(">I", b[off:off + 4])[0]


def _swap24(b: bytes, off: int) -> int:
    return (b[off] << 16) | (b[off + 1] << 8) | b[off + 2]


def _swap16(b: bytes, off: int) -> int:
    return (b[off] << 8) | b[off + 1]


def iter_commands(fifo: bytes, cp_state: CPState) -> Iterator[tuple]:
    """Yield decoded (kind, ...) tuples for each command in `fifo`.

    Tuple kinds:
      ("nop", count)
      ("cp", cmd2, value)
      ("xf", base_address, stream_size, data_bytes)
      ("indx", which, ref_array, index, address, size)
      ("call_dl", address, size)
      ("bp", cmd2, value24)
      ("primitive", primitive_id, vat, vertex_size, num_vertices, vertex_data)
      ("unknown", opcode, byte_offset)
    """
    pos = 0
    n = len(fifo)
    while pos < n:
        op = fifo[pos]
        if op == OP_NOP:
            count = 1
            while pos + count < n and fifo[pos + count] == OP_NOP:
                count += 1
            yield ("nop", count)
            pos += count
        elif op == OP_LOAD_CP_REG:
            if pos + 6 > n:
                yield ("trunc", op, pos); return
            cmd2 = fifo[pos + 1]
            value = _swap32(fifo, pos + 2)
            cp_state.on_cp(cmd2, value)
            yield ("cp", cmd2, value)
            pos += 6
        elif op == OP_LOAD_XF_REG:
            if pos + 5 > n:
                yield ("trunc", op, pos); return
            cmd2 = _swap32(fifo, pos + 1)
            base_address = cmd2 & 0xffff
            stream_size = ((cmd2 >> 16) & 0xf) + 1
            if pos + 5 + stream_size * 4 > n:
                yield ("trunc", op, pos); return
            yield ("xf", base_address, stream_size,
                   fifo[pos + 5 : pos + 5 + stream_size * 4])
            pos += 5 + stream_size * 4
        elif op in (OP_LOAD_INDX_A, OP_LOAD_INDX_B,
                    OP_LOAD_INDX_C, OP_LOAD_INDX_D):
            if pos + 5 > n:
                yield ("trunc", op, pos); return
            value = _swap32(fifo, pos + 1)
            index = value >> 16
            address = value & 0xfff
            size = ((value >> 12) & 0xf) + 1
            yield ("indx", op, (op // 8) + 8, index, address, size)
            pos += 5
        elif op == OP_CMD_CALL_DL:
            if pos + 9 > n:
                yield ("trunc", op, pos); return
            address = _swap32(fifo, pos + 1)
            size = _swap32(fifo, pos + 5)
            yield ("call_dl", address, size)
            pos += 9
        elif op == OP_LOAD_BP_REG:
            if pos + 5 > n:
                yield ("trunc", op, pos); return
            cmd2 = fifo[pos + 1]
            value = _swap24(fifo, pos + 2)
            yield ("bp", cmd2, value)
            pos += 5
        elif OP_PRIMITIVE_LO <= op <= OP_PRIMITIVE_HI:
            if pos + 3 > n:
                yield ("trunc", op, pos); return
            primitive = (op & 0x78) >> 3
            vat = op & 0x07
            num_vertices = _swap16(fifo, pos + 1)
            vertex_size = cp_state.vertex_size(vat)
            cmd_size = 3 + num_vertices * vertex_size
            if pos + cmd_size > n:
                yield ("trunc", op, pos); return
            yield ("primitive", primitive, vat, vertex_size, num_vertices,
                   fifo[pos + 3 : pos + cmd_size])
            pos += cmd_size
        else:
            yield ("unknown", op, pos)
            pos += 1


# ---------------------------------------------------------------------------
# Pretty-print state of interest near a draw call
# ---------------------------------------------------------------------------

def decode_xf_settexmtxinfo(value: int) -> str:
    """TexMtxInfo (XF address 0x1040+n, n=0..7).  Bit layout from
    Dolphin VideoCommon/XFMemory.h::TexMtxInfo:

        bit 0:     unknown
        bit 1:     projection      (TexSize: 0=ST 2x4, 1=STQ 3x4)
        bit 2:     inputform       (TexInputForm: 0=AB11, 1=ABC1)
        bit 3:     unknown2
        bits 4-6:  texgentype      (Regular=0, EmbossMap=1, Color0=2, Color1=3)
        bits 7-11: sourcerow       (5 bits — Dolphin SourceRow enum below)
        bits 12-14: embosssourceshift
        bits 15-17: embosslightshift

    SourceRow enum (Dolphin XFMemory.h::SourceRow):
        0 Geom, 1 Normal, 2 Colors, 3 BinormalT, 4 BinormalB,
        5 Tex0, 6 Tex1, 7 Tex2, 8 Tex3, 9 Tex4, 10 Tex5, 11 Tex6, 12 Tex7

    HSDLib's TObj.GXTexGenSrc uses the GX SDK enum (POS=0..TEX7=11),
    which the renderer maps to this 5-bit SourceRow (= Dolphin enum).
    The mapping isn't a 1:1 numeric copy: GX_TG_TEX0=4 in the SDK
    enum maps to SourceRow Tex0=5 here.  The print shows the raw bits
    + the SourceRow name so nothing is hidden.
    """
    unknown = value & 0x1
    proj   = (value >> 1) & 0x1
    inform = (value >> 2) & 0x1
    unk2   = (value >> 3) & 0x1
    type_  = (value >> 4) & 0x7
    source = (value >> 7) & 0x1f
    emb_s  = (value >> 12) & 0x7
    emb_l  = (value >> 15) & 0x7
    type_name = {0: "REGULAR", 1: "EMBOSS", 2: "COLOR0", 3: "COLOR1"}.get(type_, f"?{type_}")
    src_name = {
        0: "Geom", 1: "Normal", 2: "Colors", 3: "BinormalT", 4: "BinormalB",
        5: "Tex0", 6: "Tex1", 7: "Tex2", 8: "Tex3",
        9: "Tex4", 10: "Tex5", 11: "Tex6", 12: "Tex7",
    }.get(source, f"?{source}")
    return (f"proj={proj} inform={inform} type={type_name} "
            f"src=0x{source:02x}({src_name}) emb_s={emb_s} emb_l={emb_l}")


def hexdump_short(data: bytes, max_bytes: int = 32) -> str:
    h = data[:max_bytes].hex()
    return " ".join(h[i:i+2] for i in range(0, len(h), 2)) + ("..." if len(data) > max_bytes else "")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dff", type=Path)
    ap.add_argument("--frame", type=int, default=None,
                    help="restrict to one frame index (default: all)")
    ap.add_argument("--around-vertex-count", type=int, default=None,
                    help="show context around every primitive draw with this exact num_vertices")
    ap.add_argument("--list-draws", action="store_true",
                    help="just list every draw call's (frame, prim, vat, vsize, nverts)")
    ap.add_argument("--trace-xf-tex-mtx", action="store_true",
                    help="print every XF write that touches TEX_MTX[0..7] with the draw it precedes")
    args = ap.parse_args()

    raw = args.dff.read_bytes()
    hdr = parse_header(raw)
    print(f"=== {args.dff.name} ===")
    print(f"  game_id: {hdr['gameid']!r}, version: {hdr['file_version']}, "
          f"frames: {hdr['frameCount']}, flags: 0x{hdr['flags']:x}")
    print(f"  bp[{hdr['bpMemSize']}B@{hdr['bpMemOffset']}], "
          f"cp[{hdr['cpMemSize']}B@{hdr['cpMemOffset']}], "
          f"xf[{hdr['xfMemSize']}B@{hdr['xfMemOffset']}], "
          f"xf_regs[{hdr['xfRegsSize']}B@{hdr['xfRegsOffset']}], "
          f"tex[{hdr['texMemSize']}B@{hdr['texMemOffset']}]")
    frames = parse_frame_list(raw, hdr["frameListOffset"], hdr["frameCount"])
    for i, f in enumerate(frames):
        print(f"  frame[{i}]: fifoData {f['fifoDataSize']}B @ {f['fifoDataOffset']}, "
              f"start=0x{f['fifoStart']:x}, end=0x{f['fifoEnd']:x}")

    print()
    for fi, f in enumerate(frames):
        if args.frame is not None and fi != args.frame:
            continue
        fifo = raw[f["fifoDataOffset"] : f["fifoDataOffset"] + f["fifoDataSize"]]
        cp = CPState()

        # Track current effective register state (last value loaded
        # for each address).  Each draw uses whatever is current at
        # that moment.
        #
        # BP register map (Dolphin BPMemory.h, verified):
        #   0x28..0x2f: TREF (TEV_ORDER), 8 regs each covering 2 stages
        #   0x80..0x83: TX_SETMODE0_I0..I3 (units 0-3: wrap, filter, lod_bias)
        #   0x84..0x87: TX_SETMODE1_I0..I3 (units 0-3: lod min/max)
        #   0x88..0x8b: TX_SETIMAGE0_I0..I3 (units 0-3: width, height, format)
        #   0x8c..0x8f: TX_SETIMAGE1_I0..I3 (units 0-3: TMEM offset, etc.)
        #   0x90..0x93: TX_SETIMAGE2_I0..I3
        #   0x94..0x97: TX_SETIMAGE3_I0..I3 (units 0-3: image_base = addr >> 5)
        #   0x98..0x9b: TX_SETTLUT_I0..I3
        #   0xa0..0xa3: TX_SETMODE0_I4..I7 (units 4-7)
        #   0xa4..0xa7: TX_SETMODE1_I4..I7
        #   0xa8..0xab: TX_SETIMAGE0_I4..I7
        #   ...
        #   0xc0,0xc2,...,0xde: TEV_COLOR_ENV stages 0..15 (even cmd = color env)
        #   0xc1,0xc3,...,0xdf: TEV_ALPHA_ENV stages 0..15 (odd cmd = alpha env)
        xf_texmtxinfo = [None] * 8       # XF 0x1040..0x1047
        # XFMEM_POSMATRICES = 0x000..0x0FF (256 floats = 64 rows × 4 floats)
        # MATINDEX values address into this: idx 30 = TEXMTX0 (row 30..32 =
        # XF 0x078..0x083), idx 60 = "IDENTITY" slot (row 60..62 =
        # XF 0x0F0..0x0FB).  My earlier "tex_mtx[0..7]" tracking at
        # 0x000..0x05f was actually reading POSITION matrices.
        xf_pos_area = [None] * 256       # XF 0x000..0x0FF as raw u32
        xf_dual_tex_mtx = [[None]*12 for _ in range(8)]  # XF 0x500..0x55f (post-trans matrices)
        xf_matindex_a = None             # XF 0x1018 (pos + tex0..tex3 mtx indices)
        xf_matindex_b = None             # XF 0x1019 (tex4..tex7 mtx indices)
        bp_tx_setmode0 = [None] * 8      # 0x80-0x83 + 0xa0-0xa3
        bp_tx_setmode1 = [None] * 8      # 0x84-0x87 + 0xa4-0xa7
        bp_tx_setimage0 = [None] * 8     # 0x88-0x8b + 0xa8-0xab (width/height/format)
        bp_tx_setimage3 = [None] * 8     # 0x94-0x97 + 0xb4-0xb7 (image base)
        bp_tev_order = [None] * 8        # 0x28-0x2f (each covers 2 stages)
        bp_tev_color_env = [None] * 16   # 0xc0,0xc2,...
        bp_tev_alpha_env = [None] * 16   # 0xc1,0xc3,...
        bp_genmode = None                # 0x00 (num tev stages, num textures, etc.)
        # TEV registers (color & alpha): RA = red+alpha, BG = blue+green
        # 0xE0,0xE1,0xE2,0xE3 = REG0_RA, REG0_BG, ... wait actually
        # 0xE0 is REG0 RA, 0xE1 is REG0 BG, 0xE2 is REG1 RA, ..., 4 regs total
        bp_tev_reg_ra = [None] * 4       # 0xe0, 0xe2, 0xe4, 0xe6
        bp_tev_reg_bg = [None] * 4       # 0xe1, 0xe3, 0xe5, 0xe7
        # CPMemory: vtx_desc + per-VAT vtx_attr (CPState already tracks these)

        draw_count = 0
        for cmd in iter_commands(fifo, cp):
            kind = cmd[0]
            if kind == "xf":
                base, stream_size, data = cmd[1], cmd[2], cmd[3]
                for word_i in range(stream_size):
                    xf_addr = base + word_i
                    value = struct.unpack(">I", data[word_i*4:(word_i+1)*4])[0]
                    if 0x1040 <= xf_addr <= 0x1047:
                        xf_texmtxinfo[xf_addr - 0x1040] = value
                    elif 0x000 <= xf_addr <= 0x0ff:
                        xf_pos_area[xf_addr] = value
                        if args.trace_xf_tex_mtx and 0xf0 <= xf_addr <= 0xfb:
                            fv = struct.unpack('>f', struct.pack('>I', value))[0]
                            print(f"  [pre-draw#{draw_count+1}] XF write IDENTITY-slot[{xf_addr-0xf0}] = 0x{value:08x} ({fv:.4f})")
                    elif 0x500 <= xf_addr <= 0x55f:
                        m = (xf_addr - 0x500) // 12
                        c = (xf_addr - 0x500) - m * 12
                        if m < 8:
                            xf_dual_tex_mtx[m][c] = value
                    elif xf_addr == 0x1018:
                        xf_matindex_a = value
                    elif xf_addr == 0x1019:
                        xf_matindex_b = value
            elif kind == "bp":
                cmd2, value = cmd[1], cmd[2]
                if cmd2 == 0x00:
                    bp_genmode = value
                elif 0x28 <= cmd2 <= 0x2f:
                    bp_tev_order[cmd2 - 0x28] = value
                elif 0x80 <= cmd2 <= 0x83:
                    bp_tx_setmode0[cmd2 - 0x80] = value
                elif 0xa0 <= cmd2 <= 0xa3:
                    bp_tx_setmode0[cmd2 - 0xa0 + 4] = value
                elif 0x84 <= cmd2 <= 0x87:
                    bp_tx_setmode1[cmd2 - 0x84] = value
                elif 0xa4 <= cmd2 <= 0xa7:
                    bp_tx_setmode1[cmd2 - 0xa4 + 4] = value
                elif 0x88 <= cmd2 <= 0x8b:
                    bp_tx_setimage0[cmd2 - 0x88] = value
                elif 0xa8 <= cmd2 <= 0xab:
                    bp_tx_setimage0[cmd2 - 0xa8 + 4] = value
                elif 0x94 <= cmd2 <= 0x97:
                    bp_tx_setimage3[cmd2 - 0x94] = value
                elif 0xb4 <= cmd2 <= 0xb7:
                    bp_tx_setimage3[cmd2 - 0xb4 + 4] = value
                elif 0xc0 <= cmd2 <= 0xdf:
                    idx = (cmd2 - 0xc0) >> 1
                    if (cmd2 & 1) == 0:
                        bp_tev_color_env[idx] = value
                    else:
                        bp_tev_alpha_env[idx] = value
                elif 0xe0 <= cmd2 <= 0xe7:
                    # 4 TEV regs × 2 sub-registers (RA, BG)
                    reg_idx = (cmd2 - 0xe0) >> 1
                    if (cmd2 & 1) == 0:
                        bp_tev_reg_ra[reg_idx] = value
                    else:
                        bp_tev_reg_bg[reg_idx] = value
            elif kind == "primitive":
                _, prim, vat, vsize, nverts, _vd = cmd
                draw_count += 1
                if args.list_draws:
                    print(f"  frame[{fi}] draw#{draw_count}: prim={PRIMITIVE_NAMES[prim]} vat={vat} vsize={vsize} nverts={nverts}")
                if args.around_vertex_count is not None and nverts == args.around_vertex_count:
                    print(f"\n--- frame[{fi}] draw#{draw_count} ({PRIMITIVE_NAMES[prim]} vat={vat} vsize={vsize} nverts={nverts}) effective state ---")
                    # GENMODE: num_textures (bits 4..7), num_tev_stages (bits 10..13), etc.
                    v = bp_genmode
                    if v is not None:
                        n_tex = (v >> 4) & 0xf
                        n_col = v & 0xf
                        n_tev = ((v >> 10) & 0xf) + 1
                        n_ind = (v >> 16) & 0x7
                        cull = (v >> 14) & 0x3
                        print(f"    BP GENMODE=0x{v:06x}  ncols={n_col} ntex={n_tex} ntev={n_tev} nind={n_ind} cull={cull}")
                    # XF SET_TEXMTXINFO[0]
                    v = xf_texmtxinfo[0]
                    if v is not None:
                        print(f"    XF SET_TEXMTXINFO[0]=0x{v:08x}  {decode_xf_settexmtxinfo(v)}")
                    else:
                        print(f"    XF SET_TEXMTXINFO[0]=<not loaded yet>")
                    # MATINDEX_A: bits 0-5=pos, 6-11=tex0, 12-17=tex1, 18-23=tex2, 24-29=tex3
                    # mtx index value: 30 = TEXMTX0 base (XF 0x000), 33=TEXMTX1, ..., 60=GX_IDENTITY
                    if xf_matindex_a is not None:
                        v = xf_matindex_a
                        pos_idx = v & 0x3f
                        tex0_idx = (v >> 6) & 0x3f
                        tex1_idx = (v >> 12) & 0x3f
                        tex2_idx = (v >> 18) & 0x3f
                        tex3_idx = (v >> 24) & 0x3f
                        def _mtxname(i):
                            if i == 60: return "IDENTITY"
                            if 30 <= i <= 51 and (i - 30) % 3 == 0:
                                return f"TEXMTX{(i-30)//3}"
                            return f"raw={i}"
                        print(f"    XF MATINDEX_A=0x{v:08x}  pos={_mtxname(pos_idx)} tex0={_mtxname(tex0_idx)} tex1={_mtxname(tex1_idx)} tex2={_mtxname(tex2_idx)} tex3={_mtxname(tex3_idx)}")
                    else:
                        print(f"    XF MATINDEX_A=<not loaded yet>")
                    # Decode the matrix actually used by tex0 from MATINDEX_A.
                    # Matrix index N -> XF address N*4 (3 rows × 4 floats).
                    def _show_mtx_at(label, base_addr):
                        rows = []
                        any_loaded = False
                        for r in range(3):
                            cols = []
                            for c in range(4):
                                w = xf_pos_area[base_addr + r*4 + c] if base_addr + r*4 + c < 256 else None
                                if w is None:
                                    cols.append("<>")
                                else:
                                    any_loaded = True
                                    cols.append(f"{struct.unpack('>f', struct.pack('>I', w))[0]:.4f}")
                            rows.append("[" + " ".join(cols) + "]")
                        marker = "" if any_loaded else "  <NOT LOADED, hardware reads stale/zero data>"
                        print(f"    XF {label} = " + " ".join(rows) + marker)
                    # POS matrix used (matindex_a bits 0-5)
                    if xf_matindex_a is not None:
                        pos_idx = xf_matindex_a & 0x3f
                        tex0_idx = (xf_matindex_a >> 6) & 0x3f
                        _show_mtx_at(f"POS_MTX (idx={pos_idx})", pos_idx * 4)
                        _show_mtx_at(f"TEX0_MTX (idx={tex0_idx})", tex0_idx * 4)
                    # TREF: only show stages that are referenced by an active TEV stage
                    print(f"    BP TREF (TEV_ORDER):")
                    for i, v in enumerate(bp_tev_order):
                        if v is None:
                            continue
                        for h in range(2):
                            stage = 2 * i + h
                            shift = 12 * h
                            tm = (v >> (shift + 0)) & 0x7
                            tc = (v >> (shift + 3)) & 0x7
                            en = (v >> (shift + 6)) & 0x1
                            cc = (v >> (shift + 7)) & 0x7
                            print(f"      stage{stage}: texmap={tm} texcoord={tc} tex_en={en} colorchan={cc}")
                    # Texture unit 0 image / mode / base
                    v = bp_tx_setimage0[0]
                    if v is not None:
                        w = (v & 0x3ff) + 1
                        h = ((v >> 10) & 0x3ff) + 1
                        fmt = (v >> 20) & 0xf
                        print(f"    BP TX_SETIMAGE0_I0=0x{v:06x}  w={w} h={h} fmt={fmt}")
                    else:
                        print(f"    BP TX_SETIMAGE0_I0=<not loaded yet>")
                    v = bp_tx_setmode0[0]
                    if v is not None:
                        wrap_s = v & 0x3
                        wrap_t = (v >> 2) & 0x3
                        mag = (v >> 4) & 0x1
                        mipmap = (v >> 5) & 0x3
                        min_ = (v >> 7) & 0x1
                        lod_b = (v >> 9) & 0xff
                        if lod_b >= 0x80:
                            lod_b -= 0x100
                        print(f"    BP TX_SETMODE0_I0=0x{v:06x}  "
                              f"wrap_s={wrap_s} wrap_t={wrap_t} mag={mag} min={min_} mipmap={mipmap} lod_b={lod_b}")
                    else:
                        print(f"    BP TX_SETMODE0_I0=<not loaded yet>")
                    v = bp_tx_setmode1[0]
                    if v is not None:
                        min_lod = v & 0xff
                        max_lod = (v >> 8) & 0xff
                        print(f"    BP TX_SETMODE1_I0=0x{v:06x}  min_lod={min_lod} max_lod={max_lod}")
                    v = bp_tx_setimage3[0]
                    if v is not None:
                        addr = (v & 0xffffff) << 5
                        print(f"    BP TX_SETIMAGE3_I0=0x{v:06x}  base=0x{addr:08x}")
                    # TEV stage 0 color/alpha env
                    v = bp_tev_color_env[0]
                    if v is not None:
                        sela = v & 0xf
                        selb = (v >> 4) & 0xf
                        selc = (v >> 8) & 0xf
                        seld = (v >> 12) & 0xf
                        bias = (v >> 16) & 0x3
                        op   = (v >> 18) & 0x1
                        clamp= (v >> 19) & 0x1
                        scale= (v >> 20) & 0x3
                        dest = (v >> 22) & 0x3
                        print(f"    BP TEV_COLOR_ENV[0]=0x{v:06x}  "
                              f"a={sela} b={selb} c={selc} d={seld} "
                              f"bias={bias} op={op} clamp={clamp} scale={scale} dest={dest}")
                    v = bp_tev_alpha_env[0]
                    if v is not None:
                        rswap = v & 0x3
                        tswap = (v >> 2) & 0x3
                        sela = (v >> 4) & 0x7
                        selb = (v >> 7) & 0x7
                        selc = (v >> 10) & 0x7
                        seld = (v >> 13) & 0x7
                        bias = (v >> 16) & 0x3
                        op   = (v >> 18) & 0x1
                        clamp= (v >> 19) & 0x1
                        scale= (v >> 20) & 0x3
                        dest = (v >> 22) & 0x3
                        print(f"    BP TEV_ALPHA_ENV[0]=0x{v:06x}  "
                              f"rswap={rswap} tswap={tswap} a={sela} b={selb} c={selc} d={seld} "
                              f"bias={bias} op={op} clamp={clamp} scale={scale} dest={dest}")
                    # TEV registers REG0..REG3 (color & const).  REG0 is what
                    # ColorArg::C0(=2) reads.  Sign-extended 11-bit channel
                    # values; type bit picks Color vs Konst.
                    for ri in range(4):
                        ra = bp_tev_reg_ra[ri]; bg = bp_tev_reg_bg[ri]
                        if ra is None and bg is None: continue
                        def _s11(x):
                            x &= 0x7ff
                            return x - 0x800 if x & 0x400 else x
                        if ra is not None:
                            r = _s11(ra & 0x7ff)
                            a = _s11((ra >> 12) & 0x7ff)
                            t = (ra >> 23) & 1
                        else: r=a=t=None
                        if bg is not None:
                            b_ = _s11(bg & 0x7ff)
                            g = _s11((bg >> 12) & 0x7ff)
                        else: b_=g=None
                        print(f"    BP TEV_REG{ri}: RA=0x{ra if ra else 0:06x} BG=0x{bg if bg else 0:06x}  "
                              f"R={r} G={g} B={b_} A={a} type={t}")
                    # vtx_desc / vtx_attr for the active VAT (= the one this draw uses)
                    print(f"    CP vtx_desc_lo=0x{cp.vtx_desc_lo:08x}  vtx_desc_hi=0x{cp.vtx_desc_hi:08x}")
                    a, b, c = cp.vtx_attr[vat]
                    print(f"    CP vtx_attr[vat={vat}]: a=0x{a:08x} b=0x{b:08x} c=0x{c:08x}")
                    # CP ARRAY_BASE/STRIDE indexing per CPMemory.h::CPArray:
                    # 0=POS 1=NRM 2=COLOR0 3=COLOR1 4=TEX0 ... 11=TEX7.
                    _slot_names = {0:"POS", 1:"NRM", 2:"COLOR0", 3:"COLOR1",
                                   4:"TEX0", 5:"TEX1", 6:"TEX2", 7:"TEX3",
                                   8:"TEX4", 9:"TEX5", 10:"TEX6", 11:"TEX7"}
                    for si in range(12):
                        b = cp.array_base[si]
                        st = cp.array_stride[si]
                        if b or st:
                            print(f"    CP ARRAY[{_slot_names[si]}]: base=0x{b:08x} stride={st}")
                    # Per-vertex raw bytes (for INDEX modes these are the
                    # array indices we'd need to look up to get coords/UVs).
                    print(f"    vertex_data ({nverts} × {vsize}B):")
                    for vi in range(nverts):
                        vrow = _vd[vi*vsize:(vi+1)*vsize]
                        # Decode standard (POS_idx16, NRM_idx16, TEX0_idx16) layout
                        if vsize == 6:
                            pos_idx = struct.unpack(">H", vrow[0:2])[0]
                            nrm_idx = struct.unpack(">H", vrow[2:4])[0]
                            tex_idx = struct.unpack(">H", vrow[4:6])[0]
                            print(f"      v{vi}: POS_idx={pos_idx} NRM_idx={nrm_idx} TEX0_idx={tex_idx}  raw={vrow.hex()}")
                        else:
                            print(f"      v{vi}: {vrow.hex()}")
                    print()
            elif kind == "trunc":
                print(f"  frame[{fi}]: TRUNCATED at {cmd[2]} (opcode 0x{cmd[1]:02x})")
                break
        print(f"  frame[{fi}]: {draw_count} draw calls total")


if __name__ == "__main__":
    main()
