"""Verify the 'variant 0-5 share constant Y, variant 6 has real Y' observation
across vanilla _line.bin files.

Output per file:
    name | per-variant {min Y, max Y, unique Y count}
"""

import struct
import sys
from pathlib import Path


def parse(path):
    data = path.read_bytes()
    # Header: variant_offsets[N] terminated by 0
    offsets = []
    p = 0
    while True:
        v = struct.unpack_from(">I", data, p)[0]
        p += 4
        if v == 0:
            break
        offsets.append(v)

    variants = []
    for off in offsets:
        ys = []
        cur = off
        while True:
            first = struct.unpack_from(">i", data, cur)[0]
            if first < 0:
                break  # terminator
            # Waypoint: u32 type, f32 x, f32 y, f32 z
            x, y, z = struct.unpack_from(">fff", data, cur + 4)
            ys.append(y)
            cur += 0x10
        variants.append(ys)

    return variants


def summarize(name, variants):
    print(f"\n{name}  ({len(variants)} variants)")
    for i, ys in enumerate(variants):
        if not ys:
            print(f"  v{i}: <empty>")
            continue
        uniq = sorted(set(ys))
        marker = " <-- LAP/REAL" if i == 6 else ""
        if len(uniq) == 1:
            print(f"  v{i}: count={len(ys):4d}  Y = {uniq[0]:>10.3f} (constant){marker}")
        else:
            print(
                f"  v{i}: count={len(ys):4d}  Y range = [{min(ys):>10.3f}, {max(ys):>10.3f}]  uniq={len(uniq)}{marker}"
            )


def main():
    root = Path(r"C:/Users/naari/Documents/Dolphin ROMs/Triforce/mkgp2/files")
    files = sorted(root.glob("*_line.bin"))
    # exclude *_b_line.bin (battle) for clarity
    files = [f for f in files if not f.stem.endswith("_b_line")]
    for f in files:
        try:
            variants = parse(f)
            summarize(f.name, variants)
        except Exception as e:
            print(f"\n{f.name}: ERR {e}")


if __name__ == "__main__":
    main()
