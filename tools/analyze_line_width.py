"""Estimate road width from _line.bin AI variants vs lap-path variant 6.

For each waypoint index in variant 6 (lap path = centerline), measure the
perpendicular spread of variants 0..5 (AI lines) at the same arc-length
position. Output min/median/max spread per file = an upper bound on the
drivable road width (AI lines stay within the road).

Caveats:
- AI lines aren't required to span the road's full width; they pick lines
  that fit each AI's behavior. So this is an *under*estimate of road width.
- variants 0..5 may have different waypoint counts than v6. We snap each
  v6 waypoint to the nearest waypoint of each v0..v5 by XZ distance.

Usage:
    python tools/analyze_line_width.py
"""

import math
import struct
import sys
from pathlib import Path


def parse(path):
    """Returns list of variants, each a list of (x, y, z) tuples."""
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


def nearest_xz(pt, pts):
    px, _, pz = pt
    best = None
    best_d = float("inf")
    for q in pts:
        d = (q[0] - px) ** 2 + (q[2] - pz) ** 2
        if d < best_d:
            best_d = d
            best = q
    return best, math.sqrt(best_d)


def analyze(name, variants):
    if len(variants) < 7:
        print(f"{name}: only {len(variants)} variants, skip")
        return
    v6 = variants[6]
    if not v6:
        print(f"{name}: v6 empty, skip")
        return
    ai_variants = [v for v in variants[:6] if v]
    if not ai_variants:
        print(f"{name}: no non-empty AI variants, skip")
        return

    # For each v6 waypoint, find nearest AI waypoint in each AI variant.
    # Spread = max distance among all (centerline -> nearest AI variant) pairs.
    spreads = []
    for cp in v6:
        ds = []
        for av in ai_variants:
            _, d = nearest_xz(cp, av)
            ds.append(d)
        # The widest AI line offset from centerline at this section
        spreads.append(max(ds))

    spreads_sorted = sorted(spreads)
    mn = spreads_sorted[0]
    mx = spreads_sorted[-1]
    med = spreads_sorted[len(spreads_sorted) // 2]
    mean = sum(spreads) / len(spreads)
    # Road half-width estimate = max AI offset, so road width >= 2*max.
    print(f"{name}")
    print(
        f"  v6={len(v6):4d} wp, ai variants={len(ai_variants)}: "
        f"max AI offset from centerline (per v6 wp): "
        f"min={mn:6.1f}, med={med:6.1f}, mean={mean:6.1f}, max={mx:6.1f}"
    )
    print(
        f"  road width LOWER BOUND ~= 2 * max = {2*mx:.1f} units"
    )
    # Also: spread between min/max AI X at each section (independent of v6)
    cross_spreads = []
    for i in range(min(len(v) for v in ai_variants)):
        xs = [v[i][0] for v in ai_variants]
        zs = [v[i][2] for v in ai_variants]
        # rough: bounding box diag at this wp index across AI variants
        bb = math.hypot(max(xs) - min(xs), max(zs) - min(zs))
        cross_spreads.append(bb)
    if cross_spreads:
        cs = sorted(cross_spreads)
        print(
            f"  cross-variant bbox at matched wp idx: "
            f"min={cs[0]:6.1f}, med={cs[len(cs)//2]:6.1f}, max={cs[-1]:6.1f}"
        )


def main():
    root = Path(r"C:/Users/naari/Documents/Dolphin ROMs/Triforce/mkgp2/files")
    targets = sys.argv[1:] or ["mr_highway_short_line.bin", "mr_highway_long_line.bin"]
    for name in targets:
        f = root / name
        if not f.exists():
            print(f"{name}: NOT FOUND under {root}")
            continue
        try:
            analyze(name, parse(f))
        except Exception as e:
            print(f"{name}: ERR {e}")
            raise


if __name__ == "__main__":
    main()
