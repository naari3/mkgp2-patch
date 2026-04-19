#!/usr/bin/env python3
"""Generate generated_cup_courses.h from cup_courses.yaml."""

import re
import sys
from pathlib import Path

import yaml


FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# DAT_8032890c is indexed by cupId*0x10 + ccClass*8, with +0 = normal
# and +4 = ura. We overwrite all four slots (ccClass 0..1 x normal/ura) so
# CourseData_LoadPathTable finds the custom filename regardless of class.
PATH_TABLE_BASE = 0x8032890C
ROW_STRIDE = 0x10
SLOT_OFFSETS = (0, 4, 8, 12)

# Vanilla race-params table lives at 0x8040E7D0 and is indexed by
# (cupId*0x48 + ccClass*0x18 + longRound*0xc) with fields at (-0x48: byte
# laps, -0x44: float time, -0x40: float bonus). cupId=0's slots are
# occupied by g_GameModeBaseVtable, so in-place writes crash with ISI.
# Instead we hook RaceScene_Init's table load (cup_page3.cpp:RaceParamsHook)
# and serve values from kRaceParamOverrides[] below, falling back to the
# vanilla table for unlisted cupIds.
VANILLA_RACE_TABLE_BASE = 0x8040E7D0

# Per-course asset struct at (0x8040b90c + cupId*0x228). Slot layout
# (per GetCollisionFilename @ 0x8009c51c):
#   +0x84  = short-variant collision bin
#   +0x198 = long-variant collision bin (+0x84 + 0x114)
# Other offsets hold AI-path and C.dat pointers that we leave untouched.
COLLISION_TABLE_BASE = 0x8040B90C
COLLISION_COURSE_STRIDE = 0x228
COLLISION_SHORT_COLLISION_OFFSET = 0x84
COLLISION_LONG_COLLISION_OFFSET = 0x198


