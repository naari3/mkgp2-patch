#!/usr/bin/env python3
"""Extract Yoshi (cupId=0) round-select atlas crops to PNG.

Steps:
 1. Parse ResourceEntry structs from binary blobs dumped from main memory.
 2. Resolve groupKey -> TPL filename via the pointer table at 0x80350508.
 3. Load each needed TPL via tpl_dump helpers, decode to RGBA.
 4. Crop to (offset_x, offset_y, size_x, size_y).
 5. Merge RGB crop + alpha-atlas crop (alpha-atlas is I8/IA4; use R channel
    as alpha).
 6. Save to C:/Users/naari/Downloads/yoshi_cup_assets/<name>.png.
"""
import os
import struct
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from tpl_dump import read_raw, parse_tpl, decode_image, write_png  # noqa: E402

OUT_DIR = r"C:\Users\naari\Downloads\yoshi_cup_assets"
os.makedirs(OUT_DIR, exist_ok=True)

# --- Memory dumps --------------------------------------------------------

# kResourcePathTable slice: groupKey 0x042C..0x0488 (95 entries, 380 bytes).
# Source: main.dol @ 0x803515B8 (= 0x80350508 + 0x042C*4).
POINTER_TABLE_START_GK = 0x042C
POINTER_TABLE_HEX = (
    "80338a5c80338a7880338a9480338ab080338ac880338ae080338afc80338b18"
    "80338b3480338b5080338b6c80338b8880338ba480338bc480338be080338c00"
    "80338c1c80338c3c80338c5c80338c7c80338c9c80338cbc80338cd480338cf0"
    "80338d0880338d2480338d3c80338d5880338d7480338d9080338dac80338dc4"
    "80338de080338df880338e1480338e2c80338e4880338e6080338e7880338e90"
    "80338ea880338ec480338ee080338f0080338f2480338f3880338f5080338f68"
    "80338f8080338f9c80338fb880338fd480338fec8033900880339024803390408033905c"
    "8033907c80339098803390b4803390d0803390ec8033910c8033912c8033914c8033916c"
    "8033918c803391ac803391cc803391ec8033920c8033922c8033924c8033926c8033928c"
    "803392ac803392cc803392ec8033930c8033932c8033934c8033936c8033938c803393ac"
    "803393cc803393ec8033940c8033942c8033944c8033946c8033948c803394ac803394cc"
    "803394ec80339508"
)
POINTER_TABLE = bytes.fromhex(POINTER_TABLE_HEX)

# String pool covering all pointer targets. Source: main.dol @ 0x80338A5C.
STRING_POOL_BASE = 0x80338A5C


def load_string_pool():
    # Raw pool dumped from memory — large enough to cover every path we need.
    path = os.path.join(SCRIPT_DIR, "_string_pool_80338a5c.bin")
    if os.path.exists(path):
        return open(path, "rb").read()
    raise SystemExit(
        f"string pool not found at {path}; run dump script first")


STRING_POOL = load_string_pool()


def resolve_path(group_key):
    idx = group_key - POINTER_TABLE_START_GK
    if idx < 0 or idx * 4 + 4 > len(POINTER_TABLE):
        return None
    ptr = struct.unpack(">I", POINTER_TABLE[idx * 4:idx * 4 + 4])[0]
    off = ptr - STRING_POOL_BASE
    if off < 0 or off >= len(STRING_POOL):
        return None
    end = STRING_POOL.index(0, off)
    return STRING_POOL[off:end].decode("ascii")


# --- Asset definitions ---------------------------------------------------

