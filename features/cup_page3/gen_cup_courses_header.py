#!/usr/bin/env python3
"""Generate generated_cup_courses.h from cup_courses.yaml.

Input: tracks-centric yaml (see cup_courses.yaml header for full schema).
Output: a header that emits all the static data + Kamek records the
patch needs:
  - kCupPage2Courses[8]           — cursor -> cupId for the CUP3 page
  - kCustomBgmTable[(21+N)*2]     — relocated BGM dsp pointer table
                                    (vanilla 21 entries copied + N new)
  - kCustomBgmPairs[N]            — per-track (long_id, short_id) BGM pair
  - kCustomAILapBonusRules_<i>    — per-track AILapBonusRule[] (sentinel
                                    appended) for AICalcLapBonusHook
  - kCustomBaseSpeed_<i>          — per-track CupSpeedEntry[24]
                                    (ccClass=3 × round=8) for the AI
                                    GetBaseSpeedMax/Min hooks
  - kCustomTracks[N]              — cupId -> *bgm pair / *rules / *speed,
                                    scanned by hooks in cup_page3.cpp
  - kCustomTotalBgmCount          — used by cup_page3.cpp to clip
                                    ClSound_PlayBgmStream's `< 0x15` guard
  - kCustomLineBin_*, kCustomCollisionShort_*, kCustomCollisionLong_*
                                  — strings + kmWritePointer records into
                                    the vanilla path-table / asset struct
  - kRaceParamOverrides[]         — RaceParamsHook lookup
"""

import re
import sys
from pathlib import Path

import yaml


FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
IDENT_RE    = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# ----- vanilla BGM table (PTR_s_bgm01_demoL_dsp_8037ce1c) -----------------
# 21 BGM entries × L/R = 42 pointers, each pointing into vanilla rodata.
# We hardcode them so the relocated kCustomBgmTable can preserve every
# vanilla BGM id (0..20) without re-reading main.dol at gen time.
VANILLA_BGM_COUNT = 21
VANILLA_BGM_PTRS = [
    # (id, L, R)
    (0,  0x8037CAEC, 0x8037CAFC),  # bgm01_demoL  / bgm01_demoR
    (1,  0x8037CB0C, 0x8037CB20),  # bgm03_sysSltL / bgm03_sysSltR
    (2,  0x8037CB34, 0x8037CB48),  # bgm07_sysendL / bgm07_sysendR
    (3,  0x8037CB5C, 0x8037CB70),  # bgm08_chasysL / bgm08_chasysR
    (4,  0x8037CB84, 0x8037CB98),  # bgm09_chagamL / bgm09_chagamR
    (5,  0x8037CBAC, 0x8037CBC0),  # bgm11_stg1_1L  / bgm11_stg1_1sR  (cup1 long)
    (6,  0x8037CBD4, 0x8037CBE8),  # bgm11_stg1_1sL / bgm11_stg1_1sR  (cup1 short)
    (7,  0x8037CBFC, 0x8037CC10),  # cup2 long
    (8,  0x8037CC24, 0x8037CC38),  # cup2 short
    (9,  0x8037CC4C, 0x8037CC60),  # cup3 long
    (10, 0x8037CC74, 0x8037CC88),  # cup3 short
    (11, 0x8037CC9C, 0x8037CCB0),  # cup4 long
    (12, 0x8037CCC4, 0x8037CCD8),  # cup4 short
    (13, 0x8037CCEC, 0x8037CD00),  # cup5 long
    (14, 0x8037CD14, 0x8037CD28),  # cup5 short
    (15, 0x8037CD3C, 0x8037CD50),  # cup6 long
    (16, 0x8037CD64, 0x8037CD78),  # cup6 short
    (17, 0x8037CD8C, 0x8037CD9C),  # cup7 long
    (18, 0x8037CDAC, 0x8037CDC0),  # cup7 short
    (19, 0x8037CDD4, 0x8037CDE4),  # cup8 long
    (20, 0x8037CDF4, 0x8037CE08),  # cup8 short
]
assert len(VANILLA_BGM_PTRS) == VANILLA_BGM_COUNT

