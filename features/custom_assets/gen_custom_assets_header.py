#!/usr/bin/env python3
"""Generate generated_custom_assets.h + custom TPL files + Riivolution XML
fragment from features/cups.yaml.

Reads the same cup-centric yaml as gen_cup_courses_header.py. For each cup
that declares an `assets:` section, emits:
  - CustomResourceEntry rows in kCustomResourceTable[]
  - CupBinding rows in kBindings[] (g_cupId match → replace vanilla id w/ custom id)
  - PNG → TPL (RGBA32) under files/<stem>.tpl
  - <file> records in generated_riivolution.xml so Riivolution copies the TPL
    into the disc tree

Custom resource id allocation per cup:
  base = 0x9000 + cup_index * IDS_PER_CUP
  + 0  -> icon              (CUPsel02 atlas tile, cup-indexed)
  + 1  -> name              (CUPname banner, cup-indexed)
  + 2  -> trophy_locked     (trophy_01_locked atlas, cup-indexed)
  + 3  -> banner            (CUPsel01_a banner slot, NOT cup-indexed)
  + 4  -> cup_name_ribbon   (CUPsel02 diagonal name strip, cup-indexed)
  + 5  -> name_roundselect  (round-select cup-name strip)
  + 6  -> course1_thumb_road (round-select course 1 vertical thumb 128x160)
  + 7  -> course2_thumb_road (round-select course 2 vertical thumb)
  + 8  -> course1_thumb      (round-select course 1 square thumb 128x128)
  + 9  -> course2_thumb      (round-select course 2 square thumb)
  + 10..15 -> reserved
Cup index = position in cups[] (NOT cup_id), so removing/reordering cups
shifts ids. Bindings are recomputed from the same yaml so vanilla
sees no change.

Vanilla resource ids that get replaced (cup-indexed slots emit 8 bindings,
one per page-3 tile cursor position 0..7; non-cup-indexed slots emit 1):
  icon              -> 0x1777 + K   (K in 0..7)
  name              -> 0x1729 + K
  trophy_locked     -> 0x1EA2 + K
  banner            -> 0x175E       (single global id)
  cup_name_ribbon   -> 0x1780 + K
  name_roundselect  -> 0x16ED       (single, COURSEname01 atlas crop)
  course1_thumb_road -> 0x19E0      (single, ROUNDsel02_YP atlas)
  course2_thumb_road -> 0x19E1
  course1_thumb     -> 0x1A66       (single, ROUNDsel03_YP atlas)
  course2_thumb     -> 0x1A67
For round-select slots, g_cupId must be 17 at the time the round-select
scene queries these resource ids. Currently cup_page3 only forces
g_cupId=17 during cup-select page 3 hover; a separate round-select hook
on clFlowRound_Init (0x801caf34) is needed to keep g_cupId=17 for the
round-select scene to pick up these bindings (TODO).

Bindings are gated on g_cupId == cup.cup_id. cup_page3 writes
g_cupId = cup.cup_id on page 2 entry (CupForwardTransition 1->2) and
resets to 0 on page 2 exit / cup-select scene init, so the binding only
fires while the player is actually looking at page 3 or racing the cup.

Custom group_keys (>= 0x9000) route through kCustomPathTable to the
freshly-encoded TPLs — vanilla path table (PTR_s_adjust_tpl_80350508) is
not touched.
"""

import struct
import sys
import zlib
from pathlib import Path

import yaml
from PIL import Image


FEATURE_DIR = Path(__file__).resolve().parent
CUPS_YAML   = FEATURE_DIR.parent / "cups.yaml"   # features/cups.yaml
FILES_DIR   = FEATURE_DIR / "files"
OUTPUT_H    = FEATURE_DIR / "generated_custom_assets.h"
OUTPUT_XML  = FEATURE_DIR / "generated_riivolution.xml"

