#!/usr/bin/env python3
"""Dump the MKGP2 resource node table and filename PTR array directly from main.dol.

This bypasses MCP memory reads (thousands of calls) by parsing the DOL binary
itself. The resource node table lives at 0x80422208 (0x2B00 entries of 40 bytes)
and the filename pointer array at 0x80350508 (length determined by max groupKey).

Output: tools/resource_table.txt with every used entry and its resolved filename,
plus a summary of used/unused ID ranges.
"""
import os
import struct

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOL_PATH = r"C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\sys\main.dol"
OUT_PATH = os.path.join(SCRIPT_DIR, "resource_table.txt")

NORMAL_TABLE_ADDR = 0x80422208
NORMAL_TABLE_COUNT = 0x2B00
ENTRY_SIZE = 0x28

EXTENDED_TABLE_ADDR = 0x8048DA08
EXTENDED_TABLE_COUNT = 4

PTR_TABLE_ADDR = 0x80350508
EXT_PTR_TABLE_ADDR = 0x8034A418  # for id >= 0x2B00 direct index


def parse_dol(path):
    """Return (segments, addr_to_off fn). segments = [(file_off, ram_addr, size)]."""
    d = open(path, "rb").read()
    text_offs = struct.unpack(">7I", d[0x00:0x1C])
    data_offs = struct.unpack(">11I", d[0x1C:0x48])
    text_addrs = struct.unpack(">7I", d[0x48:0x64])
    data_addrs = struct.unpack(">11I", d[0x64:0x90])
    text_sizes = struct.unpack(">7I", d[0x90:0xAC])
    data_sizes = struct.unpack(">11I", d[0xAC:0xD8])
    segs = []
    for foff, addr, size in zip(
        list(text_offs) + list(data_offs),
        list(text_addrs) + list(data_addrs),
        list(text_sizes) + list(data_sizes),
    ):
        if foff and size:
            segs.append((foff, addr, size))
    return d, segs


def make_reader(dol, segs):
    def read_bytes(addr, length):
        for foff, base, size in segs:
            if base <= addr and addr + length <= base + size:
                rel = addr - base
                return dol[foff + rel:foff + rel + length]
        raise KeyError(f"0x{addr:08X} not mapped (requested {length} bytes)")
    return read_bytes


def read_cstring(read_bytes, addr, max_len=256):
    for length in (32, 64, 128, 256, max_len):
        try:
            chunk = read_bytes(addr, length)
        except KeyError:
            return None
        nul = chunk.find(b"\x00")
        if nul >= 0:
            return chunk[:nul].decode("ascii", "replace")
    return None


def parse_normal_entry(data):
    """Parse 40-byte entry. Fields identified so far."""
    self_id = struct.unpack(">H", data[0x00:0x02])[0]
    scale_x = struct.unpack(">f", data[0x04:0x08])[0]
    scale_y = struct.unpack(">f", data[0x08:0x0C])[0]
    flags = struct.unpack(">I", data[0x0C:0x10])[0]
    f10 = struct.unpack(">f", data[0x10:0x14])[0]
    group_key = struct.unpack(">h", data[0x16:0x18])[0]  # signed
    next_id = struct.unpack(">h", data[0x18:0x1A])[0]    # signed
    f1c = struct.unpack(">f", data[0x1C:0x20])[0]
    f20 = struct.unpack(">f", data[0x20:0x24])[0]
    return {
        "self_id": self_id, "scale_x": scale_x, "scale_y": scale_y,
        "flags": flags, "f10": f10,
        "group_key": group_key, "next_id": next_id,
        "f1c": f1c, "f20": f20,
    }


