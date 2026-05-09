"""
MKGP2 HSD Scene Importer (Python-only, no external CLI)

Reads a vanilla .dat directly via the vendored `hsdraw` Rust extension
and builds Blender meshes + materials + image textures, ready as L1
visual reference for collision / path editing.

The previous csx-based path (dotnet-script + HSDLib + ImageSharp +
scene.json/PNG bundle) has been retired -- `hsdraw.export_scene_json`
emits the same JSON schema in-process, and `hsdraw.gx_decode` produces
RGBA8 from raw GX bytes so each TObj's image is materialized as a
`bpy.data.images.new` object without any intermediate PNG file.

Usage:
  Method A — Blender Text Editor:
    1. Open Blender > Text Editor > Open `blender_import_hsd.py`
    2. Edit DAT_PATH below
    3. Click "Run Script"

  Method B — CLI:
    blender --python blender_import_hsd.py -- <path-to-foo.dat>

Coordinate system (matches blender_import_collision.py / _line.py / _auto.py):
    Game Y-up -> Blender Z-up:
        Blender X =  Game X
        Blender Y = -Game Z
        Blender Z =  Game Y

What gets created:
  - One Blender Collection named `mkgp2:<source_dat>`
  - One Mesh Object per scene.json mesh (parented to nothing; vertices
    already in world space).  Joint hierarchy is preserved in custom
    properties (joint_id, single_bind_joint) so re-export can rebuild it.
  - One Material per scene.json material (named `mat_<n>`).
  - One Blender Image per unique texture, built directly from raw GX
    bytes via `gx_decode` (no PNG file). Each Image carries
    `mkgp2_gx_path` / `mkgp2_gx_format` / `mkgp2_gx_width` /
    `mkgp2_gx_height` / `mkgp2_gx_size` / `mkgp2_png_hash` custom props
    so the M3 unified exporter's bypass-vs-reencode dispatch works
    identically to the legacy bundle path.

GX bytes for each texture are written to `gx_dump_dir` (defaults to a
per-source-file temp dir) so the M3 exporter can read them back via the
`mkgp2_gx_path` custom prop on each Image.

Limitations (unchanged from the csx era):
  - Empties for joint hierarchy are created but cannot drive visual
    placement (vertices are world-baked).  They serve as a Blender-
    native expression of the joint parent/children chain.
  - Skinning / shape_set are unsupported.
  - PEDesc / TEV / mipmap LOD info is not yet wired into Blender
    material nodes.
"""

import bpy
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

# ============================================================
# CONFIGURATION — edit when running from Text Editor
# ============================================================
DAT_PATH = r"C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files\test_course_road.dat"
# ============================================================

# GxTexFmt name -> integer enum (mirrors HSDLib HSDRaw/GX/GXEnums.cs).
# Used by gx_decode + by the unified exporter's bypass dispatch.
_TEXFMT = {
    "I4": 0, "I8": 1, "IA4": 2, "IA8": 3,
    "RGB565": 4, "RGB5A3": 5, "RGBA8": 6,
    "CI4": 8, "CI8": 9, "CI14X2": 10,
    "CMP": 14,
}


def game_to_blender(x, y, z):
    return (x, -z, y)


def _collect_gx_bytes(dat):
    """Walk every reachable TObj in `dat` and return {sha1[:12]: gx_bytes}.

    `sha1[:12]` is the same intern key `hsdraw.export_scene_json` uses for
    `textures[].id`, so the dict is keyed identically and lookups by id
    succeed by construction.

    Walks scene_data's JOBJDescs[*].RootJoint plus every non-`scene_data`
    root (typical course .dat layout: 1 RootJoint + many alias roots
    pointing back at it -- the dedup happens naturally because identical
    image_data SHAs collapse onto the same dict key).
    """
    import hsdraw

    out = {}

    def walk_jobjs(jobj):
        while jobj is not None:
            yield jobj
            if jobj.child is not None:
                yield from walk_jobjs(jobj.child)
            jobj = jobj.next

    def dobj_from_jobj(jobj):
        # HSD_JOBJ.DObj reference lives at struct offset 0x10. The
        # JObj wrapper has set_dobj() but no symmetric getter, so we
        # peek the struct refs directly.
        for off, s in jobj.as_struct().references():
            if off == 0x10:
                return hsdraw.DObj.from_struct(s)
        return None

    def all_root_jobjs():
        sd = dat.scene_data()
        if sd is not None:
            sobj = hsdraw.SObj.from_struct(sd.data)
            for jd in sobj.jobj_descs():
                rj = jd.root_joint
                if rj is not None:
                    yield rj
        for r in dat.roots():
            if r.name == 'scene_data':
                continue
            try:
                yield hsdraw.JObj.from_struct(r.data)
            except Exception:
                # Non-JObj root (rare in course .dat). Skip silently.
                continue

    for root in all_root_jobjs():
        for jobj in walk_jobjs(root):
            d = dobj_from_jobj(jobj)
            while d is not None:
                m_raw = d.mobj
                if m_raw is not None:
                    m = hsdraw.MObj.from_struct(m_raw)
                    t = m.textures
                    while t is not None:
                        timg = t.image_data
                        if timg is not None:
                            img = (hsdraw.Image.from_struct(timg)
                                   if isinstance(timg, hsdraw.HsdStruct)
                                   else timg)
                            gx = img.image_data()
                            if gx:
                                sha = hashlib.sha1(gx).hexdigest()[:12].upper()
                                out[sha] = gx
                        t = t.next
                d = d.next
    return out