# Per-course asset struct at (0x8040b90c + cupId*0x228). +0x84 = short
# collision, +0x198 = long collision.
#
# NOTE: the old DAT_8032890c (kCup0LineBinTable) path has been retired —
# the array only holds 9 cup slots (cupId 0..8, 144 bytes) and is immediately
# followed by DAT_8032899c (the embedded-line-data table, stride 0x20 per
# cupId). Writing to cupId>=9 via kmWritePointer corrupted that adjacent
# table. line_bin is now served by a runtime hook on CourseData_LoadPathTable
# in cup_page3.cpp, fed directly from kCustomTracks[i].lineBin.
COLLISION_TABLE_BASE = 0x8040B90C
COLLISION_COURSE_STRIDE = 0x228
COLLISION_SHORT_OFFSET = 0x84
COLLISION_LONG_OFFSET  = 0x198

# AI base speed table: ccClass (0..2) × round (0..7), 24 entries per track.
BASE_SPEED_CC_KEYS = ("50cc", "100cc", "150cc")
BASE_SPEED_ROUND_COUNT = 8

# AILapBonusRule defaults (per-rule fields user can omit).
RULE_FIELD_DEFAULTS = {
    "ccClass":         -1,
    "subMode":         -1,
    "kartIdx":         -1,
    "position":        -1,
    "lapDiffMin":       0,
    "lapDiffMax":      99,
    "excludePosition": -1,
}


def assign_cup_id(track_index: int) -> int:
    """tracks 配列の index -> cupId.

    Custom tracks occupy cupId >= 17. Slots 0 (test_course dev leftover)
    and 9..16 (vanilla minigame / challenge modes) are intentionally skipped
    so new custom content never collides with vanilla semantics. See
    cup_courses.yaml header for the rationale."""
    return 17 + track_index


def safe_ident(name: str) -> str:
    if not IDENT_RE.match(name):
        raise SystemExit(
            f"error: track name {name!r} is not a valid C identifier suffix"
        )
    return name


def safe_filename(field: str, value) -> str:
    if not isinstance(value, str) or not FILENAME_RE.match(value):
        raise SystemExit(
            f"error: {field} must be a safe filename matching "
            f"{FILENAME_RE.pattern!r}, got {value!r}"
        )
    return value


def normalize_lap_bonus_rules(field: str, value):
    """Validate ai_lap_bonus_rules list. Returns list of fully-populated
    rule dicts (with all default fields filled in), or None if absent."""
    if value is None:
        return None
    if not isinstance(value, list):
        raise SystemExit(f"error: {field} must be a list")
    norm_rules = []
    for j, rule in enumerate(value):
        if not isinstance(rule, dict):
            raise SystemExit(f"error: {field}[{j}] must be a mapping")
        bonus = rule.get("bonus")
        if not isinstance(bonus, (int, float)):
            raise SystemExit(
                f"error: {field}[{j}].bonus required (number), got {bonus!r}"
            )
        rec = {"bonus": float(bonus)}
        for k, default in RULE_FIELD_DEFAULTS.items():
            v = rule.get(k, default)
            if not isinstance(v, int):
                raise SystemExit(
                    f"error: {field}[{j}].{k} must be int, got {v!r}"
                )
            rec[k] = v
        # Sanity-check: 1-byte fields must fit in signed char range.
        for k in ("ccClass", "subMode", "kartIdx", "position", "excludePosition"):
            if not -128 <= rec[k] <= 127:
                raise SystemExit(
                    f"error: {field}[{j}].{k} out of signed-char range"
                )
        # Reject the user-supplied sentinel; gen appends it.
        if rec["ccClass"] == -100:
            raise SystemExit(
                f"error: {field}[{j}].ccClass == -100 is the sentinel; "
                "drop the rule (gen appends the sentinel automatically)"
            )
        norm_rules.append(rec)
    return norm_rules


