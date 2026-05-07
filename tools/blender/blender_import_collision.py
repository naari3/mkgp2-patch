"""
MKGP2 Collision .bin Blender Import Script

Usage:
  1. Open Blender
  2. Switch to Scripting workspace
  3. Open this script
  4. Edit BIN_PATH to point to your .bin file
  5. Run script

The script creates:
  - "CollisionMesh": All ground triangles (flags/edgeMask preserved as face attributes)
  - "WallSegments": Wall segments as edge-only mesh
  - Grid parameters stored as object custom properties for roundtrip export

Coordinate system:
  Game Y-up -> Blender Z-up:
    Blender X = Game X
    Blender Y = -Game Z
    Blender Z = Game Y
"""

import bpy
import bmesh
import struct
import os
from mathutils import Vector

# ============================================================
# CONFIGURATION - leave empty to open file dialog
# ============================================================
BIN_PATH = ""
# ============================================================


def read_big_endian(data, offset, fmt):
    return struct.unpack_from(">" + fmt, data, offset)[0]

def read_f32(data, offset):
    return read_big_endian(data, offset, "f")

def read_i32(data, offset):
    return read_big_endian(data, offset, "i")

def read_u32(data, offset):
    return read_big_endian(data, offset, "I")

def game_to_blender(x, y, z):
    return (x, -z, y)

def blender_to_game(x, y, z):
    return (x, z, -y)


class CollisionHeader:
    def __init__(self, data):
        self.grid_width = read_i32(data, 0x00)
        self.grid_height = read_i32(data, 0x04)
        self.cell_size_x = read_f32(data, 0x08)
        self.cell_size_z = read_f32(data, 0x0C)
        self.reserved = data[0x10:0x20]  # preserve raw bytes
        self.grid_origin_x = read_f32(data, 0x20)
        self.grid_origin_z = read_f32(data, 0x24)
        self.grid_data_offset = read_u32(data, 0x28)


class Triangle:
    def __init__(self, data, offset):
        self.v0 = (read_f32(data, offset + 0x10),
                    read_f32(data, offset + 0x14),
                    read_f32(data, offset + 0x18))
        self.v1 = (read_f32(data, offset + 0x1C),
                    read_f32(data, offset + 0x20),
                    read_f32(data, offset + 0x24))
        self.v2 = (read_f32(data, offset + 0x28),
                    read_f32(data, offset + 0x2C),
                    read_f32(data, offset + 0x30))
        self.edge_mask = read_u32(data, offset + 0x58)
        self.flags = read_u32(data, offset + 0x5C)

    @property
    def is_special(self):
        return bool(self.flags & 0x80000000)

    @property
    def material_id(self):
        return self.flags & 0x7FFFFFFF


class WallSegment:
    def __init__(self, data, offset):
        self.start_x = read_f32(data, offset + 0x10)
        self.start_z = read_f32(data, offset + 0x14)
        self.end_x = read_f32(data, offset + 0x18)
        self.end_z = read_f32(data, offset + 0x1C)


def parse_collision_bin(filepath):
    with open(filepath, "rb") as f:
        data = f.read()

    header = CollisionHeader(data)
    triangles = {}
    wall_segments = {}
    grid_base = header.grid_data_offset
    num_cells = header.grid_width * header.grid_height

    for cell_idx in range(num_cells):
        cell_offset = grid_base + cell_idx * 0x10
        tri_array_off = read_u32(data, cell_offset + 0x00)
        tri_count = read_i32(data, cell_offset + 0x04)
        wall_array_off = read_u32(data, cell_offset + 0x08)
        wall_count = read_i32(data, cell_offset + 0x0C)

        if tri_array_off != 0 and tri_count > 0:
            for i in range(tri_count):
                t_off = tri_array_off + i * 0x70
                if t_off not in triangles:
                    triangles[t_off] = Triangle(data, t_off)

        if wall_array_off != 0 and wall_count > 0:
            for i in range(wall_count):
                w_off = wall_array_off + i * 0x20
                if w_off not in wall_segments:
                    wall_segments[w_off] = WallSegment(data, w_off)

    # Deduplicate triangles by vertex positions (per-cell copies share identical vertices)
    unique_tris = {}
    for tri in triangles.values():
        key = (tri.v0, tri.v1, tri.v2)
        if key not in unique_tris:
            unique_tris[key] = tri
    dedup_count = len(triangles) - len(unique_tris)
    if dedup_count > 0:
        print(f"Deduplicated: {len(triangles)} -> {len(unique_tris)} triangles ({dedup_count} duplicates removed)")

    unique_walls = {}
    for wall in wall_segments.values():
        key = (wall.start_x, wall.start_z, wall.end_x, wall.end_z)
        if key not in unique_walls:
            unique_walls[key] = wall

    return header, list(unique_tris.values()), list(unique_walls.values())


