"""
MKGP2 Course Tools  --  Blender addon

Wraps the standalone scripts in `mkgp2-patch/tools/blender/`
(`blender_import_*.py`, `blender_export_*.py`, `blender_import_course_all.py`)
into Blender operators, the File > Import / Export menus and a sidebar panel
(View3D > N > MKGP2).

Install:
  - Edit > Preferences > Add-ons > Install...
    Pick this folder zipped, OR pick this `__init__.py` directly if Blender
    accepts file install.
  - After enabling: open Preferences for the addon and set
    `Source modules directory` to e.g.
    `C:/Users/naari/src/github.com/naari3/mkgp2-patch/tools/blender`
    (only required if the addon was copied out of tools/blender/.)

The addon does not duplicate parser/exporter code; it dispatches to the
existing module functions. Hot-reload them via the "Reload course modules"
button after editing the source.
"""

bl_info = {
    "name": "MKGP2 Course Tools",
    "author": "naari3",
    "version": (0, 1, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > MKGP2  /  File > Import & Export",
    "description": "Import / export MKGP2 course resources (HSD mesh, collision, line waypoints, AI auto path)",
    "category": "Import-Export",
}

import bpy
import os
import sys
import importlib

from bpy.types import Operator, Panel, AddonPreferences
from bpy.props import StringProperty


# ---------------------------------------------------------------------------
# Module discovery (delegates to tools/blender/ scripts)
# ---------------------------------------------------------------------------

# Module references, populated by reload_modules().
hsd_imp = None
col_imp = None
line_imp = None
auto_imp = None
course_imp = None
line_exp = None
auto_exp = None
col_exp = None


def _user_path():
    try:
        prefs = bpy.context.preferences.addons[__name__].preferences
        p = prefs.source_modules_path
        if p and os.path.isdir(p):
            return p
    except Exception:
        pass
    return None


def _resolve_source_path():
    p = _user_path()
    if p:
        return p
    # Fallback: assume the addon directory sits inside tools/blender/.
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _import_or_reload(name):
    if name in sys.modules:
        return importlib.reload(sys.modules[name])
    return importlib.import_module(name)


def reload_modules():
    """Resolve source path, prepend it to sys.path, (re)import importer/exporter modules.

    Returns (ok: bool, error: Optional[str]).
    """
    global hsd_imp, col_imp, line_imp, auto_imp, course_imp
    global line_exp, auto_exp, col_exp

    path = _resolve_source_path()
    if not os.path.isdir(path):
        return False, f"source modules path does not exist: {path}"
    if path not in sys.path:
        sys.path.insert(0, path)

    try:
        hsd_imp = _import_or_reload("blender_import_hsd")
        col_imp = _import_or_reload("blender_import_collision")
        line_imp = _import_or_reload("blender_import_line")
        auto_imp = _import_or_reload("blender_import_auto")
        course_imp = _import_or_reload("blender_import_course_all")
        line_exp = _import_or_reload("blender_export_line")
        auto_exp = _import_or_reload("blender_export_auto")
        col_exp = _import_or_reload("blender_export_collision")
    except Exception as ex:
        return False, f"import failed (path={path}): {ex}"
    return True, None


def _need_modules(op):
    """Lazy-load on first use; report error to operator if it fails."""
    if hsd_imp is not None:
        return True
    ok, err = reload_modules()
    if not ok:
        op.report({'ERROR'}, f"MKGP2 modules unavailable: {err}")
    return ok


# ---------------------------------------------------------------------------
# Operators -- thin wrappers around the standalone scripts
# ---------------------------------------------------------------------------

class MKGP2_OT_ImportHSD(Operator):
    """Import an HSD scene.json bundle (created by hsd_export_for_blender.csx)"""
    bl_idname = "import_scene.mkgp2_hsd_json"
    bl_label = "Import MKGP2 HSD (scene.json)"
    bl_options = {'PRESET', 'UNDO'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        if not _need_modules(self):
            return {'CANCELLED'}
        try:
            hsd_imp.import_scene(self.filepath)
        except Exception as ex:
            self.report({'ERROR'}, f"HSD import failed: {ex}")
            return {'CANCELLED'}
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class MKGP2_OT_ImportCollision(Operator):
    """Import a course collision .bin (CollisionMesh + WallSegments)"""
    bl_idname = "import_mesh.mkgp2_collision_bin"
    bl_label = "Import MKGP2 Collision (.bin)"
    bl_options = {'PRESET', 'UNDO'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.bin", options={'HIDDEN'})

    def execute(self, context):
        if not _need_modules(self):
            return {'CANCELLED'}
        try:
            col_imp.import_collision(self.filepath)
        except Exception as ex:
            self.report({'ERROR'}, f"Collision import failed: {ex}")
            return {'CANCELLED'}
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class MKGP2_OT_ImportLine(Operator):
    """Import a course _line.bin (lap waypoints + AI variants)"""
    bl_idname = "import_mesh.mkgp2_line_bin"
    bl_label = "Import MKGP2 Line (.bin)"
    bl_options = {'PRESET', 'UNDO'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.bin", options={'HIDDEN'})

    def execute(self, context):
        if not _need_modules(self):
            return {'CANCELLED'}
        try:
            line_imp.import_line(self.filepath)
        except Exception as ex:
            self.report({'ERROR'}, f"Line import failed: {ex}")
            return {'CANCELLED'}
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class MKGP2_OT_ImportAuto(Operator):
    """Import an _Auto.bin AI driving path"""
    bl_idname = "import_mesh.mkgp2_auto_bin"
    bl_label = "Import MKGP2 Auto Path (.bin)"
    bl_options = {'PRESET', 'UNDO'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.bin", options={'HIDDEN'})

    def execute(self, context):
        if not _need_modules(self):
            return {'CANCELLED'}
        try:
            auto_imp.import_auto(self.filepath)
        except Exception as ex:
            self.report({'ERROR'}, f"Auto import failed: {ex}")
            return {'CANCELLED'}
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class MKGP2_OT_ImportFullCourse(Operator):
    """Load HSD scene + collision + line + auto for one course in a single shot"""
    bl_idname = "import_scene.mkgp2_full_course"
    bl_label = "Import MKGP2 Full Course"
    bl_options = {'PRESET', 'UNDO'}

    scene_json: StringProperty(
        name="scene.json",
        description="HSD bundle JSON (sibling tex/ folder is read alongside)",
        subtype='FILE_PATH',
    )
    bin_dir: StringProperty(
        name="bin directory",
        description="Folder containing <prefix>_short.bin / _line.bin / _Auto.bin etc.",
        subtype='DIR_PATH',
    )
    prefix: StringProperty(
        name="prefix",
        description="Course prefix (e.g. mr_highway). Loads short+long collision/line/Auto by suffix.",
        default="",
    )

    def execute(self, context):
        if not _need_modules(self):
            return {'CANCELLED'}
        if not self.scene_json:
            self.report({'ERROR'}, "scene.json path required")
            return {'CANCELLED'}
        if not self.bin_dir or not self.prefix:
            self.report({'ERROR'}, "bin directory and prefix required")
            return {'CANCELLED'}
        try:
            course_imp.import_course(self.scene_json, self.bin_dir, self.prefix)
        except Exception as ex:
            self.report({'ERROR'}, f"Full course import failed: {ex}")
            return {'CANCELLED'}
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=520)


class MKGP2_OT_ExportLine(Operator):
    """Export the active line root (or any variant mesh under it) back to _line.bin"""
    bl_idname = "export_scene.mkgp2_line_bin"
    bl_label = "Export MKGP2 Line (.bin)"
    bl_options = {'PRESET'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.bin", options={'HIDDEN'})

    def execute(self, context):
        if not _need_modules(self):
            return {'CANCELLED'}
        obj = context.active_object
        if obj is None:
            self.report({'ERROR'}, "Select a line root empty or any variant mesh first")
            return {'CANCELLED'}
        try:
            line_exp.export_line(self.filepath, obj=obj)
        except Exception as ex:
            self.report({'ERROR'}, f"Line export failed: {ex}")
            return {'CANCELLED'}
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class MKGP2_OT_ExportAuto(Operator):
    """Export the active auto-path mesh back to _Auto.bin"""
    bl_idname = "export_scene.mkgp2_auto_bin"
    bl_label = "Export MKGP2 Auto Path (.bin)"
    bl_options = {'PRESET'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.bin", options={'HIDDEN'})

    def execute(self, context):
        if not _need_modules(self):
            return {'CANCELLED'}
        obj = context.active_object
        if obj is None:
            self.report({'ERROR'}, "Select an auto-path mesh first")
            return {'CANCELLED'}
        try:
            auto_exp.export_auto(self.filepath, obj=obj)
        except Exception as ex:
            self.report({'ERROR'}, f"Auto export failed: {ex}")
            return {'CANCELLED'}
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class MKGP2_OT_ExportCollision(Operator):
    """Export the CollisionMesh + WallSegments objects back to a course .bin"""
    bl_idname = "export_mesh.mkgp2_collision_bin"
    bl_label = "Export MKGP2 Collision (.bin)"
    bl_options = {'PRESET'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.bin", options={'HIDDEN'})

    def execute(self, context):
        if not _need_modules(self):
            return {'CANCELLED'}
        col_obj = bpy.data.objects.get("CollisionMesh")
        if col_obj is None:
            self.report({'ERROR'}, "Object 'CollisionMesh' not found in scene")
            return {'CANCELLED'}
        for key in ("grid_width", "grid_height", "cell_size_x", "cell_size_z",
                    "grid_origin_x", "grid_origin_z"):
            if key not in col_obj.keys():
                self.report({'ERROR'},
                    f"CollisionMesh missing custom property '{key}' "
                    "(re-import with the addon to regenerate)")
                return {'CANCELLED'}
        try:
            triangles = col_exp.collect_triangles(col_obj)
            walls = []
            wall_obj = bpy.data.objects.get("WallSegments")
            if wall_obj is not None:
                walls = col_exp.collect_wall_segments(wall_obj)
            reserved_bytes = bytes.fromhex(col_obj.get("reserved_hex", "0" * 32))
            size = col_exp.write_collision_bin(
                self.filepath, triangles, walls,
                col_obj["grid_width"], col_obj["grid_height"],
                col_obj["cell_size_x"], col_obj["cell_size_z"],
                col_obj["grid_origin_x"], col_obj["grid_origin_z"],
                reserved_bytes,
            )
            self.report({'INFO'},
                f"Wrote {size} bytes ({len(triangles)} tris, {len(walls)} walls)")
        except Exception as ex:
            self.report({'ERROR'}, f"Collision export failed: {ex}")
            return {'CANCELLED'}
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class MKGP2_OT_ReloadModules(Operator):
    """Re-import the course tool scripts (call after editing them)"""
    bl_idname = "mkgp2.reload_modules"
    bl_label = "Reload course modules"

    def execute(self, context):
        ok, err = reload_modules()
        if ok:
            self.report({'INFO'}, "Reloaded course modules")
            return {'FINISHED'}
        self.report({'ERROR'}, err or "Reload failed")
        return {'CANCELLED'}


# ---------------------------------------------------------------------------
# Sidebar panel
# ---------------------------------------------------------------------------

class MKGP2_PT_CoursePanel(Panel):
    bl_label = "MKGP2 Course"
    bl_idname = "MKGP2_PT_course_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MKGP2'

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text="Import per-asset:", icon='IMPORT')
        col = box.column(align=True)
        col.operator("import_scene.mkgp2_hsd_json", text="HSD scene.json")
        col.operator("import_mesh.mkgp2_collision_bin", text="Collision (.bin)")
        col.operator("import_mesh.mkgp2_line_bin", text="Line (.bin)")
        col.operator("import_mesh.mkgp2_auto_bin", text="Auto path (.bin)")

        box = layout.box()
        box.label(text="Import full course:", icon='WORLD')
        box.operator("import_scene.mkgp2_full_course", text="HSD + col + line + auto")

        box = layout.box()
        box.label(text="Export:", icon='EXPORT')
        col = box.column(align=True)
        col.operator("export_mesh.mkgp2_collision_bin", text="Collision (.bin)")
        col.operator("export_scene.mkgp2_line_bin", text="Line (.bin)")
        col.operator("export_scene.mkgp2_auto_bin", text="Auto path (.bin)")

        layout.separator()
        layout.operator("mkgp2.reload_modules", text="Reload course modules", icon='FILE_REFRESH')
        layout.label(text=f"src: {_resolve_source_path()}", icon='FILE_FOLDER')


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

class MKGP2AddonPreferences(AddonPreferences):
    bl_idname = __name__

    source_modules_path: StringProperty(
        name="Source modules directory",
        description=(
            "Path to mkgp2-patch/tools/blender/ (containing blender_import_*.py / "
            "blender_export_*.py). Leave empty if this addon lives inside that folder."
        ),
        subtype='DIR_PATH',
        default="",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "source_modules_path")
        layout.label(text=f"Currently resolved: {_resolve_source_path()}")
        layout.operator("mkgp2.reload_modules", icon='FILE_REFRESH')


# ---------------------------------------------------------------------------
# File menu integration
# ---------------------------------------------------------------------------

def _menu_import(self, context):
    self.layout.separator()
    self.layout.operator("import_scene.mkgp2_hsd_json", text="MKGP2 HSD (scene.json)")
    self.layout.operator("import_mesh.mkgp2_collision_bin", text="MKGP2 Collision (.bin)")
    self.layout.operator("import_mesh.mkgp2_line_bin", text="MKGP2 Line (.bin)")
    self.layout.operator("import_mesh.mkgp2_auto_bin", text="MKGP2 Auto Path (.bin)")
    self.layout.operator("import_scene.mkgp2_full_course", text="MKGP2 Full Course")


def _menu_export(self, context):
    self.layout.separator()
    self.layout.operator("export_mesh.mkgp2_collision_bin", text="MKGP2 Collision (.bin)")
    self.layout.operator("export_scene.mkgp2_line_bin", text="MKGP2 Line (.bin)")
    self.layout.operator("export_scene.mkgp2_auto_bin", text="MKGP2 Auto Path (.bin)")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

CLASSES = (
    MKGP2AddonPreferences,
    MKGP2_OT_ImportHSD,
    MKGP2_OT_ImportCollision,
    MKGP2_OT_ImportLine,
    MKGP2_OT_ImportAuto,
    MKGP2_OT_ImportFullCourse,
    MKGP2_OT_ExportLine,
    MKGP2_OT_ExportAuto,
    MKGP2_OT_ExportCollision,
    MKGP2_OT_ReloadModules,
    MKGP2_PT_CoursePanel,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(_menu_import)
    bpy.types.TOPBAR_MT_file_export.append(_menu_export)
    # Best-effort eager load so first import is fast and configuration errors
    # surface in the console instead of mid-operator.
    ok, err = reload_modules()
    if not ok:
        print(f"[MKGP2 addon] initial module load skipped: {err}")


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
    bpy.types.TOPBAR_MT_file_export.remove(_menu_export)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