def main():
    dol, segs = parse_dol(DOL_PATH)
    read_bytes = make_reader(dol, segs)

    # 1. Dump normal anim-node table
    entries = []
    table_blob = read_bytes(NORMAL_TABLE_ADDR, NORMAL_TABLE_COUNT * ENTRY_SIZE)
    for i in range(NORMAL_TABLE_COUNT):
        e = parse_normal_entry(table_blob[i * ENTRY_SIZE:(i + 1) * ENTRY_SIZE])
        e["id"] = i
        entries.append(e)

    # 2. Determine PTR array extent: max groupKey across all entries
    max_gk = max((e["group_key"] for e in entries if e["group_key"] >= 0), default=0)
    print(f"max groupKey observed: {max_gk} (0x{max_gk:X})")

    # Read PTR array up to max_gk (with a bit of slack to detect tail)
    ptr_count = max_gk + 4
    ptr_blob = read_bytes(PTR_TABLE_ADDR, ptr_count * 4)
    path_ptrs = struct.unpack(f">{ptr_count}I", ptr_blob)

    # Resolve each group_key -> filename
    groupkey_to_name = {}
    for gk in range(ptr_count):
        ptr = path_ptrs[gk]
        if ptr == 0:
            continue
        name = read_cstring(read_bytes, ptr)
        if name is not None:
            groupkey_to_name[gk] = name

    # 3. Dump extended table (4 entries)
    ext_entries = []
    ext_blob = read_bytes(EXTENDED_TABLE_ADDR, EXTENDED_TABLE_COUNT * ENTRY_SIZE)
    for i in range(EXTENDED_TABLE_COUNT):
        e = parse_normal_entry(ext_blob[i * ENTRY_SIZE:(i + 1) * ENTRY_SIZE])
        e["slot"] = i
        ext_entries.append(e)

    # 4. Extended PTR table for id >= 0x2B00 (direct index)
    #    Size unknown; read a reasonable window and stop at first NULL.
    ext_ptr_blob = read_bytes(EXT_PTR_TABLE_ADDR, 0x200 * 4)  # 512 entries worth
    ext_ptrs = struct.unpack(">512I", ext_ptr_blob)
    ext_id_to_name = {}
    for idx, ptr in enumerate(ext_ptrs):
        if ptr == 0:
            continue
        name = read_cstring(read_bytes, ptr)
        if name:
            ext_id_to_name[idx] = name

    # 5. Usage analysis
    used_ids = [e for e in entries if e["self_id"] != 0 or e["group_key"] != 0 or e["next_id"] != 0]
    unused_count = NORMAL_TABLE_COUNT - len(used_ids)

    # Find contiguous "unused" ranges (all zero)
    unused_ranges = []
    run_start = None
    for e in entries:
        is_zero = e["self_id"] == 0 and e["group_key"] == 0 and e["next_id"] == 0 and e["flags"] == 0
        if is_zero and run_start is None:
            run_start = e["id"]
        elif not is_zero and run_start is not None:
            unused_ranges.append((run_start, e["id"] - 1))
            run_start = None
    if run_start is not None:
        unused_ranges.append((run_start, NORMAL_TABLE_COUNT - 1))
    unused_ranges = [(a, b) for a, b in unused_ranges if b - a + 1 >= 16]  # only runs >= 16

    # 6. Write report
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(f"# MKGP2 Resource Table dump (from {DOL_PATH})\n")
        f.write(f"# Normal table:   0x{NORMAL_TABLE_ADDR:08X}  {NORMAL_TABLE_COUNT} entries × {ENTRY_SIZE} bytes\n")
        f.write(f"# Extended table: 0x{EXTENDED_TABLE_ADDR:08X}  {EXTENDED_TABLE_COUNT} entries × {ENTRY_SIZE} bytes\n")
        f.write(f"# PTR array:      0x{PTR_TABLE_ADDR:08X}  {ptr_count} pointers (groupKey indexed)\n")
        f.write(f"# Extended PTR:   0x{EXT_PTR_TABLE_ADDR:08X}  (id-0x2B00 indexed, scanned 512 slots)\n")
        f.write(f"# used entries: {len(used_ids)}, unused: {unused_count} "
                f"({100*unused_count/NORMAL_TABLE_COUNT:.1f}%)\n")
        f.write(f"# distinct groupKeys referenced: {len(set(e['group_key'] for e in used_ids))}\n")
        f.write(f"# max groupKey: {max_gk}, PTR slots resolved to filename: {len(groupkey_to_name)}\n\n")

        f.write("# ==== Large unused ID ranges (contiguous zero entries, len >= 16) ====\n")
        for a, b in unused_ranges:
            f.write(f"  0x{a:04X}..0x{b:04X}  ({b - a + 1} slots)\n")
        f.write("\n")

        f.write("# ==== Extended table (id >= 0x2B00, 4 slots) ====\n")
        for e in ext_entries:
            name = groupkey_to_name.get(e["group_key"], "<no name>")
            f.write(f"  slot {e['slot']}  key=0x{e['self_id']:04X}  groupKey={e['group_key']}  "
                    f"nextId=0x{e['next_id']:04X}  flags=0x{e['flags']:08X}  "
                    f"file={name}\n")
        f.write("\n")

        f.write("# ==== Extended PTR table samples (id - 0x2B00 direct index) ====\n")
        for idx in sorted(ext_id_to_name):
            f.write(f"  id=0x{0x2B00 + idx:04X} (+{idx})  {ext_id_to_name[idx]}\n")
            if idx > 32:
                f.write("  ... (truncated; full list has "
                        f"{len(ext_id_to_name)} entries)\n")
                break
        f.write("\n")

        f.write("# ==== All used resource IDs (0x0000..0x2AFF) ====\n")
        f.write("# format: id  self_id  groupKey  nextId  flags  scale  f10  pos(x,y)  file\n")
        for e in entries:
            if e["self_id"] == 0 and e["group_key"] == 0 and e["next_id"] == 0 and e["flags"] == 0:
                continue
            name = groupkey_to_name.get(e["group_key"], "") if e["group_key"] >= 0 else ""
            f.write(f"0x{e['id']:04X}  self=0x{e['self_id']:04X}  gk={e['group_key']:5d}  "
                    f"next={e['next_id']:6d}  flags=0x{e['flags']:08X}  "
                    f"scale=({e['scale_x']:.3g},{e['scale_y']:.3g})  f10={e['f10']:.3g}  "
                    f"pos=({e['f1c']:.3g},{e['f20']:.3g})  {name}\n")

    print(f"wrote {OUT_PATH}")
    print(f"  used entries: {len(used_ids)} / {NORMAL_TABLE_COUNT}")
    print(f"  distinct groupKeys: {len(set(e['group_key'] for e in used_ids))}")
    print(f"  groupKey -> filename resolved: {len(groupkey_to_name)}")
    print(f"  extended PTR files: {len(ext_id_to_name)}")
    print(f"  large unused ID ranges: {len(unused_ranges)}")


if __name__ == "__main__":
    main()