# (label, rgb_id, rgb_group, offset_x, offset_y, size_x, size_y, alpha_group)
# Values from ResourceEntry table @ 0x80422208 (verified via ghidra read).
# Naming reflects user-confirmed visual roles after extracting Yoshi atlas.
ASSETS = [
    # Cup name strip shown top-left in round-select screen.
    # Source: COURSEname01.tpl despite the visual content being a cup name.
    ("test_cup_name_roundselect",    0x16ED, 0x0439, 0.0,   352.0, 110.0, 67.0,  0x043A),
    # Course 1 / Course 2 round thumbnails (road-shape, 128x160) —
    # populated via DAT_8049aea2 / DAT_8049aea6.
    ("test_cup_course1_thumb_road",  0x19E0, 0x0477, 256.0, 0.0,   128.0, 160.0, 0x0478),
    ("test_cup_course2_thumb_road",  0x19E1, 0x0477, 384.0, 0.0,   128.0, 160.0, 0x0478),
    # Course 1 / Course 2 round thumbnails (square 128x128) —
    # populated via DAT_8049aea0 / DAT_8049aea4.
    ("test_cup_course1_thumb",       0x1A66, 0x0487, 0.0,   0.0,   128.0, 128.0, 0x0488),
    ("test_cup_course2_thumb",       0x1A67, 0x0487, 128.0, 0.0,   128.0, 128.0, 0x0488),
    # --- Excluded from current scope ---
    # ura_indicator: shared "ウラ" tag from cardinfo02 atlas (not cup-specific).
    # ("ura_indicator",  0x15F4, 0x042C, 0.0,   0.0,   58.0,  64.0,  0x042D),
    # F (DAT_8039b308 offset 0): resolves to ROUNDsel03_MH atlas, NOT Yoshi —
    # table likely not cupId-indexed in the way assumed. Needs re-investigation
    # before binding.
    # ("F_icon_DAT_b308", 0x1A24, 0x047F, 256.0, 128.0, 82.0,  82.0,  0x0480),
]


# --- TPL load + decode cache --------------------------------------------

_tpl_cache = {}


def load_tpl_rgba(group_key):
    """Return (w, h, rgba_bytes) for first image of the TPL bound to group_key."""
    if group_key in _tpl_cache:
        return _tpl_cache[group_key]
    name = resolve_path(group_key)
    if not name:
        raise RuntimeError(f"group 0x{group_key:04X}: no filename")
    raw, note = read_raw(name)
    if raw is None:
        raise RuntimeError(f"group 0x{group_key:04X} ({name}): read_raw: {note}")
    images = parse_tpl(raw)
    if not images:
        raise RuntimeError(f"group 0x{group_key:04X} ({name}): no TPL images")
    w, h, fmt, data_off, pal_off = images[0]
    rgba = decode_image(raw, w, h, fmt, data_off, pal_off)
    if rgba is None:
        raise RuntimeError(f"group 0x{group_key:04X} ({name}): unsupported fmt {fmt}")
    _tpl_cache[group_key] = (w, h, rgba, name)
    return _tpl_cache[group_key]


# --- Crop + compose helpers ---------------------------------------------

def crop_rgba(rgba, w_src, h_src, x, y, cw, ch):
    out = bytearray(cw * ch * 4)
    for row in range(ch):
        src_y = y + row
        if src_y < 0 or src_y >= h_src:
            continue
        src_off = (src_y * w_src + x) * 4
        dst_off = row * cw * 4
        out[dst_off:dst_off + cw * 4] = rgba[src_off:src_off + cw * 4]
    return bytes(out)


def compose_alpha(rgb_rgba, alpha_rgba, w, h):
    """Copy .r from alpha atlas into .a of rgb atlas."""
    out = bytearray(rgb_rgba)
    for i in range(w * h):
        a = alpha_rgba[i * 4]  # grayscale -> R == G == B
        out[i * 4 + 3] = a
    return bytes(out)


# --- Driver -------------------------------------------------------------

def main():
    for label, rid, rgb_gk, ox, oy, sx, sy, alpha_gk in ASSETS:
        try:
            w_src, h_src, rgba_rgb, name_rgb = load_tpl_rgba(rgb_gk)
            w_alp, h_alp, rgba_alp, name_alp = load_tpl_rgba(alpha_gk)
        except Exception as e:
            print(f"[{label}] LOAD FAIL: {e}")
            continue

        ox_i, oy_i, sx_i, sy_i = int(ox), int(oy), int(sx), int(sy)
        crop_rgb = crop_rgba(rgba_rgb, w_src, h_src, ox_i, oy_i, sx_i, sy_i)
        crop_alp = crop_rgba(rgba_alp, w_alp, h_alp, ox_i, oy_i, sx_i, sy_i)
        merged = compose_alpha(crop_rgb, crop_alp, sx_i, sy_i)

        out_path = os.path.join(OUT_DIR, f"{label}.png")
        write_png(out_path, sx_i, sy_i, merged)
        print(f"[{label}] wrote {out_path} ({sx_i}x{sy_i})")
        print(f"    rgb   : gk=0x{rgb_gk:04X}  {name_rgb} ({w_src}x{h_src})")
        print(f"    alpha : gk=0x{alpha_gk:04X} {name_alp} ({w_alp}x{h_alp})")
        print(f"    crop  : ({ox_i},{oy_i}) size {sx_i}x{sy_i}")


if __name__ == "__main__":
    main()
