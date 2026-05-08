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
    "version": (0, 1, 9),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > MKGP2  /  File > Import & Export",
    "description": "Import / export MKGP2 course resources (HSD mesh, collision, line waypoints, AI auto path)",
    "category": "Import-Export",
}

import bpy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import importlib
from pathlib import Path

import mathutils
from bpy.types import Operator, Panel, AddonPreferences
from bpy.props import StringProperty, IntProperty, BoolProperty, FloatProperty


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
validate = None


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
    # realpath() resolves NTFS junctions / symlinks (the recommended install
    # pattern) so we land on the actual repo location, not the Blender
    # addons/ folder where the link lives.
    return os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def _import_or_reload(name):
    if name in sys.modules:
        return importlib.reload(sys.modules[name])
    return importlib.import_module(name)


def reload_modules():
    """Resolve source path, prepend it to sys.path, (re)import importer/exporter modules.

    Returns (ok: bool, error: Optional[str]).
    """
    global hsd_imp, col_imp, line_imp, auto_imp, course_imp
    global line_exp, auto_exp, col_exp, validate

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
        validate = _import_or_reload("blender_validate")
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


def _seed_filepath(op_self, default_filename="", *, prefer_output=False):
    """Initialize op_self.filepath from a configured bin directory.

    Used by per-asset importers/exporters so the file browser opens in
    the user's configured course-asset directory instead of `<repo>`
    or wherever Blender's last cwd was.

    `prefer_output=True` opens in the output directory (suitable for
    Export operators); the default is the vanilla directory which is
    where Import operators expect to start.
    """
    if op_self.filepath:
        return  # caller already chose something
    if prefer_output:
        base = _output_bin_dir() or _vanilla_bin_dir()
    else:
        base = _vanilla_bin_dir()
    if not base:
        return
    if default_filename:
        op_self.filepath = os.path.join(base, default_filename)
    else:
        op_self.filepath = base + os.sep


# ---------------------------------------------------------------------------
# csx (HSD export pipeline) helpers
# ---------------------------------------------------------------------------
#
# `tools/hsd/hsd_export_for_blender.csx` extracts an HSD .dat file into
# a Blender-friendly bundle (scene.json + tex/<sha1>.png). Vanilla
# course .dat names follow the fixed `<Prefix>_<round>_A.dat` pattern,
# so the addon can run the csx automatically given just bin_dir +
# prefix and hide scene.json entirely as an internal representation.
#
# Strategy: locate dotnet-script, run csx via subprocess, return the
# generated scene.json path. Errors surface via raised exceptions; the
# operator turns them into Blender ERROR reports.


def _resolve_dotnet_script():
    """Locate the `dotnet-script` launcher.

    Precedence:
      1. Addon preference `dotnet_script_path` if set and existing.
      2. `dotnet-script` on PATH.
      3. Default `dotnet tool install` location:
           %USERPROFILE%/.dotnet/tools/dotnet-script(.exe)
    """
    try:
        prefs = bpy.context.preferences.addons[__name__].preferences
        p = (prefs.dotnet_script_path or "").strip()
        if p and os.path.isfile(p):
            return p
    except Exception:
        pass
    p = shutil.which("dotnet-script")
    if p:
        return p
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    for cand in ("dotnet-script.exe", "dotnet-script"):
        full = os.path.join(home, ".dotnet", "tools", cand)
        if os.path.isfile(full):
            return full
    return None


def _resolve_csx_path():
    """Path to tools/hsd/hsd_export_for_blender.csx."""
    src = _resolve_source_path()  # .../tools/blender
    return os.path.normpath(os.path.join(src, "..", "hsd",
                                         "hsd_export_for_blender.csx"))


def _run_csx_for_dat(dat_path, out_dir, *, timeout=240):
    """Run hsd_export_for_blender.csx <dat_path> <out_dir> via dotnet-script.

    Raises RuntimeError on any failure (with the captured stderr/stdout
    appended for triage). Returns the path to <out_dir>/scene.json.
    """
    csx = _resolve_csx_path()
    if not os.path.isfile(csx):
        raise RuntimeError(
            f"csx script not found at {csx}. Is the addon installed via a "
            "junction back into tools/blender? Adjust source_modules_path."
        )
    dotnet = _resolve_dotnet_script()
    if not dotnet:
        raise RuntimeError(
            "dotnet-script not found. Install it with "
            "`dotnet tool install --global dotnet-script` or set the "
            "addon preference `dotnet_script_path`."
        )

    os.makedirs(out_dir, exist_ok=True)
    cmd = [dotnet, csx, "--", str(dat_path), str(out_dir)]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(
            f"csx returned {proc.returncode} for {os.path.basename(dat_path)}\n"
            f"stdout: {proc.stdout.strip()[:500]}\n"
            f"stderr: {proc.stderr.strip()[:500]}"
        )
    scene_json = os.path.join(out_dir, "scene.json")
    if not os.path.isfile(scene_json):
        raise RuntimeError(
            f"csx finished but did not produce scene.json at {scene_json}\n"
            f"stdout: {proc.stdout.strip()[:500]}"
        )
    return scene_json


def _resolve_writer_csx_path():
    """Path to tools/hsd/hsd_import_from_blender.csx."""
    src = _resolve_source_path()  # .../tools/blender
    return os.path.normpath(os.path.join(src, "..", "hsd",
                                         "hsd_import_from_blender.csx"))


def _run_writer_csx(base_dat, bundle_dir, out_dat, *, timeout=240):
    """Run hsd_import_from_blender.csx <base.dat> <bundle.dir> <out.dat>.

    Raises RuntimeError on any failure with captured stderr/stdout for
    triage. Returns the (str) path to the produced .dat.
    """
    csx = _resolve_writer_csx_path()
    if not os.path.isfile(csx):
        raise RuntimeError(
            f"writer csx not found at {csx}. Is the addon installed via "
            "a junction back into tools/blender?"
        )
    dotnet = _resolve_dotnet_script()
    if not dotnet:
        raise RuntimeError(
            "dotnet-script not found. Install it with "
            "`dotnet tool install --global dotnet-script` or set the "
            "addon preference `dotnet_script_path`."
        )

    os.makedirs(os.path.dirname(out_dat), exist_ok=True)
    cmd = [dotnet, csx, "--", str(base_dat), str(bundle_dir), str(out_dat)]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(
            f"writer csx returned {proc.returncode} for "
            f"{os.path.basename(base_dat)} -> {os.path.basename(out_dat)}\n"
            f"stdout: {proc.stdout.strip()[:800]}\n"
            f"stderr: {proc.stderr.strip()[:800]}"
        )
    if not os.path.isfile(out_dat):
        raise RuntimeError(
            f"writer csx finished but did not produce {out_dat}\n"
            f"stdout: {proc.stdout.strip()[:500]}"
        )
    return out_dat


def _find_vanilla_dat(bin_dir, prefix, round_label):
    """Locate the vanilla .dat for `<prefix>_<round>_A` in `bin_dir`,
    matching case-insensitively. Returns Path or None."""
    bin_dir = Path(bin_dir)
    expected = f"{prefix.lower()}_{round_label.lower()}_a.dat"
    if not bin_dir.is_dir():
        return None
    for p in bin_dir.iterdir():
        if p.name.lower() == expected and p.is_file():
            return p
    return None


# ---------------------------------------------------------------------------
# Custom-course collection helpers
# ---------------------------------------------------------------------------
#
# A custom course is represented as a Blender collection tagged with
# `mkgp2_kind = "course"` and the following filename / directory props:
#
#   mkgp2_course_name     human-readable identifier (also the collection name)
#   mkgp2_collision_bin   filename for collision .bin
#   mkgp2_line_bin        filename for _line.bin
#   mkgp2_auto_f_bin      filename for _Auto.bin (forward direction)
#   mkgp2_auto_r_bin      filename for _Auto_R.bin (reverse direction)
#   mkgp2_bin_dir         absolute directory; empty means "use addon preference"
#
# All four child object roles (collision pair, line root, auto-F mesh,
# auto-R mesh) live inside the course collection.  Auto meshes carry a
# secondary `mkgp2_auto_role` custom prop ("F" or "R") so the exporter
# can pick them apart without relying on a specific name suffix.

ROOT_COLL_NAME = "MKGP2_Course"


def _ensure_courses_root():
    """Return (creating if needed) the top-level MKGP2_Course collection."""
    coll = bpy.data.collections.get(ROOT_COLL_NAME)
    if coll is None:
        coll = bpy.data.collections.new(ROOT_COLL_NAME)
        bpy.context.scene.collection.children.link(coll)
    return coll


def _find_parent_collection(child):
    """bpy.data.collections has no parent ref, so walk every collection
    looking for one that lists `child` among its children."""
    for c in bpy.data.collections:
        if any(cc is child for cc in c.children):
            return c
    if child in [cc for cc in bpy.context.scene.collection.children]:
        return bpy.context.scene.collection
    return None


def _resolve_course_collection(context):
    """Resolve the course collection from the current context.

    Resolution order:
      1. If the active layer collection is itself a course, return it.
      2. Otherwise walk up the active object's collection chain looking
         for the first ancestor with `mkgp2_kind == "course"`.
    """
    layer_coll = getattr(context.view_layer, "active_layer_collection", None)
    if layer_coll is not None:
        c = layer_coll.collection
        if c.get("mkgp2_kind") == "course":
            return c
    obj = context.active_object
    if obj is not None:
        for c in obj.users_collection:
            cur = c
            visited = set()
            while cur is not None and cur.name not in visited:
                if cur.get("mkgp2_kind") == "course":
                    return cur
                visited.add(cur.name)
                cur = _find_parent_collection(cur)
    return None


def _resolve_hsd_bundle_collection(context):
    """Resolve an HSD bundle collection from the current context.

    Bundle collections are created by `MKGP2_OT_ImportHSD` /
    `_run_csx_for_dat` and have `mkgp2_source_dat` + `mkgp2_joints` +
    `mkgp2_joint_aliases` custom props. Their conventional name is
    `mkgp2:<dat_filename>` but identification is by prop, not name.

    Resolution order:
      1. Active layer collection if it is a bundle.
      2. Walk up the active object's collection chain.
    Returns None if no bundle is in scope.
    """
    layer_coll = getattr(context.view_layer, "active_layer_collection", None)
    if layer_coll is not None:
        c = layer_coll.collection
        if c.get("mkgp2_source_dat"):
            return c
    obj = context.active_object
    if obj is not None:
        for c in obj.users_collection:
            cur = c
            visited = set()
            while cur is not None and cur.name not in visited:
                if cur.get("mkgp2_source_dat"):
                    return cur
                visited.add(cur.name)
                cur = _find_parent_collection(cur)
    return None