def normalize_base_speed(field: str, base_value, rounds_value):
    """Resolve base_speed + base_speed_rounds into a 24-entry [ccClass=3]
    × [round=8] list of (lo, hi) tuples, or None if base_speed absent."""
    if base_value is None and rounds_value is None:
        return None
    if base_value is None:
        raise SystemExit(
            f"error: {field}_rounds requires {field} as the per-cc default"
        )
    if not isinstance(base_value, dict):
        raise SystemExit(f"error: {field} must be a mapping of cc -> {{lo,hi}}")
    table = []
    for cc_idx, cc_key in enumerate(BASE_SPEED_CC_KEYS):
        if cc_key not in base_value:
            raise SystemExit(
                f"error: {field} missing entry for {cc_key!r}"
            )
        entry = base_value[cc_key]
        if not isinstance(entry, dict):
            raise SystemExit(f"error: {field}[{cc_key!r}] must be mapping")
        lo = entry.get("lo")
        hi = entry.get("hi")
        if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
            raise SystemExit(
                f"error: {field}[{cc_key!r}] requires numeric lo + hi"
            )
        if lo > hi:
            raise SystemExit(
                f"error: {field}[{cc_key!r}].lo > .hi ({lo} > {hi})"
            )
        for _ in range(BASE_SPEED_ROUND_COUNT):
            table.append((float(lo), float(hi)))

    # Per-round overrides on top of the defaults above.
    if rounds_value is not None:
        if not isinstance(rounds_value, dict):
            raise SystemExit(
                f"error: {field}_rounds must be mapping of cc -> {{round: {{lo,hi}}}}"
            )
        for cc_key, per_round in rounds_value.items():
            if cc_key not in BASE_SPEED_CC_KEYS:
                raise SystemExit(
                    f"error: {field}_rounds[{cc_key!r}] unknown cc; "
                    f"expected one of {BASE_SPEED_CC_KEYS}"
                )
            if not isinstance(per_round, dict):
                raise SystemExit(
                    f"error: {field}_rounds[{cc_key!r}] must be mapping"
                )
            cc_idx = BASE_SPEED_CC_KEYS.index(cc_key)
            for r_key, entry in per_round.items():
                if not isinstance(r_key, int) or not 0 <= r_key < BASE_SPEED_ROUND_COUNT:
                    raise SystemExit(
                        f"error: {field}_rounds[{cc_key!r}][{r_key!r}] "
                        f"round index must be 0..{BASE_SPEED_ROUND_COUNT - 1}"
                    )
                if not isinstance(entry, dict):
                    raise SystemExit(
                        f"error: {field}_rounds[{cc_key!r}][{r_key!r}] must be mapping"
                    )
                lo = entry.get("lo")
                hi = entry.get("hi")
                if (not isinstance(lo, (int, float))
                        or not isinstance(hi, (int, float))):
                    raise SystemExit(
                        f"error: {field}_rounds[{cc_key!r}][{r_key!r}] requires lo + hi"
                    )
                if lo > hi:
                    raise SystemExit(
                        f"error: {field}_rounds[{cc_key!r}][{r_key!r}] "
                        f"lo > hi ({lo} > {hi})"
                    )
                table[cc_idx * BASE_SPEED_ROUND_COUNT + r_key] = (
                    float(lo), float(hi)
                )

    assert len(table) == len(BASE_SPEED_CC_KEYS) * BASE_SPEED_ROUND_COUNT
    return table


