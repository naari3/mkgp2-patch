#!/usr/bin/env python3
"""Scan every .tpl in the MKGP2 ISO dump and list image metadata.

MKGP2 stores each TPL as:
    u32 LE uncompressed_size
    u32     pad (0)
    zlib stream
decompressed -> standard GC TPL (magic 0x0020AF30).

Writes tools/tpl_index.txt with one line per file. Use --extract ID / --png ID
to dump a single PNG, or --extract-all DIR for a full mirror (IA4/RGB5A3/
RGB565/RGBA32/CMPR supported; unknown formats skipped).
"""
import argparse
import os
import struct
import sys
import zlib

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FILES_DIR = r"C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files"
INDEX_PATH = os.path.join(SCRIPT_DIR, "tpl_index.txt")

sys.path.insert(0, SCRIPT_DIR)
from fst_lookup import parse as parse_fst  # noqa: E402

FMTS = {
    0: "I4", 1: "I8", 2: "IA4", 3: "IA8",
    4: "RGB565", 5: "RGB5A3", 6: "RGBA32",
    8: "C4", 9: "C8", 10: "C14X2", 14: "CMPR",
}


def read_raw(name):
    """Return (uncompressed TPL bytes, compressed?) or (None, reason)."""
    path = os.path.join(FILES_DIR, name)
    if not os.path.isfile(path):
        return None, "missing"
    d = open(path, "rb").read()
    if len(d) >= 4 and d[:4] == b"\x00\x20\xAF\x30":
        return d, False
    if len(d) >= 10 and d[8:10] == b"\x78\xDA":
        try:
            return zlib.decompress(d[8:]), True
        except zlib.error as e:
            return None, f"zlib err: {e}"
    return None, f"unknown header {d[:4].hex()}"


def parse_tpl(raw):
    """Return list of (width, height, fmt_code, data_off, pal_off) for each image."""
    if len(raw) < 12 or raw[:4] != b"\x00\x20\xAF\x30":
        return None
    num, tbl_off = struct.unpack(">II", raw[4:12])
    out = []
    for i in range(num):
        img_off, pal_off = struct.unpack(">II", raw[tbl_off + i*8:tbl_off + i*8 + 8])
        if img_off + 12 > len(raw):
            break
        h, w, fmt, data_off = struct.unpack(">HHII", raw[img_off:img_off + 12])
        out.append((w, h, fmt, data_off, pal_off))
    return out


# ---------- PNG writer (no external deps) ----------

def _png_chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))


def write_png(path, width, height, rgba):
    assert len(rgba) == width * height * 4
    header = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    raw = b""
    stride = width * 4
    for y in range(height):
        raw += b"\x00" + rgba[y * stride:(y + 1) * stride]
    idat = zlib.compress(raw, 9)
    with open(path, "wb") as f:
        f.write(header)
        f.write(_png_chunk(b"IHDR", ihdr))
        f.write(_png_chunk(b"IDAT", idat))
        f.write(_png_chunk(b"IEND", b""))


# ---------- GC texture format decoders -> RGBA ----------

