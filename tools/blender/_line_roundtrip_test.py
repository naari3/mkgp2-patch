"""Non-Blender round-trip test for _line.bin parse/write logic.

Replicates the parsing from blender_import_line.py and the write from
blender_export_line.py, but skips the Blender object layer. Use to
verify that the logic produces byte-identical output before relying on
it from Blender.

Run: python _line_roundtrip_test.py <path-to-line.bin>
"""

import struct
import sys


RECORD_SIZE = 16
TRAILING_SIZE = 12


def u32(data, off): return struct.unpack_from(">I", data, off)[0]
def i32(data, off): return struct.unpack_from(">i", data, off)[0]
def f32(data, off): return struct.unpack_from(">f", data, off)[0]


def parse(data):
    offsets = []
    p = 0
    while p + 4 <= len(data):
        v = u32(data, p)
        p += 4
        if v == 0:
            break
        offsets.append(v)
    trailing = tuple(u32(data, len(data) - TRAILING_SIZE + 4 * i) for i in range(3))

    variants = []
    for i, off in enumerate(offsets):
        end = offsets[i + 1] if i + 1 < len(offsets) else len(data) - TRAILING_SIZE
        wps = []
        term_value = None
        q = off
        while q + RECORD_SIZE <= end:
            first = i32(data, q)
            if first < 0:
                term_value = first
                q += RECORD_SIZE
                break
            wps.append((u32(data, q), f32(data, q+4), f32(data, q+8), f32(data, q+12)))
            q += RECORD_SIZE
        variants.append((wps, term_value if term_value is not None else -len(wps)))
    return variants, trailing


def write(variants, trailing):
    n = len(variants)
    header_size = 4 * (n + 1)
    offsets = []
    cursor = header_size
    blobs = []
    for wps, term in variants:
        offsets.append(cursor)
        buf = bytearray()
        for t, x, y, z in wps:
            buf += struct.pack(">I", t & 0xffffffff)
            buf += struct.pack(">f", x)
            buf += struct.pack(">f", y)
            buf += struct.pack(">f", z)
        buf += struct.pack(">i", term)
        buf += b"\x00" * 12
        blobs.append(buf)
        cursor += len(buf)

    out = bytearray()
    for off in offsets:
        out += struct.pack(">I", off)
    out += b"\x00\x00\x00\x00"
    for b in blobs:
        out += b
    for v in trailing:
        out += struct.pack(">I", v & 0xffffffff)
    return bytes(out)


def main():
    path = sys.argv[1]
    with open(path, "rb") as f:
        original = f.read()
    variants, trailing = parse(original)
    produced = write(variants, trailing)

    print(f"input  : {len(original)} bytes")
    print(f"output : {len(produced)} bytes")
    print(f"variants: {len(variants)}")
    for i, (wps, term) in enumerate(variants):
        print(f"  [{i}] {len(wps)} waypoints, terminator={term}")
    print(f"trailing: {trailing}")

    if original == produced:
        print("MATCH: byte-identical round-trip")
    else:
        print("DIFFER: first mismatch at offset", next(
            j for j in range(min(len(original), len(produced)))
            if original[j] != produced[j]
        ))


if __name__ == "__main__":
    main()