def id_to_color(material_id):
    import hashlib
    h = hashlib.md5(struct.pack(">I", material_id)).digest()
    return (h[0] / 255.0, h[1] / 255.0, h[2] / 255.0, 1.0)


def create_collision_mesh(name, triangles):
    """Create mesh with per-face flags/edgeMask attributes."""
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)

    bm = bmesh.new()
    color_layer = bm.loops.layers.color.new("MaterialID")
    flags_layer = bm.faces.layers.int.new("collision_flags")
    emask_layer = bm.faces.layers.int.new("edge_mask")

    for tri in triangles:
        v0 = bm.verts.new(game_to_blender(*tri.v0))
        v1 = bm.verts.new(game_to_blender(*tri.v1))
        v2 = bm.verts.new(game_to_blender(*tri.v2))
        try:
            face = bm.faces.new((v0, v1, v2))
            # Store as signed int (Blender limitation), reinterpret on export
            face[flags_layer] = struct.unpack(">i", struct.pack(">I", tri.flags))[0]
            face[emask_layer] = struct.unpack(">i", struct.pack(">I", tri.edge_mask))[0]
            color = id_to_color(tri.material_id)
            for loop in face.loops:
                loop[color_layer] = color
        except ValueError:
            pass

    bm.to_mesh(mesh)
    bm.free()

    return obj


def create_edge_wall_mesh(name, wall_segments):
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)

    vertices = []
    edges = []
    for i, wall in enumerate(wall_segments):
        p0 = game_to_blender(wall.start_x, 0, wall.start_z)
        p1 = game_to_blender(wall.end_x, 0, wall.end_z)
        idx = i * 2
        vertices.append(p0)
        vertices.append(p1)
        edges.append((idx, idx + 1))

    mesh.from_pydata(vertices, edges, [])
    mesh.update()
    return obj


class MKGP2_OT_ImportCollision(bpy.types.Operator):
    """Import MKGP2 Collision .bin file"""
    bl_idname = "import_mesh.mkgp2_collision"
    bl_label = "Import MKGP2 Collision"

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(default="*.bin", options={'HIDDEN'})

    def execute(self, context):
        import_collision(self.filepath)
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


def import_collision(path):
    if not os.path.exists(path):
        print(f"ERROR: File not found: {path}")
        return

    print(f"\nLoading: {path}")
    header, triangles, wall_segments = parse_collision_bin(path)

    normal_tris = [t for t in triangles if not t.is_special]
    special_tris = [t for t in triangles if t.is_special]

    print(f"Normal: {len(normal_tris)}, Special: {len(special_tris)}, Walls: {len(wall_segments)}")

    # Debug: show Y range (game height)
    if triangles:
        all_y = []
        for t in triangles:
            all_y.extend([t.v0[1], t.v1[1], t.v2[1]])
        print(f"  Game Y range: {min(all_y):.2f} to {max(all_y):.2f}")

    # Create collision mesh (all triangles in one object)
    all_tris = normal_tris + special_tris
    if all_tris:
        obj = create_collision_mesh("CollisionMesh", all_tris)
        # Store grid parameters as custom properties for export roundtrip
        obj["grid_width"] = header.grid_width
        obj["grid_height"] = header.grid_height
        obj["cell_size_x"] = header.cell_size_x
        obj["cell_size_z"] = header.cell_size_z
        obj["grid_origin_x"] = header.grid_origin_x
        obj["grid_origin_z"] = header.grid_origin_z
        obj["reserved_hex"] = header.reserved.hex()
        print(f"Created: CollisionMesh ({len(all_tris)} tris)")
        print(f"  Grid: {header.grid_width}x{header.grid_height}")
        print(f"  Cell: {header.cell_size_x} x {header.cell_size_z}")
        print(f"  Origin: ({header.grid_origin_x}, {header.grid_origin_z})")

    if wall_segments:
        obj_walls = create_edge_wall_mesh("WallSegments", wall_segments)
        print(f"Created: WallSegments ({len(wall_segments)} segments)")

    mat_ids = set(t.material_id for t in triangles)
    print(f"\nMaterial IDs: {len(mat_ids)}")
    for mid in sorted(mat_ids):
        count = sum(1 for t in triangles if t.material_id == mid)
        print(f"  0x{mid:08X}: {count} tris")

    print("\nImport complete!")


def main():
    if BIN_PATH:
        import_collision(BIN_PATH)
    else:
        bpy.utils.register_class(MKGP2_OT_ImportCollision)
        bpy.ops.import_mesh.mkgp2_collision('INVOKE_DEFAULT')

if __name__ == "__main__":
    main()
