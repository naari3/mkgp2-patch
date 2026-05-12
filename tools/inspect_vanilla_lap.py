"""Inspect vanilla course (line.bin v6 wp0/wp63) + start_positions geometry.

Vanilla start_positions live at 0x8040b934 (stride 0x8a pointers). We can't
read them without a running game, so this script just reports the lap-line
geometry from v6 of each line.bin. Use it to compare with vanilla
start_positions reported via game/mkgp2-view.
"""

import struct
import sys
from pathlib import Path


def parse(path):
    data = path.read_bytes()
    p = 0
    offsets = []
    while True:
        v = struct.unpack_from(">I", data, p)[0]
        p += 4
        if v == 0:
            break
        offsets.append(v)
    variants = []
    for off in offsets:
        pts = []
        cur = off
        while True:
            first = struct.unpack_from(">i", data, cur)[0]
            if first < 0:
                break
            x, y, z = struct.unpack_from(">fff", data, cur + 4)
            pts.append((x, y, z))
            cur += 0x10
        variants.append(pts)
    return variants


def report(name, v6):
    if not v6:
        print(f"{name}: v6 empty")
        return
    n = len(v6)
    print(f"\n{name}  (v6: {n} wp)")
    print(f"  wp  0: ({v6[0][0]:8.1f}, {v6[0][1]:6.1f}, {v6[0][2]:8.1f})")
    print(f"  wp  1: ({v6[1][0]:8.1f}, {v6[1][1]:6.1f}, {v6[1][2]:8.1f})")
    print(f"  wp{n-1:3d}: ({v6[-1][0]:8.1f}, {v6[-1][1]:6.1f}, {v6[-1][2]:8.1f})")
    # Tangent at wp 0 (= average of (wp0-wp{N-1}) and (wp1-wp0))
    tx = ((v6[0][0] - v6[-1][0]) + (v6[1][0] - v6[0][0])) / 2
    tz = ((v6[0][2] - v6[-1][2]) + (v6[1][2] - v6[0][2])) / 2
    import math
    mag = math.hypot(tx, tz)
    print(f"  wp 0 tangent: ({tx:+.1f}, {tz:+.1f}) mag={mag:.1f}  dir={math.degrees(math.atan2(tx, tz)):.0f}deg from +Z")
    # Distance wp 0 to wp{N-1}
    d = math.hypot(v6[0][0]-v6[-1][0], v6[0][2]-v6[-1][2])
    print(f"  wp 0 <-> wp {n-1} distance: {d:.1f}")


def main():
    root = Path(r"C:/Users/naari/Documents/Dolphin ROMs/Triforce/mkgp2/files")
    targets = sys.argv[1:] or [
        "mr_highway_short_line.bin",
        "mr_highway_long_line.bin",
        "ge_kt_line.bin",
        "ds_silenthill_line.bin",
        "dm_stadium_short_line.bin",
    ]
    for name in targets:
        f = root / name
        if not f.exists():
            print(f"{name}: NOT FOUND")
            continue
        variants = parse(f)
        if len(variants) >= 7:
            report(name, variants[6])
        else:
            print(f"{name}: only {len(variants)} variants")


if __name__ == "__main__":
    main()