def main() -> int:
    feature_dir = Path(__file__).parent
    yaml_path = feature_dir / "cup_courses.yaml"
    out_path  = feature_dir / "generated_cup_courses.h"
    xml_path  = feature_dir / "generated_riivolution.xml"

    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    tracks = data.get("tracks") or []
    if not tracks:
        raise SystemExit("error: tracks: list is empty")

    # ----- validate + normalize -----
    norm = []   # list of dict (with cup_id, ident, etc.)
    for i, t in enumerate(tracks):
        if not isinstance(t, dict):
            raise SystemExit(f"error: tracks[{i}] must be a mapping")
        name = t.get("name")
        if not isinstance(name, str):
            raise SystemExit(f"error: tracks[{i}].name must be string")
        ident = safe_ident(name)
        cup_id = assign_cup_id(i)

        coll_s = safe_filename(f"tracks[{i}].collision_short",
                               t.get("collision_short"))
        coll_l = safe_filename(f"tracks[{i}].collision_long",
                               t.get("collision_long"))
        line   = safe_filename(f"tracks[{i}].line_bin", t.get("line_bin"))
        bgm_l  = safe_filename(f"tracks[{i}].bgm_l",  t.get("bgm_l"))
        bgm_r  = safe_filename(f"tracks[{i}].bgm_r",  t.get("bgm_r"))

        laps = t.get("laps")
        time_s = t.get("time")
        bonus_s = t.get("bonus")
        if not isinstance(laps, int) or laps < 1 or laps > 127:
            raise SystemExit(
                f"error: tracks[{i}].laps must be int in 1..127, got {laps!r}"
            )
        if not isinstance(time_s, (int, float)) or time_s < 0:
            raise SystemExit(
                f"error: tracks[{i}].time must be number >= 0, got {time_s!r}"
            )
        if not isinstance(bonus_s, (int, float)) or bonus_s < 0:
            raise SystemExit(
                f"error: tracks[{i}].bonus must be number >= 0, got {bonus_s!r}"
            )

        if "ai_lap_bonus" in t:
            raise SystemExit(
                f"error: tracks[{i}].ai_lap_bonus (scalar) is removed; "
                "use ai_lap_bonus_rules: [...] instead"
            )
        lap_bonus_rules = normalize_lap_bonus_rules(
            f"tracks[{i}].ai_lap_bonus_rules",
            t.get("ai_lap_bonus_rules"),
        )

        base_speed_table = normalize_base_speed(
            f"tracks[{i}].base_speed",
            t.get("base_speed"),
            t.get("base_speed_rounds"),
        )

        # Each track gets one new BGM id, sitting after the vanilla 21:
        bgm_id = VANILLA_BGM_COUNT + i

        norm.append({
            "index":   i,
            "ident":   ident,
            "cup_id":  cup_id,
            "coll_s":  coll_s,
            "coll_l":  coll_l,
            "line":    line,
            "laps":    laps,
            "time":    float(time_s),
            "bonus":   float(bonus_s),
            "lap_bonus_rules": lap_bonus_rules,
            "base_speed":      base_speed_table,
            "bgm_l":   bgm_l,
            "bgm_r":   bgm_r,
            "bgm_id":  bgm_id,
        })

    n_tracks = len(norm)
    total_bgm = VANILLA_BGM_COUNT + n_tracks

    # ----- page2 fill -----
    page2 = []
    for i in range(8):
        src = norm[i] if i < n_tracks else norm[-1]
        page2.append(src["cup_id"])

    # ----- emit header -----
    L = []
    L.append("// Auto-generated from cup_courses.yaml - do not edit")
    L.append("#ifndef GENERATED_CUP_COURSES_H")
    L.append("#define GENERATED_CUP_COURSES_H")
    L.append("")
    L.append("#include <kamek.h>")
    L.append("")

    # --- Page 2 cursor map ---
    L.append("// CUP3 page cursor -> cupId. Filled in track order; remaining")
    L.append("// slots clone the last track to keep the 8-slot grid populated.")
    L.append("static const int kCupPage2Courses[8] = {")
    for i, v in enumerate(page2):
        L.append(f"    {v},   // cursor {i}")
    L.append("};")
    L.append("")

    # --- Per-track DSP filename strings (for new BGM entries) ---
    L.append("// DSP filenames for new BGM ids (strings live in patch rodata;")
    L.append("// pointers feed the relocated kCustomBgmTable below).")
    for t in norm:
        L.append(
            f'static const char kBgmDsp_{t["ident"]}_L[] = "{t["bgm_l"]}";'
        )
        L.append(
            f'static const char kBgmDsp_{t["ident"]}_R[] = "{t["bgm_r"]}";'
        )
    L.append("")

    # --- Relocated BGM pointer table ---
    L.append("// Vanilla PTR_s_bgm01_demoL_dsp_8037ce1c (21 ids × L/R = 42)")
    L.append("// + per-track new entries, packed identically (L,R,L,R,...).")
    L.append("// BgmTableLookupHook in cup_page3.cpp redirects ClSound_Play-")
    L.append("// BgmStream's table read at 0x80190c50 to this array, so")
    L.append("// vanilla rodata is left untouched (0x8037CEC4 padding +")
    L.append("// debug strings stay intact).")
    L.append(f"static const void* const kCustomBgmTable[{total_bgm * 2}] = {{")
    L.append("    // --- vanilla 21 entries (copied) ---")
    for vid, ptr_l, ptr_r in VANILLA_BGM_PTRS:
        L.append(
            f"    (const void*)0x{ptr_l:08X}, "
            f"(const void*)0x{ptr_r:08X},  // bgm_id {vid}"
        )
    if norm:
        L.append("    // --- new entries (one per track) ---")
        for t in norm:
            L.append(
                f"    kBgmDsp_{t['ident']}_L, kBgmDsp_{t['ident']}_R,  "
                f"// bgm_id {t['bgm_id']} ({t['ident']}, cupId={t['cup_id']})"
            )
    L.append("};")
    L.append("")
    L.append(f"#define kCustomTotalBgmCount {total_bgm}u")
    L.append("")

    # --- BGM pair table (long/short share the same id by default) ---
    L.append("// Per-track BGM id pair consumed by WeatherInitCustom.")
    L.append("// pair[0] = long-lap variant id, pair[1] = short-lap variant id.")
    L.append("// We currently use the same id for both (single dsp pair).")
    L.append("struct CustomBgmPair { unsigned int long_id; unsigned int short_id; };")
    L.append(f"static const struct CustomBgmPair kCustomBgmPairs[{n_tracks}] = {{")
    for t in norm:
        L.append(
            f"    {{ {t['bgm_id']}u, {t['bgm_id']}u }},  "
            f"// {t['ident']} (cupId={t['cup_id']})"
        )
    L.append("};")
    L.append("")

    # --- Per-track AILapBonusRule arrays ---
    L.append("// AILapBonusRule struct must match vanilla (0x14 bytes,")
    L.append("// sentinel ccClass == -100). Walked by AICalcLapBonusHook.")
    L.append("struct AILapBonusRule {")
    L.append("    signed char ccClass;")
    L.append("    signed char subMode;")
    L.append("    signed char kartIdx;")
    L.append("    signed char position;")
    L.append("    int  lapDiffMin;")
    L.append("    int  lapDiffMax;")
    L.append("    signed char excludePosition;")
    L.append("    signed char pad[3];")
    L.append("    float bonusValue;")
    L.append("};")
    L.append("")

    for t in norm:
        rules = t["lap_bonus_rules"]
        if rules is None:
            continue
        sym = f"kCustomLapBonusRules_{t['cup_id']}"
        L.append(
            f"static const struct AILapBonusRule {sym}[{len(rules) + 1}] = {{"
        )
        for r in rules:
            L.append(
                f"    {{ {r['ccClass']}, {r['subMode']}, {r['kartIdx']}, "
                f"{r['position']}, {r['lapDiffMin']}, {r['lapDiffMax']}, "
                f"{r['excludePosition']}, {{0,0,0}}, {r['bonus']!r}f }},"
            )
        # Sentinel ccClass=-100 ends iteration in vanilla walker.
        L.append("    { -100, 0, 0, 0, 0, 0, 0, {0,0,0}, 0.0f },")
        L.append("};")
    L.append("")

    # --- Per-track AI base speed tables (ccClass × round) ---
    L.append("// Per-track AI base target speed table for the RACE-mode")
    L.append("// GetBaseSpeedMax/Min hooks. Layout matches vanilla:")
    L.append("//   entry[ccClass * 8 + round] = { lo, hi }  (km/h)")
    L.append("// Vanilla file: kAIBaseSpeedTable_Race @ 0x803a01e8.")
    L.append("struct CupSpeedEntry { float lo; float hi; };")
    L.append("")

    for t in norm:
        tbl = t["base_speed"]
        if tbl is None:
            continue
        sym = f"kCustomBaseSpeed_{t['cup_id']}"
        n_entries = len(BASE_SPEED_CC_KEYS) * BASE_SPEED_ROUND_COUNT
        L.append(f"static const struct CupSpeedEntry {sym}[{n_entries}] = {{")
        for cc_idx, cc_key in enumerate(BASE_SPEED_CC_KEYS):
            for r in range(BASE_SPEED_ROUND_COUNT):
                lo, hi = tbl[cc_idx * BASE_SPEED_ROUND_COUNT + r]
                L.append(
                    f"    {{ {lo!r}f, {hi!r}f }},  // {cc_key} round {r}"
                )
        L.append("};")
    L.append("")

    # --- Per-track string assets (emitted here so kCustomTracks can reference them) ---
    L.append("// --- Per-track string assets (pointed to by kCustomTracks) ---")
    for t in norm:
        L.append(
            f'static const char kCustomLineBin_{t["cup_id"]}[] '
            f'= "{t["line"]}";'
        )
        L.append(
            f'static const char kCustomCollisionShort_{t["cup_id"]}[] '
            f'= "{t["coll_s"]}";'
        )
        L.append(
            f'static const char kCustomCollisionLong_{t["cup_id"]}[] '
            f'= "{t["coll_l"]}";'
        )
    L.append("")

    # --- Track meta ---
    L.append("// cupId -> bgm pair / lap-bonus rules / base-speed table / asset")
    L.append("// filenames. Hooks in cup_page3.cpp do a linear scan (track count")
    L.append("// is tiny). NULL pointers leave the corresponding subsystem on")
    L.append("// its vanilla path for that cupId.")
    L.append("//")
    L.append("// `lineBin` is returned by the CourseData_LoadPathTable hook for")
    L.append("// any (ccClass, ura) combination - custom tracks carry a single")
    L.append("// filename per cup, not the full 4-slot vanilla array.")
    L.append("//")
    L.append("// `collisionShort` / `collisionLong` are selected by the")
    L.append("// GetCollisionFilename hook based on g_longRoundFlag; variantIdx")
    L.append("// and g_reverseRoundFlag are ignored for custom tracks.")
    L.append("struct CustomTrack {")
    L.append("    unsigned int cupId;")
    L.append("    const char* lineBin;")
    L.append("    const char* collisionShort;")
    L.append("    const char* collisionLong;")
    L.append("    const struct CustomBgmPair* bgmPair;")
    L.append("    const struct AILapBonusRule* lapBonusRules;")
    L.append("    const struct CupSpeedEntry* baseSpeedTable;")
    L.append("};")
    L.append(f"static const struct CustomTrack kCustomTracks[{n_tracks}] = {{")
    for t in norm:
        rules_ptr = (
            f"kCustomLapBonusRules_{t['cup_id']}"
            if t["lap_bonus_rules"] is not None else "0"
        )
        speed_ptr = (
            f"kCustomBaseSpeed_{t['cup_id']}"
            if t["base_speed"] is not None else "0"
        )
        L.append(
            f"    {{ {t['cup_id']}u, kCustomLineBin_{t['cup_id']}, "
            f"kCustomCollisionShort_{t['cup_id']}, "
            f"kCustomCollisionLong_{t['cup_id']}, "
            f"&kCustomBgmPairs[{t['index']}], "
            f"{rules_ptr}, {speed_ptr} }},  "
            f"// {t['ident']}"
        )
    L.append("};")
    L.append(f"static const unsigned int kCustomTrackCount = {n_tracks}u;")
    L.append("")

    # --- ClSound_PlayBgmStream upper-bound rewrite ---
    # vanilla: cmplwi r28, 0x15  (encoding: 0x281C0015)
    # patched: cmplwi r28, kCustomTotalBgmCount
    cmpli_insn = 0x281C0000 | (total_bgm & 0xFFFF)
    L.append("// Raise ClSound_PlayBgmStream's bgm_id upper bound from 21 to")
    L.append(f"// {total_bgm} (vanilla `cmplwi r28, 0x15`).")
    L.append(f"kmWrite32(0x80190B70, 0x{cmpli_insn:08X});")
    L.append("")

    # NOTE: All line_bin and collision pointers are served by kmBranch hooks
    # (CourseDataLoadCustom / GetCollisionFilenameHook) in cup_page3.cpp that
    # read directly from kCustomTracks[]. The legacy kmWritePointer-into-
    # vanilla-rodata approach has been retired: kCup0LineBinTable is capped
    # at 9 cups (cupId 0..8) before DAT_8032899c takes over, and the
    # collision pointer array at 0x8040b990 is similarly bounded. Writing
    # at cupId>=9 in either would corrupt adjacent vanilla data.
    L.append("// Line_bin + collision lookups are hook-served from")
    L.append("// kCustomTracks[]; no rodata writes are emitted here.")
    L.append("")

    L.append("// --- (removed) Per-cup asset struct collision overrides ---")
    for t in norm:
        L.append(
            f"// cupId={t['cup_id']} ({t['ident']}): "
            f"collisionShort/Long served via GetCollisionFilename hook"
        )
        # historical addresses preserved in a comment for traceability
        course_base = COLLISION_TABLE_BASE + t["cup_id"] * COLLISION_COURSE_STRIDE
        L.append(
            f"// (was kmWritePointer 0x{course_base + COLLISION_SHORT_OFFSET:08X}, "
            f"0x{course_base + COLLISION_LONG_OFFSET:08X})"
        )
    L.append("")

    # --- Race param overrides ---
    L.append("struct RaceParamOverride { int cupId; int laps; "
             "float time; float bonus; };")
    L.append("static const struct RaceParamOverride kRaceParamOverrides[] = {")
    for t in norm:
        L.append(
            f"    {{ {t['cup_id']}, {t['laps']}, "
            f"{t['time']!r}f, {t['bonus']!r}f }},"
        )
    L.append("};")
    L.append(
        f"static const int kRaceParamOverrideCount = {n_tracks};"
    )
    L.append("")

    L.append("#endif")
    L.append("")
    out_path.write_text("\n".join(L))

    # --- Riivolution <file> fragments (de-duplicated by filename) ---
    seen = set()
    xml_lines = []
    for t in norm:
        for fn in (t["line"], t["coll_s"], t["coll_l"]):
            if fn in seen:
                continue
            seen.add(fn)
            xml_lines.append(
                f'<file disc="/{fn}" '
                f'external="/mkgp2_patch/{fn}" create="true"/>'
            )
        # BGM dsps: only emit a <file> if the track owner ships their own
        # copy under features/cup_page3/files/. ISO-supplied vanilla dsps
        # (e.g. bgm01_demoL.dsp) need no copy — DVDOpen finds them at the
        # disc root either way. We can't easily distinguish here without a
        # cross-check, so emit unconditionally; Riivolution treats a missing
        # external file as a no-op when create="true" is set... actually it
        # errors out, so we skip BGM files that aren't shipped in files/.
        files_dir = feature_dir / "files"
        for fn in (t["bgm_l"], t["bgm_r"]):
            if fn in seen:
                continue
            if not (files_dir / fn).exists():
                # not shipped → assume it's a vanilla ISO dsp, skip <file>
                continue
            seen.add(fn)
            xml_lines.append(
                f'<file disc="/{fn}" '
                f'external="/mkgp2_patch/{fn}" create="true"/>'
            )
    xml_path.write_text("\n".join(xml_lines) + ("\n" if xml_lines else ""))

    print(
        f"Generated {out_path.name}: {n_tracks} track(s), "
        f"page2={page2}, total_bgm={total_bgm}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
