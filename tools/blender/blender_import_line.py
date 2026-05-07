"""
MKGP2 _line.bin Blender Import Script

_line.bin is the racing-line / path file read by PathManager for lap and
checkpoint logic. It differs from _Auto.bin in two ways:
  1. Multiple path VARIANTS in one file (indexed by a u32 offset table at
     the start of the file). Typical courses have 7 or 11 variants.
  2. Waypoint records use type_or_flags == 1 (not 0), and variants are
     terminated by a record whose first field is NEGATIVE (typically
     -waypoint_count) rather than the "u32 count + zeros" form used by
     _Auto.bin.

Each variant is imported as its own Blender mesh object, named
"LineVariant_<index>_<stem>", parented under an empty "<stem>_line" so a
whole file is easy to re-select for export.

File format (all big-endian):
    Header:
        u32 variant_offsets[N]   // byte offsets from file start
        u32 0                    // header terminator
    For each variant at its offset:
        Waypoint (0x10 bytes) x K_i:
            +0x00  uint32  type_or_flags  (1 in vanilla data)
            +0x04  float   posX
            +0x08  float   posY
            +0x0c  float   posZ
        Variant terminator (0x10 bytes):
            +0x00  int32   negative value (observed: -K_i; game only
                           checks "< 0" so exact value is preserved
                           verbatim per variant)
            +0x04..+0x0f   zeros
    Trailing (last 12 bytes of file):
        u32[3]  unknown metadata. Observed pattern {N-1, N-1, N};
                preserved verbatim as scene/root custom property.

Coordinate system (same as other MKGP2 importers):
  Game Y-up -> Blender Z-up:
    Blender X = Game X
    Blender Y = -Game Z
    Blender Z = Game Y
"""

import bpy
import bmesh
import struct
import os
from pathlib import Path

# ============================================================
# CONFIGURATION - leave empty to open file dialog
# ============================================================
BIN_PATH = ""
# ============================================================

RECORD_SIZE = 16
TRAILING_SIZE = 12  # 3 u32s


def read_u32(data, offset):
    return struct.unpack_from(">I", data, offset)[0]

def read_i32(data, offset):
    return struct.unpack_from(">i", data, offset)[0]

def read_f32(data, offset):
    return struct.unpack_from(">f", data, offset)[0]


def game_to_blender(x, y, z):
    return (x, -z, y)


class Waypoint:
    __slots__ = ("type_or_flags", "x", "y", "z")
    def __init__(self, data, offset):
        self.type_or_flags = read_u32(data, offset + 0x00)
        self.x = read_f32(data, offset + 0x04)
        self.y = read_f32(data, offset + 0x08)
        self.z = read_f32(data, offset + 0x0c)


def parse_line_bin(filepath):
    with open(filepath, "rb") as f:
        data = f.read()

    # --- Header: u32 offsets until a 0 terminator ---
    offsets = []
    pos = 0
    while pos + 4 <= len(data):
        v = read_u32(data, pos)
        pos += 4
        if v == 0:
            break
        offsets.append(v)
    if not offsets:
        raise ValueError("No variants found in header")
    header_end = pos  # byte after the u32(0) terminator

    # --- Trailing metadata: last 12 bytes of the file ---
    if len(data) < TRAILING_SIZE:
        raise ValueError("File too small to contain trailing metadata")
    trailing = tuple(
        read_u32(data, len(data) - TRAILING_SIZE + 4 * i) for i in range(3)
    )

    # First variant must start right after the header terminator.
    if offsets[0] != header_end:
        print(
            f"WARNING: first variant offset 0x{offsets[0]:x} does not match "
            f"end of header 0x{header_end:x}"
        )

    # --- Variants ---
    variants = []  # list of (waypoints, terminator_value)
    for i, off in enumerate(offsets):
        end = offsets[i + 1] if i + 1 < len(offsets) else len(data) - TRAILING_SIZE
        # Walk records until we hit one whose first int is negative.
        wps = []
        term_value = None
        p = off
        while p + RECORD_SIZE <= end:
            first = read_i32(data, p)
            if first < 0:
                term_value = first
                # Sanity: the remaining 12 bytes should be zero.
                trail = data[p + 4:p + RECORD_SIZE]
                if any(b != 0 for b in trail):
                    print(
                        f"WARNING: variant {i} terminator at 0x{p:x} has "
                        f"non-zero trailing bytes: {trail.hex()}"
                    )
                p += RECORD_SIZE
                break
            wps.append(Waypoint(data, p))
            p += RECORD_SIZE
        if term_value is None:
            print(f"WARNING: variant {i} ran off end without a terminator")
        if p != end:
            print(
                f"WARNING: variant {i} ends at 0x{p:x} but next section "
                f"starts at 0x{end:x} (gap {end - p} bytes)"
            )
        variants.append((wps, term_value if term_value is not None else -len(wps)))

    return variants, trailing