def main() -> int:
    feature_dir = Path(__file__).parent
    yaml_path = feature_dir / "cup_courses.yaml"
    out_path = feature_dir / "generated_cup_courses.h"
    xml_path = feature_dir / "generated_riivolution.xml"

    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    # --- page2: cursor -> cupId mapping (required, exactly 8) ---
    page2 = data.get("page2") or []
    if len(page2) != 8:
        print(
            f"error: page2 must have exactly 8 entries, got {len(page2)}",
            file=sys.stderr,
        )
        return 1
    for i, v in enumerate(page2):
        if not isinstance(v, int) or v < 0 or v > 16:
            print(
                f"error: page2[{i}] must be int in 0..16, got {v!r}",
                file=sys.stderr,
            )
            return 1

    # --- line_bin: cupId -> filename mapping (optional) ---
    line_bin = data.get("line_bin") or {}
    if not isinstance(line_bin, dict):
        print(
            f"error: line_bin must be a mapping, got {type(line_bin).__name__}",
            file=sys.stderr,
        )
        return 1
    line_bin_items = []
    for cup_id, filename in sorted(line_bin.items()):
        if not isinstance(cup_id, int) or cup_id < 0 or cup_id > 16:
            print(
                f"error: line_bin key {cup_id!r} must be int in 0..16",
                file=sys.stderr,
            )
            return 1
        if not isinstance(filename, str) or not FILENAME_RE.match(filename):
            print(
                f"error: line_bin[{cup_id}] must be a safe filename "
                f"matching {FILENAME_RE.pattern!r}, got {filename!r}",
                file=sys.stderr,
            )
            return 1
        line_bin_items.append((cup_id, filename))

    # --- collision_bin: cupId -> { short, long } (optional) ---
    collision_bin = data.get("collision_bin") or {}
    if not isinstance(collision_bin, dict):
        print(
            f"error: collision_bin must be a mapping, got {type(collision_bin).__name__}",
            file=sys.stderr,
        )
        return 1
    # Each item: (cup_id, short_filename_or_None, long_filename_or_None).
    collision_bin_items = []
    for cup_id, entry in sorted(collision_bin.items()):
        if not isinstance(cup_id, int) or cup_id < 0 or cup_id > 16:
            print(
                f"error: collision_bin key {cup_id!r} must be int in 0..16",
                file=sys.stderr,
            )
            return 1
        if not isinstance(entry, dict):
            print(
                f"error: collision_bin[{cup_id}] must be a mapping with "
                f"'short' and/or 'long' keys, got {type(entry).__name__}",
                file=sys.stderr,
            )
            return 1
        short_filename = entry.get("short")
        long_filename = entry.get("long")
        if short_filename is None and long_filename is None:
            print(
                f"error: collision_bin[{cup_id}] must specify at least "
                f"one of 'short' / 'long'",
                file=sys.stderr,
            )
            return 1
        for label, fn in (("short", short_filename), ("long", long_filename)):
            if fn is None:
                continue
            if not isinstance(fn, str) or not FILENAME_RE.match(fn):
                print(
                    f"error: collision_bin[{cup_id}].{label} must be a "
                    f"safe filename matching {FILENAME_RE.pattern!r}, got {fn!r}",
                    file=sys.stderr,
                )
                return 1
        collision_bin_items.append((cup_id, short_filename, long_filename))

    # --- ai_lap_bonus: cupId -> double (optional) ---
    ai_lap_bonus = data.get("ai_lap_bonus") or {}
    if not isinstance(ai_lap_bonus, dict):
        print(
            f"error: ai_lap_bonus must be a mapping, got {type(ai_lap_bonus).__name__}",
            file=sys.stderr,
        )
        return 1
    ai_lap_bonus_items = []
    for cup_id, value in sorted(ai_lap_bonus.items()):
        if not isinstance(cup_id, int) or cup_id < 0 or cup_id > 16:
            print(
                f"error: ai_lap_bonus key {cup_id!r} must be int in 0..16",
                file=sys.stderr,
            )
            return 1
        if not isinstance(value, (int, float)):
            print(
                f"error: ai_lap_bonus[{cup_id}] must be number, got {value!r}",
                file=sys.stderr,
            )
            return 1
        ai_lap_bonus_items.append((cup_id, float(value)))

    # --- race_params: cupId -> { laps, time, bonus } (optional) ---
    race_params = data.get("race_params") or {}
    if not isinstance(race_params, dict):
        print(
            f"error: race_params must be a mapping, got {type(race_params).__name__}",
            file=sys.stderr,
        )
        return 1
    race_params_items = []
    for cup_id, params in sorted(race_params.items()):
        if not isinstance(cup_id, int) or cup_id < 0 or cup_id > 16:
            print(
                f"error: race_params key {cup_id!r} must be int in 0..16",
                file=sys.stderr,
            )
            return 1
        if not isinstance(params, dict):
            print(
                f"error: race_params[{cup_id}] must be a mapping with keys "
                f"laps/time/bonus, got {type(params).__name__}",
                file=sys.stderr,
            )
            return 1
        laps = params.get("laps")
        time_s = params.get("time")
        bonus_s = params.get("bonus")
        if not isinstance(laps, int) or laps < 1 or laps > 127:
            print(
                f"error: race_params[{cup_id}].laps must be int in 1..127, "
                f"got {laps!r}",
                file=sys.stderr,
            )
            return 1
        if not isinstance(time_s, (int, float)) or time_s < 0:
            print(
                f"error: race_params[{cup_id}].time must be number >= 0, "
                f"got {time_s!r}",
                file=sys.stderr,
            )
            return 1
        if not isinstance(bonus_s, (int, float)) or bonus_s < 0:
            print(
                f"error: race_params[{cup_id}].bonus must be number >= 0, "
                f"got {bonus_s!r}",
                file=sys.stderr,
            )
            return 1
        race_params_items.append((cup_id, laps, float(time_s), float(bonus_s)))

    # --- Emit header ---
    lines = [
        "// Auto-generated from cup_courses.yaml - do not edit",
        "#ifndef GENERATED_CUP_COURSES_H",
        "#define GENERATED_CUP_COURSES_H",
        "",
        "#include <kamek.h>",
        "",
        "static const int kCupPage2Courses[8] = {",
    ]
    for i, v in enumerate(page2):
        lines.append(f"    {v},   // cursor {i}")
    lines += ["};", ""]

    # Filename strings. Kept in patch rodata; their final addresses are
    # resolved by the Kamek linker, so kmWritePointer records below contain
    # real absolute pointers by the time Riivolution applies the memory
    # patches at game boot.
    for cup_id, filename in line_bin_items:
        lines.append(
            f'static const char kCustomLineBin_{cup_id}[] = "{filename}";'
        )
    if line_bin_items:
        lines.append("")
    for cup_id, short_fn, long_fn in collision_bin_items:
        if short_fn is not None:
            lines.append(
                f'static const char kCustomCollisionShort_{cup_id}[] = "{short_fn}";'
            )
        if long_fn is not None:
            lines.append(
                f'static const char kCustomCollisionLong_{cup_id}[] = "{long_fn}";'
            )
    if collision_bin_items:
        lines.append("")

    # kmWritePointer records install the path-table overrides at boot time,
    # before any game code runs. This replaces the previous runtime-install
    # path (which depended on a scene hook firing before course load — and
    # in practice produced a silent no-op on test_course).
    for cup_id, _ in line_bin_items:
        row = PATH_TABLE_BASE + cup_id * ROW_STRIDE
        for off in SLOT_OFFSETS:
            lines.append(
                f"kmWritePointer(0x{row + off:08X}, kCustomLineBin_{cup_id});"
            )
    if line_bin_items:
        lines.append("")

    # Race-params overrides consumed by RaceParamsHook. Listed cupIds
    # apply uniformly across all ccClass/longRound combinations; unlisted ones
    # pass through to the vanilla table read.
    lines.append("struct RaceParamOverride { int cupId; int laps; float time; float bonus; };")
    lines.append("static const struct RaceParamOverride kRaceParamOverrides[] = {")
    for cup_id, laps, time_s, bonus_s in race_params_items:
        lines.append(
            f"    {{ {cup_id}, {laps}, {time_s!r}f, {bonus_s!r}f }},"
        )
    lines.append("};")
    lines.append(
        f"static const int kRaceParamOverrideCount = {len(race_params_items)};"
    )
    lines.append("")

    # AI lap-speed bonus overrides consumed by AICalcLapBonusHook. Listed
    # cupIds short-circuit AI_CalcLapSpeedBonus to a constant; unlisted
    # cupIds let the vanilla rule-table walk run.
    lines.append("struct AILapBonusOverride { int cupId; double value; };")
    lines.append("static const struct AILapBonusOverride kAILapBonusOverrides[] = {")
    for cup_id, value in ai_lap_bonus_items:
        lines.append(f"    {{ {cup_id}, {value!r} }},")
    lines.append("};")
    lines.append(
        f"static const int kAILapBonusOverrideCount = {len(ai_lap_bonus_items)};"
    )
    lines.append("")

    # kmWritePointer records install collision filename pointers for the
    # short and/or long variant collision slots of each listed cupId.
    # Auto_R / C.dat slots are left at vanilla pointers.
    any_collision_write = False
    for cup_id, short_fn, long_fn in collision_bin_items:
        course_base = COLLISION_TABLE_BASE + cup_id * COLLISION_COURSE_STRIDE
        if short_fn is not None:
            addr = course_base + COLLISION_SHORT_COLLISION_OFFSET
            lines.append(
                f"kmWritePointer(0x{addr:08X}, kCustomCollisionShort_{cup_id});"
                f"  // course={cup_id} short collision"
            )
            any_collision_write = True
        if long_fn is not None:
            addr = course_base + COLLISION_LONG_COLLISION_OFFSET
            lines.append(
                f"kmWritePointer(0x{addr:08X}, kCustomCollisionLong_{cup_id});"
                f"  // course={cup_id} long collision"
            )
            any_collision_write = True
    if any_collision_write:
        lines.append("")

    lines += [
        "#endif",
        "",
    ]
    out_path.write_text("\n".join(lines))

    # Emit Riivolution <file> fragments for each line_bin / collision_bin
    # entry so build.sh can splice them into the final XML. Files are
    # served from /mkgp2_patch/<filename> on the Riivolution root
    # (Dolphin's Load/Riivolution/mkgp2_patch/).
    xml_lines = []
    seen = set()
    all_filenames = [fn for _, fn in line_bin_items]
    for _, short_fn, long_fn in collision_bin_items:
        if short_fn is not None:
            all_filenames.append(short_fn)
        if long_fn is not None:
            all_filenames.append(long_fn)
    for filename in all_filenames:
        if filename in seen:
            continue
        seen.add(filename)
        xml_lines.append(
            f'<file disc="/{filename}" '
            f'external="/mkgp2_patch/{filename}" create="true"/>'
        )
    xml_path.write_text("\n".join(xml_lines) + ("\n" if xml_lines else ""))

    print(
        f"Generated {out_path.name}: page2={page2}, "
        f"line_bin={len(line_bin_items)}, collision_bin={len(collision_bin_items)}, "
        f"race_params={len(race_params_items)}, ai_lap_bonus={len(ai_lap_bonus_items)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
