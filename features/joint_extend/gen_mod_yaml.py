#!/usr/bin/env python3
"""Generate mod_joints.yaml from course_joints.yaml, listing joints not in the hardcoded table."""

import yaml
import sys
from pathlib import Path

HARDCODED_SUFFIXES = {
    "share_road_joint", "short_road_joint", "long_road_joint",
    "short_branch_joint", "long_branch_joint",
    "short_occlusion_joint", "long_occlusion_joint",
    "opac_joint", "alpha_joint",
    "short_alpha_joint", "long_alpha_joint",
    "short_normal_joint", "long_normal_joint",
    "nofog_joint",
    "short_reverse_joint", "long_reverse_joint",
}

PREFIXES = {
    "MR_highway": "MR_highway",
    "DK_jungle": "DK_jungle",
    "WC_dcity": "WC_dcity",
    "PC_land": "PC_land",
    "KP_castle": "KP_castle",
    "RB_road": "RB_road",
    "YI_land": "YI_land",
    "DN_stadium": "DN_stadium",
}

def get_hardcoded_set(prefix):
    return {f"{prefix}_{s}" for s in HARDCODED_SUFFIXES}

def main():
    base = Path(__file__).parent.parent
    with open(base / "course_joints.yaml") as f:
        data = yaml.safe_load(f)

    result = {}

    for course_prefix, variants in sorted(data.items()):
        prefix = PREFIXES.get(course_prefix, course_prefix)
        hc_set = get_hardcoded_set(prefix)

        course_entry = {}
        for variant_name, variant_data in sorted(variants.items()):
            joints = variant_data.get("joints") or []
            extra = [j for j in joints if j not in hc_set]
            hardcoded_present = [j for j in joints if j in hc_set]

            course_entry[variant_name] = {
                "file": variant_data["file"],
                "hardcoded": hardcoded_present,
                "extra": extra,
            }

        result[course_prefix] = course_entry

    output = base.parent / "mkgp2docs" / "mkgp2-patch" / "mod_joints.yaml"
    # Use manual output for clean formatting
    with open(output, "w") as f:
        f.write("# MKGP2 Course Joint Registry for Kamek Patch\n")
        f.write("# 'hardcoded' = handled by existing game code (18-slot table)\n")
        f.write("# 'extra' = present in .dat but NOT in hardcoded table\n")
        f.write("# Add custom joints to 'extra' list to have them loaded by the patch\n")
        f.write("#\n")
        f.write("# Generated from course_joints.yaml\n\n")

        for course_prefix, variants in sorted(result.items()):
            f.write(f"{course_prefix}:\n")
            for variant_name, vdata in sorted(variants.items()):
                f.write(f"  {variant_name}:\n")
                f.write(f"    file: \"{vdata['file']}\"\n")

                f.write(f"    hardcoded:\n")
                for j in vdata["hardcoded"]:
                    f.write(f"      - {j}\n")
                if not vdata["hardcoded"]:
                    f.write(f"      []\n")

                f.write(f"    extra:\n")
                for j in vdata["extra"]:
                    f.write(f"      - {j}\n")
                if not vdata["extra"]:
                    f.write(f"      []\n")

            f.write("\n")

    print(f"Written to {output}")

    # Summary
    total_extra = 0
    for course_prefix, variants in result.items():
        for vname, vdata in variants.items():
            total_extra += len(vdata["extra"])
    print(f"Total extra joints across all variants: {total_extra}")

if __name__ == "__main__":
    main()
