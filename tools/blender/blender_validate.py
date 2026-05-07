"""
MKGP2 course validators.

Pure functions that consume Blender objects + collections and return
human-readable issue lists. Invoked from the addon's "Validate" operators
to catch mistakes before the user discovers them in Dolphin.

Design notes:
  - Each validator yields strings (one per issue). An empty list means
    the asset is OK.
  - Round-trip checks (line / auto) write the asset to a temp file and
    re-parse it. They depend on the corresponding import/export modules
    being importable, so the addon does the imports and passes them in.
  - We never raise: a missing custom prop or a bad mesh becomes an
    issue string, not an exception, so a single bad asset never aborts
    the whole validate sweep.
"""

import os
import struct
import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Collision (CollisionMesh + WallSegments)
# ---------------------------------------------------------------------------

def validate_collision(col_obj, wall_obj=None,
                       *, max_examples=4, fp_tolerance=1e-3):
    """Geometric sanity for the collision pair.

    Checks:
      1. CollisionMesh has the grid props expected by the exporter.
      2. Every triangle vertex falls inside the declared grid AABB.
      3. Triangles are non-degenerate (any pair of vertices distinct
         and the cross product non-zero).
      4. Wall segments live at game-Y == 0 (which is Blender Z == 0
         after the Y-up -> Z-up swap in blender_export_collision).
    """
    issues = []

    if col_obj is None:
        issues.append("collision: no CollisionMesh object found")
        return issues

    required_props = ("grid_width", "grid_height", "cell_size_x", "cell_size_z",
                      "grid_origin_x", "grid_origin_z")
    missing = [p for p in required_props if p not in col_obj.keys()]
    if missing:
        issues.append(
            f"collision[{col_obj.name}]: missing custom props "
            f"{', '.join(missing)} (re-import to regenerate)"
        )
        return issues

    grid_w = int(col_obj["grid_width"])
    grid_h = int(col_obj["grid_height"])
    cell_x = float(col_obj["cell_size_x"])
    cell_z = float(col_obj["cell_size_z"])
    origin_x = float(col_obj["grid_origin_x"])
    origin_z = float(col_obj["grid_origin_z"])

    # Grid AABB in *game* coordinates:
    #   game_x in [-origin_x .. -origin_x + grid_w * cell_x]
    #   game_z in [-origin_z .. -origin_z + grid_h * cell_z]
    # blender_export_collision applies game = (B.x, B.z, -B.y), so
    # game_x == B.x  and  game_z == -B.y. The grid bounds therefore
    # live in (B.x, B.y) plane; B.y is negated relative to game_z.
    gx_min = -origin_x
    gx_max = -origin_x + grid_w * cell_x
    gz_min = -origin_z
    gz_max = -origin_z + grid_h * cell_z
    bx_min, bx_max = gx_min, gx_max
    by_min, by_max = -gz_max, -gz_min  # because game_z = -B.y

    mesh = col_obj.data
    out_of_bounds = []
    degenerate = []
    if mesh is None or len(mesh.polygons) == 0:
        issues.append(f"collision[{col_obj.name}]: no triangles in mesh")
    else:
        # Apply object world matrix so users who moved the mesh see
        # the right answer.
        mw = col_obj.matrix_world
        for poly in mesh.polygons:
            if len(poly.vertices) != 3:
                issues.append(
                    f"collision[{col_obj.name}] poly#{poly.index}: "
                    f"non-triangle ({len(poly.vertices)} verts)"
                )
                continue
            v0 = mw @ mesh.vertices[poly.vertices[0]].co
            v1 = mw @ mesh.vertices[poly.vertices[1]].co
            v2 = mw @ mesh.vertices[poly.vertices[2]].co
            for v in (v0, v1, v2):
                if not (bx_min - fp_tolerance <= v.x <= bx_max + fp_tolerance and
                        by_min - fp_tolerance <= v.y <= by_max + fp_tolerance):
                    out_of_bounds.append((poly.index, v.x, v.y, v.z))
                    break
            # Degenerate triangle test
            ax, ay, az = v1.x - v0.x, v1.y - v0.y, v1.z - v0.z
            bx, by, bz = v2.x - v0.x, v2.y - v0.y, v2.z - v0.z
            cx = ay * bz - az * by
            cy = az * bx - ax * bz
            cz_ = ax * by - ay * bx
            if cx * cx + cy * cy + cz_ * cz_ < fp_tolerance * fp_tolerance:
                degenerate.append(poly.index)

    if out_of_bounds:
        sample = ", ".join(
            f"#{i}@({x:.1f},{y:.1f},{z:.1f})"
            for (i, x, y, z) in out_of_bounds[:max_examples]
        )
        more = f" + {len(out_of_bounds) - max_examples} more" \
            if len(out_of_bounds) > max_examples else ""
        issues.append(
            f"collision[{col_obj.name}]: {len(out_of_bounds)} triangle(s) "
            f"outside grid bounds (Blender XY in "
            f"[{bx_min:.0f},{bx_max:.0f}] x [{by_min:.0f},{by_max:.0f}]): "
            f"{sample}{more}"
        )
    if degenerate:
        sample = ", ".join(f"#{i}" for i in degenerate[:max_examples])
        more = f" + {len(degenerate) - max_examples} more" \
            if len(degenerate) > max_examples else ""
        issues.append(
            f"collision[{col_obj.name}]: {len(degenerate)} degenerate "
            f"triangle(s) (zero-area): {sample}{more}"
        )

    # Wall segments: each vertex must have Blender Z == 0 (= game Y == 0)
    if wall_obj is not None and wall_obj.data is not None:
        wmw = wall_obj.matrix_world
        bad_walls = []
        for v in wall_obj.data.vertices:
            wv = wmw @ v.co
            if abs(wv.z) > fp_tolerance:
                bad_walls.append((v.index, wv.z))
        if bad_walls:
            sample = ", ".join(
                f"v#{i}@Z={z:.3f}" for (i, z) in bad_walls[:max_examples]
            )
            more = f" + {len(bad_walls) - max_examples} more" \
                if len(bad_walls) > max_examples else ""
            issues.append(
                f"walls[{wall_obj.name}]: {len(bad_walls)} wall vertex/vertices "
                f"off Z=0 (game-Y plane): {sample}{more}"
            )

    return issues