def _link_objs_to_collection(target_coll, objs):
    """Move objects so that `target_coll` is their *only* collection.

    Importers add objects to whatever collection was active when they
    ran (typically the scene's master collection). Course import wants
    them to live exclusively inside the course collection.
    """
    for o in objs:
        for c in list(o.users_collection):
            c.objects.unlink(o)
        target_coll.objects.link(o)


def _link_collections_to_collection(parent_coll, child_colls):
    """Re-link a list of child collections so that they live exclusively
    under `parent_coll`. Used to capture HSD's `mkgp2:<dat>` collection
    (created by the HSD importer) into a course collection."""
    scene_root = bpy.context.scene.collection
    for cc in child_colls:
        # Unlink from anywhere it currently sits
        for c in bpy.data.collections:
            if cc.name in [x.name for x in c.children]:
                c.children.unlink(cc)
        if cc.name in [x.name for x in scene_root.children]:
            scene_root.children.unlink(cc)
        parent_coll.children.link(cc)


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


class MKGP2_OT_ExportHSD(Operator):
    """Write the active HSD bundle collection back to a .dat file via
    hsd_import_from_blender.csx.

    Phase 2 v0 wiring — re-emits the collection's stashed scene.json
    (mkgp2_joints + mkgp2_joint_aliases) verbatim and runs the writer
    csx with the bundle's source vanilla .dat as the structural base.
    Mesh / material / texture content stays at byte-equivalent of the
    base; structural changes (joint TRS / flags / hierarchy / aliases)
    that are made on the bundle's stashed JSON props will land in the
    output .dat.

    Geometry edit (vertex moves on existing meshes) and brand-new
    meshes are NOT supported in this phase — the writer csx itself
    does not yet rebuild POBJ display lists. Edits done in Blender's
    mesh editor will silently revert in the output.
    """
    bl_idname = "export_scene.mkgp2_hsd_json"
    bl_label = "Export MKGP2 HSD (.dat)"
    bl_options = {'PRESET'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.dat", options={'HIDDEN'})

    def execute(self, context):
        bundle = _resolve_hsd_bundle_collection(context)
        if bundle is None:
            self.report({'ERROR'},
                "No HSD bundle in context. Activate an `mkgp2:<dat>` "
                "collection in the Outliner or pick an object that lives "
                "inside one.")
            return {'CANCELLED'}

        source_dat = bundle.get("mkgp2_source_dat", "")
        if not source_dat:
            self.report({'ERROR'},
                f"Bundle '{bundle.name}' has no `mkgp2_source_dat` "
                "property set; cannot identify base .dat.")
            return {'CANCELLED'}

        # Resolve the base .dat (the unmodified vanilla source the bundle
        # was originally extracted from). Look it up from the vanilla bin
        # directory preference; the writer csx will read from this file
        # but never write to it.
        van_dir = _vanilla_bin_dir()
        if not van_dir:
            self.report({'ERROR'},
                "Vanilla bin directory not configured in addon "
                "preferences; cannot resolve base .dat.")
            return {'CANCELLED'}
        base_dat = Path(van_dir) / source_dat
        if not base_dat.is_file():
            self.report({'ERROR'},
                f"Base .dat '{source_dat}' not found under the vanilla "
                f"bin directory ({van_dir}). Re-extract the ROM or "
                "correct the preference.")
            return {'CANCELLED'}

        if not self.filepath:
            self.report({'ERROR'}, "No output filepath set.")
            return {'CANCELLED'}
        if _refuse_if_vanilla(self, self.filepath, what="HSD .dat"):
            return {'CANCELLED'}

        # Materialize a temp bundle dir holding scene.json built from
        # the collection's stashed props. We don't need the tex/*.png
        # subdir because the writer csx (Phase 1 scope) doesn't read
        # textures.
        try:
            joints = json.loads(bundle.get("mkgp2_joints", "[]"))
            aliases = json.loads(bundle.get("mkgp2_joint_aliases", "{}"))
        except json.JSONDecodeError as ex:
            self.report({'ERROR'},
                f"Bundle '{bundle.name}' has malformed stashed JSON: {ex}")
            return {'CANCELLED'}

        scene = {
            "source_dat": source_dat,
            "tex_dir": "tex",
            "textures": [],
            "materials": [],
            "joints": joints,
            "joint_aliases": aliases,
            "meshes": [],
        }
        bundle_dir = tempfile.mkdtemp(prefix="mkgp2_hsd_export_")
        try:
            with open(os.path.join(bundle_dir, "scene.json"), "w",
                      encoding="utf-8") as f:
                json.dump(scene, f)

            try:
                _run_writer_csx(str(base_dat), bundle_dir, self.filepath)
            except RuntimeError as ex:
                self.report({'ERROR'}, f"writer csx failed: {ex}")
                return {'CANCELLED'}
        finally:
            shutil.rmtree(bundle_dir, ignore_errors=True)

        size = Path(self.filepath).stat().st_size
        self.report({'INFO'},
            f"Wrote {Path(self.filepath).name} ({size} bytes) using base "
            f"{source_dat}")
        return {'FINISHED'}

    def invoke(self, context, event):
        bundle = _resolve_hsd_bundle_collection(context)
        if bundle is None:
            self.report({'ERROR'},
                "No HSD bundle in context. Activate an `mkgp2:<dat>` "
                "collection or pick an object inside one.")
            return {'CANCELLED'}
        if not self.filepath:
            source_dat = bundle.get("mkgp2_source_dat", "")
            base_name = source_dat or "out.dat"
            out_dir = _output_bin_dir() or _vanilla_bin_dir() or ""
            if out_dir:
                self.filepath = os.path.join(out_dir, base_name)
            else:
                self.filepath = base_name
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
        _seed_filepath(self)
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
        _seed_filepath(self)
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
        _seed_filepath(self)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class MKGP2_OT_ImportFullCourse(Operator):
    """Load HSD scene (auto-extracted via csx) + collision + line + auto
    for one vanilla course.

    Default flow (scene_json blank): the addon runs
    `tools/hsd/hsd_export_for_blender.csx` over
    `<bin_dir>/<Prefix>_short_A.dat` and `<Prefix>_long_A.dat`,
    importing each generated bundle. csx + dotnet-script are auto
    detected; override paths in addon preferences if needed.

    Manual flow (scene_json filled): treat the explicit bundle as
    short, then sweep collision/line/auto by suffix as before. Useful
    when you've pre-generated a single scene.json.
    """
    bl_idname = "import_scene.mkgp2_full_course"
    bl_label = "Import MKGP2 Full Course"
    bl_options = {'PRESET', 'UNDO'}

    scene_json: StringProperty(
        name="scene.json (legacy)",
        description=(
            "Optional explicit HSD bundle. Leave empty to let the "
            "addon auto-discover and run csx on the matching .dat "
            "files in bin directory."
        ),
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

    def invoke(self, context, event):
        if not self.bin_dir:
            # Vanilla full-course import reads from the read-only dump.
            self.bin_dir = _vanilla_bin_dir()
        return context.window_manager.invoke_props_dialog(self, width=520)

    def execute(self, context):
        if not _need_modules(self):
            return {'CANCELLED'}
        if not self.bin_dir or not self.prefix:
            self.report({'ERROR'}, "bin directory and prefix required")
            return {'CANCELLED'}
        try:
            if self.scene_json:
                # Legacy single-bundle path; preserved so existing
                # workflows that pre-extracted scene.json keep working.
                course_imp.import_course(self.scene_json, self.bin_dir,
                                         self.prefix)
            else:
                # Auto path: csx on each round's .dat, then sweep .bin set.
                self._auto_import(self.bin_dir, self.prefix)
        except Exception as ex:
            self.report({'ERROR'}, f"Full course import failed: {ex}")
            return {'CANCELLED'}
        return {'FINISHED'}

    def _auto_import(self, bin_dir, prefix):
        """Run csx over every <Prefix>_<round>_A.dat we can find,
        import each bundle, then sweep the standard .bin filenames."""
        bin_dir_p = Path(bin_dir)

        # 1) HSD bundles per round
        bundles = []
        for round_label in ("short", "long"):
            dat = _find_vanilla_dat(bin_dir_p, prefix, round_label)
            if dat is None:
                print(f"[mkgp2 full] no .dat for {round_label} "
                      f"(<{prefix}>_{round_label}_A.dat); HSD skipped")
                continue
            out_dir = tempfile.mkdtemp(
                prefix=f"mkgp2_hsd_{prefix}_{round_label}_")
            try:
                scene_json = _run_csx_for_dat(str(dat), out_dir)
            except Exception as ex:
                # Make HSD failures non-fatal: collision/line/auto are
                # still importable without the visual reference.
                print(f"[mkgp2 full] HSD csx failed for {dat.name}: {ex}")
                continue
            print(f"[mkgp2 full] csx OK: {dat.name} -> {scene_json}")
            bundles.append(scene_json)

        for sj in bundles:
            try:
                hsd_imp.import_scene(sj)
            except Exception as ex:
                print(f"[mkgp2 full] hsd import failed for {sj}: {ex}")

        # 2) Collision / Line / Auto, mirroring blender_import_course_all.
        for round_label in ("short", "long"):
            self._import_round_bins(bin_dir_p, prefix, round_label)

    def _import_round_bins(self, bin_dir_p, prefix, round_label):
        targets = (
            (f"{prefix}_{round_label}.bin", col_imp.import_collision),
            (f"{prefix}_{round_label}_line.bin", line_imp.import_line),
            (f"{prefix}_{round_label}_Auto.bin", auto_imp.import_auto),
            (f"{prefix}_{round_label}_Auto_R.bin", auto_imp.import_auto),
        )
        for fname, fn in targets:
            p = bin_dir_p / fname
            if not p.exists():
                print(f"[mkgp2 full] missing {fname}; skipping")
                continue
            try:
                fn(str(p))
            except Exception as ex:
                print(f"[mkgp2 full] error importing {fname}: {ex}")


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
        if _refuse_if_vanilla(self, self.filepath, what="line .bin"):
            return {'CANCELLED'}
        try:
            line_exp.export_line(self.filepath, obj=obj)
        except Exception as ex:
            self.report({'ERROR'}, f"Line export failed: {ex}")
            return {'CANCELLED'}
        return {'FINISHED'}

    def invoke(self, context, event):
        # Preferred filename: <disk_stem>.bin where root.name = <disk_stem>_line.
        if not self.filepath:
            obj = context.active_object
            if obj is not None:
                root = obj if obj.name.endswith("_line") else obj.parent
                if root is not None and root.name.endswith("_line"):
                    disk_stem = root.name[:-len("_line")]
                    _seed_filepath(self, default_filename=f"{disk_stem}.bin",
                                   prefer_output=True)
        _seed_filepath(self, prefer_output=True)
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
        if _refuse_if_vanilla(self, self.filepath, what="auto .bin"):
            return {'CANCELLED'}
        try:
            auto_exp.export_auto(self.filepath, obj=obj)
        except Exception as ex:
            self.report({'ERROR'}, f"Auto export failed: {ex}")
            return {'CANCELLED'}
        return {'FINISHED'}

    def invoke(self, context, event):
        if not self.filepath:
            obj = context.active_object
            if obj is not None and obj.name.startswith("Auto_"):
                disk_stem = obj.name[len("Auto_"):]
                _seed_filepath(self, default_filename=f"{disk_stem}.bin",
                               prefer_output=True)
        _seed_filepath(self, prefer_output=True)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


def _resolve_collision_pair(active_obj):
    """Locate (CollisionMesh, WallSegments) pair from the active object.

    Modern (post-2026-05-07) imports tag both objects with a shared
    `mkgp2_collision_stem` custom property. Older .blends used the fixed
    names CollisionMesh / WallSegments without suffix; we fall back to
    those if the stem property is missing.

    Returns (col_obj, wall_obj_or_None, error_msg_or_None).
    """
    if active_obj is not None:
        stem = active_obj.get("mkgp2_collision_stem")
        if stem:
            col_obj = next(
                (o for o in bpy.data.objects
                 if o.get("mkgp2_collision_stem") == stem
                 and o.name.startswith("CollisionMesh")),
                None,
            )
            wall_obj = next(
                (o for o in bpy.data.objects
                 if o.get("mkgp2_collision_stem") == stem
                 and o.name.startswith("WallSegments")),
                None,
            )
            if col_obj is None:
                return None, None, (
                    f"No CollisionMesh_* object found with stem '{stem}'. "
                    "Re-import the .bin to regenerate the pair."
                )
            return col_obj, wall_obj, None
        # Active object is not a tagged collision pair member: look for
        # legacy fixed names.
    col_obj = bpy.data.objects.get("CollisionMesh")
    wall_obj = bpy.data.objects.get("WallSegments")
    if col_obj is None:
        return None, None, (
            "No collision object selected. Pick a CollisionMesh_* / "
            "WallSegments_* object in the Outliner first."
        )
    return col_obj, wall_obj, None


class MKGP2_OT_ExportCollision(Operator):
    """Export the CollisionMesh / WallSegments pair (resolved from the active object) back to a course .bin"""
    bl_idname = "export_mesh.mkgp2_collision_bin"
    bl_label = "Export MKGP2 Collision (.bin)"
    bl_options = {'PRESET'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.bin", options={'HIDDEN'})

    def execute(self, context):
        if not _need_modules(self):
            return {'CANCELLED'}
        col_obj, wall_obj, err = _resolve_collision_pair(context.active_object)
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        for key in ("grid_width", "grid_height", "cell_size_x", "cell_size_z",
                    "grid_origin_x", "grid_origin_z"):
            if key not in col_obj.keys():
                self.report({'ERROR'},
                    f"{col_obj.name} missing custom property '{key}' "
                    "(re-import with the addon to regenerate)")
                return {'CANCELLED'}
        if _refuse_if_vanilla(self, self.filepath, what="collision .bin"):
            return {'CANCELLED'}
        try:
            triangles = col_exp.collect_triangles(col_obj)
            walls = []
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
                f"Wrote {size} bytes from {col_obj.name} "
                f"({len(triangles)} tris, {len(walls)} walls)")
        except Exception as ex:
            self.report({'ERROR'}, f"Collision export failed: {ex}")
            return {'CANCELLED'}
        return {'FINISHED'}

    def invoke(self, context, event):
        # Default the file name from the active object's stem if available.
        if not self.filepath and context.active_object is not None:
            stem = context.active_object.get("mkgp2_collision_stem")
            if stem:
                _seed_filepath(self, default_filename=f"{stem}.bin",
                               prefer_output=True)
        _seed_filepath(self, prefer_output=True)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class MKGP2_OT_ExportFullCourse(Operator):
    """Bulk export every collision / line / auto set in the scene back to course .bin files.

    Collision pairs are discovered via the `mkgp2_collision_stem` custom
    property; line roots by name suffix "_line"; auto meshes by name
    prefix "Auto_". Output filenames are derived from those stems, so a
    re-export overwrites the originals when bin_dir points at the dump.
    """
    bl_idname = "export_scene.mkgp2_full_course"
    bl_label = "Export MKGP2 Full Course"
    bl_options = {'PRESET'}

    bin_dir: StringProperty(
        name="bin directory",
        description="Folder to write *.bin files into. Existing files will be overwritten.",
        subtype='DIR_PATH',
    )
    overwrite: bpy.props.BoolProperty(
        name="overwrite existing",
        description="Allow overwriting *.bin files that already exist in bin_dir",
        default=True,
    )

    def execute(self, context):
        if not _need_modules(self):
            return {'CANCELLED'}
        if not self.bin_dir:
            self.report({'ERROR'}, "bin directory required")
            return {'CANCELLED'}
        bin_dir = Path(self.bin_dir)
        if not bin_dir.is_dir():
            self.report({'ERROR'}, f"bin directory does not exist: {bin_dir}")
            return {'CANCELLED'}
        if _refuse_if_vanilla(self, str(bin_dir), what="full-course bin dir"):
            return {'CANCELLED'}

        written = []
        errors = []

        def _maybe_skip(out_path):
            if out_path.exists() and not self.overwrite:
                return f"exists (overwrite disabled): {out_path.name}"
            return None

        # ---- Collision pairs (1 per unique mkgp2_collision_stem) ---------
        seen_stems = set()
        for o in bpy.data.objects:
            stem = o.get("mkgp2_collision_stem")
            if not stem or stem in seen_stems:
                continue
            seen_stems.add(stem)
            cm, ws, err = _resolve_collision_pair_by_stem(stem)
            if err:
                errors.append(f"collision {stem}: {err}")
                continue
            try:
                out = bin_dir / f"{stem}.bin"
                if (skip := _maybe_skip(out)):
                    errors.append(f"collision {stem}: {skip}")
                    continue
                tris = col_exp.collect_triangles(cm)
                walls = col_exp.collect_wall_segments(ws) if ws is not None else []
                reserved = bytes.fromhex(cm.get("reserved_hex", "0" * 32))
                size = col_exp.write_collision_bin(
                    str(out), tris, walls,
                    cm["grid_width"], cm["grid_height"],
                    cm["cell_size_x"], cm["cell_size_z"],
                    cm["grid_origin_x"], cm["grid_origin_z"],
                    reserved,
                )
                written.append((out.name, size, f"{len(tris)} tris / {len(walls)} walls"))
            except Exception as ex:
                errors.append(f"collision {stem}: {ex}")

        # ---- Line roots (root empty name = "<disk_stem>_line"; the disk
        # stem itself already ends in "_line", so the importer doubles it
        # to "_line_line". Strip the trailing "_line" so we round-trip
        # back to <disk_stem>.bin instead of <disk_stem>_line.bin).
        for root in [o for o in bpy.data.objects
                     if o.type == 'EMPTY' and o.name.endswith("_line")]:
            try:
                disk_stem = root.name[:-len("_line")]
                out = bin_dir / f"{disk_stem}.bin"
                if (skip := _maybe_skip(out)):
                    errors.append(f"line {disk_stem}: {skip}")
                    continue
                line_exp.export_line(str(out), obj=root)
                size = out.stat().st_size if out.exists() else 0
                written.append((out.name, size, "line"))
            except Exception as ex:
                errors.append(f"line {root.name}: {ex}")

        # ---- Auto meshes (name prefix "Auto_") --------------------------
        for am in [o for o in bpy.data.objects
                   if o.type == 'MESH' and o.name.startswith("Auto_")]:
            try:
                stem = am.name[len("Auto_"):]
                out = bin_dir / f"{stem}.bin"
                if (skip := _maybe_skip(out)):
                    errors.append(f"auto {stem}: {skip}")
                    continue
                auto_exp.export_auto(str(out), obj=am)
                size = out.stat().st_size if out.exists() else 0
                written.append((out.name, size, "auto"))
            except Exception as ex:
                errors.append(f"auto {am.name}: {ex}")

        # ---- Report -----------------------------------------------------
        for w_name, w_size, w_kind in written:
            print(f"  wrote {w_name} ({w_kind}, {w_size} bytes)")
        for e in errors:
            self.report({'WARNING'}, e)
            print(f"  ERR: {e}")
        n_files = len(written)
        if errors:
            self.report({'WARNING'},
                f"Wrote {n_files} files, {len(errors)} errors (see system console)")
        else:
            self.report({'INFO'}, f"Wrote {n_files} files to {bin_dir.name}/")
        return {'FINISHED'}

    def invoke(self, context, event):
        if not self.bin_dir:
            # Export side defaults to the writable output directory.
            self.bin_dir = _output_bin_dir()
        return context.window_manager.invoke_props_dialog(self, width=520)


def _resolve_collision_pair_by_stem(stem):
    """Same idea as _resolve_collision_pair() but driven by an explicit stem."""
    cm = next((o for o in bpy.data.objects
               if o.get("mkgp2_collision_stem") == stem
               and o.name.startswith("CollisionMesh")), None)
    ws = next((o for o in bpy.data.objects
               if o.get("mkgp2_collision_stem") == stem
               and o.name.startswith("WallSegments")), None)
    if cm is None:
        return None, None, f"no CollisionMesh_* with stem '{stem}'"
    return cm, ws, None


# ---------------------------------------------------------------------------
# Custom-course operators (Phase B)
# ---------------------------------------------------------------------------

class MKGP2_OT_NewCourse(Operator):
    """Create an empty MKGP2 course collection.

    Filename custom properties default to `<name>.bin`, `<name>_line.bin`,
    `<name>_Auto.bin`, `<name>_Auto_R.bin`. Edit the resulting collection's
    custom properties to override individual filenames.
    """
    bl_idname = "scene.mkgp2_new_course"
    bl_label = "New MKGP2 Course"
    bl_options = {'UNDO'}

    name: StringProperty(
        name="Course name",
        description="Becomes the collection name and the default filename stem",
        default="my_course",
    )
    bin_dir: StringProperty(
        name="bin directory",
        description="Absolute folder the course .bin files will be written to. "
                    "Empty = fall back to the addon's default bin directory.",
        subtype='DIR_PATH',
        default="",
    )

    def execute(self, context):
        if not self.name:
            self.report({'ERROR'}, "Course name required")
            return {'CANCELLED'}
        if bpy.data.collections.get(self.name) is not None:
            self.report({'ERROR'}, f"Collection named '{self.name}' already exists")
            return {'CANCELLED'}

        parent = _ensure_courses_root()
        coll = bpy.data.collections.new(self.name)
        parent.children.link(coll)

        coll["mkgp2_kind"] = "course"
        coll["mkgp2_course_name"] = self.name
        coll["mkgp2_collision_bin"] = f"{self.name}.bin"
        coll["mkgp2_line_bin"] = f"{self.name}_line.bin"
        coll["mkgp2_auto_f_bin"] = f"{self.name}_Auto.bin"
        coll["mkgp2_auto_r_bin"] = f"{self.name}_Auto_R.bin"
        coll["mkgp2_bin_dir"] = self.bin_dir or ""
        # HSD .dat is optional: leave empty so the user can later attach a
        # bundle via Import Course or set the property by hand.
        coll["mkgp2_hsd_dat"] = ""

        self.report({'INFO'},
            f"Created empty course '{self.name}' under {ROOT_COLL_NAME}/")
        return {'FINISHED'}

    def invoke(self, context, event):
        if not self.bin_dir:
            # New course is authoring-side: target is the writable
            # output directory.
            self.bin_dir = _output_bin_dir()
        return context.window_manager.invoke_props_dialog(self, width=440)


def _on_course_collision_path_change(self, context):
    """Auto-fill sibling filenames when the user picks the collision .bin."""
    if not self.collision_path:
        return
    p = Path(self.collision_path)
    if not p.exists():
        return
    stem = p.stem
    parent_dir = p.parent
    if not self.line_path:
        guess = parent_dir / f"{stem}_line.bin"
        if guess.exists():
            self.line_path = str(guess)
    if not self.auto_f_path:
        guess = parent_dir / f"{stem}_Auto.bin"
        if guess.exists():
            self.auto_f_path = str(guess)
    if not self.auto_r_path:
        guess = parent_dir / f"{stem}_Auto_R.bin"
        if guess.exists():
            self.auto_r_path = str(guess)
    if not self.name:
        self.name = stem
    if not self.hsd_path:
        # Conventional bundle layouts produced by hsd_export_for_blender.csx:
        #   <hsd_export_dir>/scene.json (one bundle per .dat)
        # Plain ".dat" siblings are not directly importable by Blender; pick a
        # scene.json sibling if present so the user doesn't have to hunt.
        for candidate in (parent_dir / f"{stem}_scene.json",
                          parent_dir / f"{stem}.json",
                          parent_dir / "scene.json"):
            if candidate.exists():
                self.hsd_path = str(candidate)
                break


class MKGP2_OT_ImportCourse(Operator):
    """Import a custom course from a 4-file .bin set into one collection.

    Pick the collision .bin first; the other paths are auto-filled by
    convention (<stem>_line.bin / <stem>_Auto.bin / <stem>_Auto_R.bin)
    and can be overridden individually. Course name defaults to the
    collision stem.
    """
    bl_idname = "scene.mkgp2_import_course"
    bl_label = "Import MKGP2 Course (file-set)"
    bl_options = {'UNDO'}

    name: StringProperty(name="Course name", default="")
    collision_path: StringProperty(
        name="Collision .bin",
        subtype='FILE_PATH',
        update=_on_course_collision_path_change,
    )
    line_path: StringProperty(name="Line .bin", subtype='FILE_PATH')
    auto_f_path: StringProperty(name="Auto F .bin", subtype='FILE_PATH')
    auto_r_path: StringProperty(name="Auto R .bin", subtype='FILE_PATH')
    hsd_path: StringProperty(
        name="HSD scene.json",
        description=(
            "Optional HSD bundle (scene.json produced by hsd_export_for_blender.csx). "
            "When given, the bundle's mesh collection is nested inside the course "
            "collection. Leave empty to skip — a custom course can author collision/"
            "line/auto without an HSD reference."
        ),
        subtype='FILE_PATH',
    )
    hsd_dat_filename: StringProperty(
        name="HSD .dat filename",
        description=(
            "Name of the .dat the HSD bundle came from (e.g. test_course_road.dat). "
            "Stored as mkgp2_hsd_dat on the course collection. Leave empty to use "
            "the source_dat field embedded in scene.json."
        ),
        default="",
    )

    def execute(self, context):
        if not _need_modules(self):
            return {'CANCELLED'}
        if not self.collision_path:
            self.report({'ERROR'}, "Collision .bin path is required")
            return {'CANCELLED'}

        # Course name: explicit > collision filename stem
        course_name = (self.name or Path(self.collision_path).stem).strip()
        if not course_name:
            self.report({'ERROR'}, "Course name could not be derived")
            return {'CANCELLED'}
        if bpy.data.collections.get(course_name) is not None:
            self.report({'ERROR'},
                f"Collection '{course_name}' already exists; pick a different name")
            return {'CANCELLED'}

        # Where to write the eventual export. If the collision was
        # picked from inside the vanilla bin dir, refuse to remember
        # that as `mkgp2_bin_dir` -- empty falls back to the writable
        # `Output bin directory` preference at export time, which keeps
        # the vanilla dump intact.
        source_dir = str(Path(self.collision_path).parent)
        if _is_inside_vanilla(source_dir):
            bin_dir = ""
        else:
            bin_dir = source_dir

        parent = _ensure_courses_root()
        coll = bpy.data.collections.new(course_name)
        parent.children.link(coll)
        coll["mkgp2_kind"] = "course"
        coll["mkgp2_course_name"] = course_name
        coll["mkgp2_collision_bin"] = Path(self.collision_path).name
        coll["mkgp2_line_bin"] = Path(self.line_path).name if self.line_path else ""
        coll["mkgp2_auto_f_bin"] = Path(self.auto_f_path).name if self.auto_f_path else ""
        coll["mkgp2_auto_r_bin"] = Path(self.auto_r_path).name if self.auto_r_path else ""
        coll["mkgp2_bin_dir"] = bin_dir
        coll["mkgp2_hsd_dat"] = ""

        # Helper to capture newly created objects after each importer call
        # and move them into the course collection.
        def _capture_new(call):
            before = set(bpy.data.objects)
            call()
            return [o for o in bpy.data.objects if o not in before]

        def _capture_new_collections(call):
            before = set(bpy.data.collections)
            call()
            return [c for c in bpy.data.collections if c not in before]

        try:
            new_objs = _capture_new(lambda: col_imp.import_collision(self.collision_path))
            _link_objs_to_collection(coll, new_objs)
            if self.line_path:
                new_objs = _capture_new(lambda: line_imp.import_line(self.line_path))
                _link_objs_to_collection(coll, new_objs)
            if self.auto_f_path:
                new_objs = _capture_new(lambda: auto_imp.import_auto(self.auto_f_path))
                for o in new_objs:
                    o["mkgp2_auto_role"] = "F"
                _link_objs_to_collection(coll, new_objs)
            if self.auto_r_path:
                new_objs = _capture_new(lambda: auto_imp.import_auto(self.auto_r_path))
                for o in new_objs:
                    o["mkgp2_auto_role"] = "R"
                _link_objs_to_collection(coll, new_objs)
            if self.hsd_path:
                new_colls = _capture_new_collections(
                    lambda: hsd_imp.import_scene(self.hsd_path))
                _link_collections_to_collection(coll, new_colls)
                # Capture .dat name: explicit override > nested collection's
                # mkgp2_source_dat custom prop (set by the HSD importer).
                dat_name = self.hsd_dat_filename.strip()
                if not dat_name:
                    for cc in new_colls:
                        src = cc.get("mkgp2_source_dat")
                        if src:
                            dat_name = str(src)
                            break
                coll["mkgp2_hsd_dat"] = dat_name
        except Exception as ex:
            self.report({'ERROR'}, f"Course import failed: {ex}")
            return {'CANCELLED'}

        n = len(coll.all_objects)
        self.report({'INFO'},
            f"Imported course '{course_name}' ({n} objects under {ROOT_COLL_NAME}/)")
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=600)


class MKGP2_OT_ExportCourse(Operator):
    """Write the active custom course back to the .bin files described
    by its custom properties (collision/line/auto-F/auto-R).

    The course collection is resolved from the active layer collection
    or, failing that, by walking up from the active object's collections.

    Invoked from a button (the normal UI path), this pops a small dialog
    so the user can confirm or change the destination directory before
    anything is written. Scripted callers may pass `bin_dir` explicitly
    (or via `EXEC_DEFAULT`) to suppress the dialog and use the
    collection's saved `mkgp2_bin_dir` / output preference fallback.
    """
    bl_idname = "scene.mkgp2_export_course"
    bl_label = "Export MKGP2 Course"
    bl_options = {'PRESET'}

    bin_dir: StringProperty(
        name="Destination",
        description="Folder to write the course .bin files into. Pre-filled "
                    "from the collection's saved mkgp2_bin_dir or the addon's "
                    "output preference. Vanilla paths are rejected.",
        subtype='DIR_PATH',
    )

    def invoke(self, context, event):
        coll = _resolve_course_collection(context)
        if coll is None:
            self.report({'ERROR'},
                "No course collection in context. Activate a course collection "
                "in the Outliner or pick an object that lives inside one.")
            return {'CANCELLED'}
        # Pre-fill: collection's saved choice (if not vanilla), else output pref.
        if not self.bin_dir:
            saved = coll.get("mkgp2_bin_dir") or ""
            if saved and not _is_inside_vanilla(saved):
                self.bin_dir = saved
            else:
                self.bin_dir = _output_bin_dir()
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, context):
        layout = self.layout
        coll = _resolve_course_collection(context)
        if coll is not None:
            layout.label(text=f"Course: {coll.name}", icon='OUTLINER_COLLECTION')
        layout.prop(self, "bin_dir")
        if self.bin_dir and _is_inside_vanilla(self.bin_dir):
            layout.label(text="⚠ vanilla path - will be refused on Export",
                         icon='ERROR')

    def execute(self, context):
        if not _need_modules(self):
            return {'CANCELLED'}
        coll = _resolve_course_collection(context)
        if coll is None:
            self.report({'ERROR'},
                "No course collection in context. Activate a course collection "
                "in the Outliner or pick an object that lives inside one.")
            return {'CANCELLED'}

        # Priority: dialog/operator override > saved on collection > preference.
        bin_dir_str = (self.bin_dir
                       or coll.get("mkgp2_bin_dir")
                       or _output_bin_dir())
        if not bin_dir_str:
            self.report({'ERROR'},
                f"Course '{coll.name}' has no bin directory set. "
                "Pick a folder in the dialog, edit the collection's "
                "mkgp2_bin_dir custom property, or set 'Output bin "
                "directory' in addon preferences.")
            return {'CANCELLED'}
        bin_dir = Path(bin_dir_str)
        if not bin_dir.is_dir():
            self.report({'ERROR'}, f"bin directory does not exist: {bin_dir}")
            return {'CANCELLED'}
        if _refuse_if_vanilla(self, str(bin_dir),
                              what=f"course '{coll.name}' export"):
            return {'CANCELLED'}
        # Persist the user's pick on the collection so the next Export
        # defaults to the same place without re-prompting (matches the
        # mental model of "this course lives over there").
        coll["mkgp2_bin_dir"] = str(bin_dir)

        # Freeze the course root (if any) for the duration of the
        # export so user-applied root transforms don't bake into .bin.
        course_root = _resolve_course_root(coll)
        if course_root is not None:
            ident_eps = 1e-5
            world = course_root.matrix_world
            is_ident = all(
                abs(world[r][c] - (1.0 if r == c else 0.0)) < ident_eps
                for r in range(4) for c in range(4)
            )
            if not is_ident:
                self.report({'INFO'},
                    f"Course root '{course_root.name}' has a non-identity "
                    "transform; freezing it for export so output is "
                    "world-relative.")

        with _FreezeRoot(course_root):
            written, errors = self._do_export(coll, bin_dir)

        for w_name, w_size in written:
            print(f"  wrote {w_name} ({w_size} bytes)")
        for e in errors:
            self.report({'WARNING'}, e)
            print(f"  ERR: {e}")
        if errors:
            self.report({'WARNING'},
                f"Course '{coll.name}': wrote {len(written)} files, {len(errors)} errors")
        else:
            self.report({'INFO'},
                f"Course '{coll.name}': wrote {len(written)} files to {bin_dir.name}/")
        return {'FINISHED'}

    def _do_export(self, coll, bin_dir):
        """Per-asset write phase. Runs inside _FreezeRoot so that
        matrix_world reflects the un-rooted layout."""
        written = []
        errors = []

        # Collision pair
        cf = coll.get("mkgp2_collision_bin")
        if cf:
            col_obj = next((o for o in coll.all_objects
                            if o.name.startswith("CollisionMesh")), None)
            wall_obj = next((o for o in coll.all_objects
                             if o.name.startswith("WallSegments")), None)
            if col_obj is None:
                errors.append("collision: no CollisionMesh in course")
            else:
                try:
                    out = bin_dir / cf
                    triangles = col_exp.collect_triangles(col_obj)
                    walls = col_exp.collect_wall_segments(wall_obj) if wall_obj else []
                    reserved = bytes.fromhex(col_obj.get("reserved_hex", "0" * 32))
                    size = col_exp.write_collision_bin(
                        str(out), triangles, walls,
                        col_obj["grid_width"], col_obj["grid_height"],
                        col_obj["cell_size_x"], col_obj["cell_size_z"],
                        col_obj["grid_origin_x"], col_obj["grid_origin_z"],
                        reserved,
                    )
                    written.append((cf, size))
                except Exception as ex:
                    errors.append(f"collision: {ex}")

        # Line
        lf = coll.get("mkgp2_line_bin")
        if lf:
            root = next((o for o in coll.all_objects
                         if o.type == 'EMPTY' and o.name.endswith("_line")), None)
            if root is None:
                errors.append("line: no <stem>_line empty in course")
            else:
                try:
                    out = bin_dir / lf
                    line_exp.export_line(str(out), obj=root)
                    written.append((lf, out.stat().st_size if out.exists() else 0))
                except Exception as ex:
                    errors.append(f"line: {ex}")

        # Auto F / R (matched by mkgp2_auto_role custom prop)
        for prop_name, role_label in (("mkgp2_auto_f_bin", "F"),
                                       ("mkgp2_auto_r_bin", "R")):
            af = coll.get(prop_name)
            if not af:
                continue
            obj = next((o for o in coll.all_objects
                        if o.type == 'MESH'
                        and o.get("mkgp2_auto_role") == role_label),
                       None)
            if obj is None:
                # Fallback: best guess by name suffix.
                if role_label == "R":
                    obj = next((o for o in coll.all_objects
                                if o.type == 'MESH' and o.name.endswith("_R")),
                               None)
                else:
                    obj = next((o for o in coll.all_objects
                                if o.type == 'MESH' and o.name.startswith("Auto_")
                                and not o.name.endswith("_R")), None)
            if obj is None:
                errors.append(f"auto-{role_label}: no matching mesh in course")
                continue
            try:
                out = bin_dir / af
                auto_exp.export_auto(str(out), obj=obj)
                written.append((af, out.stat().st_size if out.exists() else 0))
            except Exception as ex:
                errors.append(f"auto-{role_label}: {ex}")

        return written, errors


