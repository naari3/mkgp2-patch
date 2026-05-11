"""
MKGP2 Collision .bin Blender Export Script

Usage:
  1. Import a collision .bin with blender_import_collision.py first
  2. Edit the "CollisionMesh" object in Blender (move vertices, add/remove faces)
  3. Optionally edit "WallSegments" edges
  4. Edit OUTPUT_PATH below
  5. Run this script

The script reads:
  - "CollisionMesh": triangulated faces -> collision triangles
  - "WallSegments": edges -> wall segments
  - Grid parameters from CollisionMesh custom properties

For each triangle, the script recomputes:
  - AABB, edge normals, plane equation from vertex positions
  - flags/edgeMask from face attributes (defaults for new faces)

Output: Big-endian .bin file compatible with MKGP2
"""

import bpy
import bmesh
import struct
import math
import os

# ============================================================
# CONFIGURATION
# ============================================================
OUTPUT_PATH = r"C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files\mr_highway_short_modified.bin"
COLLISION_OBJ_NAME = "CollisionMesh"
WALL_OBJ_NAME = "WallSegments"
# ============================================================


def blender_to_game(x, y, z):
    """Blender Z-up -> Game Y-up."""
    return (x, z, -y)


def write_f32(val):
    return struct.pack(">f", val)

def write_i32(val):
    return struct.pack(">i", val)

def write_u32(val):
    return struct.pack(">I", val)


def signed_to_unsigned(val):
    """Reinterpret signed int as unsigned (for flags stored in Blender's signed int attrs)."""
    return struct.unpack(">I", struct.pack(">i", val))[0]


def calc_edge_normal(v_from, v_to):
    """Calculate edge normal for half-plane test.

    The game's test is: -normalX * (posX - refX) + normalZ * (posZ - refZ) <= 0
    where normalX = edge_dZ, normalZ = edge_dX (2D perpendicular in XZ).
    Returns (normalZ, normalY, normalX) matching the game's {Z,Y,X} layout.
    Y is set to 0 (only XZ components used in the test).
    """
    dx = v_to[0] - v_from[0]  # game X
    dz = v_to[2] - v_from[2]  # game Z
    # normalX = dz, normalZ = dx
    nx = dz
    nz = dx
    length = math.sqrt(nx * nx + nz * nz)
    if length < 1e-10:
        return (0.0, 0.0, 0.0)
    return (nz / length, 0.0, nx / length)


def calc_plane_equation(v0, v1, v2):
    """Calculate plane normal and D from 3 vertices.

    Plane equation: Ax + By + Cz + D = 0
    Returns (A, B, C, D) = (normalX, normalY, normalZ, D)
    """
    # Edge vectors
    e1 = (v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2])
    e2 = (v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2])
    # Cross product e1 x e2
    nx = e1[1] * e2[2] - e1[2] * e2[1]
    ny = e1[2] * e2[0] - e1[0] * e2[2]
    nz = e1[0] * e2[1] - e1[1] * e2[0]
    # Normalize
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length < 1e-10:
        return (0.0, 1.0, 0.0, 0.0)  # degenerate -> flat
    nx /= length
    ny /= length
    nz /= length
    # D = -(N dot v0)
    d = -(nx * v0[0] + ny * v0[1] + nz * v0[2])
    return (nx, ny, nz, d)


class ExportTriangle:
    __slots__ = ['v0', 'v1', 'v2', 'flags', 'edge_mask',
                 'aabb', 'edge_normals', 'plane']

    def __init__(self, v0, v1, v2, flags=0, edge_mask=0):
        self.v0 = v0
        self.v1 = v1
        self.v2 = v2
        self.flags = flags
        self.edge_mask = edge_mask
        self._compute_derived()

    def _compute_derived(self):
        xs = [self.v0[0], self.v1[0], self.v2[0]]
        zs = [self.v0[2], self.v1[2], self.v2[2]]
        self.aabb = (min(xs), min(zs), max(xs), max(zs))

        self.plane = calc_plane_equation(self.v0, self.v1, self.v2)

        # Edge normals: edge0 = v0->v1, edge1 = v1->v2, edge2 = v2->v0
        self.edge_normals = (
            calc_edge_normal(self.v0, self.v1),
            calc_edge_normal(self.v1, self.v2),
            calc_edge_normal(self.v2, self.v0),
        )

    def to_bytes(self):
        """Serialize to 0x70 bytes (big-endian)."""
        buf = bytearray(0x70)
        # AABB
        struct.pack_into(">ffff", buf, 0x00, *self.aabb)
        # Vertices
        struct.pack_into(">fff", buf, 0x10, *self.v0)
        struct.pack_into(">fff", buf, 0x1C, *self.v1)
        struct.pack_into(">fff", buf, 0x28, *self.v2)
        # Edge normals (Z, Y, X order per edge)
        for i, en in enumerate(self.edge_normals):
            off = 0x34 + i * 0x0C
            struct.pack_into(">fff", buf, off, en[0], en[1], en[2])
        # edgeMask, flags
        struct.pack_into(">I", buf, 0x58, self.edge_mask)
        struct.pack_into(">I", buf, 0x5C, self.flags)
        # Plane equation
        struct.pack_into(">ffff", buf, 0x60, *self.plane)
        return bytes(buf)


