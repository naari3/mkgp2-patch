"""Extract a GameCube/Triforce GCM ISO to <out_dir>/{files,sys}/.

Why this exists: DolphinTool refuses Triforce ISOs with
"Unknown volume type", so we re-implement the GCM file system traversal
ourselves. Format is well-documented (YAGCD) and Triforce inherits the
GameCube DiscHeader / Apploader / FST layout verbatim, so this works
unchanged for both.

Usage:
  python extract_gc_iso.py <iso> <out_dir>

Layout produced (matches Dolphin's `Tools > Filesystem > Extract Entire
System`):
  <out_dir>/sys/boot.bin            DiscHeader (0x440 bytes)
  <out_dir>/sys/bi2.bin             Disc identifier 2 (0x2000 bytes)
  <out_dir>/sys/apploader.img       Apploader (header + binary + trailer)
  <out_dir>/sys/main.dol            DOL executable
  <out_dir>/sys/fst.bin             FileSystem Table
  <out_dir>/files/...               All game files in their FST hierarchy
"""

import os
import struct
import sys
from pathlib import Path


def u32(buf, off):
    return struct.unpack_from(">I", buf, off)[0]


def read_at(f, offset, size):
    f.seek(offset)
    return f.read(size)


def extract(iso_path, out_dir):
    iso_path = Path(iso_path)
    out_dir = Path(out_dir)

    sys_dir = out_dir / "sys"
    files_dir = out_dir / "files"
    sys_dir.mkdir(parents=True, exist_ok=True)
    files_dir.mkdir(parents=True, exist_ok=True)

    with open(iso_path, "rb") as f:
        # DiscHeader (boot.bin) is the first 0x440 bytes.
        boot = read_at(f, 0, 0x440)
        magic = u32(boot, 0x1C)
        if magic != 0xC2339F3D:
            raise ValueError(
                f"Not a GameCube/Triforce GCM (magic=0x{magic:08X}); "
                "expected 0xC2339F3D"
            )

        dol_offset = u32(boot, 0x420)
        fst_offset = u32(boot, 0x424)
        fst_size = u32(boot, 0x428)

        # ---- sys/ files --------------------------------------------
        (sys_dir / "boot.bin").write_bytes(boot)
        (sys_dir / "bi2.bin").write_bytes(read_at(f, 0x440, 0x2000))

        # Apploader: header(0x20) + main(u32 size) + trailer(u32 trailer_size).
        # Total length = 0x20 + main_size + trailer_size, aligned to 0x20.
        ap_header = read_at(f, 0x2440, 0x20)
        ap_main_size = u32(ap_header, 0x14)
        ap_trailer_size = u32(ap_header, 0x18)
        apploader_size = 0x20 + ap_main_size + ap_trailer_size
        # Round to 4 byte alignment for safety
        apploader_size = (apploader_size + 0x1F) & ~0x1F
        (sys_dir / "apploader.img").write_bytes(
            read_at(f, 0x2440, apploader_size)
        )

        # main.dol
        dol_header = read_at(f, dol_offset, 0x100)
        dol_size = 0x100  # header
        for i in range(7):  # 7 text sections
            offset = u32(dol_header, 0x00 + i * 4)
            size = u32(dol_header, 0x90 + i * 4)
            if offset + size > dol_size:
                dol_size = offset + size
        for i in range(11):  # 11 data sections
            offset = u32(dol_header, 0x1C + i * 4)
            size = u32(dol_header, 0xAC + i * 4)
            if offset + size > dol_size:
                dol_size = offset + size
        (sys_dir / "main.dol").write_bytes(read_at(f, dol_offset, dol_size))

        # fst.bin
        fst = read_at(f, fst_offset, fst_size)
        (sys_dir / "fst.bin").write_bytes(fst)

        # ---- files/ via FST ----------------------------------------
        # Entry layout (12 bytes each):
        #   +0x00 type (u8): 0=file, 1=directory
        #   +0x01 name offset (u24)
        #   +0x04 file: file_offset; dir: parent_index
        #   +0x08 file: file_size;   dir: next_index (== first index after
        #                                     this directory's contents)
        # Entry 0 is the root directory; entry[0].next_index = total entries.
        n_entries = u32(fst, 0x08)
        name_table_off = n_entries * 12

        def name_of(idx):
            name_off = u32(fst, idx * 12) & 0x00FFFFFF
            end = fst.index(b"\x00", name_table_off + name_off)
            return fst[name_table_off + name_off:end].decode("ascii")

        # Stack of (until_index, on_disk_path) describing the
        # directory we're currently inside. We exit a directory once
        # `i` reaches its `until_index`.
        stack = [(n_entries, files_dir)]
        i = 1  # entry 0 is the root dummy
        n_files = 0
        n_dirs = 0
        while i < n_entries:
            while i >= stack[-1][0]:
                stack.pop()
            entry_type = fst[i * 12]
            name = name_of(i)
            cur_dir = stack[-1][1]
            if entry_type == 1:
                # Directory: next_index is the first entry AFTER us.
                next_idx = u32(fst, i * 12 + 8)
                child_dir = cur_dir / name
                child_dir.mkdir(parents=True, exist_ok=True)
                stack.append((next_idx, child_dir))
                n_dirs += 1
                i += 1
            else:
                file_off = u32(fst, i * 12 + 4)
                file_size = u32(fst, i * 12 + 8)
                data = read_at(f, file_off, file_size)
                (cur_dir / name).write_bytes(data)
                n_files += 1
                i += 1

    print(f"Extracted {n_files} files in {n_dirs} directories")
    print(f"  sys/      -> {sys_dir}")
    print(f"  files/    -> {files_dir}")


def main():
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <iso> <out_dir>")
        sys.exit(1)
    extract(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()