def _find_unhosted_hsd_for(canonical):
    """Pick a `mkgp2:<dat>` bundle whose .dat name contains `canonical`
    (case-insensitive) and that does NOT already live inside a
    course-tagged collection. Returns None if no match.

    Used by Promote (auto-discover at promote time) and by
    AttachHsdToCourse (after-the-fact wiring of a bundle imported
    later).
    """
    canon_lower = canonical.lower()
    for coll in bpy.data.collections:
        if not coll.name.startswith("mkgp2:"):
            continue
        host = _find_parent_collection(coll)
        if host is not None and host.get("mkgp2_kind") == "course":
            continue
        dat_lower = coll.name[len("mkgp2:"):].lower()
        if canon_lower in dat_lower:
            return coll
    return None


class MKGP2_OT_AttachHsdToCourse(Operator):
    """Nest a scene-root HSD bundle (`mkgp2:<dat>`) under the active
    course collection.

    Matching: the bundle's .dat name is checked against the course
    canonical name (collection name OR `mkgp2_course_name` prop)
    case-insensitive substring. A long course gets the bundle whose
    .dat name contains 'long', short gets 'short'.

    Cancels if the course already has a nested HSD bundle (use a fresh
    course or strip it manually first), or if no eligible bundle
    matches.
    """
    bl_idname = "scene.mkgp2_attach_hsd"
    bl_label = "Attach HSD bundle to course"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        coll = _resolve_course_collection(context)
        if coll is None:
            self.report({'ERROR'},
                "No course collection in context. Activate a course "
                "collection or one of its members first.")
            return {'CANCELLED'}

        # Refuse if a bundle is already nested -- silently overwriting
        # the link drops orphan meshes into bpy.data.collections.
        existing = next((c for c in coll.children
                         if c.name.startswith("mkgp2:")), None)
        if existing is not None:
            self.report({'WARNING'},
                f"'{coll.name}' already has '{existing.name}' nested. "
                "Detach it manually first if you really want to swap.")
            return {'CANCELLED'}

        canonical = str(coll.get("mkgp2_course_name") or coll.name)
        hsd = _find_unhosted_hsd_for(canonical)
        if hsd is None:
            # Help the user diagnose: list the candidates we saw.
            roots = [c.name for c in bpy.data.collections
                     if c.name.startswith("mkgp2:")
                     and not (
                         _find_parent_collection(c) is not None
                         and _find_parent_collection(c).get("mkgp2_kind") == "course"
                     )]
            hint = (f"Available unhosted bundles: {roots}. "
                    "Generate the matching .dat scene.json via "
                    "tools/hsd/hsd_export_for_blender.csx and re-import."
                    if roots else
                    "No unhosted mkgp2:<dat> collection in the scene.")
            self.report({'ERROR'},
                f"No HSD bundle name contains '{canonical}'. {hint}")
            return {'CANCELLED'}

        _link_collections_to_collection(coll, [hsd])
        coll["mkgp2_hsd_dat"] = str(hsd.get("mkgp2_source_dat", ""))
        self.report({'INFO'},
            f"Nested {hsd.name} under '{coll.name}' "
            f"(mkgp2_hsd_dat='{coll['mkgp2_hsd_dat']}')")
        return {'FINISHED'}