# ---------------------------------------------------------------------------
# Line round-trip
# ---------------------------------------------------------------------------

def validate_line_root(root_obj, *, line_imp, line_exp,
                       max_examples=3):
    """Round-trip a line root via export → temp file → re-parse.

    Compares variant count, per-variant waypoint count, and per-variant
    terminator. We do NOT compare floats directly because export quantises
    and Blender stores its own rotation: the structural shape is the
    operationally important thing.
    """
    issues = []
    if root_obj is None:
        issues.append("line: no _line empty in course")
        return issues

    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "_validate.bin")
        try:
            line_exp.export_line(out, obj=root_obj)
        except Exception as ex:
            issues.append(f"line[{root_obj.name}]: export raised {type(ex).__name__}: {ex}")
            return issues

        if not os.path.exists(out) or os.path.getsize(out) == 0:
            issues.append(f"line[{root_obj.name}]: export wrote no file or zero bytes")
            return issues

        try:
            variants, trailing = line_imp.parse_line_bin(out)
        except Exception as ex:
            issues.append(f"line[{root_obj.name}]: re-parse raised {type(ex).__name__}: {ex}")
            return issues

    # Pull what export *should* have written from the Blender state.
    children = [c for c in root_obj.children
                if c.type == 'MESH' and c.name.startswith("LineVariant_")]

    def _key(c):
        try:
            return int(c.name.split("_")[1])
        except (ValueError, IndexError):
            return 0
    children.sort(key=_key)

    if children:
        # If REPLICATE_TO_N kicked in (1 child + N variants), accept it.
        if len(children) == 1 and len(variants) > 1:
            pass
        elif len(variants) != len(children):
            issues.append(
                f"line[{root_obj.name}]: round-trip variant count differs "
                f"(blender={len(children)} vs round-trip={len(variants)})"
            )

        # Per-variant: when 1:1 mapping, compare wp counts.
        if len(variants) == len(children):
            for i, (child, (wps, term)) in enumerate(zip(children, variants)):
                # Subtract any auxiliary waypoints not stored as edges
                expected_n = max(len(child.data.edges), len(child.data.vertices) - 1)
                if abs(len(wps) - expected_n) > 1:
                    issues.append(
                        f"line[{root_obj.name}] variant {i} ({child.name}): "
                        f"wp count {len(wps)} differs from blender ~{expected_n}"
                    )
                stored_term = child.get("terminator_value")
                if stored_term is not None and int(stored_term) != int(term):
                    issues.append(
                        f"line[{root_obj.name}] variant {i}: terminator "
                        f"{int(term)} differs from stored {int(stored_term)}"
                    )

    return issues


# ---------------------------------------------------------------------------
# Auto path round-trip
# ---------------------------------------------------------------------------