class ExportWallSegment:
    __slots__ = ['start_x', 'start_z', 'end_x', 'end_z']

    def __init__(self, sx, sz, ex, ez):
        self.start_x = sx
        self.start_z = sz
        self.end_x = ex
        self.end_z = ez

    def to_bytes(self):
        buf = bytearray(0x20)
        min_x = min(self.start_x, self.end_x)
        min_z = min(self.start_z, self.end_z)
        max_x = max(self.start_x, self.end_x)
        max_z = max(self.start_z, self.end_z)
        struct.pack_into(">ffff", buf, 0x00, min_x, min_z, max_x, max_z)
        struct.pack_into(">ffff", buf, 0x10, self.start_x, self.start_z,
                         self.end_x, self.end_z)
        return bytes(buf)


def collect_triangles(obj):
    """Read triangles from Blender mesh object.

    Geometry is read through the depsgraph so modifier stacks (Subdivision
    Surface, Solidify, Mirror, Array, Geometry Nodes scatter etc.) are
    baked into the exported collision without `Apply Modifier`.
    """
    # Ensure edit mode changes are committed to mesh data
    bpy.context.view_layer.objects.active = obj
    if obj.mode == 'EDIT':
        bpy.ops.object.mode_set(mode='OBJECT')

    depsgraph = bpy.context.evaluated_depsgraph_get()
    eval_obj = obj.evaluated_get(depsgraph)
    eval_me = eval_obj.to_mesh()
    try:
        bm = bmesh.new()
        bm.from_mesh(eval_me)
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
        bm.faces.ensure_lookup_table()

        flags_layer = bm.faces.layers.int.get("collision_flags")
        emask_layer = bm.faces.layers.int.get("edge_mask")

        triangles = []
        for face in bm.faces:
            if len(face.verts) != 3:
                continue
            verts_blender = [v.co for v in face.verts]
            verts_game = [blender_to_game(v.x, v.y, v.z) for v in verts_blender]

            flags = 0
            edge_mask = 0
            if flags_layer:
                flags = signed_to_unsigned(face[flags_layer])
            if emask_layer:
                edge_mask = signed_to_unsigned(face[emask_layer])

            tri = ExportTriangle(verts_game[0], verts_game[1], verts_game[2],
                                 flags=flags, edge_mask=edge_mask)
            triangles.append(tri)

        # Debug: show Y range (game height) to verify edits are captured
        if triangles:
            all_y = []
            for t in triangles:
                all_y.extend([t.v0[1], t.v1[1], t.v2[1]])
            print(f"  Game Y range: {min(all_y):.2f} to {max(all_y):.2f}")
            # Show first 3 triangles
            for i, t in enumerate(triangles[:3]):
                print(f"  tri[{i}] v0={t.v0} v1={t.v1} v2={t.v2}")

        bm.free()
        return triangles
    finally:
        eval_obj.to_mesh_clear()


def collect_wall_segments(obj):
    """Read wall segments from Blender edge mesh.

    Geometry is read through the depsgraph so modifier stacks are baked
    transparently at export.
    """
    depsgraph = bpy.context.evaluated_depsgraph_get()
    eval_obj = obj.evaluated_get(depsgraph)
    mesh = eval_obj.to_mesh()
    try:
        segments = []
        for edge in mesh.edges:
            v0 = mesh.vertices[edge.vertices[0]].co
            v1 = mesh.vertices[edge.vertices[1]].co
            g0 = blender_to_game(v0.x, v0.y, v0.z)
            g1 = blender_to_game(v1.x, v1.y, v1.z)
            seg = ExportWallSegment(g0[0], g0[2], g1[0], g1[2])
            segments.append(seg)
        return segments
    finally:
        eval_obj.to_mesh_clear()