class MKGP2_OT_PromoteVanilla(Operator):
    """Wrap each course currently sitting at the scene root (after a
    Vanilla Full Course import) into a tagged `mkgp2_kind=course`
    collection under MKGP2_Course/ so Validate / Export / Add coordinate
    root act on it.

    Discovery:
      - Each `CollisionMesh_<canonical>` defines one course.
      - Sibling members are picked up by name convention:
          WallSegments_<canonical>
          <canonical>_line_line       (empty -- importer doubles "_line")
          LineVariant_*_<canonical>_line
          Auto_<canonical>_Auto       (tagged mkgp2_auto_role="F")
          Auto_<canonical>_Auto_R     (tagged mkgp2_auto_role="R")
      - HSD bundles `mkgp2:<dat>` whose .dat name contains the
        canonical stem (case-insensitive) are nested into the course
        collection. A vanilla course typically has the short bundle
        only; long is left without an HSD nest.

    Re-running on an already-promoted scene is safe: existing
    `mkgp2_kind=course` collections and HSD bundles already nested in
    one are skipped.
    """
    bl_idname = "scene.mkgp2_promote_vanilla"
    bl_label = "Promote vanilla import to course(s)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        # Discover canonical stems from CollisionMesh_* objects that
        # do *not* already live inside a course collection.
        candidates = {}  # stem -> CollisionMesh object
        for o in bpy.data.objects:
            if not o.name.startswith("CollisionMesh_"):
                continue
            stem = o.name[len("CollisionMesh_"):]
            # Strip Blender's ".001" disambiguation suffix
            stem = stem.split(".", 1)[0]
            if any(c.get("mkgp2_kind") == "course" for c in o.users_collection):
                continue
            candidates.setdefault(stem, o)

        if not candidates:
            self.report({'WARNING'},
                "No vanilla course found at the scene root. "
                "Either nothing was imported yet or every course is "
                "already promoted.")
            return {'CANCELLED'}

        # Promote-time bin_dir = writable output. If the user hasn't
        # configured one yet we leave it blank; export then complains
        # explicitly instead of silently overwriting the vanilla dump.
        bin_dir = _output_bin_dir()
        parent = _ensure_courses_root()
        promoted = []

        for canonical in sorted(candidates):
            existing = bpy.data.collections.get(canonical)
            if existing is not None:
                self.report({'WARNING'},
                    f"Collection '{canonical}' already exists; skipping. "
                    "Rename it first if you want a fresh promote.")
                continue

            members = self._gather_members(canonical)
            if not members:
                continue

            coll = bpy.data.collections.new(canonical)
            parent.children.link(coll)
            coll["mkgp2_kind"] = "course"
            coll["mkgp2_course_name"] = canonical
            coll["mkgp2_collision_bin"] = f"{canonical}.bin"
            coll["mkgp2_line_bin"] = f"{canonical}_line.bin"
            coll["mkgp2_auto_f_bin"] = f"{canonical}_Auto.bin"
            coll["mkgp2_auto_r_bin"] = f"{canonical}_Auto_R.bin"
            coll["mkgp2_bin_dir"] = bin_dir or ""
            coll["mkgp2_hsd_dat"] = ""

            _link_objs_to_collection(coll, members)

            # Auto F/R role tagging (the per-asset Auto importer doesn't
            # set this; the Custom course flow does).
            for o in members:
                if o.name == f"Auto_{canonical}_Auto":
                    o["mkgp2_auto_role"] = "F"
                elif o.name == f"Auto_{canonical}_Auto_R":
                    o["mkgp2_auto_role"] = "R"

            # HSD bundle: the closest mkgp2:<dat> whose .dat name
            # contains the canonical stem and that isn't already
            # nested in a different course.
            hsd = self._find_matching_hsd(canonical)
            if hsd is not None:
                _link_collections_to_collection(coll, [hsd])
                coll["mkgp2_hsd_dat"] = str(hsd.get("mkgp2_source_dat", ""))

            promoted.append(canonical)

        if not promoted:
            self.report({'WARNING'},
                "Found candidate stems but nothing got promoted "
                "(name conflict on every one). Resolve and retry.")
            return {'CANCELLED'}

        self.report({'INFO'},
            f"Promoted {len(promoted)} course(s): {', '.join(promoted)}")
        return {'FINISHED'}

    def _gather_members(self, canonical):
        """Pick every scene-root object whose name fits the vanilla
        layout for `canonical`."""
        prefix_line_var = f"LineVariant_"
        suffix_line_var = f"_{canonical}_line"
        members = []
        for o in bpy.data.objects:
            # Skip objects already in a course collection (re-run safety)
            if any(c.get("mkgp2_kind") == "course" for c in o.users_collection):
                continue
            n = o.name
            # Strip Blender ".001" suffix for the name comparisons that
            # follow; we want all variants of the canonical stem.
            base = n.split(".", 1)[0]
            if base in (
                f"CollisionMesh_{canonical}",
                f"WallSegments_{canonical}",
                f"{canonical}_line_line",
                f"Auto_{canonical}_Auto",
                f"Auto_{canonical}_Auto_R",
            ):
                members.append(o)
                continue
            if base.startswith(prefix_line_var) and base.endswith(suffix_line_var):
                members.append(o)
        return members

    def _find_matching_hsd(self, canonical):
        return _find_unhosted_hsd_for(canonical)