CUSTOM_ID_BASE       = 0x9000
CUSTOM_GROUPKEY_BASE = 0x9000   # must match custom_assets.h
IDS_PER_CUP          = 16       # per-cup reserved id block
U16_MAX = 0xFFFF

# Per-slot meta. (yaml key, vanilla resource id base, atlas size, slot offset
# inside a cup's reserved 8-id block, cup_indexed flag).
# cup_indexed = True   -> binding.from = vanilla_base + K for K in 0..7
# cup_indexed = False  -> binding.from = vanilla_base (single global id; the
#                         cup_id gate still scopes which cup triggers it).
ASSET_SLOTS = [
    # key                , vanilla_base, default_size, slot_off, cup_indexed
    ("icon"              , 0x1777, (128.0, 128.0),  0, True),
    ("name"              , 0x1729, (256.0,  46.0),  1, True),
    ("trophy"            , 0x1EA2, ( 92.0,  86.0),  2, True),
    ("banner"            , 0x175E, (301.0, 125.0),  3, False),
    # Diagonal cup-name ribbon shown only on the hovered tile in cup-select.
    # Vanilla 0x1780..0x1787 in CUPsel02 atlas (148x64, group_key 0x0445).
    ("cup_name_ribbon"   , 0x1780, (148.0,  64.0),  4, True),
    # --- Round-select scene assets (require g_cupId == 17 in clFlowRound_*) ---
    # Cup-name strip top-left of round-select (COURSEname01 atlas crop).
    ("name_roundselect"  , 0x16ED, (110.0,  67.0),  5, False),
    # Course thumbnails. Vertical 128x160 = ROUNDsel02_YP, square 128x128 =
    # ROUNDsel03_YP. Course1 vs Course2 differ in source vanilla id.
    ("course1_thumb_road", 0x19E0, (128.0, 160.0),  6, False),
    ("course2_thumb_road", 0x19E1, (128.0, 160.0),  7, False),
    ("course1_thumb"     , 0x1A66, (128.0, 128.0),  8, False),
    ("course2_thumb"     , 0x1A67, (128.0, 128.0),  9, False),
]