def build_grid(triangles, wall_segments, grid_width, grid_height,
               cell_size_x, cell_size_z, origin_x, origin_z):
    """Assign triangles and wall segments to grid cells by AABB overlap."""
    cells = []
    for _ in range(grid_width * grid_height):
        cells.append({"tris": [], "walls": []})

    for tri_idx, tri in enumerate(triangles):
        min_cx = int((tri.aabb[0] + origin_x) / cell_size_x)
        min_cz = int((tri.aabb[1] + origin_z) / cell_size_z)
        max_cx = int((tri.aabb[2] + origin_x) / cell_size_x)
        max_cz = int((tri.aabb[3] + origin_z) / cell_size_z)
        min_cx = max(0, min(min_cx, grid_width - 1))
        max_cx = max(0, min(max_cx, grid_width - 1))
        min_cz = max(0, min(min_cz, grid_height - 1))
        max_cz = max(0, min(max_cz, grid_height - 1))
        for cz in range(min_cz, max_cz + 1):
            for cx in range(min_cx, max_cx + 1):
                cells[cx + cz * grid_width]["tris"].append(tri_idx)

    for wall_idx, wall in enumerate(wall_segments):
        min_x = min(wall.start_x, wall.end_x)
        max_x = max(wall.start_x, wall.end_x)
        min_z = min(wall.start_z, wall.end_z)
        max_z = max(wall.start_z, wall.end_z)
        min_cx = int((min_x + origin_x) / cell_size_x)
        min_cz = int((min_z + origin_z) / cell_size_z)
        max_cx = int((max_x + origin_x) / cell_size_x)
        max_cz = int((max_z + origin_z) / cell_size_z)
        min_cx = max(0, min(min_cx, grid_width - 1))
        max_cx = max(0, min(max_cx, grid_width - 1))
        min_cz = max(0, min(min_cz, grid_height - 1))
        max_cz = max(0, min(max_cz, grid_height - 1))
        for cz in range(min_cz, max_cz + 1):
            for cx in range(min_cx, max_cx + 1):
                cells[cx + cz * grid_width]["walls"].append(wall_idx)

    return cells


def write_collision_bin(filepath, triangles, wall_segments,
                        grid_width, grid_height, cell_size_x, cell_size_z,
                        origin_x, origin_z, reserved_bytes=None):
    """Write MKGP2 collision .bin file.

    Each grid cell needs a pointer to a *contiguous* array of triangles/walls.
    Since one triangle can span multiple cells, we build per-cell arrays with
    duplicated triangle data (matching the original game format).
    """

    cells = build_grid(triangles, wall_segments,
                       grid_width, grid_height,
                       cell_size_x, cell_size_z, origin_x, origin_z)

    # Per-cell contiguous layout: each cell gets its own contiguous copy of
    # its triangles/walls. This duplicates shared triangles but guarantees
    # each cell references EXACTLY its triangles (no extras).
    #
    # CRITICAL: The pointer fixup loop (FUN_80035220) terminates when
    # tri_array_ptr == 0. Empty cells MUST have non-zero offsets.

    cell_tri_blobs = []
    cell_wall_blobs = []
    for cell in cells:
        cell_tri_blobs.append(b"".join(triangles[i].to_bytes() for i in cell["tris"]))
        cell_wall_blobs.append(b"".join(wall_segments[i].to_bytes() for i in cell["walls"]))

    # Layout:
    # [Header: 0x2C bytes]
    # [Per-cell triangle arrays]
    # [Per-cell wall arrays]
    # [Empty sentinel stubs: 0x20 bytes]
    # [Grid cells: num_cells * 0x10]
    # [Terminator cell: 0x10 bytes of zeros]

    header_size = 0x2C

    # Calculate offsets for triangle blobs
    tri_cursor = header_size
    cell_tri_offsets = []
    for blob in cell_tri_blobs:
        if blob:
            cell_tri_offsets.append(tri_cursor)
            tri_cursor += len(blob)
        else:
            cell_tri_offsets.append(None)  # placeholder

    # Wall blobs follow
    wall_cursor = tri_cursor
    cell_wall_offsets = []
    for blob in cell_wall_blobs:
        if blob:
            cell_wall_offsets.append(wall_cursor)
            wall_cursor += len(blob)
        else:
            cell_wall_offsets.append(None)

    # Sentinel stubs for empty cells
    empty_tri_stub_offset = wall_cursor
    empty_wall_stub_offset = empty_tri_stub_offset + 0x10
    sentinel_end = empty_wall_stub_offset + 0x10

    # Fill in empty cell offsets with sentinels
    for i in range(len(cell_tri_offsets)):
        if cell_tri_offsets[i] is None:
            cell_tri_offsets[i] = empty_tri_stub_offset
    for i in range(len(cell_wall_offsets)):
        if cell_wall_offsets[i] is None:
            cell_wall_offsets[i] = empty_wall_stub_offset

    grid_data_offset = sentinel_end
    num_cells = len(cells)
    total_size = grid_data_offset + (num_cells + 1) * 0x10

    buf = bytearray(total_size)

    # Header
    struct.pack_into(">i", buf, 0x00, grid_width)
    struct.pack_into(">i", buf, 0x04, grid_height)
    struct.pack_into(">f", buf, 0x08, cell_size_x)
    struct.pack_into(">f", buf, 0x0C, cell_size_z)
    if reserved_bytes and len(reserved_bytes) == 16:
        buf[0x10:0x20] = reserved_bytes
    struct.pack_into(">f", buf, 0x20, origin_x)
    struct.pack_into(">f", buf, 0x24, origin_z)
    struct.pack_into(">I", buf, 0x28, grid_data_offset)

    # Write triangle blobs
    cursor = header_size
    for blob in cell_tri_blobs:
        if blob:
            buf[cursor:cursor + len(blob)] = blob
            cursor += len(blob)

    # Write wall blobs
    for blob in cell_wall_blobs:
        if blob:
            buf[cursor:cursor + len(blob)] = blob
            cursor += len(blob)

    # Grid cells
    for cell_idx in range(num_cells):
        off = grid_data_offset + cell_idx * 0x10
        struct.pack_into(">I", buf, off + 0x00, cell_tri_offsets[cell_idx])
        struct.pack_into(">i", buf, off + 0x04, len(cells[cell_idx]["tris"]))
        struct.pack_into(">I", buf, off + 0x08, cell_wall_offsets[cell_idx])
        struct.pack_into(">i", buf, off + 0x0C, len(cells[cell_idx]["walls"]))

    # Terminator cell (all zeros) — already zero from bytearray init

    with open(filepath, "wb") as f:
        f.write(buf)

    print(f"Written: {filepath} ({len(buf)} bytes)")
    return len(buf)