class MKGP2_OT_ValidateCourse(Operator):
    """Run integrity checks against the active course collection.

    Validates collision (grid AABB / degenerate triangles / wall plane),
    line round-trip, auto round-trip, and member naming. Issues are
    emitted as Blender warnings and printed to the system console for
    triage. A clean run reports INFO 'OK'.
    """
    bl_idname = "scene.mkgp2_validate_course"
    bl_label = "Validate MKGP2 Course"
    bl_options = {'REGISTER'}

    def execute(self, context):
        if not _need_modules(self):
            return {'CANCELLED'}
        coll = _resolve_course_collection(context)
        if coll is None:
            self.report({'ERROR'},
                "No course collection in context. Activate a course "
                "collection in the Outliner or pick an object inside one.")
            return {'CANCELLED'}

        try:
            issues = validate.validate_course(
                coll,
                line_imp=line_imp, line_exp=line_exp,
                auto_imp=auto_imp, auto_exp=auto_exp,
            )
        except Exception as ex:
            self.report({'ERROR'}, f"Validate failed: {ex}")
            return {'CANCELLED'}

        if not issues:
            self.report({'INFO'},
                f"Course '{coll.name}': OK ({len(coll.all_objects)} objects)")
            print(f"[mkgp2 validate] {coll.name}: OK")
            return {'FINISHED'}

        self.report({'WARNING'},
            f"Course '{coll.name}': {len(issues)} issue(s) -- see system console")
        print(f"[mkgp2 validate] {coll.name}: {len(issues)} issue(s)")
        for s in issues:
            print(f"  - {s}")
            self.report({'WARNING'}, s)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Visualization operators (T1b)
