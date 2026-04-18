#!/usr/bin/env python3
"""Generate generated_joints.h from course_joints.yaml"""

import yaml
import sys
from pathlib import Path

def main():
    patch_dir = Path(__file__).parent
    yaml_path = patch_dir / "course_joints.yaml"
    out_path = patch_dir / "generated_joints.h"

    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    lines = []
    lines.append("// Auto-generated from course_joints.yaml - do not edit")
    lines.append("#ifndef GENERATED_JOINTS_H")
    lines.append("#define GENERATED_JOINTS_H")
    lines.append("")

    for course, cdata in data.items():
        joints = cdata.get("joints") or []
        # Deduplicate while preserving order, skip "null"
        seen = set()
        unique = []
        for j in joints:
            if j is None or j == "null" or j in seen:
                continue
            seen.add(j)
            unique.append(j)

        array_name = f"sJoints_{course}"
        lines.append(f"// {course}: {len(unique)} unique joints")
        lines.append(f"static const char* {array_name}[] = {{")
        for j in unique:
            lines.append(f'    "{j}",')
        lines.append("    0")
        lines.append("};")
        lines.append("")

    # Course lookup table
    lines.append("struct CourseJointDef {")
    lines.append("    int courseId;")
    lines.append("    const char** joints;")
    lines.append("    int count;")
    lines.append("};")
    lines.append("")

    course_ids = {
        "MR_highway": 1, "DK_jungle": 2, "WC_dcity": 3, "PC_land": 4,
        "KP_castle": 5, "RB_road": 6, "YI_land": 7, "DN_stadium": 8,
    }

    lines.append("static const CourseJointDef sCourseJointDefs[] = {")
    for course, cdata in data.items():
        joints = cdata.get("joints") or []
        seen = set()
        count = 0
        for j in joints:
            if j is not None and j != "null" and j not in seen:
                seen.add(j)
                count += 1
        cid = course_ids.get(course, -1)
        lines.append(f"    {{ {cid}, sJoints_{course}, {count} }},")
    lines.append("    { -1, 0, 0 }")
    lines.append("};")
    lines.append("")
    lines.append("#endif")
    lines.append("")

    with open(out_path, "w") as f:
        f.write("\n".join(lines))

    print(f"Generated {out_path.name}: {len(data)} courses")

if __name__ == "__main__":
    main()