def make_image_from_gx(tex_id, gx_bytes, fmt_name, width, height,
                      gx_dump_dir, image_cache):
    """Build a Blender Image from raw GX bytes (no PNG file).

    Stashes the same custom-prop set the legacy `make_image` (csx-era,
    PNG-loading) used, so the M3 unified exporter sees identical
    bypass-vs-reencode metadata:

      mkgp2_gx_path     absolute path to a `<tex_id>.gx` written under
                        `gx_dump_dir` (the exporter reads it back when
                        the Blender Image is untouched, byte-for-byte).
      mkgp2_gx_format   GX format name ("CMP" / "RGB5A3" / "RGBA8" /
                        "RGB565" / ...). Format-changing edits rejected.
      mkgp2_gx_width / mkgp2_gx_height / mkgp2_gx_size
                        source dimensions + raw payload size.
      mkgp2_png_hash    SHA-1 of the decoded RGBA8 bytes. The exporter
                        re-derives a live hash from `Image.pixels` to
                        decide whether the user has edited the Image.

    `gx_decode(format_int, width, height, gx_bytes)` returns RGBA8 bytes
    of length `4 * width * height` (Rust core handles the HSDLib BGRA→
    RGBA quirk internally — no swap on the Python side).
    """
    import hsdraw

    if tex_id in image_cache:
        return image_cache[tex_id]

    fmt_int = _TEXFMT.get(fmt_name)
    if fmt_int is None:
        print(f"  WARN: unknown GX format {fmt_name!r} for texture {tex_id}")
        return None

    try:
        rgba = hsdraw.gx_decode(fmt_int, width, height, gx_bytes)
    except Exception as ex:
        print(f"  ERR: gx_decode failed for {tex_id} (fmt={fmt_name} "
              f"{width}x{height}): {ex}")
        return None

    # Build the Blender Image. `pixels.foreach_set` accepts a flat float
    # iterable; we feed normalized 0..1 floats from the RGBA8 bytes.
    img = bpy.data.images.new(
        name=tex_id, width=width, height=height, alpha=True)
    pixel_floats = [b / 255.0 for b in rgba]
    img.pixels.foreach_set(pixel_floats)
    img.alpha_mode = 'STRAIGHT'

    # Materialize the Image to disk as PNG so the M3 exporter's bypass
    # dispatch (which compares SHA-1 of the on-disk PNG bytes to detect
    # whether the user has edited the Image) keeps functioning.
    # `bpy.data.images.new` images have empty `filepath_raw` until we
    # bind one and call `save()`.
    png_path = Path(gx_dump_dir) / f"{tex_id}.png"
    img.filepath_raw = str(png_path)
    img.file_format = 'PNG'
    img.save()

    # Persist the raw GX payload too: bypass picks the .gx bytes
    # byte-for-byte (avoiding CMP DXT1 round-trip quality loss) when
    # the PNG hash still matches.
    gx_path = Path(gx_dump_dir) / f"{tex_id}.gx"
    gx_path.write_bytes(gx_bytes)

    img['mkgp2_gx_path'] = str(gx_path)
    img['mkgp2_gx_format'] = fmt_name
    img['mkgp2_gx_width'] = int(width)
    img['mkgp2_gx_height'] = int(height)
    img['mkgp2_gx_size'] = len(gx_bytes)
    # Hash matches what `_png_bytes_for_image` will read at export time
    # (= the file content `img.save()` just wrote).
    img['mkgp2_png_hash'] = hashlib.sha1(png_path.read_bytes()).hexdigest()

    image_cache[tex_id] = img
    return img