# ---------------------------------------------------------------------------
#
# 5 small QoL features sharing infrastructure:
#   T1b-1  show only one line variant / show all
#   T1b-2  auto path direction arrows  (3D Viewport draw handler)
#   T1b-3  waypoint index overlay       (3D Viewport draw handler)
#   T1b-4  flatten collision material vertex color into face color
#          (already populated as MaterialID by the importer; the operator
#          just nudges the viewport into Solid + Attribute mode)
#   T1b-5  course origin marker (empty at game-(0,0,0) under the active
#          course collection)
#
# Draw-handler state lives on WindowManager so it survives panel
# refreshes but does not poison .blend files (handlers are torn down on
# unregister regardless).


def _line_variants_under(root_obj):
    """Return [(variant_index, mesh_obj), ...] sorted by index."""
    out = []
    if root_obj is None:
        return out
    for c in root_obj.children:
        if c.type == 'MESH' and c.name.startswith("LineVariant_"):
            try:
                idx = int(c.name.split("_")[1])
            except (ValueError, IndexError):
                idx = 0
            out.append((idx, c))
    out.sort(key=lambda p: p[0])
    return out


def _resolve_line_root(context):
    """Find a `<stem>_line` empty from the active object or course collection."""
    obj = context.active_object
    if obj is not None:
        # Active is the root itself
        if obj.type == 'EMPTY' and obj.name.endswith("_line"):
            return obj
        # Active is a variant -> walk up
        if obj.parent is not None and obj.parent.type == 'EMPTY' \
                and obj.parent.name.endswith("_line"):
            return obj.parent
    coll = _resolve_course_collection(context)
    if coll is not None:
        for o in coll.all_objects:
            if o.type == 'EMPTY' and o.name.endswith("_line"):
                return o
    return None


class MKGP2_OT_ShowOnlyVariant(Operator):
    """Hide every line variant except the chosen one"""
    bl_idname = "mkgp2.show_only_variant"
    bl_label = "Show only this variant"
    bl_options = {'REGISTER', 'UNDO'}

    variant_index: IntProperty(name="Variant", default=0, min=0)

    def execute(self, context):
        root = _resolve_line_root(context)
        if root is None:
            self.report({'ERROR'},
                "No line root in context. Pick a <stem>_line empty or a "
                "LineVariant_* mesh first.")
            return {'CANCELLED'}
        any_shown = False
        for idx, m in _line_variants_under(root):
            m.hide_viewport = (idx != self.variant_index)
            any_shown = any_shown or (idx == self.variant_index)
        if not any_shown:
            self.report({'WARNING'},
                f"No LineVariant_{self.variant_index}_* under '{root.name}'")
        return {'FINISHED'}


class MKGP2_OT_ShowAllVariants(Operator):
    """Show every line variant under the active line root"""
    bl_idname = "mkgp2.show_all_variants"
    bl_label = "Show all variants"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        root = _resolve_line_root(context)
        if root is None:
            self.report({'ERROR'}, "No line root in context")
            return {'CANCELLED'}
        for _, m in _line_variants_under(root):
            m.hide_viewport = False
        return {'FINISHED'}


class MKGP2_OT_HideAllVariants(Operator):
    """Hide every line variant under the active line root"""
    bl_idname = "mkgp2.hide_all_variants"
    bl_label = "Hide all variants"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        root = _resolve_line_root(context)
        if root is None:
            self.report({'ERROR'}, "No line root in context")
            return {'CANCELLED'}
        for _, m in _line_variants_under(root):
            m.hide_viewport = True
        return {'FINISHED'}


# ---- Draw handlers --------------------------------------------------------
# A pair of toggles on WindowManager controls overlays:
#   wm.mkgp2_show_arrows        — auto-path direction arrows
#   wm.mkgp2_show_waypoint_ids  — line/auto waypoint index labels
#
# Handlers walk every visible Auto_* / LineVariant_* / <stem>_line member,
# so they cover both vanilla Full Course imports and custom courses.

_draw_handles = {"arrows": None, "waypoints": None}


def _iter_arrow_targets():
    """Yield (object, world_matrix) pairs whose edges should grow arrows."""
    for o in bpy.context.scene.objects:
        if o.hide_get() or o.type != 'MESH':
            continue
        if o.name.startswith("Auto_") or o.name.startswith("LineVariant_"):
            yield o


def _draw_arrows_callback():
    import gpu
    from gpu_extras.batch import batch_for_shader
    from mathutils import Vector

    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    shader.bind()

    head_size = 0.7  # blender units; tune via WM if it becomes annoying
    coords = []
    for o in _iter_arrow_targets():
        mw = o.matrix_world
        # Walk along edges in vertex order (most importer-built meshes
        # store edges as a chain v0->v1->v2->...).
        verts = [mw @ v.co for v in o.data.vertices]
        for i in range(len(verts) - 1):
            a = verts[i]
            b = verts[i + 1]
            d = b - a
            seg_len = d.length
            if seg_len < 1e-4:
                continue
            d.normalize()
            # Pick a perpendicular for the arrow head (project away from Z
            # so head sits in the horizontal plane like the path).
            perp = Vector((-d.y, d.x, 0.0))
            if perp.length < 1e-4:
                perp = Vector((1.0, 0.0, 0.0))
            perp.normalize()
            tip = b
            base = b - d * head_size
            l = base + perp * (head_size * 0.4)
            r = base - perp * (head_size * 0.4)
            coords.extend([tip, l])
            coords.extend([tip, r])
    if not coords:
        return

    if bpy.app.version >= (4, 0, 0):
        gpu.state.line_width_set(1.5)
    batch = batch_for_shader(shader, 'LINES', {"pos": coords})
    shader.uniform_float("color", (1.0, 0.85, 0.1, 1.0))
    batch.draw(shader)


def _draw_waypoint_ids_callback():
    import blf
    from bpy_extras.view3d_utils import location_3d_to_region_2d

    region = bpy.context.region
    rv3d = bpy.context.region_data
    if region is None or rv3d is None:
        return

    font_id = 0
    blf.size(font_id, 11)
    blf.color(font_id, 1.0, 1.0, 1.0, 1.0)

    for o in _iter_arrow_targets():
        mw = o.matrix_world
        for v in o.data.vertices:
            world = mw @ v.co
            screen = location_3d_to_region_2d(region, rv3d, world)
            if screen is None:
                continue
            blf.position(font_id, screen.x + 4, screen.y + 4, 0)
            blf.draw(font_id, str(v.index))


def _refresh_3d_views():
    for w in bpy.context.window_manager.windows:
        for area in w.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def _set_overlay(kind, enable, callback):
    """Idempotent toggle for a 3D Viewport draw handler."""
    handle = _draw_handles.get(kind)
    if enable:
        if handle is None:
            _draw_handles[kind] = bpy.types.SpaceView3D.draw_handler_add(
                callback, (), 'WINDOW', 'POST_VIEW' if kind == 'arrows' else 'POST_PIXEL')
    else:
        if handle is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
            except Exception:
                pass
            _draw_handles[kind] = None
    _refresh_3d_views()


def _on_show_arrows_toggle(self, context):
    _set_overlay('arrows', bool(self.mkgp2_show_arrows),
                 _draw_arrows_callback)


def _on_show_waypoint_ids_toggle(self, context):
    _set_overlay('waypoints', bool(self.mkgp2_show_waypoint_ids),
                 _draw_waypoint_ids_callback)


# ---- Material color viewport setup ----------------------------------------