def fatal(msg):
    print(f"gen_custom_assets: ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


# ---- TPL encoder (RGBA32 format 6, single image, no mipmaps) --------------

def _rgba32_encode(rgba_bytes, w, h):
    """Pack row-major RGBA (w*h*4 bytes) into tiled 4x4 RGBA32.

    Each 4x4 tile = 64 bytes: first 32 = AR pairs (a[0..15], r[0..15]
    interleaved 2 bytes per pixel), then 32 = GB pairs. Tiles arranged
    left-to-right, top-to-bottom. Dimensions padded up to multiple of 4;
    padding pixels contribute transparent black.
    """
    pad_w = (w + 3) & ~3
    pad_h = (h + 3) & ~3
    out = bytearray()
    for ty in range(0, pad_h, 4):
        for tx in range(0, pad_w, 4):
            ar = bytearray(32)
            gb = bytearray(32)
            for py in range(4):
                for px in range(4):
                    x = tx + px
                    y = ty + py
                    if x < w and y < h:
                        off = (y * w + x) * 4
                        r, g, b, a = rgba_bytes[off:off + 4]
                    else:
                        r = g = b = a = 0
                    k = (py * 4 + px) * 2
                    ar[k], ar[k + 1] = a, r
                    gb[k], gb[k + 1] = g, b
            out.extend(ar)
            out.extend(gb)
    return bytes(out)


def _build_tpl_rgba32(w, h, rgba_bytes):
    """Return raw (uncompressed) TPL bytes for a single RGBA32 image."""
    img_off = 0x14
    data_off = 0x40
    pixel_bytes = _rgba32_encode(rgba_bytes, w, h)
    hdr = bytearray()
    hdr += struct.pack(">I", 0x0020AF30)   # magic
    hdr += struct.pack(">I", 1)            # num_images
    hdr += struct.pack(">I", 0x0C)         # tbl_off
    hdr += struct.pack(">II", img_off, 0)  # image table entry: img_off, pal_off
    hdr += struct.pack(">HH", h, w)        # height, width
    hdr += struct.pack(">I",  6)           # format: RGBA32
    hdr += struct.pack(">I",  data_off)
    hdr += struct.pack(">I",  1)           # wrap_s = repeat
    hdr += struct.pack(">I",  1)           # wrap_t = repeat
    hdr += struct.pack(">I",  1)           # min_filter (linear)
    hdr += struct.pack(">I",  1)           # mag_filter (linear)
    hdr += struct.pack(">f",  0.0)         # lod_bias
    hdr += struct.pack(">BBBB", 0, 0, 0, 0)
    # 12 (outer) + 8 (img table entry) + 36 (image descriptor) = 56 bytes.
    # data_off=0x40 (64) leaves 8 bytes of zero padding before pixel data.
    assert len(hdr) == 56, f"header size {len(hdr)} != 56"
    hdr += b"\x00" * (data_off - len(hdr))
    return bytes(hdr) + pixel_bytes


def _wrap_tpl_envelope(raw_tpl):
    """Wrap in the (u32 LE uncomp_size, u32 pad, zlib stream) envelope the
    MKGP2 DVD loader expects."""
    payload = zlib.compress(raw_tpl, 9)
    return struct.pack("<II", len(raw_tpl), 0) + payload


def encode_png_to_tpl(png_path, out_path):
    img = Image.open(png_path).convert("RGBA")
    w, h = img.size
    rgba = img.tobytes()
    raw_tpl = _build_tpl_rgba32(w, h, rgba)
    out_bytes = _wrap_tpl_envelope(raw_tpl)
    out_path.write_bytes(out_bytes)
    return w, h


# ---- yaml driver ----------------------------------------------------------

def collect_assets(cups):
    """Walk cups[] and produce flat lists for asset entries + bindings + tpl
    encodes. Returns (assets, bindings, custom_paths, alias_map)."""
    assets = []
    bindings = []
    custom_paths = []   # index = group_key - CUSTOM_GROUPKEY_BASE
    alias_map = []      # [{custom_cup_id, alias_vanilla_cup_id, _cup_ident}, ...]
    next_gk = CUSTOM_GROUPKEY_BASE

    for cup_idx, cup in enumerate(cups):
        cup_loc = f"cups[{cup_idx}]"
        if not isinstance(cup, dict):
            fatal(f"{cup_loc}: must be a mapping")
        cup_ident = cup.get("id") or cup_loc
        cup_id = cup.get("cup_id")
        if not isinstance(cup_id, int):
            fatal(f"{cup_loc}.cup_id required (int)")
        alias = cup.get("display_alias_cup", 0)
        if not isinstance(alias, int) or not (0 <= alias <= 7):
            fatal(f"{cup_loc}.display_alias_cup must be int 0..7, got {alias!r}")

        # Custom cups (cup_id >= 17) drive the round-select g_cupId swap.
        # Vanilla cups (cup_id 0..15) don't need an alias entry.
        if cup_id >= 17:
            alias_map.append({
                "custom_cup_id":       cup_id,
                "alias_vanilla_cup":   alias,
                "_cup_ident":          cup_ident,
            })

        a_section = cup.get("assets")
        if a_section is None:
            continue
        if not isinstance(a_section, dict):
            fatal(f"{cup_loc}.assets must be a mapping")

        for key, vanilla_base, default_size, slot_off, cup_indexed in ASSET_SLOTS:
            png_rel = a_section.get(key)
            if png_rel is None:
                continue
            png_path = (FEATURE_DIR / png_rel).resolve()
            if not png_path.is_file():
                fatal(f"{cup_loc}.assets.{key}: '{png_rel}' not found at {png_path}")

            custom_id = CUSTOM_ID_BASE + cup_idx * IDS_PER_CUP + slot_off
            if custom_id > U16_MAX:
                fatal(f"{cup_loc}.assets.{key}: custom id 0x{custom_id:x} overflows u16")

            # Optional explicit size; default from atlas slot. Use PIL to
            # validate against actual PNG dimensions.
            with Image.open(png_path) as im:
                pw, ph = im.size

            gk = next_gk
            next_gk += 1
            tpl_filename = f"mkgp2_custom_{custom_id:04x}.tpl"
            custom_paths.append(tpl_filename)

            assets.append({
                "id":           custom_id,
                "group_key":    gk,
                "slot_index":   0,
                # next_id: keep vanilla alpha-mask sibling chain alive for the
                # icon/name/trophy slots so the alpha overlay still preloads.
                # Vanilla pattern: 0x1777 -> 0x178B alpha, 0x1729 -> 0x1736,
                # 0x1EA2 -> 0x1EBA. We mirror that by computing
                # next = vanilla.next_id chain head + alias offset, but until
                # we sweep that, just terminate (-1) — alpha overlay will fail
                # silently (acceptable for MVP).
                "next_id":      -1,
                "flags":        4,
                "offset":       (0.0, 0.0),
                "size":         (float(pw), float(ph)),
                "scale":        (1.0, 1.0),
                "png_path":     png_path,
                "tpl_filename": tpl_filename,
                "_meta_key":    key,
                "_cup_ident":   cup_ident,
                "_cup_id":      cup_id,
            })

            # Binding: when g_cupId == cup.cup_id, intercept the vanilla id
            # and serve the custom id. Cup-indexed slots emit 8 bindings
            # (one per cursor position 0..7) all routing to the same custom
            # id — page 3 is a "single-cup grid" where every tile shows
            # test_cup regardless of its position-native vanilla id.
            if cup_indexed:
                positions = range(8)
            else:
                positions = (0,)
            for pos in positions:
                bindings.append({
                    "cup_id":  cup_id,
                    "from":    vanilla_base + pos if cup_indexed else vanilla_base,
                    "to":      custom_id,
                    "source":  f"{cup_loc}({cup_ident}).assets.{key}"
                               + (f" [pos={pos}]" if cup_indexed else ""),
                })

    return assets, bindings, custom_paths, alias_map


# ---- emitters -------------------------------------------------------------

def emit_header(assets, bindings, custom_paths, alias_map):
    lines = []
    lines.append("// GENERATED by gen_custom_assets_header.py — do not edit.")
    lines.append("// Source: features/cups.yaml")
    lines.append("#ifndef GENERATED_CUSTOM_ASSETS_H")
    lines.append("#define GENERATED_CUSTOM_ASSETS_H")
    lines.append("")
    lines.append('#include "custom_assets.h"')
    lines.append("")

    lines.append("const CustomResourceEntry kCustomResourceTable[] = {")
    for a in assets:
        ox, oy = a["offset"]
        sx, sy = a["size"]
        cx, cy = a["scale"]
        ni = a["next_id"]
        ni_str = f"0x{ni:04x}" if ni >= 0 else str(ni)
        lines.append(
            f"    // {a['_cup_ident']} (cupId={a['_cup_id']}) {a['_meta_key']}"
        )
        lines.append("    {")
        lines.append(f"        /* self_id    */ 0x{a['id']:04x},")
        lines.append(f"        /* pad_02     */ 0,")
        lines.append(f"        /* offset_x   */ {ox!r}f,")
        lines.append(f"        /* offset_y   */ {oy!r}f,")
        lines.append(f"        /* size_x     */ {sx!r}f,")
        lines.append(f"        /* size_y     */ {sy!r}f,")
        lines.append(f"        /* slot_index */ {a['slot_index']},")
        lines.append(f"        /* group_key  */ 0x{a['group_key']:04x},")
        lines.append(f"        /* next_id    */ {ni_str},")
        lines.append(f"        /* pad_1a     */ 0,")
        lines.append(f"        /* scale_x    */ {cx!r}f,")
        lines.append(f"        /* scale_y    */ {cy!r}f,")
        lines.append(f"        /* flags      */ {a['flags']},")
        lines.append(f"        /* pad_tail   */ {{0,0,0}},")
        lines.append("    },")
    if not assets:
        lines.append("    { 0, 0, 0.0f, 0.0f, 1.0f, 1.0f, 0, 0, -1, 0, 1.0f, 1.0f, 0, {0,0,0} }, // sentinel")
    lines.append("};")
    lines.append(f"const unsigned int kCustomResourceCount = {len(assets)}u;")
    lines.append("")

    lines.append("const CupBinding kBindings[] = {")
    for b in bindings:
        lines.append(
            f"    {{ /*cupId*/ {b['cup_id']}, "
            f"/*from*/ 0x{b['from']:04x}, "
            f"/*to*/ 0x{b['to']:04x}, "
            f"0 }}, // {b['source']}"
        )
    if not bindings:
        lines.append("    { 0, 0, 0, 0 }, // sentinel")
    lines.append("};")
    lines.append(f"const unsigned int kBindingCount = {len(bindings)}u;")
    lines.append("")

    lines.append("const char* const kCustomPathTable[] = {")
    for i, name in enumerate(custom_paths):
        if name is None:
            lines.append(f"    0,  // gap @ 0x{CUSTOM_GROUPKEY_BASE + i:04x}")
        else:
            lines.append(f'    "{name}",  // 0x{CUSTOM_GROUPKEY_BASE + i:04x}')
    if not custom_paths:
        lines.append("    0,  // sentinel")
    lines.append("};")
    lines.append(f"const unsigned int kCustomPathCount = {len(custom_paths)}u;")
    lines.append("")

    lines.append("// Maps each custom cupId to a vanilla cupId whose tables we mimic.")
    lines.append("// Drives features/round_select g_cupId swap (OOB-safe table reads).")
    lines.append("const CupAliasEntry kCupAliasMap[] = {")
    for e in alias_map:
        lines.append(
            f"    {{ /*custom*/ {e['custom_cup_id']}, "
            f"/*alias*/ {e['alias_vanilla_cup']}, "
            f"{{0,0}} }}, // {e['_cup_ident']}"
        )
    if not alias_map:
        lines.append("    { 0, 0, {0,0} }, // sentinel")
    lines.append("};")
    lines.append(f"const unsigned int kCupAliasMapCount = {len(alias_map)}u;")
    lines.append("")

    lines.append("#endif")
    lines.append("")
    return "\n".join(lines)


def emit_riivolution_xml(custom_paths):
    lines = []
    for name in custom_paths:
        if name is None:
            continue
        lines.append(f'<file disc="/{name}" external="/mkgp2_patch/{name}" create="true"/>')
    return "\n".join(lines) + ("\n" if lines else "")


# ---- driver ---------------------------------------------------------------

def main():
    if not CUPS_YAML.exists():
        fatal(f"missing {CUPS_YAML}")
    doc = yaml.safe_load(CUPS_YAML.read_text(encoding="utf-8")) or {}
    cups = doc.get("cups") or []
    if not isinstance(cups, list):
        fatal("cups.yaml: 'cups' must be a list")

    assets, bindings, custom_paths, alias_map = collect_assets(cups)

    FILES_DIR.mkdir(exist_ok=True)
    encoded = 0
    for a in assets:
        tpl_path = FILES_DIR / a["tpl_filename"]
        encode_png_to_tpl(a["png_path"], tpl_path)
        encoded += 1

    OUTPUT_H.write_text(emit_header(assets, bindings, custom_paths, alias_map),
                        encoding="utf-8")
    OUTPUT_XML.write_text(emit_riivolution_xml(custom_paths), encoding="utf-8")

    print(f"Generated {OUTPUT_H.name}: "
          f"{len(assets)} asset(s), {len(bindings)} binding(s), "
          f"{encoded} custom TPL(s), {len(alias_map)} alias entry(s)")


if __name__ == "__main__":
    main()
