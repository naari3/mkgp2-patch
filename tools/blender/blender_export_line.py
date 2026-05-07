"""
MKGP2 _line.bin Blender Export Script

Counterpart to blender_import_line.py.

Usage:
  1. Have a scene imported by blender_import_line.py, OR build the same
     object hierarchy by hand:
        <root Empty>  (custom prop "mkgp2_line_root" = true)
          |- LineVariant_0_<stem>   (custom prop "variant_index" = 0)
          |- LineVariant_1_<stem>   (custom prop "variant_index" = 1)
          |- ...
     Each variant mesh carries a "wp_type" int vertex attribute
     (defaults to 1 when missing — the value written by vanilla data)
     and a "terminator_value" object custom property (defaults to
     -vertex_count when missing).
  2. Select either the root empty OR any one of the variant meshes in
     the 3D viewport. The script finds the root from the selection.
  3. Edit OUTPUT_PATH below (leave empty for a save dialog).
  4. Run the script.

Replication mode:
  If REPLICATE_TO_N below is non-zero AND the root has exactly one
  variant mesh child, the exporter serializes that single mesh into all
  N variant slots (identical waypoints in each slot). Useful for a
  minimal test_course file where you only need one line but the game
  requires 7 slots (CourseData_GetDefaultPathKey reads slot 6
  unconditionally — a shorter file crashes).

The trailing 12-byte metadata (3 u32s, observed pattern {N-1, N-1, N})
is read from root["trailing_metadata"] if present, otherwise synthesized
as [N-1, N-1, N] where N is the variant count.

Coordinate system (same as blender_import_line.py):
  Blender Z-up -> Game Y-up:
    Game X = Blender X
    Game Y = Blender Z
    Game Z = -Blender Y
"""

import bpy
import bmesh
import struct
import os

# ============================================================
# CONFIGURATION - leave empty to open file dialog
# ============================================================
OUTPUT_PATH = ""
# Replicate a single variant mesh into N variant slots. 0 disables.
# Set to 7 for a minimal test_course _line.bin.
REPLICATE_TO_N = 7
# ============================================================

RECORD_SIZE = 16
TRAILING_SIZE = 12


def pack_u32(v):
    return struct.pack(">I", v & 0xffffffff)

def pack_i32(v):
    return struct.pack(">i", v)

def pack_f32(v):
    return struct.pack(">f", v)


def blender_to_game(x, y, z):
    return (x, z, -y)


def find_root_and_variants(obj):
    """From any selected object, locate the line root empty and its
    ordered list of variant mesh children.

    If the selection is a standalone Mesh without the mkgp2 markers
    (e.g. a raw Plane used to draw a single line by hand), treat it as
    a lone variant — no root empty required. REPLICATE_TO_N then
    expands it into N identical slots on export.

    Returns (root, [variant_obj, ...]). root may be None for the
    standalone case; callers must not dereference root blindly (but
    export_line() already gates trailing_metadata on `root is not
    None`)."""
    if obj is None:
        raise RuntimeError("Select the line root empty or a mesh to export")

    # Walk up to find a mkgp2_line_root; or, if the selection IS a root
    # empty, use it directly.
    root = obj
    while root is not None and not root.get("mkgp2_line_root"):
        root = root.parent

    if root is None:
        # Fallback: no root empty in the selection chain. Accept a bare
        # mesh object as a single variant.
        if obj.type != 'MESH':
            raise RuntimeError(
                f"'{obj.name}' is {obj.type}, not a mesh. Select either "
                "a mesh or a mkgp2_line_root empty."
            )
        print(
            f"  no mkgp2_line_root in ancestry of '{obj.name}'; "
            "treating selection as a standalone line mesh"
        )
        return None, [obj]

    children = [
        c for c in bpy.data.objects
        if c.parent is root and c.type == 'MESH' and c.get("mkgp2_line_variant")
    ]
    if not children:
        raise RuntimeError(
            f"Root '{root.name}' has no LineVariant mesh children"
        )

    # Sort by explicit variant_index; fall back to name if missing.
    def key(c):
        idx = c.get("variant_index")
        return (idx if idx is not None else 0, c.name)
    children.sort(key=key)

    # Warn if indices are not 0..N-1.
    expected = list(range(len(children)))
    actual = [int(c.get("variant_index", i)) for i, c in enumerate(children)]
    if actual != expected:
        print(
            f"WARNING: variant_index values {actual} do not form 0..{len(children)-1}; "
            "exporting in sorted order anyway"
        )

    return root, children