def _mix_multiply(nt, a_out, b_out, x=0):
    """a * b の Mix(MULTIPLY)、Blender 3.4 deprecated だが 4.3 でも動作"""
    n = nt.nodes.new('ShaderNodeMixRGB')
    n.blend_type = 'MULTIPLY'
    n.inputs['Fac'].default_value = 1.0
    n.location = (x, 0)
    nt.links.new(a_out, n.inputs['Color1'])
    nt.links.new(b_out, n.inputs['Color2'])
    return n.outputs['Color']


def _mix_lerp(nt, a_out, b_out, fac, x=0):
    """mix(a, b, fac), Blender Mix RGB の MIX blend"""
    n = nt.nodes.new('ShaderNodeMixRGB')
    n.blend_type = 'MIX'
    n.inputs['Fac'].default_value = fac
    n.location = (x, 0)
    nt.links.new(a_out, n.inputs['Color1'])
    nt.links.new(b_out, n.inputs['Color2'])
    return n.outputs['Color']


def _rgb_const(nt, rgba, x=0):
    n = nt.nodes.new('ShaderNodeRGB')
    n.outputs['Color'].default_value = rgba
    n.location = (x, 0)
    return n.outputs['Color']


def make_material(mat_dto, image_cache):
    """Build a Blender material that mirrors HSD gx.frag / gx_lightmap.frag
    composition for the common course-mesh paths.

    Composition (matching gx.frag):
        diffusePass = TexturePass(vec4(Material.DIF.rgb, Material.Alpha * DIF.a),
                                  PASS_DIFFUSE)
            -> per-TObj ColorOperation chain (MODULATE/BLEND/REPLACE/RGB_MASK/ADD)
        if (CONSTANT && !DIFFUSE):  diff = vec3(1); -> fragColor.rgb = diffusePass.rgb
        else if (DIFFUSE):          fragColor.rgb = diffusePass * lighting (skipped here)
        if (VERTEX): fragColor.rgb *= vertexColor.rgb * vertexColor.aaa  (alpha premul)

    Course meshes are basically CONSTANT or VERTEX (no DIFFUSE), so the
    pipeline collapses to: Emission = (texture op chain) * (vc.rgb * vc.a).
    """
    m = bpy.data.materials.new(name=mat_dto['id'])
    m.use_nodes = True
    nt = m.node_tree

    # Wipe default Principled+Output, replace with Emission for CONSTANT/VERTEX
    # path (= unlit, matches gx.frag with useConstant=1,enableDiffuse=0).
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    out.location = (600, 0)
    emission = nt.nodes.new('ShaderNodeEmission')
    emission.location = (400, 0)
    emission.inputs['Strength'].default_value = 1.0
    nt.links.new(emission.outputs['Emission'], out.inputs['Surface'])

    flags = mat_dto['render_flags']
    has_constant = 'CONSTANT' in flags
    has_vertex = 'VERTEX' in flags
    has_xlu = 'XLU' in flags
    has_texedge = 'TEXEDGE' in flags
    has_alpha_vtx = 'ALPHA_VTX' in flags

    # Starting passColor.rgb = Material.DIF.rgb
    r, g, b, a = mat_dto['diffuse_rgba']
    diff_rgba = (r / 255, g / 255, b / 255, a / 255)
    pass_color_rgb = _rgb_const(nt, diff_rgba, x=-1200)
    pass_alpha = a / 255 * mat_dto.get('alpha', 1.0)
    alpha_socket = None  # if a socket needs to drive alpha (e.g. tex.alpha)

    tex_node = None
    tex_refs = mat_dto.get('textures') or []
    if tex_refs:
        tex_ref = tex_refs[0]
        img = image_cache.get(tex_ref['tex_id'])
        if img is not None:
            tex_node = nt.nodes.new('ShaderNodeTexImage')
            tex_node.image = img
            tex_node.location = (-1000, -300)
            tex_node.interpolation = 'Linear' if 'LINEAR' in tex_ref['mag_filter'] else 'Closest'
            wrap = tex_ref.get('wrap_s', 'REPEAT')
            tex_node.extension = 'REPEAT' if wrap == 'REPEAT' else ('CLIP' if wrap == 'CLAMP' else 'EXTEND')

            # Apply ColorOperation (PerformTextureOp in gx_lightmap.frag)
            color_op = tex_ref.get('color_op', 'MODULATE')
            if color_op == 'COLORMAP_MODULATE' or color_op == 'MODULATE':
                pass_color_rgb = _mix_multiply(nt, pass_color_rgb, tex_node.outputs['Color'], x=-700)
            elif color_op == 'COLORMAP_REPLACE' or color_op == 'REPLACE':
                pass_color_rgb = tex_node.outputs['Color']
            elif color_op == 'COLORMAP_BLEND' or color_op == 'BLEND':
                blend = float(tex_ref.get('blending', 0.5))
                pass_color_rgb = _mix_lerp(nt, pass_color_rgb, tex_node.outputs['Color'], blend, x=-700)
            elif color_op == 'COLORMAP_RGB_MASK' or color_op == 'RGB_MASK':
                # mix(passColor.rgb, tex.rgb, tex.a) — emulate via Mix node with
                # Factor driven by texture alpha.
                n = nt.nodes.new('ShaderNodeMixRGB')
                n.blend_type = 'MIX'
                n.location = (-700, 0)
                nt.links.new(tex_node.outputs['Alpha'], n.inputs['Fac'])
                nt.links.new(pass_color_rgb, n.inputs['Color1'])
                nt.links.new(tex_node.outputs['Color'], n.inputs['Color2'])
                pass_color_rgb = n.outputs['Color']
            elif color_op == 'COLORMAP_ADD' or color_op == 'ADD':
                n = nt.nodes.new('ShaderNodeMixRGB')
                n.blend_type = 'ADD'
                n.location = (-700, 0)
                # passColor.rgb += pass.rgb * pass.a
                # → pre-multiply tex.rgb by tex.alpha first
                premul = nt.nodes.new('ShaderNodeMixRGB')
                premul.blend_type = 'MULTIPLY'
                premul.inputs['Fac'].default_value = 1.0
                premul.location = (-850, -200)
                tex_alpha_combine = nt.nodes.new('ShaderNodeCombineColor')
                tex_alpha_combine.mode = 'RGB'
                tex_alpha_combine.location = (-1000, -500)
                nt.links.new(tex_node.outputs['Alpha'], tex_alpha_combine.inputs['Red'])
                nt.links.new(tex_node.outputs['Alpha'], tex_alpha_combine.inputs['Green'])
                nt.links.new(tex_node.outputs['Alpha'], tex_alpha_combine.inputs['Blue'])
                nt.links.new(tex_node.outputs['Color'], premul.inputs['Color1'])
                nt.links.new(tex_alpha_combine.outputs['Color'], premul.inputs['Color2'])
                n.inputs['Fac'].default_value = 1.0
                nt.links.new(pass_color_rgb, n.inputs['Color1'])
                nt.links.new(premul.outputs['Color'], n.inputs['Color2'])
                pass_color_rgb = n.outputs['Color']
            else:
                # COLORMAP_NONE / PASS / SUB / unknown — fallback to MODULATE
                pass_color_rgb = _mix_multiply(nt, pass_color_rgb, tex_node.outputs['Color'], x=-700)

            # Alpha path: AlphaOperation
            alpha_op = tex_ref.get('alpha_op', 'MODULATE')
            if alpha_op in ('ALPHAMAP_MODULATE', 'MODULATE'):
                # pass.a *= tex.a — multiply nodes
                amul = nt.nodes.new('ShaderNodeMath')
                amul.operation = 'MULTIPLY'
                amul.location = (-700, -500)
                amul.inputs[0].default_value = pass_alpha
                nt.links.new(tex_node.outputs['Alpha'], amul.inputs[1])
                alpha_socket = amul.outputs['Value']
            elif alpha_op in ('ALPHAMAP_REPLACE', 'REPLACE'):
                alpha_socket = tex_node.outputs['Alpha']
            else:
                # NONE / PASS — leave as material.alpha constant
                alpha_socket = None

    # Vertex color modulate: fragColor.rgb *= vc.rgb * vc.aaa  (alpha pre-mul)
    if has_vertex:
        vc = nt.nodes.new('ShaderNodeVertexColor')
        vc.layer_name = 'Col'
        vc.location = (-1000, 400)
        # Build (a, a, a) via CombineColor
        ac = nt.nodes.new('ShaderNodeCombineColor')
        ac.mode = 'RGB'
        ac.location = (-700, 500)
        nt.links.new(vc.outputs['Alpha'], ac.inputs['Red'])
        nt.links.new(vc.outputs['Alpha'], ac.inputs['Green'])
        nt.links.new(vc.outputs['Alpha'], ac.inputs['Blue'])
        vc_premul = _mix_multiply(nt, vc.outputs['Color'], ac.outputs['Color'], x=-400)
        pass_color_rgb = _mix_multiply(nt, pass_color_rgb, vc_premul, x=-100)

        # Also vertex alpha modulates final alpha
        if alpha_socket is not None:
            am = nt.nodes.new('ShaderNodeMath')
            am.operation = 'MULTIPLY'
            am.location = (-100, -500)
            nt.links.new(alpha_socket, am.inputs[0])
            nt.links.new(vc.outputs['Alpha'], am.inputs[1])
            alpha_socket = am.outputs['Value']
        else:
            am = nt.nodes.new('ShaderNodeMath')
            am.operation = 'MULTIPLY'
            am.location = (-100, -500)
            am.inputs[0].default_value = pass_alpha
            nt.links.new(vc.outputs['Alpha'], am.inputs[1])
            alpha_socket = am.outputs['Value']

    # Wire to Emission
    nt.links.new(pass_color_rgb, emission.inputs['Color'])

    # Transparency
    if has_xlu or has_texedge:
        # Mix Emission with Transparent BSDF using alpha
        trans = nt.nodes.new('ShaderNodeBsdfTransparent')
        trans.location = (200, 200)
        mix = nt.nodes.new('ShaderNodeMixShader')
        mix.location = (500, 0)
        if alpha_socket is not None:
            nt.links.new(alpha_socket, mix.inputs['Fac'])
        else:
            mix.inputs['Fac'].default_value = pass_alpha
        nt.links.new(trans.outputs['BSDF'], mix.inputs[1])
        nt.links.new(emission.outputs['Emission'], mix.inputs[2])
        # Re-route output
        for link in list(out.inputs['Surface'].links):
            nt.links.remove(link)
        nt.links.new(mix.outputs['Shader'], out.inputs['Surface'])
        # blend_method (attribute names changed across Blender versions)
        try:
            if has_texedge:
                m.blend_method = 'CLIP'
                if hasattr(m, 'alpha_threshold'):
                    m.alpha_threshold = 0.5
            else:
                m.blend_method = 'BLEND'
        except (AttributeError, TypeError):
            pass
        if hasattr(m, 'shadow_method'):
            m.shadow_method = 'NONE'
        # Blender 4.2+ uses surface_render_method instead
        if hasattr(m, 'surface_render_method'):
            m.surface_render_method = 'BLENDED' if has_xlu else 'DITHERED'

    return m