def validate_auto_obj(auto_obj, *, auto_imp, auto_exp,
                      max_examples=3):
    """Round-trip an auto-path mesh and compare record count."""
    issues = []
    if auto_obj is None:
        issues.append("auto: no Auto_* mesh in course")
        return issues

    if auto_obj.data is None or len(auto_obj.data.vertices) == 0:
        issues.append(f"auto[{auto_obj.name}]: empty mesh")
        return issues

    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "_validate.bin")
        try:
            auto_exp.export_auto(out, obj=auto_obj)
        except Exception as ex:
            issues.append(f"auto[{auto_obj.name}]: export raised "
                          f"{type(ex).__name__}: {ex}")
            return issues

        if not os.path.exists(out) or os.path.getsize(out) == 0:
            issues.append(f"auto[{auto_obj.name}]: export wrote no file or zero bytes")
            return issues

        try:
            parsed = auto_imp.parse_auto_bin(out)
        except Exception as ex:
            issues.append(f"auto[{auto_obj.name}]: re-parse raised "
                          f"{type(ex).__name__}: {ex}")
            return issues

    # parse_auto_bin returns (records, terminator_count). Pick the list.
    records = parsed[0] if isinstance(parsed, tuple) else parsed
    expected = len(auto_obj.data.vertices)
    if abs(len(records) - expected) > 1:
        issues.append(
            f"auto[{auto_obj.name}]: round-trip record count {len(records)} "
            f"differs from blender vertex count {expected}"
        )
    return issues


# ---------------------------------------------------------------------------
# Naming convention
# ---------------------------------------------------------------------------

_NAMING_PREFIXES = ("CollisionMesh_", "WallSegments_", "LineVariant_", "Auto_")


def validate_naming(course_coll, *, max_examples=6):
    """Flag objects in the course collection that don't fit the
    convention used by the importers/exporters.

    Allowed:
      - CollisionMesh_<stem>      (mesh)
      - WallSegments_<stem>       (mesh)
      - <stem>_line               (empty)
      - LineVariant_<i>_<stem>    (mesh under the line empty)
      - Auto_<stem>               (mesh)
      - HSD bundle members (live in a child collection, not directly
        in course_coll.objects)
    """
    issues = []
    weird = []

    # Direct course-collection members (HSD bundles live in nested colls
    # and don't show up in objects iteration).
    for o in course_coll.objects:
        if o.type == 'EMPTY' and o.name.endswith("_line"):
            continue
        if o.type == 'MESH' and any(o.name.startswith(p) for p in _NAMING_PREFIXES):
            continue
        weird.append(o.name)

    if weird:
        sample = ", ".join(weird[:max_examples])
        more = f" + {len(weird) - max_examples} more" \
            if len(weird) > max_examples else ""
        issues.append(
            f"naming: {len(weird)} object(s) in '{course_coll.name}' don't fit "
            f"convention (CollisionMesh_*, WallSegments_*, <stem>_line, "
            f"LineVariant_*, Auto_*): {sample}{more}"
        )

    return issues


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

def validate_course(course_coll, *, line_imp, line_exp, auto_imp, auto_exp):
    """Run every validator on the course collection.

    Resolves member objects by the same conventions the exporter uses so
    a green Validate Course == a clean export run.

    Returns a list of issue strings (one per finding).
    """
    issues = []

    # Resolve members (same logic export_course uses)
    col_obj = next(
        (o for o in course_coll.all_objects if o.name.startswith("CollisionMesh")),
        None,
    )
    wall_obj = next(
        (o for o in course_coll.all_objects if o.name.startswith("WallSegments")),
        None,
    )
    line_root = next(
        (o for o in course_coll.all_objects
         if o.type == 'EMPTY' and o.name.endswith("_line")),
        None,
    )
    auto_f = next(
        (o for o in course_coll.all_objects
         if o.type == 'MESH' and o.get("mkgp2_auto_role") == "F"),
        None,
    )
    auto_r = next(
        (o for o in course_coll.all_objects
         if o.type == 'MESH' and o.get("mkgp2_auto_role") == "R"),
        None,
    )

    # Collision
    if col_obj is not None or wall_obj is not None:
        issues.extend(validate_collision(col_obj, wall_obj))
    elif course_coll.get("mkgp2_collision_bin"):
        issues.append("collision: course has mkgp2_collision_bin set but "
                      "no CollisionMesh object")

    # Line
    if line_root is not None:
        issues.extend(validate_line_root(
            line_root, line_imp=line_imp, line_exp=line_exp))
    elif course_coll.get("mkgp2_line_bin"):
        issues.append("line: course has mkgp2_line_bin set but no _line empty")

    # Auto F/R
    for role_label, obj, prop in (
        ("F", auto_f, "mkgp2_auto_f_bin"),
        ("R", auto_r, "mkgp2_auto_r_bin"),
    ):
        if obj is not None:
            sub = validate_auto_obj(
                obj, auto_imp=auto_imp, auto_exp=auto_exp)
            issues.extend(f"auto-{role_label}: {s.split(': ', 1)[-1]}"
                          if s.startswith("auto[") else s for s in sub)
        elif course_coll.get(prop):
            issues.append(
                f"auto-{role_label}: course has {prop} set but no Auto_* mesh "
                f"with mkgp2_auto_role='{role_label}'"
            )

    # Naming
    issues.extend(validate_naming(course_coll))

    return issues