def _i4(data, w, h):
    out = bytearray(w * h * 4)
    ti_w, ti_h = 8, 8
    for ty in range(0, h, ti_h):
        for tx in range(0, w, ti_w):
            for py in range(ti_h):
                for px in range(0, ti_w, 2):
                    b = data[((ty // ti_h) * (w // ti_w) + tx // ti_w) * (ti_w * ti_h // 2)
                             + py * (ti_w // 2) + px // 2]
                    for n, nib in enumerate((b >> 4, b & 0xF)):
                        x, y = tx + px + n, ty + py
                        if x < w and y < h:
                            v = (nib << 4) | nib
                            o = (y * w + x) * 4
                            out[o:o+4] = bytes((v, v, v, 0xFF))
    return bytes(out)


def _i8(data, w, h):
    out = bytearray(w * h * 4)
    ti_w, ti_h = 8, 4
    idx = 0
    for ty in range(0, h, ti_h):
        for tx in range(0, w, ti_w):
            for py in range(ti_h):
                for px in range(ti_w):
                    v = data[idx]; idx += 1
                    x, y = tx + px, ty + py
                    if x < w and y < h:
                        o = (y * w + x) * 4
                        out[o:o+4] = bytes((v, v, v, 0xFF))
    return bytes(out)


def _ia4(data, w, h):
    out = bytearray(w * h * 4)
    ti_w, ti_h = 8, 4
    idx = 0
    for ty in range(0, h, ti_h):
        for tx in range(0, w, ti_w):
            for py in range(ti_h):
                for px in range(ti_w):
                    b = data[idx]; idx += 1
                    a = (b >> 4) * 0x11
                    i = (b & 0xF) * 0x11
                    x, y = tx + px, ty + py
                    if x < w and y < h:
                        o = (y * w + x) * 4
                        out[o:o+4] = bytes((i, i, i, a))
    return bytes(out)


def _ia8(data, w, h):
    out = bytearray(w * h * 4)
    ti_w, ti_h = 4, 4
    idx = 0
    for ty in range(0, h, ti_h):
        for tx in range(0, w, ti_w):
            for py in range(ti_h):
                for px in range(ti_w):
                    a = data[idx]; i = data[idx+1]; idx += 2
                    x, y = tx + px, ty + py
                    if x < w and y < h:
                        o = (y * w + x) * 4
                        out[o:o+4] = bytes((i, i, i, a))
    return bytes(out)


def _rgb565(data, w, h):
    out = bytearray(w * h * 4)
    ti_w, ti_h = 4, 4
    idx = 0
    for ty in range(0, h, ti_h):
        for tx in range(0, w, ti_w):
            for py in range(ti_h):
                for px in range(ti_w):
                    v = (data[idx] << 8) | data[idx+1]; idx += 2
                    r = ((v >> 11) & 0x1F) * 0xFF // 0x1F
                    g = ((v >> 5) & 0x3F) * 0xFF // 0x3F
                    b = (v & 0x1F) * 0xFF // 0x1F
                    x, y = tx + px, ty + py
                    if x < w and y < h:
                        o = (y * w + x) * 4
                        out[o:o+4] = bytes((r, g, b, 0xFF))
    return bytes(out)


def _rgb5a3(data, w, h):
    out = bytearray(w * h * 4)
    ti_w, ti_h = 4, 4
    idx = 0
    for ty in range(0, h, ti_h):
        for tx in range(0, w, ti_w):
            for py in range(ti_h):
                for px in range(ti_w):
                    v = (data[idx] << 8) | data[idx+1]; idx += 2
                    if v & 0x8000:
                        r = ((v >> 10) & 0x1F) * 0xFF // 0x1F
                        g = ((v >> 5) & 0x1F) * 0xFF // 0x1F
                        b = (v & 0x1F) * 0xFF // 0x1F
                        a = 0xFF
                    else:
                        a = ((v >> 12) & 0x7) * 0xFF // 0x7
                        r = ((v >> 8) & 0xF) * 0x11
                        g = ((v >> 4) & 0xF) * 0x11
                        b = (v & 0xF) * 0x11
                    x, y = tx + px, ty + py
                    if x < w and y < h:
                        o = (y * w + x) * 4
                        out[o:o+4] = bytes((r, g, b, a))
    return bytes(out)


def _rgba32(data, w, h):
    out = bytearray(w * h * 4)
    ti_w, ti_h = 4, 4
    idx = 0
    for ty in range(0, h, ti_h):
        for tx in range(0, w, ti_w):
            # AR pairs (32 bytes) then GB pairs (32 bytes)
            ar = data[idx:idx + 32]; gb = data[idx + 32:idx + 64]; idx += 64
            for py in range(ti_h):
                for px in range(ti_w):
                    k = (py * ti_w + px) * 2
                    a = ar[k]; r = ar[k + 1]
                    g = gb[k]; b = gb[k + 1]
                    x, y = tx + px, ty + py
                    if x < w and y < h:
                        o = (y * w + x) * 4
                        out[o:o+4] = bytes((r, g, b, a))
    return bytes(out)


def _cmpr_sub(out, w, h, x0, y0, block):
    c0 = (block[0] << 8) | block[1]
    c1 = (block[2] << 8) | block[3]

    def decode(c):
        r = ((c >> 11) & 0x1F) * 0xFF // 0x1F
        g = ((c >> 5) & 0x3F) * 0xFF // 0x3F
        b = (c & 0x1F) * 0xFF // 0x1F
        return (r, g, b)
    r0, g0, b0 = decode(c0)
    r1, g1, b1 = decode(c1)
    if c0 > c1:
        pal = [
            (r0, g0, b0, 0xFF),
            (r1, g1, b1, 0xFF),
            ((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3, 0xFF),
            ((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3, 0xFF),
        ]
    else:
        pal = [
            (r0, g0, b0, 0xFF),
            (r1, g1, b1, 0xFF),
            ((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2, 0xFF),
            (0, 0, 0, 0),
        ]
    idx = (block[4] << 24) | (block[5] << 16) | (block[6] << 8) | block[7]
    for py in range(4):
        for px in range(4):
            shift = (15 - (py * 4 + px)) * 2
            p = (idx >> shift) & 0x3
            x, y = x0 + px, y0 + py
            if x < w and y < h:
                o = (y * w + x) * 4
                out[o:o+4] = bytes(pal[p])


def _cmpr(data, w, h):
    out = bytearray(w * h * 4)
    ti_w, ti_h = 8, 8
    idx = 0
    for ty in range(0, h, ti_h):
        for tx in range(0, w, ti_w):
            for sy in range(0, ti_h, 4):
                for sx in range(0, ti_w, 4):
                    if idx + 8 > len(data):
                        return bytes(out)
                    block = data[idx:idx + 8]; idx += 8
                    _cmpr_sub(out, w, h, tx + sx, ty + sy, block)
    return bytes(out)


DECODERS = {
    0: _i4, 1: _i8, 2: _ia4, 3: _ia8,
    4: _rgb565, 5: _rgb5a3, 6: _rgba32, 14: _cmpr,
}


def _decode_palette(data, count, pal_fmt):
    """GameCube TPL palette → RGBA bytes. fmt 0=IA8, 1=RGB565, 2=RGB5A3."""
    out = bytearray(count * 4)
    for i in range(count):
        v = (data[i * 2] << 8) | data[i * 2 + 1]
        if pal_fmt == 0:
            a = data[i * 2]; inten = data[i * 2 + 1]
            out[i * 4:i * 4 + 4] = bytes((inten, inten, inten, a))
        elif pal_fmt == 1:
            r = ((v >> 11) & 0x1F) * 0xFF // 0x1F
            g = ((v >> 5) & 0x3F) * 0xFF // 0x3F
            b = (v & 0x1F) * 0xFF // 0x1F
            out[i * 4:i * 4 + 4] = bytes((r, g, b, 0xFF))
        else:  # RGB5A3
            if v & 0x8000:
                r = ((v >> 10) & 0x1F) * 0xFF // 0x1F
                g = ((v >> 5) & 0x1F) * 0xFF // 0x1F
                b = (v & 0x1F) * 0xFF // 0x1F
                a = 0xFF
            else:
                a = ((v >> 12) & 0x7) * 0xFF // 0x7
                r = ((v >> 8) & 0xF) * 0x11
                g = ((v >> 4) & 0xF) * 0x11
                b = (v & 0xF) * 0x11
            out[i * 4:i * 4 + 4] = bytes((r, g, b, a))
    return bytes(out)


def _c4(data, w, h, pal):
    out = bytearray(w * h * 4)
    ti_w, ti_h = 8, 8
    idx = 0
    for ty in range(0, h, ti_h):
        for tx in range(0, w, ti_w):
            for py in range(ti_h):
                for px in range(0, ti_w, 2):
                    b = data[idx]; idx += 1
                    for n, nib in enumerate((b >> 4, b & 0xF)):
                        x, y = tx + px + n, ty + py
                        if x < w and y < h:
                            o = (y * w + x) * 4
                            out[o:o+4] = pal[nib * 4:nib * 4 + 4]
    return bytes(out)


def _c8(data, w, h, pal):
    out = bytearray(w * h * 4)
    ti_w, ti_h = 8, 4
    idx = 0
    for ty in range(0, h, ti_h):
        for tx in range(0, w, ti_w):
            for py in range(ti_h):
                for px in range(ti_w):
                    pi = data[idx]; idx += 1
                    x, y = tx + px, ty + py
                    if x < w and y < h:
                        o = (y * w + x) * 4
                        out[o:o+4] = pal[pi * 4:pi * 4 + 4]
    return bytes(out)


def decode_image(raw, w, h, fmt, data_off, pal_off=0):
    if fmt in (8, 9) and pal_off:
        pal_count = struct.unpack(">H", raw[pal_off:pal_off + 2])[0]
        pal_fmt = struct.unpack(">I", raw[pal_off + 4:pal_off + 8])[0]
        pal_data_off = struct.unpack(">I", raw[pal_off + 8:pal_off + 12])[0]
        pal_data = raw[pal_data_off:pal_data_off + pal_count * 2]
        pal_rgba = _decode_palette(pal_data, pal_count, pal_fmt)
        if fmt == 8:
            return _c4(raw[data_off:], w, h, pal_rgba)
        return _c8(raw[data_off:], w, h, pal_rgba)
    if fmt not in DECODERS:
        return None
    return DECODERS[fmt](raw[data_off:], w, h)


# ---------- driver ----------

def build_index(verbose=False):
    fst = parse_fst()
    rows = []
    for i, flag, name, off, size in fst:
        if flag or not name.lower().endswith(".tpl"):
            continue
        raw, meta = read_raw(name)
        if raw is None:
            rows.append((i, name, size, None, meta))
            continue
        images = parse_tpl(raw)
        rows.append((i, name, size, images, "zlib" if meta else "raw"))
        if verbose and i % 500 == 0:
            print(f"  scanned {i}", file=sys.stderr)
    return rows


def format_row(row):
    i, name, size, images, note = row
    head = f"0x{i:04X} ({i:5d}) {name}  fst_size={size}  [{note}]"
    if images is None:
        return head
    parts = []
    for (w, h, fmt, off, _pal) in images:
        parts.append(f"{w}x{h} {FMTS.get(fmt, f'fmt{fmt}')}")
    return head + "  " + "; ".join(parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--id", type=lambda s: int(s, 0))
    ap.add_argument("--extract", type=lambda s: int(s, 0), help="PNG for one ID")
    ap.add_argument("--extract-all", metavar="DIR", help="PNG for every TPL")
    ap.add_argument("--out", default=None, help="PNG output path (with --extract)")
    args = ap.parse_args()

    if args.extract is not None:
        fst = parse_fst()
        i, flag, name, off, size = fst[args.extract]
        raw, note = read_raw(name)
        if raw is None:
            print(f"error: {note}", file=sys.stderr); sys.exit(1)
        images = parse_tpl(raw) or []
        if not images:
            print("no images", file=sys.stderr); sys.exit(1)
        w, h, fmt, data_off, pal_off = images[0]
        rgba = decode_image(raw, w, h, fmt, data_off, pal_off)
        if rgba is None:
            print(f"unsupported format {fmt} ({FMTS.get(fmt)})", file=sys.stderr); sys.exit(1)
        out = args.out or os.path.join(SCRIPT_DIR, f"{os.path.splitext(name)[0]}.png")
        write_png(out, w, h, rgba)
        print(f"wrote {out} ({w}x{h} {FMTS.get(fmt)})")
        return

    if args.extract_all:
        os.makedirs(args.extract_all, exist_ok=True)
        fst = parse_fst()
        ok = skip = fail = 0
        for i, flag, name, off, size in fst:
            if flag or not name.lower().endswith(".tpl"):
                continue
            raw, note = read_raw(name)
            if raw is None:
                fail += 1; continue
            images = parse_tpl(raw)
            if not images:
                skip += 1; continue
            w, h, fmt, data_off = images[0]
            rgba = decode_image(raw, w, h, fmt, data_off)
            if rgba is None:
                skip += 1; continue
            out = os.path.join(args.extract_all, f"{i:04X}_{os.path.splitext(name)[0]}.png")
            try:
                write_png(out, w, h, rgba)
                ok += 1
            except Exception as e:
                print(f"fail {name}: {e}", file=sys.stderr); fail += 1
            if (ok + skip + fail) % 500 == 0:
                print(f"  {ok} ok / {skip} skip / {fail} fail", file=sys.stderr)
        print(f"done: {ok} ok, {skip} skip (unsupported fmt / empty), {fail} fail")
        return

    if args.id is not None:
        rows = build_index()
        for r in rows:
            if r[0] == args.id:
                print(format_row(r)); return
        print("not a TPL or not found", file=sys.stderr); sys.exit(1)

    rows = build_index(verbose=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        f.write(f"# MKGP2 TPL index (files/ scan, {len(rows)} entries)\n")
        f.write(f"# Source: {FILES_DIR}\n")
        f.write("# Format: 0xHEX (decimal) <name>  fst_size=... [note]  WxH fmt [; ...]\n\n")
        for r in rows:
            f.write(format_row(r) + "\n")
    print(f"wrote {INDEX_PATH} ({len(rows)} TPL entries)")


if __name__ == "__main__":
    main()
