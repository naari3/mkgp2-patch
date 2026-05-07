"""
MKGP2 _Auto.bin Blender Import Script

_Auto.bin is NOT the PathManager/lap-judgment waypoint file — that's
_line.bin (see blender_import_line.py). _Auto.bin's runtime purpose is
not yet confirmed; candidates include AI spline data, replay cameras,
or an authoring-tool leftover. Format-wise it's a single flat list of
XYZ points with a trailing count record.

Usage:
  1. Open Blender
  2. Switch to Scripting workspace
  3. Open this script
  4. Edit BIN_PATH to point to your _Auto.bin file (leave empty for file dialog)
  5. Run script

The script creates:
  - "Auto_<name>": Mesh with vertices at each record's XYZ position.
    Consecutive records are connected by edges (and last -> first as a
    closing edge, since the game treats paths as circular).
  - Per-vertex int attribute "wp_type": the +0x00 field of each record
    (meaning not confirmed; always 0 in vanilla data).
  - Object custom property "terminator_count": the count stored in the
    trailing terminator record (usually equals vertex count; preserved
    verbatim for roundtrip).

File format (all big-endian):
    Record (0x10 bytes) x N:
        +0x00  uint32  type_or_flags  (always 0 in vanilla data)
        +0x04  float   posX
        +0x08  float   posY
        +0x0C  float   posZ
    Terminator record (0x10 bytes, exactly one at end):
        +0x00  uint32  count (== N)
        +0x04..+0x0f  zeros
    Total file size = (N + 1) * 16.

Coordinate system (same as blender_import_collision.py):
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


def read_u32(data, offset):
    return struct.unpack_from(">I", data, offset)[0]

def read_f32(data, offset):
    return struct.unpack_from(">f", data, offset)[0]


def game_to_blender(x, y, z):
    return (x, -z, y)


class AutoRecord:
    __slots__ = ("type_or_flags", "x", "y", "z")
    def __init__(self, data, offset):
        self.type_or_flags = read_u32(data, offset + 0x00)
        self.x = read_f32(data, offset + 0x04)
        self.y = read_f32(data, offset + 0x08)
        self.z = read_f32(data, offset + 0x0C)


def parse_auto_bin(filepath):
    with open(filepath, "rb") as f:
        data = f.read()

    if len(data) % RECORD_SIZE != 0:
        raise ValueError(
            f"File size {len(data)} is not a multiple of {RECORD_SIZE}"
        )
    total_records = len(data) // RECORD_SIZE
    if total_records < 2:
        raise ValueError(
            f"File has only {total_records} records; need at least "
            "1 record + 1 terminator"
        )

    # Last record is the terminator: {u32 count, 12 bytes of zeros}.
    term_offset = (total_records - 1) * RECORD_SIZE
    terminator_count = read_u32(data, term_offset)
    trailing = data[term_offset + 4:term_offset + 16]
    if any(b != 0 for b in trailing):
        print(
            f"WARNING: terminator record at offset 0x{term_offset:x} has "
            f"non-zero trailing bytes: {trailing.hex()}"
        )

    record_count = total_records - 1
    if terminator_count != record_count:
        print(
            f"WARNING: terminator count {terminator_count} != record "
            f"count {record_count}; using {record_count} from file size"
        )

    records = []
    for i in range(record_count):
        records.append(AutoRecord(data, i * RECORD_SIZE))

    return records, terminator_count


def create_auto_mesh(name, records, terminator_count):
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)

    bm = bmesh.new()
    type_layer = bm.verts.layers.int.new("wp_type")

    verts = []
    for rec in records:
        v = bm.verts.new(game_to_blender(rec.x, rec.y, rec.z))
        # Blender int layers are signed; reinterpret u32 via struct dance.
        v[type_layer] = struct.unpack(">i", struct.pack(">I", rec.type_or_flags))[0]
        verts.append(v)

    # Chain: 0-1, 1-2, ..., (n-1)-0  (circular).
    n = len(verts)
    for i in range(n):
        try:
            bm.edges.new((verts[i], verts[(i + 1) % n]))
        except ValueError:
            pass  # edge already exists

    bm.to_mesh(mesh)
    bm.free()

    obj["terminator_count"] = terminator_count
    obj["mkgp2_auto"] = True  # tag for exporter
    return obj


class MKGP2_OT_ImportAuto(bpy.types.Operator):
    """Import MKGP2 _Auto.bin file"""
    bl_idname = "import_mesh.mkgp2_auto"
    bl_label = "Import MKGP2 Auto"

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(default="*.bin", options={'HIDDEN'})

    def execute(self, context):
        import_auto(self.filepath)
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


def import_auto(path):
    path = str(path)
    if not os.path.exists(path):
        print(f"ERROR: File not found: {path}")
        return

    print(f"\nLoading: {path}")
    records, term_count = parse_auto_bin(path)
    print(f"  Records: {len(records)} (terminator count={term_count})")

    xs = [rec.x for rec in records]
    ys = [rec.y for rec in records]
    zs = [rec.z for rec in records]
    print(
        f"  Game X: [{min(xs):+.2f}, {max(xs):+.2f}]  "
        f"Y: [{min(ys):+.2f}, {max(ys):+.2f}]  "
        f"Z: [{min(zs):+.2f}, {max(zs):+.2f}]"
    )

    types = set(rec.type_or_flags for rec in records)
    if types != {0}:
        print(f"  Non-zero type_or_flags values present: {sorted(types)}")

    obj_name = f"Auto_{Path(path).stem}"
    obj = create_auto_mesh(obj_name, records, term_count)
    print(f"Created: {obj.name}")


def main():
    if BIN_PATH:
        import_auto(BIN_PATH)
    else:
        bpy.utils.register_class(MKGP2_OT_ImportAuto)
        bpy.ops.import_mesh.mkgp2_auto('INVOKE_DEFAULT')


if __name__ == "__main__":
    main()
