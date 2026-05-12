#!/usr/bin/env python3
"""Generate generated_cup_courses.h from features/cups.yaml + course_models.yaml.

Inputs:
  - features/cups.yaml          — cup + per-round full-spec definitions
  - features/course_models.yaml — model id -> (file, joints) lookup

Output (generated_cup_courses.h) provides everything cup_page3.cpp +
round_select.cpp need at runtime, all per-round:
  - kCupPage2Courses[8]            — cursor -> cupId for the CUP3 page
  - kCustomBgmTable[(21+M)*2]      — relocated BGM dsp pointer table
                                     (M = sum of rounds across all custom cups)
  - kCustomLapBonusRules_<sym>[]   — AI rule table per unique rules instance
                                     (deduplicated by Python object identity)
  - kCustomBaseSpeed_<sym>[3]      — per-round base-speed (lo,hi) per cc
  - kCustomCollision_<cup>_<r>     — per-round collision filename string
  - kCustomLineBin_<cup>_<r>       — per-round line.bin filename string
  - kCustomCourseModel_<cup>_<r>   — per-round HSD model filename string
  - kCustomRounds_<cup>[N]         — CustomRound entries
  - kCustomCups[]                  — CustomCup entries (cup_id, *rounds, n_rounds)
  - kCustomTotalBgmCount           — bumps ClSound_PlayBgmStream's bound
  - kRaceParamOverrides[]          — RaceParamsHook lookup, keyed by (cup, round)

For backward compat during the transition, a `kCustomTracks` alias is also
emitted that maps cupId -> first round only (= legacy "1 cup = 1 course").
"""

import re
import sys
from pathlib import Path

import yaml


FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
IDENT_RE    = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Vanilla BGM table copied from main.dol rodata (PTR_s_bgm01_demoL_dsp_8037ce1c)
# -- 21 entries × L/R = 42 pointers. Hardcoded so the relocated table preserves
# all vanilla BGM ids without needing to re-read main.dol at gen time.
VANILLA_BGM_COUNT = 21
VANILLA_BGM_PTRS = [
    (0,  0x8037CAEC, 0x8037CAFC),  # bgm01_demoL  / bgm01_demoR
    (1,  0x8037CB0C, 0x8037CB20),  # bgm03_sysSltL / bgm03_sysSltR
    (2,  0x8037CB34, 0x8037CB48),  # bgm07_sysendL / bgm07_sysendR
    (3,  0x8037CB5C, 0x8037CB70),  # bgm08_chasysL / bgm08_chasysR
    (4,  0x8037CB84, 0x8037CB98),  # bgm09_chagamL / bgm09_chagamR
    (5,  0x8037CBAC, 0x8037CBC0),  # bgm11_stg1_1L  / bgm11_stg1_1sR
    (6,  0x8037CBD4, 0x8037CBE8),  # bgm11_stg1_1sL / bgm11_stg1_1sR
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

BASE_SPEED_CC_KEYS = ("50cc", "100cc", "150cc")
BASE_SPEED_CC_COUNT = len(BASE_SPEED_CC_KEYS)

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


def safe_ident(name: str) -> str:
    if not IDENT_RE.match(name):
        raise SystemExit(
            f"error: identifier {name!r} is not a valid C identifier suffix"
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
        for k in ("ccClass", "subMode", "kartIdx", "position", "excludePosition"):
            if not -128 <= rec[k] <= 127:
                raise SystemExit(
                    f"error: {field}[{j}].{k} out of signed-char range"
                )
        if rec["ccClass"] == -100:
            raise SystemExit(
                f"error: {field}[{j}].ccClass == -100 is the sentinel; "
                "drop the rule (gen appends the sentinel automatically)"
            )
        norm_rules.append(rec)
    return norm_rules


def normalize_start_positions(field: str, value):
    """Per-round start_positions: list of [x, y, z] in HSD world coords.
    Returns list of (x, y, z) tuples (length 1..8), or None if absent.

    GetStartPositionHook (cup_page3.cpp 0x8009c688) は slot 0..7 を引く
    (8 kart). 8 個未満を渡した場合は最終要素が繰り返される (kart 5..7 が
    重なって stack されるが、宙浮きよりはマシなので警告のみ)。"""
    if value is None:
        return None
    if not isinstance(value, list) or not value:
        raise SystemExit(
            f"error: {field} must be a non-empty list of [x, y, z] "
            f"(HSD world coords)"
        )
    if len(value) > 8:
        raise SystemExit(
            f"error: {field}: max 8 entries (kart slots), got {len(value)}"
        )
    out = []
    for k, p in enumerate(value):
        if not isinstance(p, list) or len(p) != 3 or \
           not all(isinstance(c, (int, float)) for c in p):
            raise SystemExit(
                f"error: {field}[{k}] must be [x, y, z] of numbers, got {p!r}"
            )
        out.append((float(p[0]), float(p[1]), float(p[2])))
    return out


def normalize_start_yaw(field: str, value):
    """Per-round start_yaw: scalar in **degrees**, applied to all karts.
    Returns float or None.

    GetCourseStartYawHook (cup_page3.cpp 0x8009c634) が返す yaw 値で、
    RaceInit -> CarObject_Init -> KartMovement_Init を経て KartMovement+0x1C8
    に保存され、`DOUBLE_806d96f0 (= π/180) * yaw` でラジアンに変換されて
    Y 軸回転行列を組む。つまり game 側の保存単位は **度**。

    回転行列 row0 = [cos, *, -sin, *], row2 = [sin, *, cos, *] を kart の
    local forward (+Z) に適用すると world forward = (-sin, 0, cos):
        yaw =   0° → +Z (south, minimap で下)
        yaw =  90° → -X (west)
        yaw = 180° → -Z (north, minimap で上)
        yaw = 270° → +X (east)
    つまり「西向きスタート」(meadow_loop wp0 の -X tangent) は yaw=90°。
    vanilla test_course は 1000.0 = 280° (= ~ENE) を返す。
    """
    if value is None:
        return None
    if not isinstance(value, (int, float)):
        raise SystemExit(
            f"error: {field} must be a number (degrees), got {value!r}"
        )
    return float(value)


def normalize_finish_line(field: str, value):
    """Per-round finish_line: 2 points [[x,y,z],[x,y,z]] in HSD world coords.
    Returns ((x1,y1,z1),(x2,y2,z2)) or None if absent.

    PathManager_UpdateAll @ 0x8003b6c4 が FUN_8009c3c4 から取り出す pointer の
    指す struct と同じ layout (6 floats, XZ のみ参照、Y は無視) で
    Math_Segment2DIntersect に渡される。"""
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 2:
        raise SystemExit(
            f"error: {field} must be 2-point list [[x,y,z],[x,y,z]], "
            f"got {value!r}"
        )
    pts = []
    for k, p in enumerate(value):
        if not isinstance(p, list) or len(p) != 3 or \
           not all(isinstance(c, (int, float)) for c in p):
            raise SystemExit(
                f"error: {field}[{k}] must be [x, y, z] of numbers, got {p!r}"
            )
        pts.append((float(p[0]), float(p[1]), float(p[2])))
    if pts[0][0] == pts[1][0] and pts[0][2] == pts[1][2]:
        raise SystemExit(
            f"error: {field}: the two endpoints have identical XZ; "
            "Math_Segment2DIntersect early-exits 0 on degenerate segments"
        )
    return tuple(pts)


def normalize_base_speed_per_round(field: str, value):
    """Per-round base_speed: cc -> {lo, hi}. Returns 3-entry list of
    (lo, hi) tuples in cc order (50cc, 100cc, 150cc), or None if absent."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise SystemExit(f"error: {field} must be a mapping cc -> {{lo,hi}}")
    table = []
    for cc_key in BASE_SPEED_CC_KEYS:
        if cc_key not in value:
            raise SystemExit(f"error: {field} missing entry for {cc_key!r}")
        entry = value[cc_key]
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
        table.append((float(lo), float(hi)))
    return table


# ---- main -----------------------------------------------------------------

def load_models(features_dir: Path) -> dict:
    """Load course_models.yaml. Returns dict: id -> {file, joints}."""
    path = features_dir / "course_models.yaml"
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    raw = data.get("course_models")
    if not isinstance(raw, dict) or not raw:
        raise SystemExit(
            f"error: {path.name}: top-level 'course_models:' (mapping) required"
        )
    out = {}
    for mid, mdef in raw.items():
        if not IDENT_RE.match(mid):
            raise SystemExit(
                f"error: {path.name}: model id {mid!r} not a valid C identifier"
            )
        if not isinstance(mdef, dict):
            raise SystemExit(f"error: {path.name}: course_models.{mid} must be a mapping")
        file = safe_filename(f"{path.name}: course_models.{mid}.file",
                             mdef.get("file"))
        joints = mdef.get("joints", [])
        if not isinstance(joints, list):
            raise SystemExit(
                f"error: {path.name}: course_models.{mid}.joints must be a list"
            )
        out[mid] = {"file": file, "joints": joints}
    return out


def main() -> int:
    feature_dir = Path(__file__).parent
    features_dir = feature_dir.parent
    yaml_path = features_dir / "cups.yaml"
    out_path  = feature_dir / "generated_cup_courses.h"
    xml_path  = feature_dir / "generated_riivolution.xml"

    models = load_models(features_dir)

    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    cups = data.get("cups") or []
    if not cups:
        raise SystemExit(f"error: {yaml_path.name}: cups: list is empty")

    # ----- validate + normalize ------------------------------------------
    norm_cups = []
    seen_cup_ids = {}
    for ci, cup in enumerate(cups):
        if not isinstance(cup, dict):
            raise SystemExit(f"error: cups[{ci}] must be a mapping")
        cup_ident = safe_ident(cup.get("id") or "")
        cup_id = cup.get("cup_id")
        if not isinstance(cup_id, int) or cup_id < 17:
            raise SystemExit(
                f"error: cups[{ci}].cup_id must be int >= 17, got {cup_id!r}"
            )
        if cup_id in seen_cup_ids:
            raise SystemExit(
                f"error: cups[{ci}].cup_id={cup_id} duplicated "
                f"(also cups[{seen_cup_ids[cup_id]}])"
            )
        seen_cup_ids[cup_id] = ci

        if "courses" in cup:
            raise SystemExit(
                f"error: cups[{ci}]({cup_ident}).courses is removed; "
                "use `rounds: [{course_model: ..., collision: ..., ...}, ...]`"
            )

        rounds_raw = cup.get("rounds")
        if not isinstance(rounds_raw, list) or not rounds_raw:
            raise SystemExit(
                f"error: cups[{ci}]({cup_ident}).rounds must be a non-empty list"
            )
        if len(rounds_raw) > 4:
            raise SystemExit(
                f"error: cups[{ci}]({cup_ident}).rounds has {len(rounds_raw)} "
                "entries; max 4 (round-select UI shows at most 4 rows; "
                "long variants would be a separate cup)"
            )

        norm_rounds = []
        for ri, rentry in enumerate(rounds_raw):
            if not isinstance(rentry, dict):
                raise SystemExit(
                    f"error: cups[{ci}]({cup_ident}).rounds[{ri}] must be mapping"
                )
            r_ident = safe_ident(rentry.get("id") or f"round{ri+1}")
            r_loc = f"cups[{ci}]({cup_ident}).rounds[{ri}]({r_ident})"

            cm_id = rentry.get("course_model")
            if not isinstance(cm_id, str) or cm_id not in models:
                raise SystemExit(
                    f"error: {r_loc}.course_model must reference an id in "
                    f"course_models.yaml, got {cm_id!r}"
                )
            cm = models[cm_id]

            coll = safe_filename(f"{r_loc}.collision", rentry.get("collision"))
            line = safe_filename(f"{r_loc}.line_bin",  rentry.get("line_bin"))
            bgm_l = safe_filename(f"{r_loc}.bgm_l",    rentry.get("bgm_l"))
            bgm_r = safe_filename(f"{r_loc}.bgm_r",    rentry.get("bgm_r"))

            laps   = rentry.get("laps")
            time_s = rentry.get("time")
            bonus_s = rentry.get("bonus")
            if not isinstance(laps, int) or not 1 <= laps <= 127:
                raise SystemExit(
                    f"error: {r_loc}.laps must be int 1..127, got {laps!r}"
                )
            if not isinstance(time_s, (int, float)) or time_s < 0:
                raise SystemExit(
                    f"error: {r_loc}.time must be number >= 0, got {time_s!r}"
                )
            if not isinstance(bonus_s, (int, float)) or bonus_s < 0:
                raise SystemExit(
                    f"error: {r_loc}.bonus must be number >= 0, got {bonus_s!r}"
                )

            ai_rules_raw = rentry.get("ai_lap_bonus_rules")
            ai_rules = normalize_lap_bonus_rules(
                f"{r_loc}.ai_lap_bonus_rules", ai_rules_raw
            )
            base_speed_raw = rentry.get("base_speed")
            base_speed = normalize_base_speed_per_round(
                f"{r_loc}.base_speed", base_speed_raw
            )
            start_positions = normalize_start_positions(
                f"{r_loc}.start_positions", rentry.get("start_positions")
            )
            finish_line = normalize_finish_line(
                f"{r_loc}.finish_line", rentry.get("finish_line")
            )
            start_yaw = normalize_start_yaw(
                f"{r_loc}.start_yaw", rentry.get("start_yaw")
            )

            norm_rounds.append({
                "start_positions": start_positions,
                "finish_line":     finish_line,
                "start_yaw":       start_yaw,
                "ident":   r_ident,
                "course_model_id":   cm_id,
                "course_model_file": cm["file"],
                "course_model_joints": cm["joints"],
                "collision":  coll,
                "line_bin":   line,
                "laps":       laps,
                "time":       float(time_s),
                "bonus":      float(bonus_s),
                "bgm_l":      bgm_l,
                "bgm_r":      bgm_r,
                "ai_rules":   ai_rules,
                "ai_rules_raw_id": id(ai_rules_raw) if ai_rules_raw is not None else None,
                "base_speed": base_speed,
                "base_speed_raw_id": id(base_speed_raw) if base_speed_raw is not None else None,
                "bgm_id":     None,    # filled below
            })

        norm_cups.append({
            "ci":        ci,
            "ident":     cup_ident,
            "cup_id":    cup_id,
            "rounds":    norm_rounds,
        })

    # Assign bgm_id sequentially across all rounds of all cups.
    next_bgm_id = VANILLA_BGM_COUNT
    for nc in norm_cups:
        for nr in nc["rounds"]:
            nr["bgm_id"] = next_bgm_id
            next_bgm_id += 1
    total_bgm = next_bgm_id

    # Page 2 cursor map: cup-only (round agnostic).
    page2 = []
    for i in range(8):
        src = norm_cups[i] if i < len(norm_cups) else norm_cups[-1]
        page2.append(src["cup_id"])

    # Deduplicate per-round AI rules / base_speed by yaml object identity
    # (so anchor `*shared_X` in yaml emits 1 C array used by N rounds).
    ai_rules_syms = {}     # raw_obj_id -> symbol
    base_speed_syms = {}
    ai_rules_unique = []   # [(symbol, rules_list)]
    base_speed_unique = [] # [(symbol, table_list)]
    for nc in norm_cups:
        for ri, nr in enumerate(nc["rounds"]):
            if nr["ai_rules"] is not None:
                key = nr["ai_rules_raw_id"]
                if key not in ai_rules_syms:
                    sym = f"kCustomLapBonusRules_{nc['cup_id']}_{nr['ident']}"
                    ai_rules_syms[key] = sym
                    ai_rules_unique.append((sym, nr["ai_rules"]))
                nr["ai_rules_sym"] = ai_rules_syms[key]
            else:
                nr["ai_rules_sym"] = "0"
            if nr["base_speed"] is not None:
                key = nr["base_speed_raw_id"]
                if key not in base_speed_syms:
                    sym = f"kCustomBaseSpeed_{nc['cup_id']}_{nr['ident']}"
                    base_speed_syms[key] = sym
                    base_speed_unique.append((sym, nr["base_speed"]))
                nr["base_speed_sym"] = base_speed_syms[key]
            else:
                nr["base_speed_sym"] = "0"

    # ----- emit C header -------------------------------------------------
    L = []
    L.append("// Auto-generated from features/cups.yaml + course_models.yaml -- do not edit")
    L.append("#ifndef GENERATED_CUP_COURSES_H")
    L.append("#define GENERATED_CUP_COURSES_H")
    L.append("")
    L.append("#include <kamek.h>")
    L.append("")

    # --- Page 2 cursor map ---
    L.append("// CUP3 page cursor -> cupId. Filled in cup order; remaining slots")
    L.append("// clone the last cup to keep the 8-slot grid populated.")
    L.append("static const int kCupPage2Courses[8] = {")
    for i, v in enumerate(page2):
        L.append(f"    {v},   // cursor {i}")
    L.append("};")
    L.append("")

    # --- BGM dsp filename strings (per round) ---
    L.append("// DSP filenames per round (strings live in patch rodata).")
    for nc in norm_cups:
        for nr in nc["rounds"]:
            sym_l = f"kBgmDsp_{nc['cup_id']}_{nr['ident']}_L"
            sym_r = f"kBgmDsp_{nc['cup_id']}_{nr['ident']}_R"
            L.append(f'static const char {sym_l}[] = "{nr["bgm_l"]}";')
            L.append(f'static const char {sym_r}[] = "{nr["bgm_r"]}";')
    L.append("")

    # --- Relocated BGM pointer table ---
    L.append("// Vanilla 21 entries (copied from PTR_s_bgm01_demoL_dsp_8037ce1c)")
    L.append("// + per-round new entries, packed (L,R,L,R,...).")
    L.append(f"static const void* const kCustomBgmTable[{total_bgm * 2}] = {{")
    L.append("    // --- vanilla 21 entries (copied) ---")
    for vid, ptr_l, ptr_r in VANILLA_BGM_PTRS:
        L.append(
            f"    (const void*)0x{ptr_l:08X}, "
            f"(const void*)0x{ptr_r:08X},  // bgm_id {vid}"
        )
    L.append("    // --- per-round new entries ---")
    for nc in norm_cups:
        for nr in nc["rounds"]:
            sym_l = f"kBgmDsp_{nc['cup_id']}_{nr['ident']}_L"
            sym_r = f"kBgmDsp_{nc['cup_id']}_{nr['ident']}_R"
            L.append(
                f"    {sym_l}, {sym_r},  // bgm_id {nr['bgm_id']} "
                f"(cup {nc['cup_id']} round {nr['ident']})"
            )
    L.append("};")
    L.append("")
    L.append(f"#define kCustomTotalBgmCount {total_bgm}u")
    L.append("")

    # Raise ClSound_PlayBgmStream's bgm_id upper bound from 21 to total_bgm.
    cmpli_insn = 0x281C0000 | (total_bgm & 0xFFFF)
    L.append("// Raise ClSound_PlayBgmStream's bgm_id upper bound from 21 to")
    L.append(f"// {total_bgm} (vanilla `cmplwi r28, 0x15`).")
    L.append(f"kmWrite32(0x80190B70, 0x{cmpli_insn:08X});")
    L.append("")

    # --- AILapBonusRule struct ---
    L.append("// AILapBonusRule struct must match vanilla (0x14 bytes, sentinel")
    L.append("// ccClass == -100). Walked by AICalcLapBonusHook.")
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

    # --- Per-round AI rules tables (deduplicated by yaml-object identity) ---
    for sym, rules in ai_rules_unique:
        L.append(
            f"static const struct AILapBonusRule {sym}[{len(rules) + 1}] = {{"
        )
        for r in rules:
            L.append(
                f"    {{ {r['ccClass']}, {r['subMode']}, {r['kartIdx']}, "
                f"{r['position']}, {r['lapDiffMin']}, {r['lapDiffMax']}, "
                f"{r['excludePosition']}, {{0,0,0}}, {r['bonus']!r}f }},"
            )
        L.append("    { -100, 0, 0, 0, 0, 0, 0, {0,0,0}, 0.0f },")
        L.append("};")
    L.append("")

    # --- Per-round AI base speed (3 cc entries each) ---
    L.append("// Per-round AI base target speed table for the RACE-mode hooks.")
    L.append("// Layout: entry[ccClass] = { lo, hi } (km/h). 3 entries per round.")
    L.append("// Vanilla equivalent at kAIBaseSpeedTable_Race indexes by")
    L.append("// [cc*8 + round]; our per-round override flattens that to 3.")
    L.append("struct CupSpeedEntry { float lo; float hi; };")
    L.append("")
    for sym, table in base_speed_unique:
        L.append(f"static const struct CupSpeedEntry {sym}[{BASE_SPEED_CC_COUNT}] = {{")
        for cc_idx, cc_key in enumerate(BASE_SPEED_CC_KEYS):
            lo, hi = table[cc_idx]
            L.append(f"    {{ {lo!r}f, {hi!r}f }},  // {cc_key}")
        L.append("};")
    L.append("")

    # --- Per-round string assets ---
    L.append("// --- Per-round string assets ---")
    for nc in norm_cups:
        for nr in nc["rounds"]:
            base = f"{nc['cup_id']}_{nr['ident']}"
            L.append(f'static const char kCustomCollision_{base}[] = "{nr["collision"]}";')
            L.append(f'static const char kCustomLineBin_{base}[]   = "{nr["line_bin"]}";')
            L.append(f'static const char kCustomCourseModel_{base}[] = "{nr["course_model_file"]}";')
    L.append("")

    # --- Per-round start position arrays (8 kart slots) ---
    L.append("// Per-round start positions (HSD world coords, X Y Z float).")
    L.append("// Schema: cups.yaml -> rounds[].start_positions: [[x,y,z], ... up to 8].")
    L.append("// 8 未満は最終要素を繰り返して slot 7 まで埋める (kart 重なるが宙浮きは避ける).")
    L.append("// 完全省略時は kCustomStartPos_<...> を emit せず、ポインタは NULL =")
    L.append("// GetStartPositionHook が vanilla alias path に fallback (= 原点 / 宙浮き).")
    for nc in norm_cups:
        for nr in nc["rounds"]:
            sp = nr["start_positions"]
            if not sp:
                continue
            base = f"{nc['cup_id']}_{nr['ident']}"
            # Pad to 8 by repeating last entry
            pad = list(sp) + [sp[-1]] * (8 - len(sp))
            L.append(f"static const float kCustomStartPos_{base}[8][3] = {{")
            for k, (x, y, z) in enumerate(pad):
                tag = f"slot {k}"
                if k >= len(sp):
                    tag += " (padded from last)"
                L.append(f"    {{ {x!r}f, {y!r}f, {z!r}f }},  // {tag}")
            L.append("};")
    L.append("")

    # --- Per-round finish line arrays (2 points × 3 floats) ---
    L.append("// Per-round lap-judgment line (HSD world coords, p1+p2 each XYZ).")
    L.append("// 渡される先 (PathManager_UpdateAll @ 0x8003b6c4) は floats[0/2/3/5]")
    L.append("// = (x1, z1, x2, z2) のみ参照、Y は無視される。NULL のままだと")
    L.append("// FUN_8009c3c4_Hook が cupId=0 alias = test_course の判定線を")
    L.append("// 返してしまうので、独自レイアウトの custom course は必ず指定する.")
    for nc in norm_cups:
        for nr in nc["rounds"]:
            fl = nr["finish_line"]
            if not fl:
                continue
            base = f"{nc['cup_id']}_{nr['ident']}"
            L.append(f"static const float kCustomFinishLine_{base}[6] = {{")
            for k, (x, y, z) in enumerate(fl):
                tag = "p1" if k == 0 else "p2"
                L.append(
                    f"    {x!r}f, {y!r}f, {z!r}f,  // {tag} (X={x!r}, Y={y!r}, Z={z!r})"
                )
            L.append("};")
    L.append("")

    # --- Per-round start yaw scalars ---
    L.append("// Per-round start yaw (DEGREES, single float per round).")
    L.append("// 全 kart slot 共通の初期向き。KartMovement+0x1C8 raw, *(π/180) で")
    L.append("// 内部ラジアン化。convention: 0° = +Z (south), 90° = -X (west),")
    L.append("// 180° = -Z (north), 270° = +X (east).")
    L.append("// Reverse Challenge では +π (= 180°相当) されて反対向きになる。")
    L.append("// 省略時は NULL ポインタ = GetCourseStartYawHook が vanilla alias path")
    L.append("// (cupId=0 = test_course の 1000.0° = 280°) に fallback。コース path の")
    L.append("// wp 0 tangent と一致させないと cars がコース外を向いて発進する。")
    for nc in norm_cups:
        for nr in nc["rounds"]:
            if nr["start_yaw"] is None:
                continue
            base = f"{nc['cup_id']}_{nr['ident']}"
            L.append(
                f"static const float kCustomStartYaw_{base} = {nr['start_yaw']!r}f;"
            )
    L.append("")

    # --- CustomRound struct ---
    L.append("// CustomRound = full per-round resource + setting bundle.")
    L.append("// All getter hooks read these via (cupId, round_index) lookup.")
    L.append("struct CustomRound {")
    L.append("    const char* collision;")
    L.append("    const char* lineBin;")
    L.append("    const char* courseModelFile;")
    L.append("    int   laps;")
    L.append("    float time;")
    L.append("    float bonus;")
    L.append("    unsigned int bgmIdL;   // bgm id resolved through kCustomBgmTable")
    L.append("    unsigned int bgmIdR;")
    L.append("    const struct AILapBonusRule* lapBonusRules;  // NULL = vanilla")
    L.append("    const struct CupSpeedEntry*  baseSpeed;      // NULL = vanilla; [3]")
    L.append("    const float (*startPositions)[3];            // NULL = vanilla; [8] HSD world (x,y,z)")
    L.append("    const float* finishLine;                     // NULL = vanilla alias; [6] = (p1.xyz, p2.xyz)")
    L.append("    const float* startYaw;                       // NULL = vanilla alias; single degree scalar (*(π/180) in KartMovement_Init)")
    L.append("};")
    L.append("")

    # --- Per-cup round arrays ---
    for nc in norm_cups:
        sym = f"kCustomRounds_{nc['cup_id']}"
        L.append(f"static const struct CustomRound {sym}[{len(nc['rounds'])}] = {{")
        for nr in nc["rounds"]:
            base = f"{nc['cup_id']}_{nr['ident']}"
            sp_sym = f"kCustomStartPos_{base}" if nr["start_positions"] else "0"
            fl_sym = f"kCustomFinishLine_{base}" if nr["finish_line"] else "0"
            sy_sym = f"&kCustomStartYaw_{base}" if nr["start_yaw"] is not None else "0"
            L.append(
                f"    {{ kCustomCollision_{base}, kCustomLineBin_{base}, "
                f"kCustomCourseModel_{base}, "
                f"{nr['laps']}, {nr['time']!r}f, {nr['bonus']!r}f, "
                f"{nr['bgm_id']}u, {nr['bgm_id']}u, "
                f"{nr['ai_rules_sym']}, {nr['base_speed_sym']}, {sp_sym}, "
                f"{fl_sym}, {sy_sym} }},  // {nr['ident']}"
            )
        L.append("};")
    L.append("")

    # --- CustomCup struct + array ---
    L.append("struct CustomCup {")
    L.append("    unsigned int cupId;")
    L.append("    const struct CustomRound* rounds;")
    L.append("    unsigned int nRounds;")
    L.append("};")
    L.append(f"static const struct CustomCup kCustomCups[{len(norm_cups)}] = {{")
    for nc in norm_cups:
        sym = f"kCustomRounds_{nc['cup_id']}"
        L.append(
            f"    {{ {nc['cup_id']}u, {sym}, {len(nc['rounds'])}u }},  // {nc['ident']}"
        )
    L.append("};")
    L.append(f"static const unsigned int kCustomCupCount = {len(norm_cups)}u;")
    L.append("")

    L.append("#endif")
    L.append("")
    out_path.write_text("\n".join(L))

    # ----- Riivolution <file> fragments (deduped) -----
    seen = set()
    xml_lines = []
    files_dir = feature_dir / "files"
    for nc in norm_cups:
        for nr in nc["rounds"]:
            for fn in (nr["line_bin"], nr["collision"]):
                if fn in seen:
                    continue
                seen.add(fn)
                xml_lines.append(
                    f'<file disc="/{fn}" '
                    f'external="/mkgp2_patch/{fn}" create="true"/>'
                )
            for fn in (nr["bgm_l"], nr["bgm_r"]):
                if fn in seen:
                    continue
                if not (files_dir / fn).exists():
                    continue
                seen.add(fn)
                xml_lines.append(
                    f'<file disc="/{fn}" '
                    f'external="/mkgp2_patch/{fn}" create="true"/>'
                )
            # HSD .dat: emit only when shipped under files/. Vanilla course
            # models (test_course_road.dat etc.) live in the ISO root and
            # don't need a Riivolution entry; custom .dats do.
            cm_file = nr["course_model_file"]
            if cm_file not in seen and (files_dir / cm_file).exists():
                seen.add(cm_file)
                xml_lines.append(
                    f'<file disc="/{cm_file}" '
                    f'external="/mkgp2_patch/{cm_file}" create="true"/>'
                )
    xml_path.write_text("\n".join(xml_lines) + ("\n" if xml_lines else ""))

    n_rounds_total = sum(len(nc["rounds"]) for nc in norm_cups)
    print(
        f"Generated {out_path.name}: {len(norm_cups)} cup(s), "
        f"{n_rounds_total} round(s), page2={page2}, total_bgm={total_bgm}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
