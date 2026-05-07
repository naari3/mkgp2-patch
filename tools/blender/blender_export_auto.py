"""
MKGP2 _Auto.bin Blender Export Script

Counterpart to blender_import_auto.py.

_Auto.bin is NOT the PathManager/lap-judgment waypoint file (that's
_line.bin, handled by blender_export_line.py). Its runtime purpose is
not yet confirmed — kept here for round-trip experiments on existing
vanilla _Auto.bin files.

Usage:
  1. Have a Blender mesh representing the path. Either import one with
     blender_import_auto.py (preserves vertex order + "wp_type" layer +
     "terminator_count" custom property) or build one by hand — see the
     "Building from scratch" section below.
  2. Select the mesh object in the 3D viewport.
  3. Edit OUTPUT_PATH below (leave empty to open a save dialog).
  4. Run script.

Output format matches the importer: N records (16 bytes each, ordered
by Blender vertex index) followed by 1 terminator record (u32 count,
12 bytes of zeros). Total file size = (N + 1) * 16.

Coordinate system (same as blender_import_collision.py):
  Blender Z-up -> Game Y-up:
    Game X = Blender X
    Game Y = Blender Z
    Game Z = -Blender Y

Building a path from scratch:
  - The vertex order in the mesh is the play order. Blender assigns
    indices as vertices are created; verify with Mesh Edit Mode >
    Overlays > "Indices" if needed.
  - Edges are not required for export — vertex positions alone drive the
    file. The chain edges produced by the importer are only for visual
    reference.
  - To set the "wp_type" field (+0x00 of each record, always 0 in
    vanilla data), use Mesh > Attributes > "wp_type" (int). If the
    attribute is absent, every record gets 0.
"""

import bpy
import bmesh
import struct
import os

# ============================================================
# CONFIGURATION - leave empty to open file dialog
# ============================================================
OUTPUT_PATH = ""
# ============================================================

RECORD_SIZE = 16


def pack_u32(v):
    return struct.pack(">I", v & 0xffffffff)

def pack_i32_as_u32(v):
    # Blender int attributes are signed; reinterpret.
    return struct.pack(">I", struct.unpack(">I", struct.pack(">i", v))[0])

def pack_f32(v):
    return struct.pack(">f", v)


def blender_to_game(x, y, z):
    return (x, z, -y)


def collect_records(obj):
    """Walk obj's mesh vertices in index order. Return list of
    (type_or_flags, gameX, gameY, gameZ)."""
    if obj is None or obj.type != 'MESH':
        raise RuntimeError("Select a mesh object before exporting")

    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()

    type_layer = bm.verts.layers.int.get("wp_type")

    records = []
    world_matrix = obj.matrix_world
    for v in bm.verts:
        rec_type = v[type_layer] if type_layer else 0
        # Apply world matrix so the exporter respects object transform.
        world_co = world_matrix @ v.co
        gx, gy, gz = blender_to_game(world_co.x, world_co.y, world_co.z)
        records.append((rec_type, gx, gy, gz))

    bm.free()
    return records


def write_auto_bin(path, records, terminator_count=None):
    if not records:
        raise RuntimeError("No records to export (mesh has no vertices)")

    if terminator_count is None:
        terminator_count = len(records)

    buf = bytearray()
    for rec_type, x, y, z in records:
        buf += pack_i32_as_u32(rec_type)
        buf += pack_f32(x)
        buf += pack_f32(y)
        buf += pack_f32(z)
    # Terminator: {u32 count, 12 bytes of zeros}.
    buf += pack_u32(terminator_count)
    buf += b"\x00" * 12

    assert len(buf) == (len(records) + 1) * RECORD_SIZE

    with open(path, "wb") as f:
        f.write(buf)
    return len(buf)


class MKGP2_OT_ExportAuto(bpy.types.Operator):
    """Export MKGP2 _Auto.bin from selected mesh"""
    bl_idname = "export_mesh.mkgp2_auto"
    bl_label = "Export MKGP2 Auto"

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filename_ext = ".bin"
    filter_glob: bpy.props.StringProperty(default="*.bin", options={'HIDDEN'})

    def execute(self, context):
        export_auto(self.filepath, context.active_object)
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


def export_auto(path, obj=None):
    path = str(path)
    if obj is None:
        obj = bpy.context.active_object
    if obj is None:
        print("ERROR: select a mesh object first")
        return

    print(f"\nExporting from: {obj.name}")
    records = collect_records(obj)
    print(f"  Records: {len(records)}")

    # Preserve original terminator_count if the object was imported.
    terminator_count = obj.get("terminator_count", len(records))
    if terminator_count != len(records):
        print(
            f"  NOTE: preserving terminator_count={terminator_count} from "
            f"import (differs from current vertex count {len(records)})"
        )

    size = write_auto_bin(path, records, terminator_count)
    print(f"Wrote {size} bytes to {path}")


def main():
    if OUTPUT_PATH:
        export_auto(OUTPUT_PATH)
    else:
        bpy.utils.register_class(MKGP2_OT_ExportAuto)
        bpy.ops.export_mesh.mkgp2_auto('INVOKE_DEFAULT')


if __name__ == "__main__":
    main()