def collect_variant_waypoints(obj):
    """Walk obj's mesh vertices in index order. Return list of
    (type_or_flags, gameX, gameY, gameZ)."""
    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()

    type_layer = bm.verts.layers.int.get("wp_type")

    waypoints = []
    world_matrix = obj.matrix_world
    for v in bm.verts:
        if type_layer is not None:
            raw = v[type_layer]
            wp_type = struct.unpack(">I", struct.pack(">i", raw))[0]
        else:
            wp_type = 1  # vanilla _line.bin uses 1
        world_co = world_matrix @ v.co
        gx, gy, gz = blender_to_game(world_co.x, world_co.y, world_co.z)
        waypoints.append((wp_type, gx, gy, gz))

    bm.free()
    return waypoints


def write_line_bin(path, variants, trailing):
    """variants: list of (waypoints, terminator_value).
    trailing: iterable of exactly 3 u32 values."""
    n = len(variants)
    if n == 0:
        raise RuntimeError("No variants to export")

    header_size = 4 * (n + 1)  # N offsets + u32 0 terminator

    # First compute each variant's byte size and its offset.
    offsets = []
    cursor = header_size
    variant_blobs = []
    for wps, term_value in variants:
        offsets.append(cursor)
        vbuf = bytearray()
        for wp_type, x, y, z in wps:
            vbuf += pack_u32(wp_type)
            vbuf += pack_f32(x)
            vbuf += pack_f32(y)
            vbuf += pack_f32(z)
        # Terminator: 16 bytes, first int negative, rest zero.
        vbuf += pack_i32(term_value)
        vbuf += b"\x00" * 12
        assert len(vbuf) == (len(wps) + 1) * RECORD_SIZE
        variant_blobs.append(vbuf)
        cursor += len(vbuf)

    buf = bytearray()
    for off in offsets:
        buf += pack_u32(off)
    buf += pack_u32(0)  # header terminator
    for blob in variant_blobs:
        buf += blob
    for v in trailing:
        buf += pack_u32(v)
    assert len(buf) == cursor + TRAILING_SIZE

    with open(path, "wb") as f:
        f.write(buf)
    return len(buf)


class MKGP2_OT_ExportLine(bpy.types.Operator):
    """Export MKGP2 _line.bin from selected root/variant"""
    bl_idname = "export_mesh.mkgp2_line"
    bl_label = "Export MKGP2 Line"

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filename_ext = ".bin"
    filter_glob: bpy.props.StringProperty(default="*.bin", options={'HIDDEN'})

    def execute(self, context):
        export_line(self.filepath, context.active_object)
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


def export_line(path, obj=None):
    path = str(path)
    if obj is None:
        obj = bpy.context.active_object

    root, variant_objs = find_root_and_variants(obj)
    root_label = root.name if root is not None else "<standalone mesh>"
    print(f"\nExporting line from root: {root_label}  ({len(variant_objs)} variants)")

    # Replication: one mesh becomes N identical variant slots.
    if REPLICATE_TO_N and len(variant_objs) == 1:
        src = variant_objs[0]
        wps = collect_variant_waypoints(src)
        term_value = int(src.get("terminator_value", -len(wps)))
        variants = [(wps, term_value) for _ in range(REPLICATE_TO_N)]
        print(
            f"  REPLICATE_TO_N={REPLICATE_TO_N}: single mesh '{src.name}' "
            f"({len(wps)} wps) copied into {REPLICATE_TO_N} slots"
        )
    else:
        variants = []
        for v_obj in variant_objs:
            wps = collect_variant_waypoints(v_obj)
            term_value = v_obj.get("terminator_value", -len(wps))
            # Blender custom props come back as ints; ensure signed 32-bit fit.
            term_value = int(term_value)
            variants.append((wps, term_value))
            print(
                f"  variant_index={v_obj.get('variant_index','?')} "
                f"obj='{v_obj.name}' wps={len(wps)} term={term_value}"
            )

    # Trailing metadata: preserved verbatim if available.
    raw_trailing = root.get("trailing_metadata") if root is not None else None
    if raw_trailing is not None:
        trailing = [int(x) for x in raw_trailing]
        if len(trailing) != 3:
            raise RuntimeError(
                f"root['trailing_metadata'] must have 3 entries, got {len(trailing)}"
            )
    else:
        n = len(variants)
        trailing = [n - 1, n - 1, n]
        print(f"  no trailing_metadata on root; synthesizing {trailing}")

    size = write_line_bin(path, variants, trailing)
    print(f"Wrote {size} bytes to {path}")


def main():
    if OUTPUT_PATH:
        export_line(OUTPUT_PATH)
    else:
        bpy.utils.register_class(MKGP2_OT_ExportLine)
        bpy.ops.export_mesh.mkgp2_line('INVOKE_DEFAULT')


if __name__ == "__main__":
    main()