def triangulate(prim_type, indices):
    """Convert any GX primitive group into a flat list of (i0, i1, i2) tris."""
    n = len(indices)
    out = []
    if prim_type == 'Triangles':
        for i in range(0, n - 2, 3):
            out.append((indices[i], indices[i+1], indices[i+2]))
    elif prim_type == 'TriangleStrip':
        for i in range(n - 2):
            if (i & 1) == 0:
                out.append((indices[i], indices[i+1], indices[i+2]))
            else:
                out.append((indices[i+1], indices[i], indices[i+2]))
    elif prim_type == 'TriangleFan':
        for i in range(1, n - 1):
            out.append((indices[0], indices[i], indices[i+1]))
    elif prim_type == 'Quads':
        for i in range(0, n - 3, 4):
            out.append((indices[i], indices[i+1], indices[i+2]))
            out.append((indices[i], indices[i+2], indices[i+3]))
    else:
        print(f"  WARN: unhandled primitive type {prim_type}")
    return out


def build_mesh(mesh_dto, materials_by_id):
    mesh = bpy.data.meshes.new(mesh_dto['id'])

    # Vertices with coordinate flip
    verts = [game_to_blender(*v) for v in mesh_dto['vertices']]

    # Triangulate all primitives → single tri list
    tris = []
    for prim in mesh_dto['primitives']:
        tris.extend(triangulate(prim['type'], prim['indices']))

    mesh.from_pydata(verts, [], tris)
    mesh.update(calc_edges=True)

    # UVs
    uvs = mesh_dto.get('uvs')
    if uvs and len(mesh.polygons) > 0:
        uv_layer = mesh.uv_layers.new(name='UVMap')
        for poly in mesh.polygons:
            for li in poly.loop_indices:
                vi = mesh.loops[li].vertex_index
                u, v = uvs[vi]
                uv_layer.data[li].uv = (u, 1.0 - v)  # HSD V flipped relative to Blender

    # Normals (custom split)
    nrms = mesh_dto.get('normals')
    if nrms:
        # Apply same coordinate flip as positions
        flipped = [game_to_blender(*n) for n in nrms]
        try:
            mesh.normals_split_custom_set_from_vertices(flipped)
            # Auto-smooth removed in Blender 4.1+ (now done via modifier).
            if hasattr(mesh, 'use_auto_smooth'):
                mesh.use_auto_smooth = True
        except Exception as ex:
            print(f"  WARN: normal apply failed for {mesh_dto['id']}: {ex}")

    # Vertex colors
    cols = mesh_dto.get('colors')
    if cols and len(mesh.polygons) > 0:
        col_layer = mesh.color_attributes.new(name='Col', type='FLOAT_COLOR', domain='CORNER')
        for poly in mesh.polygons:
            for li in poly.loop_indices:
                vi = mesh.loops[li].vertex_index
                c = cols[vi]
                col_layer.data[li].color = (c[0], c[1], c[2], c[3])

    # Material
    mat_id = mesh_dto.get('material')
    if mat_id and mat_id in materials_by_id:
        mesh.materials.append(materials_by_id[mat_id])

    return mesh