def main():
    # Find collision mesh
    col_obj = bpy.data.objects.get(COLLISION_OBJ_NAME)
    if col_obj is None:
        print(f"ERROR: Object '{COLLISION_OBJ_NAME}' not found")
        return

    # Read grid parameters from custom properties
    grid_width = col_obj.get("grid_width")
    if grid_width is None:
        print("ERROR: No grid parameters on CollisionMesh. Import with updated script first.")
        return

    grid_height = col_obj["grid_height"]
    cell_size_x = col_obj["cell_size_x"]
    cell_size_z = col_obj["cell_size_z"]
    origin_x = col_obj["grid_origin_x"]
    origin_z = col_obj["grid_origin_z"]
    reserved_hex = col_obj.get("reserved_hex", "0" * 32)
    reserved_bytes = bytes.fromhex(reserved_hex)

    print(f"Grid: {grid_width}x{grid_height}, cell: {cell_size_x}x{cell_size_z}")
    print(f"Origin: ({origin_x}, {origin_z})")

    # Collect triangles
    triangles = collect_triangles(col_obj)
    print(f"Triangles: {len(triangles)}")

    # Collect wall segments
    wall_segments = []
    wall_obj = bpy.data.objects.get(WALL_OBJ_NAME)
    if wall_obj:
        wall_segments = collect_wall_segments(wall_obj)
        print(f"Wall segments: {len(wall_segments)}")
    else:
        print("No WallSegments object found, exporting without walls")

    # Verify triangles fit in grid
    for i, tri in enumerate(triangles):
        max_game_x = (grid_width * cell_size_x) - origin_x
        max_game_z = (grid_height * cell_size_z) - origin_z
        min_game_x = -origin_x
        min_game_z = -origin_z
        if (tri.aabb[0] < min_game_x - cell_size_x or
            tri.aabb[2] > max_game_x + cell_size_x or
            tri.aabb[1] < min_game_z - cell_size_z or
            tri.aabb[3] > max_game_z + cell_size_z):
            print(f"WARNING: Triangle {i} AABB ({tri.aabb}) outside grid bounds!")

    # Write
    size = write_collision_bin(
        OUTPUT_PATH, triangles, wall_segments,
        grid_width, grid_height, cell_size_x, cell_size_z,
        origin_x, origin_z, reserved_bytes
    )

    special_count = sum(1 for t in triangles if t.flags & 0x80000000)
    print(f"\nExport complete!")
    print(f"  Triangles: {len(triangles)} ({special_count} special)")
    print(f"  Walls: {len(wall_segments)}")
    print(f"  File size: {size} bytes")


if __name__ == "__main__":
    main()
