#!/usr/bin/env python3
"""MKGP2 FST (vanilla ISO only) lookup / dump.

Parses ~/Documents/Dolphin ROMs/Triforce/mkgp2/sys/fst.bin and lets you
resolve FST index <-> filename. PreloadResource(id) in MKGP2 takes an
FST entry index directly (confirmed: id 0x1A6C == tpl2_sysFONT_24_154_a.tpl),
so this is the authoritative vanilla ID table.

Run with no args to regenerate tools/fst_dump.txt (full listing).
"""
import argparse
import os
import struct
import sys

FST_PATH = r"C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\sys\fst.bin"
DUMP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fst_dump.txt")


def parse(path=FST_PATH):
    with open(path, "rb") as f:
        d = f.read()
    total = struct.unpack(">I", d[8:12])[0]
    names = d[total * 12:]
    out = []
    for i in range(total):
        e = d[i * 12:(i + 1) * 12]
        flag = e[0]
        noff = struct.unpack(">I", b"\x00" + e[1:4])[0]
        a = struct.unpack(">I", e[4:8])[0]
        b = struct.unpack(">I", e[8:12])[0]
        end = names.find(b"\x00", noff)
        name = names[noff:end].decode("ascii", "replace") if noff < len(names) else ""
        out.append((i, flag, name, a, b))
    return out


def fmt(e):
    i, flag, name, a, b = e
    if flag:
        return f"0x{i:04X} ({i:5d}) [DIR] {name}  parent={a} next={b}"
    return f"0x{i:04X} ({i:5d})       {name}  off=0x{a:08X} size={b}"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--id", type=lambda s: int(s, 0), help="ID -> name (hex OK)")
    ap.add_argument("--name", help="substring match (case-insensitive) -> IDs")
    ap.add_argument("--range", nargs=2, type=lambda s: int(s, 0),
                    metavar=("LO", "HI"), help="dump [LO, HI] range")
    ap.add_argument("--dump", action="store_true",
                    help=f"write full listing to {DUMP_PATH}")
    args = ap.parse_args()

    entries = parse()

    if args.id is not None:
        if args.id >= len(entries):
            print(f"out of range (total={len(entries)})", file=sys.stderr); sys.exit(1)
        print(fmt(entries[args.id]))
    elif args.name:
        needle = args.name.lower()
        for e in entries:
            if needle in e[2].lower():
                print(fmt(e))
    elif args.range:
        lo, hi = args.range
        for e in entries[lo:hi + 1]:
            print(fmt(e))
    elif args.dump:
        with open(DUMP_PATH, "w", encoding="utf-8") as f:
            f.write(f"# MKGP2 vanilla FST dump (total={len(entries)} entries)\n")
            f.write(f"# Source: {FST_PATH}\n")
            f.write("# Format: 0xHEX (decimal) [DIR?] <name>  off=... size=...\n")
            f.write("# PreloadResource(id) takes ID as FST entry index.\n\n")
            for e in entries:
                f.write(fmt(e) + "\n")
        print(f"wrote {DUMP_PATH} ({len(entries)} entries)")
    else:
        # Default: regenerate dump
        args.dump = True
        with open(DUMP_PATH, "w", encoding="utf-8") as f:
            f.write(f"# MKGP2 vanilla FST dump (total={len(entries)} entries)\n")
            f.write(f"# Source: {FST_PATH}\n")
            f.write("# Format: 0xHEX (decimal) [DIR?] <name>  off=... size=...\n")
            f.write("# PreloadResource(id) takes ID as FST entry index.\n\n")
            for e in entries:
                f.write(fmt(e) + "\n")
        print(f"wrote {DUMP_PATH} ({len(entries)} entries)")


if __name__ == "__main__":
    main()
