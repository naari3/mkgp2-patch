"""
MKGP2 HSD Scene Importer (PoC, single script, no addon UI)

Reads the scene.json + tex/ bundle produced by hsd_export_for_blender.csx and
builds Blender meshes + materials + image textures, ready as L1 visual
reference for collision / path editing.

Usage:
  Method A — Blender Text Editor:
    1. Open Blender > Text Editor > Open `blender_import_hsd.py`
    2. Edit SCENE_JSON below (or pass via Python console)
    3. Click "Run Script"

  Method B — CLI:
    blender --python blender_import_hsd.py -- <path-to-scene.json>

Coordinate system (matches blender_import_collision.py / _line.py / _auto.py):
    Game Y-up -> Blender Z-up:
        Blender X =  Game X
        Blender Y = -Game Z
        Blender Z =  Game Y

What gets created:
  - One Blender Collection named after source_dat
  - One Mesh Object per JSON mesh (parented to nothing; vertices already
    in world space).  Joint hierarchy is preserved in custom properties
    (joint_id, single_bind_joint) so a future re-export can reconstruct it.
  - One Material per JSON material (named mat_<n>).  Diffuse base color
    set from texture; XLU render_flags get blend_method=BLEND.
  - One Image per unique texture (loaded with check_existing so the same
    PNG is shared across materials; matches HSD's struct-sharing dedup).

Limitations (PoC):
  - Empties for joint hierarchy are NOT created (vertices are world-baked,
    so empties would double-transform).  Hierarchy is recorded as custom
    props only.  A proper addon will reconstruct hierarchy AND keep verts
    in JObj-local space for round-trip.
  - Skinning / shape_set are unsupported (course mesh data does not use
    them, but custom mods someday might).
  - PEDesc / TEV / mipmap LOD info is not yet wired into Blender material
    nodes.
"""

import bpy
import json
import os
import sys
from pathlib import Path

# ============================================================
# CONFIGURATION — edit when running from Text Editor
# ============================================================
SCENE_JSON = r"C:\Users\naari\Documents\blender\mr_highway_export\scene.json"
# ============================================================


def game_to_blender(x, y, z):
    return (x, -z, y)


def make_image(tex, base_dir, image_cache):
    tex_id = tex['id']
    if tex_id in image_cache:
        return image_cache[tex_id]
    png_path = (base_dir / tex['file']).resolve()
    if not png_path.exists():
        print(f"  WARN: missing texture file {png_path}")
        return None
    img = bpy.data.images.load(str(png_path), check_existing=True)
    img.alpha_mode = 'STRAIGHT'
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


def import_scene(scene_json_path):
    scene_json_path = Path(scene_json_path).resolve()
    base_dir = scene_json_path.parent
    print(f"\n[mkgp2 hsd import] {scene_json_path}")
    with open(scene_json_path, 'r', encoding='utf-8') as f:
        scene = json.load(f)

    # 1. Images
    image_cache = {}
    for tex in scene['textures']:
        make_image(tex, base_dir, image_cache)
    print(f"  loaded textures: {len(image_cache)} / {len(scene['textures'])}")

    # 2. Materials
    materials_by_id = {}
    for mat_dto in scene['materials']:
        materials_by_id[mat_dto['id']] = make_material(mat_dto, image_cache)
    print(f"  built materials: {len(materials_by_id)}")

    # 3. Collection
    coll_name = f"mkgp2:{scene['source_dat']}"
    coll = bpy.data.collections.new(coll_name)
    bpy.context.scene.collection.children.link(coll)

    # 4. Meshes
    n_meshes = 0
    n_skipped = 0
    for mesh_dto in scene['meshes']:
        try:
            mesh = build_mesh(mesh_dto, materials_by_id)
            if len(mesh.vertices) == 0 or len(mesh.polygons) == 0:
                n_skipped += 1
                continue
            obj = bpy.data.objects.new(mesh_dto['id'], mesh)
            coll.objects.link(obj)
            # Round-trip metadata
            obj['mkgp2_joint_id'] = mesh_dto['joint']
            if mesh_dto.get('single_bind_joint'):
                obj['mkgp2_single_bind_joint'] = mesh_dto['single_bind_joint']
            obj['mkgp2_cull'] = mesh_dto['cull']
            obj['mkgp2_source_path'] = mesh_dto.get('source_path', '')
            n_meshes += 1
        except Exception as ex:
            print(f"  ERR mesh {mesh_dto['id']}: {ex}")
            n_skipped += 1

    # 5. Stash joint table on the collection for round-trip
    coll['mkgp2_source_dat'] = scene['source_dat']
    coll['mkgp2_joint_aliases'] = json.dumps(scene['joint_aliases'])
    coll['mkgp2_joints'] = json.dumps(scene['joints'])

    print(f"  built meshes: {n_meshes} (skipped {n_skipped})")
    print(f"  collection: {coll_name}")
    print(f"[done]")


if __name__ == "__main__":
    # Detect "--" CLI arg (blender passes everything after it)
    #   blender --background --python this.py -- <scene.json> [save-blend-path]
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = []
    target = argv[0] if argv else SCENE_JSON
    import_scene(target)
    if len(argv) >= 2:
        save_path = argv[1]
        bpy.ops.wm.save_as_mainfile(filepath=save_path)
        print(f"  saved blend: {save_path}")