class MKGP2_OT_ShowCollisionMaterial(Operator):
    """Switch the active 3D Viewport into Solid + Attribute color mode
    so collision triangles render with their MaterialID color (set by
    the importer)."""
    bl_idname = "mkgp2.show_collision_material"
    bl_label = "Show collision material color"

    def execute(self, context):
        view = context.space_data
        if view is None or view.type != 'VIEW_3D':
            for area in context.screen.areas:
                if area.type == 'VIEW_3D':
                    view = area.spaces.active
                    break
        if view is None or view.type != 'VIEW_3D':
            self.report({'ERROR'}, "No 3D Viewport found in current screen")
            return {'CANCELLED'}
        view.shading.type = 'SOLID'
        view.shading.color_type = 'VERTEX'
        # Make sure the active color attribute on every CollisionMesh
        # member resolves to "MaterialID" so the color cycler picks it.
        for o in bpy.data.objects:
            if o.type == 'MESH' and o.name.startswith("CollisionMesh"):
                ca = o.data.color_attributes
                ml = ca.get("MaterialID")
                if ml is not None:
                    ca.active_color_index = list(ca).index(ml)
        self.report({'INFO'}, "Viewport shading -> Solid + vertex color (MaterialID)")
        return {'FINISHED'}


# ---- Coordinate root system (T2c) -----------------------------------------
#
# A course collection optionally contains a single "course root" empty
# (named `<course>_root`, tagged `mkgp2_course_root=True`) that every
# top-level course member is parented to with `matrix_parent_inverse =
# identity`. Effects:
#
#   * The user can drag the root in the viewport to translate / rotate
#     / scale the entire course as one rigid body, then experiment
#     freely.
#   * On export, the root is temporarily *frozen* to identity. Each
#     child's `matrix_world` collapses to its `matrix_local` (= the
#     original world matrix at parent-time) so the .bin output is
#     unchanged regardless of where the user dragged the root.
#
# Parenting is done with parent_inverse=identity (NOT keep_transform).
# That way "freeze to identity" is the simple expression
# `child.matrix_world = child.matrix_local`, matching the vanilla case.


COURSE_ROOT_PROP = "mkgp2_course_root"


def _resolve_course_root(course_coll):
    """Return the course-root empty for `course_coll`, or None."""
    if course_coll is None:
        return None
    for o in course_coll.objects:
        if o.type == 'EMPTY' and o.get(COURSE_ROOT_PROP):
            return o
    return None


class _FreezeRoot:
    """Context manager that snaps a course-root empty to identity for
    the lifetime of the block, restoring its prior world matrix on exit.

    Children parented with `matrix_parent_inverse = identity` see their
    `matrix_world` collapse back to their `matrix_local`, which was
    captured at parent-time as the *pre-root* world matrix. This makes
    user offsets to the root invisible to the export pipeline.
    """

    def __init__(self, root_obj):
        self.root = root_obj
        self.saved = None

    def __enter__(self):
        if self.root is not None:
            self.saved = self.root.matrix_world.copy()
            self.root.matrix_world = mathutils.Matrix.Identity(4)
            # Force Blender to flush parent->child world-matrix
            # propagation now; export readers consult `matrix_world`
            # directly and would otherwise see stale cached values.
            try:
                bpy.context.view_layer.update()
            except Exception:
                pass
        return self

    def __exit__(self, *exc):
        if self.root is not None and self.saved is not None:
            self.root.matrix_world = self.saved
            try:
                bpy.context.view_layer.update()
            except Exception:
                pass
        return False


class MKGP2_OT_AddCourseRoot(Operator):
    """Wrap the active course collection's top-level members in a single
    course-root empty so the entire course can be moved as one piece."""
    bl_idname = "mkgp2.add_course_root"
    bl_label = "Add course coordinate root"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        coll = _resolve_course_collection(context)
        if coll is None:
            self.report({'ERROR'},
                "No course collection in context. Activate a course "
                "collection or one of its members first.")
            return {'CANCELLED'}

        existing = _resolve_course_root(coll)
        if existing is not None:
            self.report({'INFO'}, f"Course '{coll.name}' already has a "
                                   f"root: {existing.name}")
            return {'CANCELLED'}

        empty = bpy.data.objects.new(f"{coll.name}_root", None)
        empty.empty_display_type = 'SPHERE'
        empty.empty_display_size = 80.0
        empty.show_name = True
        empty[COURSE_ROOT_PROP] = True
        coll.objects.link(empty)

        # Parent every existing member with parent_inverse=identity.
        # `obj.matrix_local` (= world before parenting because root is
        # at origin) is preserved as the rest position; freeze-to-
        # identity later collapses matrix_world back to matrix_local.
        for obj in list(coll.objects):
            if obj is empty:
                continue
            # Skip objects that already have a parent inside the course
            # (e.g. LineVariant_* under <stem>_line) to keep grand-child
            # chains intact. Re-parenting through the chain happens
            # automatically because their parent will get parented to
            # the empty.
            if obj.parent is not None and obj.parent in coll.objects.values():
                continue
            obj.parent = empty
            obj.matrix_parent_inverse = mathutils.Matrix.Identity(4)

        n = sum(1 for o in coll.objects if o.parent is empty)
        self.report({'INFO'},
            f"Created {empty.name} ({n} top-level children parented)")
        return {'FINISHED'}


# ---- Course origin marker -------------------------------------------------

ORIGIN_MARKER_NAME = "MKGP2_OriginMarker"


class MKGP2_OT_AddOriginMarker(Operator):
    """Create (or move) an axis-cross empty at game (0,0,0) under the
    active course collection. Helpful for orienting custom courses
    relative to MKGP2's world origin."""
    bl_idname = "mkgp2.add_origin_marker"
    bl_label = "Add course origin marker"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        coll = _resolve_course_collection(context)
        if coll is None:
            # No course active -- still create one in the scene root so
            # the user can see where world origin is.
            coll = context.scene.collection
        existing = bpy.data.objects.get(ORIGIN_MARKER_NAME)
        if existing is not None:
            # Re-link if it lost its parent collection.
            if coll not in [c for c in existing.users_collection]:
                for c in list(existing.users_collection):
                    c.objects.unlink(existing)
                coll.objects.link(existing)
            existing.location = (0.0, 0.0, 0.0)
            self.report({'INFO'}, f"Reused {ORIGIN_MARKER_NAME}")
            return {'FINISHED'}

        empty = bpy.data.objects.new(ORIGIN_MARKER_NAME, None)
        empty.empty_display_type = 'PLAIN_AXES'
        empty.empty_display_size = 100.0
        empty.location = (0.0, 0.0, 0.0)
        empty.show_in_front = True
        empty.show_name = True
        coll.objects.link(empty)
        self.report({'INFO'}, f"Created {ORIGIN_MARKER_NAME} in '{coll.name}'")
        return {'FINISHED'}


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
# Active object → export target detection (powers the panel hint)
# ---------------------------------------------------------------------------