def _variant_color(index):
    """Evenly spaced hues so each variant is visually distinct in the viewport."""
    import colorsys
    hue = (index * 0.1381966) % 1.0  # golden-ratio spacing
    r, g, b = colorsys.hsv_to_rgb(hue, 0.85, 1.0)
    return (r, g, b, 1.0)


def create_variant_mesh(name, waypoints, terminator_value, variant_index):
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)

    # Per-variant viewport color + per-variant material so color_type='OBJECT'
    # or solid shading in the 3D viewport both show the distinction.
    color = _variant_color(variant_index)
    obj.color = color
    mat = bpy.data.materials.new(name=f"{name}_mat")
    mat.diffuse_color = color
    mat.use_nodes = False
    mesh.materials.append(mat)

    bm = bmesh.new()
    type_layer = bm.verts.layers.int.new("wp_type")

    verts = []
    for wp in waypoints:
        v = bm.verts.new(game_to_blender(wp.x, wp.y, wp.z))
        v[type_layer] = struct.unpack(">i", struct.pack(">I", wp.type_or_flags))[0]
        verts.append(v)

    # Chain: 0-1, 1-2, ..., (n-1)-0 (circular, same convention as
    # blender_import_auto.py).
    n = len(verts)
    for i in range(n):
        try:
            bm.edges.new((verts[i], verts[(i + 1) % n]))
        except ValueError:
            pass  # edge already exists

    bm.to_mesh(mesh)
    bm.free()

    # Preserve the exact terminator first field so round-trip is byte-identical.
    obj["terminator_value"] = terminator_value
    obj["variant_index"] = variant_index
    obj["mkgp2_line_variant"] = True
    return obj


class MKGP2_OT_ImportLine(bpy.types.Operator):
    """Import MKGP2 _line.bin file"""
    bl_idname = "import_mesh.mkgp2_line"
    bl_label = "Import MKGP2 Line"

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(default="*.bin", options={'HIDDEN'})

    def execute(self, context):
        import_line(self.filepath)
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


def import_line(path):
    path = str(path)
    if not os.path.exists(path):
        print(f"ERROR: File not found: {path}")
        return

    print(f"\nLoading: {path}")
    variants, trailing = parse_line_bin(path)
    print(f"  Variants: {len(variants)}, trailing={trailing}")

    stem = Path(path).stem

    # Parent empty to keep all variants together.
    root = bpy.data.objects.new(f"{stem}_line", None)
    root.empty_display_type = 'PLAIN_AXES'
    bpy.context.collection.objects.link(root)
    root["mkgp2_line_root"] = True
    root["trailing_metadata"] = list(trailing)
    root["source_filename"] = os.path.basename(path)

    for i, (wps, term_value) in enumerate(variants):
        xs = [wp.x for wp in wps]
        ys = [wp.y for wp in wps]
        zs = [wp.z for wp in wps]
        if wps:
            print(
                f"  variant {i}: {len(wps)} wps (term={term_value})  "
                f"X[{min(xs):+.1f},{max(xs):+.1f}] "
                f"Y[{min(ys):+.1f},{max(ys):+.1f}] "
                f"Z[{min(zs):+.1f},{max(zs):+.1f}]"
            )
        else:
            print(f"  variant {i}: empty (term={term_value})")

        types = set(wp.type_or_flags for wp in wps)
        if types and types != {1}:
            print(f"    non-1 type_or_flags: {sorted(types)}")

        name = f"LineVariant_{i}_{stem}"
        obj = create_variant_mesh(name, wps, term_value, i)
        obj.parent = root

    print(f"Created: {root.name} with {len(variants)} variant meshes")


def main():
    if BIN_PATH:
        import_line(BIN_PATH)
    else:
        bpy.utils.register_class(MKGP2_OT_ImportLine)
        bpy.ops.import_mesh.mkgp2_line('INVOKE_DEFAULT')


if __name__ == "__main__":
    main()