def import_dat_directly(dat_path, *, gx_dump_dir=None):
    """Import a vanilla .dat into Blender as a `mkgp2:<dat>` bundle.

    Pipeline (Python-only, no external CLI):
      1. Read .dat bytes from disk.
      2. `hsdraw.export_scene_json(...)` -> scene.json equivalent in
         memory (joints / materials / textures / meshes / aliases).
      3. Walk the .dat's JObj/DObj/MObj/TObj chains in Python to
         collect each unique TObj's raw GX bytes. SHA-1 dedup keys
         match scene.json `textures[].id` by construction.
      4. `gx_decode` each unique texture into RGBA8 -> Blender Image
         via `bpy.data.images.new`. GX bytes also written to
         `gx_dump_dir` so the M3 exporter's bypass path works.
      5. Reuse `make_material` / `build_mesh` / joint-Empty pipeline
         from the legacy importer (their inputs are scene.json DTOs
         which have not changed).
      6. Stash `mkgp2_source_dat` / `mkgp2_joint_aliases` /
         `mkgp2_joints` / `mkgp2_scene_json` (now a JSON STRING, not a
         file path) on the collection for the export round-trip.

    `gx_dump_dir` defaults to a per-source-file temp dir; pass an
    explicit dir if you want the artifacts to live somewhere stable.
    Returns the new `bpy.types.Collection`.
    """
    import hsdraw

    dat_path = Path(dat_path).resolve()
    print(f"\n[mkgp2 hsd import (Python)] {dat_path}")
    raw = dat_path.read_bytes()

    # --- 1. scene.json equivalent metadata ---------------------------
    scene_json_str = hsdraw.export_scene_json(
        raw, source_dat=dat_path.name, tex_dir="tex")
    scene = json.loads(scene_json_str)

    # --- 2. GX bytes per unique texture ------------------------------
    if gx_dump_dir is None:
        gx_dump_dir = Path(tempfile.gettempdir()) / f"mkgp2_gx_{dat_path.stem}"
    gx_dump_dir = Path(gx_dump_dir)
    gx_dump_dir.mkdir(parents=True, exist_ok=True)

    dat = hsdraw.parse_dat(raw)
    gx_by_sha = _collect_gx_bytes(dat)

    # --- 3. Build Blender Images (one per unique texture) ------------
    image_cache = {}
    for tex in scene['textures']:
        tex_id = tex['id']
        gx_bytes = gx_by_sha.get(tex_id)
        if gx_bytes is None:
            print(f"  WARN: scene.json references texture {tex_id} but the "
                  ".dat walk did not surface matching GX bytes; skipping")
            continue
        make_image_from_gx(
            tex_id, gx_bytes,
            tex['format'], int(tex['width']), int(tex['height']),
            gx_dump_dir, image_cache)
    print(f"  loaded textures: {len(image_cache)} / {len(scene['textures'])}")

    # --- 4. Materials -------------------------------------------------
    materials_by_id = {}
    for mat_dto in scene['materials']:
        materials_by_id[mat_dto['id']] = make_material(mat_dto, image_cache)
    print(f"  built materials: {len(materials_by_id)}")

    # --- 5. Collection ------------------------------------------------
    coll_name = f"mkgp2:{scene['source_dat']}"
    coll = bpy.data.collections.new(coll_name)
    bpy.context.scene.collection.children.link(coll)

    # --- 6. Meshes ----------------------------------------------------
    n_meshes = n_skipped = 0
    for mesh_dto in scene['meshes']:
        try:
            mesh = build_mesh(mesh_dto, materials_by_id)
            if len(mesh.vertices) == 0 or len(mesh.polygons) == 0:
                n_skipped += 1
                continue
            obj = bpy.data.objects.new(mesh_dto['id'], mesh)
            coll.objects.link(obj)
            obj['mkgp2_joint_id'] = mesh_dto['joint']
            if mesh_dto.get('single_bind_joint'):
                obj['mkgp2_single_bind_joint'] = mesh_dto['single_bind_joint']
            obj['mkgp2_cull'] = mesh_dto['cull']
            obj['mkgp2_source_path'] = mesh_dto.get('source_path', '')
            n_meshes += 1
        except Exception as ex:
            print(f"  ERR mesh {mesh_dto['id']}: {ex}")
            n_skipped += 1

    # --- 7. Stash for round-trip --------------------------------------
    # `mkgp2_scene_json` is now the JSON STRING itself rather than a
    # path on disk. The M3 exporter (`_export_mkgp2_bundle`) accepts
    # either form for backward compatibility but new bundles always
    # carry the inline string.
    coll['mkgp2_source_dat'] = scene['source_dat']
    coll['mkgp2_joint_aliases'] = json.dumps(scene['joint_aliases'])
    coll['mkgp2_joints'] = json.dumps(scene['joints'])
    coll['mkgp2_scene_json'] = scene_json_str

    # --- 8. Joint Empty hierarchy -------------------------------------
    # World-baked vertices mean these Empties cannot drive visual
    # placement (would double-transform). They serve as a Blender-
    # native expression of the joint parent/children chain. The Export
    # HSD operator reads each Empty's `.parent` to update the stashed
    # `mkgp2_joints` parent / children fields before invoking the writer.
    id_to_empty = {}
    for jdto in scene['joints']:
        jid = jdto['id']
        empty = bpy.data.objects.new(f"{coll_name}:{jid}", None)
        empty.empty_display_type = 'PLAIN_AXES'
        empty.empty_display_size = 0.5
        empty['mkgp2_jobj_id'] = jid
        if jdto.get('flags'):
            empty['mkgp2_jobj_flags'] = ",".join(jdto['flags'])
        empty['mkgp2_jobj_local_t'] = list(jdto.get('translation', [0, 0, 0]))
        empty['mkgp2_jobj_local_r'] = list(jdto.get('rotation', [0, 0, 0]))
        empty['mkgp2_jobj_local_s'] = list(jdto.get('scale', [1, 1, 1]))
        coll.objects.link(empty)
        id_to_empty[jid] = empty
    for jdto in scene['joints']:
        parent_id = jdto.get('parent')
        if parent_id and parent_id in id_to_empty:
            id_to_empty[jdto['id']].parent = id_to_empty[parent_id]
    print(f"  joint Empties: {len(id_to_empty)} "
          f"(metadata only -- Empty TRS does not drive mesh placement)")

    print(f"  built meshes: {n_meshes} (skipped {n_skipped})")
    print(f"  collection: {coll_name}")
    print(f"[done]")
    return coll


# Backwards-compat shim: addon code historically called
# `hsd_imp.import_scene(...)`. Forward to the new entry point so a
# stale prefs/test still functions if it lands during the transition.
def import_scene(target):
    """Compat shim. Routes to import_dat_directly."""
    return import_dat_directly(target)


if __name__ == "__main__":
    # CLI usage: blender --background --python this.py -- <foo.dat> [save.blend]
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = []
    target = argv[0] if argv else DAT_PATH
    import_dat_directly(target)
    if len(argv) >= 2:
        save_path = argv[1]
        bpy.ops.wm.save_as_mainfile(filepath=save_path)
        print(f"  saved blend: {save_path}")
