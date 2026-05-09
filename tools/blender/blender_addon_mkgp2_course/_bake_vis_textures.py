"""Bake each vis:<name> material's BSDF Base Color into a small colored
Blender Image and wire it as an Image Texture node.

Goal: provide an Image Texture node that the user can **see in Blender**
(viewport shading shows it, addon can read it back) and that the HSD
exporter (`_promote_vis_to_hsd.py`) can pull image data out of.

Vanilla MKGP2 の primary course geometry は **全件 textured** で、
textureless mesh は shadow / overlay 用しか存在しない。textureless POBJ
だと GX TEV pipeline の lighting / texture sampling stage の register が
未初期化のまま draw され、scene 内の他描画の register 残骸が漏れて
mesh の opacity が camera 移動依存に変動 (= トンネル点滅) する。

このベイクパスで全 material に少なくとも 4x4 単色テクスチャを与え、
HSD 側を vanilla course 互換 (`CONSTANT|TEX0|ALPHA_MAT`) にする。
"""

from __future__ import annotations


def _bsdf(mat):
    """Find Principled BSDF node, or None."""
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return None
    for n in mat.node_tree.nodes:
        if n.type == 'BSDF_PRINCIPLED':
            return n
    return None


def _existing_image_texture(bsdf):
    """If BSDF.Base Color is connected to an Image Texture node, return it
    (the node, not the image).  Else None."""
    if bsdf is None:
        return None
    bc = bsdf.inputs.get("Base Color")
    if bc is None or not bc.is_linked:
        return None
    src = bc.links[0].from_node
    if src is None or src.type != 'TEX_IMAGE':
        return None
    return src


def _ensure_solid_image(bpy, name, rgba, size=4):
    """Create or reuse a `size x size` Blender Image filled with `rgba`
    (each component 0..255).  Returns the Blender Image."""
    img = bpy.data.images.get(name)
    if img is None:
        img = bpy.data.images.new(name, width=size, height=size, alpha=True)
    elif img.size[0] != size or img.size[1] != size:
        img.scale(size, size)
    img.alpha_mode = 'STRAIGHT'
    img.colorspace_settings.name = 'sRGB'

    r, g, b, a = (c / 255.0 for c in rgba)
    pixels = [r, g, b, a] * (size * size)
    img.pixels = pixels
    img.pack()
    return img


def _attach_image_texture(mat, img):
    """Insert an Image Texture node feeding mat's BSDF Base Color.
    Replaces any existing connection so the bake is idempotent."""
    nt = mat.node_tree
    bsdf = _bsdf(mat)
    if bsdf is None:
        return None
    bc = bsdf.inputs["Base Color"]

    # Drop any existing link first (might be a stale Image Texture).
    for link in list(bc.links):
        nt.links.remove(link)

    tex_node = nt.nodes.new(type='ShaderNodeTexImage')
    tex_node.image = img
    # Position to the left of the BSDF for visibility in editor.
    tex_node.location = (bsdf.location.x - 320, bsdf.location.y)
    nt.links.new(tex_node.outputs["Color"], bc)
    return tex_node


def bake_vis_collection_materials(vis_collection, *, log_fn=None) -> dict:
    """Walk each MESH object in `vis_collection`, ensure every material
    slot has a 4x4 colored Image Texture wired into its BSDF.

    - If BSDF Base Color already feeds from an Image Texture node, leave it
      untouched (= the user's hand-authored texture wins).
    - Otherwise read the BSDF.Base Color value (default float RGBA), create
      a 4x4 solid-color image named `<material>_solid`, attach it.

    Returns a stats dict (`materials_seen`, `attached`, `preserved`).
    """
    import bpy

    log = log_fn if log_fn is not None else print
    seen = 0
    attached = 0
    preserved = 0
    visited_mats = set()

    for obj in [o for o in vis_collection.objects if o.type == 'MESH']:
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None or mat.name in visited_mats:
                continue
            visited_mats.add(mat.name)
            seen += 1
            if not mat.use_nodes:
                mat.use_nodes = True
            bsdf = _bsdf(mat)
            if bsdf is None:
                log(f"  bake skip {mat.name!r}: no Principled BSDF node")
                continue
            existing = _existing_image_texture(bsdf)
            if existing is not None:
                preserved += 1
                log(f"  bake keep {mat.name!r}: already wired to "
                    f"{existing.image.name if existing.image else '(empty image)'}")
                continue
            # Read BSDF.Base Color default (Blender stores float RGBA)
            r, g, b, a = bsdf.inputs["Base Color"].default_value
            rgba = (
                int(round(r * 255)),
                int(round(g * 255)),
                int(round(b * 255)),
                int(round(a * 255)),
            )
            img_name = f"{mat.name}_solid"
            img = _ensure_solid_image(bpy, img_name, rgba)
            _attach_image_texture(mat, img)
            attached += 1
            log(f"  bake make {mat.name!r}: {rgba} -> {img_name} 4x4 attached")

    return {
        "materials_seen": seen,
        "attached": attached,
        "preserved": preserved,
    }