def _detect_export_target(obj):
    """Inspect an object (or current context) and return (hint_text,
    operator_id_or_None, icon).

    A custom-course collection takes precedence: if the active layer
    collection or the active object's collection chain is tagged
    `mkgp2_kind == "course"`, the suggested operator is the
    course-level export. Otherwise fall back to per-asset detection
    based on naming + custom props.
    """
    # 1) Active course collection wins, even with no active object.
    layer_coll = getattr(bpy.context.view_layer, "active_layer_collection", None)
    if layer_coll is not None:
        c = layer_coll.collection
        if c.get("mkgp2_kind") == "course":
            return (f"course: {c.name}",
                    "scene.mkgp2_export_course",
                    'OUTLINER_COLLECTION')

    # 2) Active object's chain belongs to a course collection.
    if obj is not None:
        for c in obj.users_collection:
            cur = c
            visited = set()
            while cur is not None and cur.name not in visited:
                if cur.get("mkgp2_kind") == "course":
                    return (f"course: {cur.name} (via {obj.name})",
                            "scene.mkgp2_export_course",
                            'OUTLINER_COLLECTION')
                visited.add(cur.name)
                cur = _find_parent_collection(cur)

    # 3) Per-asset detection (vanilla flow).
    if obj is None:
        return "(no active object)", None, 'INFO'
    stem = obj.get("mkgp2_collision_stem")
    if stem:
        return f"collision: {stem}", "export_mesh.mkgp2_collision_bin", 'MOD_PHYSICS'
    if obj.type == 'EMPTY' and obj.name.endswith("_line"):
        return f"line root: {obj.name}", "export_scene.mkgp2_line_bin", 'TRACKING'
    if obj.type == 'MESH' and obj.name.startswith("LineVariant_"):
        return f"line variant: {obj.name}", "export_scene.mkgp2_line_bin", 'TRACKING'
    if obj.type == 'MESH' and obj.name.startswith("Auto_"):
        return f"auto path: {obj.name}", "export_scene.mkgp2_auto_bin", 'CURVE_PATH'
    return f"{obj.name} (not a known MKGP2 target)", None, 'QUESTION'


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

        # ---- Active target hint (top of panel for visibility) ----------
        obj = context.active_object
        hint, op_id, icon = _detect_export_target(obj)
        box = layout.box()
        box.label(text="Active target:", icon='RESTRICT_SELECT_OFF')
        box.label(text=hint, icon=icon)
        row = box.row()
        row.enabled = op_id is not None
        if op_id:
            row.operator(op_id, text="Export this", icon='EXPORT')
        else:
            row.operator("export_mesh.mkgp2_collision_bin", text="Export this", icon='EXPORT')

        # ---- Custom course (1 file-set per course, default workflow) ----
        box = layout.box()
        box.label(text="Custom course:", icon='OUTLINER_COLLECTION')
        col = box.column(align=True)
        col.operator("scene.mkgp2_new_course", text="New (empty)")
        col.operator("scene.mkgp2_import_course", text="Import file-set")
        col.operator("scene.mkgp2_export_course", text="Export selected course")
        col.operator("scene.mkgp2_validate_course", text="Validate selected course",
                     icon='CHECKMARK')
        col.operator("mkgp2.add_course_root", text="Add coordinate root",
                     icon='EMPTY_AXIS')
        col.operator("scene.mkgp2_attach_hsd", text="Attach HSD bundle",
                     icon='LINK_BLEND')

        # ---- Vanilla course (short/long pair, retained for round-trip) --
        box = layout.box()
        box.label(text="Vanilla course (short+long pair):", icon='WORLD')
        col = box.column(align=True)
        col.operator("import_scene.mkgp2_full_course", text="Import HSD + col + line + auto")
        col.operator("export_scene.mkgp2_full_course", text="Export all collision / line / auto")
        col.operator("scene.mkgp2_promote_vanilla",
                     text="Promote to course collection(s)", icon='OUTLINER_COLLECTION')

        # ---- Visualization (T1b) -------------------------------------
        box = layout.box()
        box.label(text="Visualization:", icon='HIDE_OFF')
        wm = context.window_manager
        # Overlay toggles
        row = box.row(align=True)
        row.prop(wm, "mkgp2_show_arrows", toggle=True,
                 text="Direction arrows", icon='FORWARD')
        row.prop(wm, "mkgp2_show_waypoint_ids", toggle=True,
                 text="Waypoint #", icon='SORTBYEXT')
        # Line variant visibility (only meaningful when a line root is
        # locatable from context).
        line_root = _resolve_line_root(context)
        if line_root is not None:
            n = len(_line_variants_under(line_root))
            sub = box.column(align=True)
            sub.label(text=f"Line variants ({n}) under '{line_root.name}':",
                      icon='TRACKING')
            grid = sub.grid_flow(row_major=True, columns=4, align=True)
            for idx, _ in _line_variants_under(line_root):
                op = grid.operator("mkgp2.show_only_variant",
                                   text=f"v{idx}")
                op.variant_index = idx
            row = sub.row(align=True)
            row.operator("mkgp2.show_all_variants", text="Show all",
                         icon='HIDE_OFF')
            row.operator("mkgp2.hide_all_variants", text="Hide all",
                         icon='HIDE_ON')
        # One-shot collision color helper + origin marker
        row = box.row(align=True)
        row.operator("mkgp2.show_collision_material",
                     text="Color collision", icon='COLOR')
        row.operator("mkgp2.add_origin_marker",
                     text="Origin marker", icon='EMPTY_AXIS')

        # ---- Per-asset (escape hatch) ----------------------------------
        box = layout.box()
        box.label(text="Per-asset import:", icon='IMPORT')
        col = box.column(align=True)
        col.operator("import_scene.mkgp2_hsd_json", text="HSD scene.json")
        col.operator("import_mesh.mkgp2_collision_bin", text="Collision (.bin)")
        col.operator("import_mesh.mkgp2_line_bin", text="Line (.bin)")
        col.operator("import_mesh.mkgp2_auto_bin", text="Auto path (.bin)")

        box = layout.box()
        box.label(text="Per-asset export:", icon='EXPORT')
        col = box.column(align=True)
        col.operator("export_mesh.mkgp2_collision_bin", text="Collision (.bin)")
        col.operator("export_scene.mkgp2_line_bin", text="Line (.bin)")
        col.operator("export_scene.mkgp2_auto_bin", text="Auto path (.bin)")
        col.operator("export_scene.mkgp2_hsd_json",
                     text="HSD bundle (.dat) [structural only]")

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

    default_bin_dir: StringProperty(
        name="Vanilla bin directory (read-only)",
        description=(
            "Folder containing the vanilla course .bin / .dat files "
            "(typically <Dolphin dump>/files/). Used as the source for "
            "Vanilla Full Course Import and as the file-browser starting "
            "directory for per-asset Import operators. Export operators "
            "REFUSE to write here -- this folder is treated as read-only "
            "to prevent accidental ROM dump corruption."
        ),
        subtype='DIR_PATH',
        default="",
    )

    output_bin_dir: StringProperty(
        name="Output bin directory (writable)",
        description=(
            "Folder where Course / Per-asset Export operators write "
            "their .bin output by default. Typically a Riivolution "
            "patch source directory (e.g. mkgp2-patch/features/<my>/files). "
            "MUST be different from Vanilla bin directory; the export "
            "guard refuses overlap."
        ),
        subtype='DIR_PATH',
        default="",
    )

    dotnet_script_path: StringProperty(
        name="dotnet-script path (optional)",
        description=(
            "Override for the dotnet-script launcher. Leave empty to "
            "auto-detect via PATH and ~/.dotnet/tools/. Required only "
            "if Vanilla auto-import via csx fails to find dotnet-script."
        ),
        subtype='FILE_PATH',
        default="",
    )

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.label(text="Source code:")
        col.prop(self, "source_modules_path")
        col.label(text=f"  Currently resolved: {_resolve_source_path()}")
        col.separator()
        col.label(text="Course assets:")
        col.prop(self, "default_bin_dir")
        col.prop(self, "output_bin_dir")
        # Warn loudly if the two paths collide -- this is exactly the
        # configuration that caused vanilla bin overwrites previously.
        van = (self.default_bin_dir or "").strip()
        out = (self.output_bin_dir or "").strip()
        if van and out:
            try:
                if Path(van).resolve() == Path(out).resolve():
                    col.label(
                        text="WARNING: Vanilla and Output point to the "
                             "same folder; export will refuse.",
                        icon='ERROR')
            except Exception:
                pass
        col.separator()
        col.label(text="HSD pipeline (Vanilla auto-import):")
        col.prop(self, "dotnet_script_path")
        ds = _resolve_dotnet_script()
        col.label(text=f"  Resolved dotnet-script: {ds or '(not found)'}")
        cs = _resolve_csx_path()
        col.label(text=f"  Resolved csx: {cs}"
                       f"{'' if os.path.isfile(cs) else ' (missing!)'}")
        layout.operator("mkgp2.reload_modules", icon='FILE_REFRESH')


def _vanilla_bin_dir():
    """Return the user-configured vanilla bin directory, or ''.

    This is the import source -- typically the Dolphin filesystem dump
    of the vanilla ROM. It is treated as read-only by the addon: any
    Export operator that would land inside this directory aborts.
    """
    try:
        prefs = bpy.context.preferences.addons[__name__].preferences
        p = prefs.default_bin_dir
        if p:
            return p
    except Exception:
        pass
    return ""


def _output_bin_dir():
    """Return the user-configured output bin directory, or ''.

    This is the export destination (e.g. a Riivolution patch source
    folder). Writable. Used as the default `mkgp2_bin_dir` for new
    courses and as the fallback when a course's `mkgp2_bin_dir` is
    empty.
    """
    try:
        prefs = bpy.context.preferences.addons[__name__].preferences
        p = prefs.output_bin_dir
        if p:
            return p
    except Exception:
        pass
    return ""


# Back-compat shim -- existing tests / scripts can keep calling this.
# Prefer _vanilla_bin_dir or _output_bin_dir at the source.
_default_bin_dir = _vanilla_bin_dir


def _is_inside_vanilla(path):
    """True iff `path` is the vanilla bin dir or a descendant. Empty
    paths or unset preference -> False (= no protection)."""
    van = _vanilla_bin_dir()
    if not van or not path:
        return False
    try:
        van_r = Path(van).resolve()
        p_r = Path(path).resolve()
        return p_r == van_r or van_r in p_r.parents
    except Exception:
        return False


def _refuse_if_vanilla(op, path, *, what="output"):
    """Operator-side guard. Reports ERROR and returns True if `path`
    falls inside the vanilla bin dir, else False (= proceed).
    """
    if _is_inside_vanilla(path):
        op.report({'ERROR'},
            f"Refusing to write {what} '{path}': it lives inside the "
            "vanilla bin directory (read-only). Set Output bin "
            "directory in addon preferences, or override mkgp2_bin_dir "
            "on the course collection.")
        return True
    return False


# ---------------------------------------------------------------------------
# File menu integration
# ---------------------------------------------------------------------------

def _menu_import(self, context):
    self.layout.separator()
    self.layout.operator("scene.mkgp2_import_course", text="MKGP2 Course (file-set)")
    self.layout.operator("import_scene.mkgp2_full_course", text="MKGP2 Vanilla Full Course")
    self.layout.operator("import_scene.mkgp2_hsd_json", text="MKGP2 HSD (scene.json)")
    self.layout.operator("import_mesh.mkgp2_collision_bin", text="MKGP2 Collision (.bin)")
    self.layout.operator("import_mesh.mkgp2_line_bin", text="MKGP2 Line (.bin)")
    self.layout.operator("import_mesh.mkgp2_auto_bin", text="MKGP2 Auto Path (.bin)")


def _menu_export(self, context):
    self.layout.separator()
    self.layout.operator("scene.mkgp2_export_course", text="MKGP2 Course (active collection)")
    self.layout.operator("export_scene.mkgp2_full_course", text="MKGP2 Vanilla Full Course")
    self.layout.operator("export_scene.mkgp2_hsd_json", text="MKGP2 HSD (.dat) [structural only]")
    self.layout.operator("export_mesh.mkgp2_collision_bin", text="MKGP2 Collision (.bin)")
    self.layout.operator("export_scene.mkgp2_line_bin", text="MKGP2 Line (.bin)")
    self.layout.operator("export_scene.mkgp2_auto_bin", text="MKGP2 Auto Path (.bin)")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

CLASSES = (
    MKGP2AddonPreferences,
    MKGP2_OT_ImportHSD,
    MKGP2_OT_ExportHSD,
    MKGP2_OT_ImportCollision,
    MKGP2_OT_ImportLine,
    MKGP2_OT_ImportAuto,
    MKGP2_OT_ImportFullCourse,
    MKGP2_OT_ExportLine,
    MKGP2_OT_ExportAuto,
    MKGP2_OT_ExportCollision,
    MKGP2_OT_ExportFullCourse,
    MKGP2_OT_NewCourse,
    MKGP2_OT_ImportCourse,
    MKGP2_OT_ExportCourse,
    MKGP2_OT_ValidateCourse,
    MKGP2_OT_PromoteVanilla,
    MKGP2_OT_AttachHsdToCourse,
    MKGP2_OT_ShowOnlyVariant,
    MKGP2_OT_ShowAllVariants,
    MKGP2_OT_HideAllVariants,
    MKGP2_OT_ShowCollisionMaterial,
    MKGP2_OT_AddOriginMarker,
    MKGP2_OT_AddCourseRoot,
    MKGP2_OT_ReloadModules,
    MKGP2_PT_CoursePanel,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(_menu_import)
    bpy.types.TOPBAR_MT_file_export.append(_menu_export)
    # Overlay toggles. Stored on WindowManager so they don't pollute
    # .blend files; the draw handler is torn down in unregister().
    bpy.types.WindowManager.mkgp2_show_arrows = BoolProperty(
        name="Auto/Line direction arrows",
        default=False,
        update=_on_show_arrows_toggle,
    )
    bpy.types.WindowManager.mkgp2_show_waypoint_ids = BoolProperty(
        name="Waypoint index labels",
        default=False,
        update=_on_show_waypoint_ids_toggle,
    )
    # Best-effort eager load so first import is fast and configuration errors
    # surface in the console instead of mid-operator.
    ok, err = reload_modules()
    if not ok:
        print(f"[MKGP2 addon] initial module load skipped: {err}")


def unregister():
    # Tear down draw handlers first; tagging redraw against an unloaded
    # callback would crash on the next viewport refresh.
    _set_overlay('arrows', False, _draw_arrows_callback)
    _set_overlay('waypoints', False, _draw_waypoint_ids_callback)
    if hasattr(bpy.types.WindowManager, "mkgp2_show_arrows"):
        del bpy.types.WindowManager.mkgp2_show_arrows
    if hasattr(bpy.types.WindowManager, "mkgp2_show_waypoint_ids"):
        del bpy.types.WindowManager.mkgp2_show_waypoint_ids
    bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
    bpy.types.TOPBAR_MT_file_export.remove(_menu_export)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
