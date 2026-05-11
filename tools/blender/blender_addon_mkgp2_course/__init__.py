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
    "version": (0, 2, 1),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > MKGP2  /  File > Import & Export",
    "description": "Import / export MKGP2 course resources (HSD mesh, collision, line waypoints, AI auto path)",
    "category": "Import-Export",
}

import bpy
import json
import os
import platform
import sys
import tempfile
import importlib
from pathlib import Path

import mathutils
from bpy.types import Operator, Panel, AddonPreferences
from bpy.props import StringProperty, IntProperty, BoolProperty, FloatProperty, EnumProperty


# ---------------------------------------------------------------------------
# Vendored Rust extension: hsdraw (HSD .dat reader/writer)
# ---------------------------------------------------------------------------
#
# The wheel under vendor/<platform>/ ships an abi3 .pyd built by maturin
# from the standalone hsdraw repo. abi3 means Blender 4.x's bundled
# CPython 3.11 picks up the cp37-abi3 wheel without rebuild. We add the
# matching platform dir to sys.path before any other addon code so all
# downstream module imports can rely on `import hsdraw`.
#
# If the platform isn't covered (e.g. Linux distro shipped with a
# vendor/ stripped of the matching wheel) the import simply fails. HSD
# import / export operators check `HSDRAW_AVAILABLE` and refuse with a
# clear error when the extension is missing.

def _resolve_hsdraw_platform_dir():
    arch = platform.machine().lower()
    if sys.platform.startswith("linux"):
        return "linux_aarch64" if arch in ("aarch64", "arm64") else "linux_x86_64"
    if sys.platform == "darwin":
        return "macos_arm64" if arch in ("arm64", "aarch64") else "macos_x86_64"
    if sys.platform == "win32":
        return "windows_x86_64"
    return None

_hsdraw_dir = Path(__file__).parent / "vendor" / (
    _resolve_hsdraw_platform_dir() or "_unsupported")
if _hsdraw_dir.is_dir() and str(_hsdraw_dir) not in sys.path:
    sys.path.insert(0, str(_hsdraw_dir))

try:
    import hsdraw as _hsdraw_module
    HSDRAW_AVAILABLE = True
    HSDRAW_VERSION = getattr(_hsdraw_module, "__version__", "unknown")
except ImportError as _ex:
    HSDRAW_AVAILABLE = False
    HSDRAW_VERSION = None
    _hsdraw_module = None
    print(f"[mkgp2 addon] hsdraw not available ({_ex}); HSD import / "
          f"export operators will refuse until the wheel is installed at "
          f"{_hsdraw_dir}")


# ---------------------------------------------------------------------------
# Module discovery (delegates to tools/blender/ scripts)
# ---------------------------------------------------------------------------

# Module references, populated by reload_modules().
hsd_imp = None
col_imp = None
line_imp = None
auto_imp = None
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
    global hsd_imp, col_imp, line_imp, auto_imp
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
# HSD .dat -> bundle (Python-only, no csx)
# ---------------------------------------------------------------------------
#
# Earlier versions shelled out to `tools/hsd/hsd_export_for_blender.csx`
# (HSDLib + dotnet-script + ImageSharp) to produce a scene.json + PNG
# bundle from a vanilla .dat, then handed the bundle to the importer.
# The vendored `hsdraw` Rust extension now covers both halves of that
# pipeline (`hsdraw.export_scene_json` for the JSON, `hsdraw.gx_decode`
# for the texture pixels), so the importer reads .dat files directly.
# The csx file still ships in `tools/hsd/` as a parity oracle for
# `hsdraw.export_scene_json`, but the addon does not invoke it.
#
# M3b retired the `_run_writer_*` family along with the
# `MKGP2_OT_PromoteVisToHSD` operator. The unified MKGP2_OT_ExportHSD
# now invokes `_export_mkgp2_bundle` (for HSD bundles) or
# `_promote_vis_to_hsd` (for vis: collections) directly via the
# vendored hsdraw extension.


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

    Bundle collections are created by `MKGP2_OT_ImportHSD` (.dat
    direct read via hsdraw) and carry `mkgp2_source_dat` +
    `mkgp2_joints` + `mkgp2_joint_aliases` custom props. Their
    conventional name is
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
    """Import an HSD .dat directly into Blender as a `mkgp2:<dat>` bundle.

    Uses `hsdraw.export_scene_json` + `hsdraw.gx_decode` to materialize
    the scene fully in Python (no csx / dotnet-script). GX bytes for
    each unique texture are written to a per-source-file temp dir so
    the unified exporter's bypass dispatch keeps working.
    """
    bl_idname = "import_scene.mkgp2_hsd_json"
    bl_label = "Import MKGP2 HSD (.dat)"
    bl_options = {'PRESET', 'UNDO'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.dat", options={'HIDDEN'})

    def execute(self, context):
        if not _need_modules(self):
            return {'CANCELLED'}
        try:
            hsd_imp.import_dat_directly(self.filepath)
        except Exception as ex:
            self.report({'ERROR'}, f"HSD import failed: {ex}")
            return {'CANCELLED'}
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


def _resolve_vis_collection(context):
    """Find a `vis:<name>` editor-only collection from the active
    layer collection, or by walking the active object's collection
    chain. Returns None if none in scope."""
    layer = getattr(context.view_layer, "active_layer_collection", None)
    if layer is not None:
        c = layer.collection
        if c.name.startswith("vis:"):
            return c
    obj = context.active_object
    if obj is not None:
        for c in obj.users_collection:
            if c.name.startswith("vis:"):
                return c
    return None


class MKGP2_OT_ExportHSD(Operator):
    """Write an HSD .dat from the active context.

    Dispatcher: handles two source kinds in one operator (M3b unification).

      * **HSD bundle** (`mkgp2:<dat>` collection with stashed
        `mkgp2_joints` + `mkgp2_joint_aliases` + `mkgp2_scene_json`):
        invokes `_export_mkgp2_bundle.export_bundle_to_dat`. Vanilla
        `.dat` is no longer read -- the new writer reconstructs the
        scene from scratch via `hsdraw`. Mesh edits (vertex moves,
        material color changes) and texture edits (in-Blender or via
        external editor) are reflected; untouched textures bypass the
        encoder via the M2-stashed raw GX bytes.

      * **vis: editor-only collection** (`vis:<name>` populated with
        Blender meshes + Principled BSDF materials): invokes
        `_promote_vis_to_hsd.promote_vis_to_dat`. Promotion synthesizes
        one POBJ per (mesh, material slot), one root JObj, and a fresh
        scene_data SObj allocated from scratch via
        `hsdraw.Dat.alloc_scene_data_minimal()`. No vanilla `.dat` is read.

    The dispatcher prefers an HSD bundle if both are reachable from
    context (the bundle is the typical edit target).
    """
    bl_idname = "export_scene.mkgp2_hsd_json"
    bl_label = "Export MKGP2 HSD (.dat)"
    bl_options = {'PRESET'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.dat", options={'HIDDEN'})

    def _resolve_target(self, context):
        """Decide which branch to take. Returns ('bundle', coll) or
        ('vis', coll) or (None, None) on no match."""
        bundle = _resolve_hsd_bundle_collection(context)
        if bundle is not None:
            return "bundle", bundle
        vis = _resolve_vis_collection(context)
        if vis is not None:
            return "vis", vis
        return None, None

    def execute(self, context):
        if not HSDRAW_AVAILABLE:
            self.report({'ERROR'},
                "hsdraw is not vendored for this platform; the unified "
                "HSD exporter requires it. See addon vendor/<platform>/.")
            return {'CANCELLED'}
        kind, coll = self._resolve_target(context)
        if kind is None:
            self.report({'ERROR'},
                "No HSD bundle or vis: collection in context. Activate "
                "an `mkgp2:<dat>` (re-export) or `vis:<name>` "
                "(synthesize) collection in the Outliner first.")
            return {'CANCELLED'}
        if not self.filepath:
            self.report({'ERROR'}, "No output filepath set.")
            return {'CANCELLED'}
        if _refuse_if_vanilla(self, self.filepath, what="HSD .dat"):
            return {'CANCELLED'}

        if kind == "bundle":
            return self._execute_bundle(context, coll)
        elif kind == "vis":
            return self._execute_vis(context, coll)
        return {'CANCELLED'}

    def _execute_bundle(self, context, bundle):
        # `mkgp2_scene_json` is either an inline JSON string (post-csx-
        # retirement bundles) or a path on disk (legacy csx-era bundles).
        # `_export_mkgp2_bundle.export_bundle_to_dat` accepts either form.
        scene_json = bundle.get("mkgp2_scene_json", "")
        if not scene_json:
            self.report({'ERROR'},
                f"Bundle '{bundle.name}' has no mkgp2_scene_json prop. "
                "Re-import the source .dat to re-stash it.")
            return {'CANCELLED'}
        if not (isinstance(scene_json, str)
                and scene_json.lstrip().startswith('{')) \
                and not Path(scene_json).is_file():
            self.report({'ERROR'},
                f"Bundle '{bundle.name}' mkgp2_scene_json is neither inline "
                f"JSON nor an existing path ({scene_json!r:.80}). Re-import "
                "the source .dat to re-stash it.")
            return {'CANCELLED'}

        # Sync joint parent / children from any Empty hierarchy that was
        # built at import time. Each Empty carries a `mkgp2_jobj_id`
        # custom prop; its `.parent.mkgp2_jobj_id` supplies the joint's
        # parent. The stashed JSON is rewritten so the new exporter sees
        # the Empty tree as source of truth.
        try:
            joints = json.loads(bundle.get("mkgp2_joints", "[]"))
        except json.JSONDecodeError as ex:
            self.report({'ERROR'},
                f"Bundle '{bundle.name}' has malformed mkgp2_joints: {ex}")
            return {'CANCELLED'}
        empty_by_id = {}
        for o in bundle.objects:
            if o.type == 'EMPTY' and o.get("mkgp2_jobj_id"):
                empty_by_id[o["mkgp2_jobj_id"]] = o
        if empty_by_id:
            joint_by_id = {j["id"]: j for j in joints if isinstance(j, dict)}
            for j in joints:
                j["parent"] = None
                j["children"] = []
            for jid, e in empty_by_id.items():
                if jid not in joint_by_id:
                    continue
                if e.parent is not None and e.parent.get("mkgp2_jobj_id"):
                    pid = e.parent["mkgp2_jobj_id"]
                    if pid in joint_by_id:
                        joint_by_id[jid]["parent"] = pid
                        if jid not in joint_by_id[pid]["children"]:
                            joint_by_id[pid]["children"].append(jid)
            bundle["mkgp2_joints"] = json.dumps(joints)

        from . import _export_mkgp2_bundle
        template_dat = _resolve_scene_template_dat()
        try:
            stats = _export_mkgp2_bundle.export_bundle_to_dat(
                bundle, scene_json, self.filepath,
                template_dat=template_dat,
            )
        except _export_mkgp2_bundle.TextureBuildError as ex:
            self.report({'ERROR'}, f"Texture failure: {ex}")
            return {'CANCELLED'}
        except Exception as ex:
            self.report({'ERROR'}, f"HSD bundle export failed: {ex}")
            return {'CANCELLED'}

        self.report({'INFO'},
            f"Wrote {Path(self.filepath).name} "
            f"({stats['output_size']} bytes): "
            f"{stats['meshes']} meshes / {stats['verts']} verts / "
            f"{stats['textures']} textures "
            f"(bypass={stats['tex_bypass']}, "
            f"reencode={stats['tex_reencode']})")
        return {'FINISHED'}

    def _execute_vis(self, context, vis):
        # vis: branch threads a vanilla scene template through to the
        # promote pipeline so the resulting Dat keeps LObj (lights) +
        # COBJ (camera) descriptors.  Without those the in-game renderer
        # leaves character meshes dark and texture sampling collapses on
        # our own course geometry.  When the template is missing (the
        # vanilla bin dir preference is unset or the file is absent),
        # the helper falls back to `Dat.alloc_scene_data_minimal()` and emits a
        # loud WARN to its log -- the operator still completes, but the
        # output is unsafe to ship.
        from . import _promote_vis_to_hsd
        template_dat = _resolve_scene_template_dat()
        try:
            stats = _promote_vis_to_hsd.promote_vis_to_dat(
                vis, self.filepath,
                template_dat=template_dat,
            )
        except Exception as ex:
            self.report({'ERROR'}, f"vis: promote failed: {ex}")
            return {'CANCELLED'}

        self.report({'INFO'},
            f"Wrote {Path(self.filepath).name}: {stats['dobj_count']} "
            f"DObjs, {stats['total_verts']} verts, "
            f"{stats['total_tris']} tris, {stats['output_size']} bytes")
        return {'FINISHED'}

    def invoke(self, context, event):
        kind, coll = self._resolve_target(context)
        if kind is None:
            self.report({'ERROR'},
                "No HSD bundle or vis: collection in context. Activate "
                "an `mkgp2:<dat>` or `vis:<name>` collection first.")
            return {'CANCELLED'}
        if not self.filepath:
            if kind == "bundle":
                base_name = coll.get("mkgp2_source_dat", "") or "out.dat"
            else:
                stem = coll.name[len("vis:"):] if coll.name.startswith("vis:") else coll.name
                base_name = f"{stem}.dat"
            out_dir = _output_bin_dir() or ""
            if out_dir:
                self.filepath = os.path.join(out_dir, base_name)
            else:
                self.filepath = base_name
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


# ---------------------------------------------------------------------------
# HSD bundle: alias edit operators
# ---------------------------------------------------------------------------
#
# The HSD reader stashes `joint_aliases` (public root symbol -> jobj_id)
# on the bundle collection as a JSON-encoded string custom property. The
# writer reads it back from the same prop. These operators wrap the dict
# editing so users don't have to poke at the raw JSON via Blender's
# custom-property panel.
#
# UI lives in the `MKGP2_PT_HsdAliasPanel` sub-panel (only visible when
# an HSD bundle is in context).

def _bundle_load_aliases(coll):
    """Parse coll's mkgp2_joint_aliases JSON. Returns ({}, error_str) on
    malformed input."""
    raw = coll.get("mkgp2_joint_aliases", "{}")
    try:
        d = json.loads(raw) if raw else {}
        if not isinstance(d, dict):
            return {}, f"mkgp2_joint_aliases is not a dict: {type(d).__name__}"
        return d, None
    except json.JSONDecodeError as ex:
        return {}, f"malformed JSON: {ex}"


def _bundle_save_aliases(coll, aliases):
    """Persist the alias dict back to the bundle's stashed prop."""
    coll["mkgp2_joint_aliases"] = json.dumps(aliases)


def _bundle_load_joint_ids(coll):
    """Returns the list of joint IDs in the bundle's joints stash, or
    [] if the prop is missing / malformed."""
    raw = coll.get("mkgp2_joints", "[]")
    try:
        joints = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return []
    return [j.get("id", "") for j in joints if isinstance(j, dict) and j.get("id")]


class MKGP2_OT_HsdAliasAdd(Operator):
    """Add a public root alias to the active HSD bundle's stashed
    joint_aliases dict.

    The alias name is the public symbol that game code will look up
    (e.g. `MR_highway_inu_joint`); the target joint id is one of the
    bundle's `jobj_<n>` entries from its joints list. On the next
    Export HSD, the writer splices this name into file.Roots pointing
    at the same struct as the target joint.
    """
    bl_idname = "scene.mkgp2_hsd_alias_add"
    bl_label = "Add HSD alias"
    bl_options = {'INTERNAL'}

    name: StringProperty(name="Alias name")
    target_id: StringProperty(name="Target joint id")

    def execute(self, context):
        bundle = _resolve_hsd_bundle_collection(context)
        if bundle is None:
            self.report({'ERROR'}, "No HSD bundle in context")
            return {'CANCELLED'}
        name = self.name.strip()
        target = self.target_id.strip()
        if not name:
            self.report({'ERROR'}, "Alias name is required")
            return {'CANCELLED'}
        if not target:
            self.report({'ERROR'}, "Target joint id is required")
            return {'CANCELLED'}
        valid_ids = set(_bundle_load_joint_ids(bundle))
        if target not in valid_ids:
            self.report({'ERROR'},
                f"Target '{target}' is not a known joint id in this bundle "
                f"({len(valid_ids)} candidates: jobj_0..jobj_{len(valid_ids)-1})")
            return {'CANCELLED'}
        aliases, err = _bundle_load_aliases(bundle)
        if err is not None:
            self.report({'ERROR'}, f"Bundle stash unreadable: {err}")
            return {'CANCELLED'}
        if name in aliases:
            self.report({'WARNING'},
                f"Alias '{name}' already exists -> {aliases[name]}; "
                f"overwriting with {target}")
        aliases[name] = target
        _bundle_save_aliases(bundle, aliases)
        self.report({'INFO'}, f"Added alias '{name}' -> {target}")
        return {'FINISHED'}


class MKGP2_OT_HsdAliasRemove(Operator):
    """Remove a public root alias entry from the active HSD bundle's
    stashed joint_aliases dict. The next Export HSD will drop the
    corresponding entry from file.Roots."""
    bl_idname = "scene.mkgp2_hsd_alias_remove"
    bl_label = "Remove HSD alias"
    bl_options = {'INTERNAL'}

    name: StringProperty(name="Alias name")

    def execute(self, context):
        bundle = _resolve_hsd_bundle_collection(context)
        if bundle is None:
            self.report({'ERROR'}, "No HSD bundle in context")
            return {'CANCELLED'}
        if not self.name:
            self.report({'ERROR'}, "Alias name required")
            return {'CANCELLED'}
        aliases, err = _bundle_load_aliases(bundle)
        if err is not None:
            self.report({'ERROR'}, f"Bundle stash unreadable: {err}")
            return {'CANCELLED'}
        if self.name not in aliases:
            self.report({'WARNING'}, f"Alias '{self.name}' not present, no-op")
            return {'CANCELLED'}
        old_target = aliases.pop(self.name)
        _bundle_save_aliases(bundle, aliases)
        self.report({'INFO'}, f"Removed alias '{self.name}' (was -> {old_target})")
        return {'FINISHED'}


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
    """Load HSD + collision + line + auto for one vanilla course.

    Auto-discovers `<bin_dir>/<Prefix>_short_A.dat` and
    `<Prefix>_long_A.dat` and reads each via `hsd_imp.import_dat_directly`
    (Python-only, no external CLI), then sweeps the collision / line /
    auto .bin files by suffix.
    """
    bl_idname = "import_scene.mkgp2_full_course"
    bl_label = "Import MKGP2 Full Course"
    bl_options = {'PRESET', 'UNDO'}

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
            self._auto_import(self.bin_dir, self.prefix)
        except Exception as ex:
            self.report({'ERROR'}, f"Full course import failed: {ex}")
            return {'CANCELLED'}
        return {'FINISHED'}

    def _auto_import(self, bin_dir, prefix):
        """Import every <Prefix>_<round>_A.dat we can find via the
        Python-only .dat reader, then sweep the standard .bin filenames."""
        bin_dir_p = Path(bin_dir)

        # 1) HSD bundles per round (Python direct .dat read; no csx)
        for round_label in ("short", "long"):
            dat = _find_vanilla_dat(bin_dir_p, prefix, round_label)
            if dat is None:
                print(f"[mkgp2 full] no .dat for {round_label} "
                      f"(<{prefix}>_{round_label}_A.dat); HSD skipped")
                continue
            try:
                hsd_imp.import_dat_directly(str(dat))
            except Exception as ex:
                # Make HSD failures non-fatal: collision/line/auto are
                # still importable without the visual reference.
                print(f"[mkgp2 full] HSD import failed for {dat.name}: {ex}")
                continue
            print(f"[mkgp2 full] HSD OK: {dat.name}")

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
    """Create an empty MKGP2 course shell collection (collision / line / auto).

    Generates `MKGP2_Course/<name>/` with custom properties for the
    `<name>.bin` / `<name>_line.bin` / `<name>_Auto.bin` / `<name>_Auto_R.bin`
    filenames. The collection is intended as the **container** for a
    CollisionMesh + WallSegments + line Empty + auto-path mesh that the
    MKGP2 collision / line / auto exporters will read.

    This operator does **not** create the visual mesh side. Two parallel
    workflows live in different collection prefixes:

      * `vis:<name>` -- a user-authored Blender collection holding the
        visual course mesh (Principled BSDF materials, ordinary mesh
        geometry). Promoted from-scratch into a fresh `<name>.dat` by
        File > Export > MKGP2 HSD (with the `vis:<name>` collection
        active, no vanilla `.dat` is read).
      * `mkgp2:<dat>` -- the result of File > Import > MKGP2 HSD on an
        existing `.dat` (vanilla or previously exported). Re-export
        round-trips through `_export_mkgp2_bundle` (scene.json + mesh
        bundle) for vanilla-edit workflows.

    For a brand-new course you typically (1) call this operator, (2) build
    `vis:<name>` by hand or via File > Import on an existing course you
    want to start from, then (3) Full Course Export bundles the .bin
    triplet from `MKGP2_Course/<name>/` and the .dat from `vis:<name>`.
    """
    bl_idname = "scene.mkgp2_new_course"
    bl_label = "New MKGP2 Course"
    bl_description = ("Create an empty course shell (MKGP2_Course/<name>/) "
                      "with collision/line/auto bin filename properties. "
                      "vis:<name> for visual mesh editing must be created separately.")
    bl_options = {'UNDO'}

    name: StringProperty(
        name="Course name",
        description="Becomes the collection name and the default filename stem (without .bin)",
        default="my_course",
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
        # mkgp2_bin_dir is left empty so Export Course's destination
        # dialog falls through to the addon preference at write time.
        # A per-course override can still be set later by editing the
        # collection's custom property, or implicitly by Export Course
        # remembering the user's last destination pick.
        coll["mkgp2_bin_dir"] = ""
        # HSD .dat is optional: leave empty so the user can later attach a
        # bundle via Import Course or set the property by hand.
        coll["mkgp2_hsd_dat"] = ""

        self.report({'INFO'},
            f"Created empty course '{self.name}' under {ROOT_COLL_NAME}/")
        return {'FINISHED'}

    def invoke(self, context, event):
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
        # Sibling .dat conventions:
        #   <stem>.dat            (line / auto / collision share one .dat)
        #   <stem>_road.dat       (vanilla MKGP2 naming for road geometry)
        # Pick the first hit so the user doesn't have to hunt for it.
        for candidate in (parent_dir / f"{stem}.dat",
                          parent_dir / f"{stem}_road.dat"):
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
        name="HSD .dat",
        description=(
            "Optional HSD source .dat. When given, the resulting "
            "`mkgp2:<dat>` collection (= visual reference geometry) is "
            "nested inside the course collection. Leave empty to skip — "
            "a custom course can author collision/line/auto without an "
            "HSD reference."
        ),
        subtype='FILE_PATH',
    )
    hsd_dat_filename: StringProperty(
        name="HSD .dat filename (override)",
        description=(
            "Optional override for the recorded .dat filename "
            "(e.g. test_course_road.dat). Stored as mkgp2_hsd_dat on "
            "the course collection. Leave empty to derive from the HSD "
            "source path."
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
                    lambda: hsd_imp.import_dat_directly(self.hsd_path))
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
                    "Re-import the matching .dat via Import HSD so the "
                    "bundle name picks up the course canonical name."
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


# MKGP2_OT_PromoteVisToHSD was retired in M3b: vis: collections are
# now exported through the unified MKGP2_OT_ExportHSD operator (its
# execute() dispatches to `_promote_vis_to_hsd.promote_vis_to_dat`
# whenever the active layer collection is `vis:*`). The legacy
# `scene.mkgp2_promote_vis_to_hsd` bl_idname is no longer registered.


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
    """Container panel. All actual content lives in the sub-panels below
    so each section is independently collapsible (Active target / Custom
    course / Visualization / Vanilla course / Per-asset import / Per-asset
    export / HSD aliases / Texture format / Reload course modules). The
    top three are DEFAULT_OPEN (= daily workflow), the rest are
    DEFAULT_CLOSED."""
    bl_label = "MKGP2 Course"
    bl_idname = "MKGP2_PT_course_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MKGP2'

    def draw(self, context):
        # Empty -- everything is in the sub-panels below.
        pass


class MKGP2_PT_ActiveTargetPanel(Panel):
    """Context-aware 'Export this' button driven by the active object.
    Mirrors what selection / active layer collection implies and hands off
    to the right per-asset exporter so the user doesn't have to think
    about which sub-panel to open."""
    bl_label = "Active target"
    bl_idname = "MKGP2_PT_active_target_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MKGP2'
    bl_parent_id = "MKGP2_PT_course_panel"

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        hint, op_id, icon = _detect_export_target(obj)
        layout.label(text=hint, icon=icon)
        row = layout.row()
        row.enabled = op_id is not None
        if op_id:
            row.operator(op_id, text="Export this", icon='EXPORT')
        else:
            row.operator("export_mesh.mkgp2_collision_bin", text="Export this", icon='EXPORT')


class MKGP2_PT_CustomCoursePanel(Panel):
    """Custom course operators -- 1 file-set per course is the default
    workflow. New / Export selected course are the two daily buttons;
    the rest cover one-time setup or rare resume-existing-project cases."""
    bl_label = "Custom course"
    bl_idname = "MKGP2_PT_custom_course_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MKGP2'
    bl_parent_id = "MKGP2_PT_course_panel"

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.operator("scene.mkgp2_new_course", text="New (empty)")
        col.operator("scene.mkgp2_import_course", text="Import file-set")
        col.operator("scene.mkgp2_export_course", text="Export selected course")
        col.operator("scene.mkgp2_validate_course", text="Validate selected course",
                     icon='CHECKMARK')
        col.operator("mkgp2.add_course_root", text="Add coordinate root",
                     icon='EMPTY_AXIS')
        col.operator("scene.mkgp2_attach_hsd", text="Attach HSD bundle",
                     icon='LINK_BLEND')


class MKGP2_PT_VisualizationPanel(Panel):
    """Viewport overlay toggles + line variant isolation + one-shot
    collision/origin helpers. Used during line / collision editing."""
    bl_label = "Visualization"
    bl_idname = "MKGP2_PT_visualization_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MKGP2'
    bl_parent_id = "MKGP2_PT_course_panel"

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        row = layout.row(align=True)
        row.prop(wm, "mkgp2_show_arrows", toggle=True,
                 text="Direction arrows", icon='FORWARD')
        row.prop(wm, "mkgp2_show_waypoint_ids", toggle=True,
                 text="Waypoint #", icon='SORTBYEXT')
        # Line variant visibility (only meaningful when a line root is
        # locatable from context).
        line_root = _resolve_line_root(context)
        if line_root is not None:
            n = len(_line_variants_under(line_root))
            sub = layout.column(align=True)
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
        row = layout.row(align=True)
        row.operator("mkgp2.show_collision_material",
                     text="Color collision", icon='COLOR')
        row.operator("mkgp2.add_origin_marker",
                     text="Origin marker", icon='EMPTY_AXIS')


class MKGP2_PT_VanillaCoursePanel(Panel):
    """Vanilla course full-set import/export and one-time wrapper into
    a custom course collection. Collapsed by default -- typical use is
    a single round of vanilla import early in a project's life."""
    bl_label = "Vanilla course (short+long pair)"
    bl_idname = "MKGP2_PT_vanilla_course_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MKGP2'
    bl_parent_id = "MKGP2_PT_course_panel"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.operator("import_scene.mkgp2_full_course", text="Import HSD + col + line + auto")
        col.operator("export_scene.mkgp2_full_course", text="Export all collision / line / auto")
        col.operator("scene.mkgp2_promote_vanilla",
                     text="Promote to course collection(s)", icon='OUTLINER_COLLECTION')


class MKGP2_PT_PerAssetImportPanel(Panel):
    """Per-asset import escape hatch. Same operators are duplicated in
    File > Import > MKGP2 ... -- the Sidebar mirror is kept collapsed by
    default since the typical workflow uses Custom course or the File
    menu."""
    bl_label = "Per-asset import"
    bl_idname = "MKGP2_PT_per_asset_import_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MKGP2'
    bl_parent_id = "MKGP2_PT_course_panel"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.operator("import_scene.mkgp2_hsd_json", text="HSD scene.json")
        col.operator("import_mesh.mkgp2_collision_bin", text="Collision (.bin)")
        col.operator("import_mesh.mkgp2_line_bin", text="Line (.bin)")
        col.operator("import_mesh.mkgp2_auto_bin", text="Auto path (.bin)")


class MKGP2_PT_PerAssetExportPanel(Panel):
    """Per-asset export escape hatch. Active target > Export this and
    Custom course > Export selected course cover most needs; this panel
    is kept collapsed by default for cases where the user wants to write
    a single .bin without re-emitting the full set."""
    bl_label = "Per-asset export"
    bl_idname = "MKGP2_PT_per_asset_export_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MKGP2'
    bl_parent_id = "MKGP2_PT_course_panel"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.operator("export_mesh.mkgp2_collision_bin", text="Collision (.bin)")
        col.operator("export_scene.mkgp2_line_bin", text="Line (.bin)")
        col.operator("export_scene.mkgp2_auto_bin", text="Auto path (.bin)")
        col.operator("export_scene.mkgp2_hsd_json",
                     text="HSD bundle (.dat)")


class MKGP2_PT_HsdAliasPanel(Panel):
    """Sub-panel for editing the active HSD bundle's public root alias
    table. Only visible when an HSD bundle is in context (active layer
    collection or active object's parent chain has `mkgp2_source_dat`).
    """
    bl_label = "HSD aliases"
    bl_idname = "MKGP2_PT_hsd_alias_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MKGP2'
    bl_parent_id = "MKGP2_PT_course_panel"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return _resolve_hsd_bundle_collection(context) is not None

    def draw(self, context):
        layout = self.layout
        bundle = _resolve_hsd_bundle_collection(context)
        if bundle is None:
            layout.label(text="(no HSD bundle in context)", icon='INFO')
            return
        layout.label(text=f"bundle: {bundle.name}",
                     icon='OUTLINER_COLLECTION')

        aliases, err = _bundle_load_aliases(bundle)
        if err is not None:
            layout.label(text=err, icon='ERROR')
            return

        if aliases:
            layout.label(text=f"Current aliases ({len(aliases)}):")
            box = layout.box()
            for name in sorted(aliases.keys()):
                row = box.row(align=True)
                row.label(text=name)
                row.label(text=f"-> {aliases[name]}")
                op = row.operator("scene.mkgp2_hsd_alias_remove",
                                  text="", icon='X')
                op.name = name
        else:
            layout.label(text="No aliases declared.", icon='INFO')

        # Add row
        wm = context.window_manager
        layout.separator()
        layout.label(text="Add alias:")
        box = layout.box()
        box.prop(wm, "mkgp2_alias_new_name", text="Name")
        box.prop(wm, "mkgp2_alias_new_target", text="Target")
        op = box.operator("scene.mkgp2_hsd_alias_add",
                          text="Add", icon='ADD')
        op.name = wm.mkgp2_alias_new_name
        op.target_id = wm.mkgp2_alias_new_target

        # Joint id hint -- show count + first few candidates
        ids = _bundle_load_joint_ids(bundle)
        if ids:
            layout.label(text=f"Available joint ids: {len(ids)} "
                              f"(jobj_0..jobj_{len(ids)-1})",
                         icon='INFO')


class MKGP2_PT_TextureFormatPanel(Panel):
    """Sub-panel for picking the GX texture format the addon will use
    when re-encoding a fresh material's BSDF Image / Base Color on
    export. Only visible when an active object has an active material
    slot in scope; the picker writes ``mat["mkgp2_target_format"]``
    (str) which both the bundle and vis: exporters consult.

    Existing materials imported from a vanilla .dat take the bypass
    path and are NOT affected by this dropdown -- the original GX
    bytes (and their original format) are reused byte-for-byte unless
    the user dirties the underlying Image. The picker matters for:

      * vis:<name> course synthesis (every BSDF goes through encode)
      * mkgp2:<dat> bundle edits where the user added a brand-new
        Blender material to a hand-added mesh
    """
    bl_label = "Texture format"
    bl_idname = "MKGP2_PT_texture_format_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MKGP2'
    bl_parent_id = "MKGP2_PT_course_panel"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and
                getattr(obj, "active_material", None) is not None)

    def draw(self, context):
        layout = self.layout
        mat = context.active_object.active_material
        layout.label(text=f"material: {mat.name}",
                     icon='MATERIAL')
        layout.prop(mat, "mkgp2_target_format", text="Target format")
        # Surface the concrete behavior so users don't have to guess
        # what the encoder will do for non-aligned CMP images.
        if mat.mkgp2_target_format == "CMP":
            box = layout.box()
            box.label(
                text="CMP demands 4x4 tile alignment.",
                icon='INFO')
            box.label(
                text="Non-aligned images silently fall back to RGBA8.")
        layout.label(text="Default: RGBA8 (lossless, ~8x larger than CMP)",
                     icon='QUESTION')


class MKGP2_PT_DevPanel(Panel):
    """Module hot-reload helper + addon source path indicator. Always
    last in the sub-panel chain (= sits at the bottom of the MKGP2
    Sidebar)."""
    bl_label = "Reload course modules"
    bl_idname = "MKGP2_PT_dev_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MKGP2'
    bl_parent_id = "MKGP2_PT_course_panel"

    def draw(self, context):
        layout = self.layout
        layout.operator("mkgp2.reload_modules",
                        text="Reload course modules",
                        icon='FILE_REFRESH')
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

    # (M3b removed `hsd_writer_backend`: there's only one writer path now,
    # the in-process `hsdraw` extension; the CSX/HSDLib detour is gone
    # from the operator's execute path. Subsequent retirement of csx from
    # the importer dropped the `dotnet_script_path` preference too.)

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
        col.label(text="HSD reader / writer (Import / Export HSD):")
        if HSDRAW_AVAILABLE:
            col.label(text=f"  hsdraw vendored: yes (v{HSDRAW_VERSION})",
                      icon='CHECKMARK')
        else:
            col.label(text="  hsdraw vendored: no -- export operator will "
                          "refuse until the wheel is installed",
                      icon='ERROR')
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


# Candidate template files, in fallback order.  Any vanilla course .dat
# whose SObj carries LObj + COBJ works; we prefer `MR_highway_long_A`
# because it's a long-form variant (= ships in every install) and its
# scene_data is well-formed.  If one is missing, we step down the list.
_SCENE_TEMPLATE_CANDIDATES = (
    "MR_highway_long_A.dat",
    "MR_highway_long_B.dat",
    "MR_highway_long_C.dat",
    "DN_stadium_long_a.dat",
)


def _scene_template_default_dirs():
    """Built-in Dolphin dump locations to probe when the addon
    preference `default_bin_dir` is empty.  Conservative list -- only
    the paths the Dolphin emulator hands a fresh user by default.
    """
    home = Path.home()
    return (
        home / "Documents" / "Dolphin ROMs" / "Triforce" / "mkgp2" / "files",
        home / "Documents" / "Dolphin Emulator" / "Triforce" / "mkgp2" / "files",
    )


def _resolve_scene_template_dat():
    """Return an absolute path string to a vanilla course .dat to use as
    a scene template, or `None` when nothing usable is configured.

    The HSD export pipeline (both `vis: -> .dat` and `mkgp2: -> .dat`)
    seeds the new Dat from this template so it keeps the SObj's LObj
    (lights) + COBJ (camera) descriptors -- `Dat.alloc_scene_data_minimal()`
    only allocates JObjDescs, and a Dat without LObj/COBJ leaves
    character meshes dark and breaks texture sampling on our own course
    geometry in-game.

    Lookup order (first hit wins):
      1. addon preference `default_bin_dir` (= the vanilla ROM dump
         directory the user already configures for vanilla import).
      2. Built-in Dolphin dump conventions probed by
         ``_scene_template_default_dirs()`` -- this gives a working
         default on a stock setup so the user does not have to wire
         up the preference just to get characters lit.
      3. Returns `None` -- the export helper falls back to
         ``alloc_scene_data_minimal()`` and warns loudly.
    """
    candidate_dirs = []
    bin_dir = _vanilla_bin_dir()
    if bin_dir:
        candidate_dirs.append(Path(bin_dir))
    candidate_dirs.extend(_scene_template_default_dirs())
    for d in candidate_dirs:
        for name in _SCENE_TEMPLATE_CANDIDATES:
            cand = d / name
            if cand.is_file():
                return str(cand)
    return None


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
    self.layout.operator("export_scene.mkgp2_hsd_json", text="MKGP2 HSD (.dat)")
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
    MKGP2_OT_HsdAliasAdd,
    MKGP2_OT_HsdAliasRemove,
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
    MKGP2_PT_ActiveTargetPanel,
    MKGP2_PT_CustomCoursePanel,
    MKGP2_PT_VisualizationPanel,
    MKGP2_PT_VanillaCoursePanel,
    MKGP2_PT_PerAssetImportPanel,
    MKGP2_PT_PerAssetExportPanel,
    MKGP2_PT_HsdAliasPanel,
    MKGP2_PT_TextureFormatPanel,
    MKGP2_PT_DevPanel,
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
    # Transient input fields for the HSD alias add row. Stored on
    # WindowManager so they don't pollute .blend files; the panel
    # always shows them blank on first open.
    bpy.types.WindowManager.mkgp2_alias_new_name = StringProperty(
        name="New alias name",
        description=(
            "Public root symbol that game code will look up "
            "(e.g. MR_highway_inu_joint)"
        ),
    )
    bpy.types.WindowManager.mkgp2_alias_new_target = StringProperty(
        name="New alias target",
        description="Target joint id (jobj_<n> from the bundle's joints list)",
    )
    # Per-Material GX texture format picker. Persists in the .blend
    # file as a Material attribute so the choice survives session
    # restart. The shipped exporters (bundle / vis) consult this via
    # `_blender_material.material_target_format(mat)`; default RGBA8
    # keeps untouched .blend files byte-equiv with the pre-UI era.
    from . import _blender_material as _bm
    bpy.types.Material.mkgp2_target_format = EnumProperty(
        name="MKGP2 target texture format",
        description=(
            "GX texture format the exporter will use when re-encoding "
            "this material's BSDF on a fresh-material code path "
            "(vis: course synthesis or bundle hand-added mesh). "
            "Existing vanilla materials take the bypass path and ignore "
            "this property."),
        items=[
            ("RGBA8",  "RGBA8",
             "Lossless, 4 bytes/pixel. Default; matches the vanilla "
             "round-trip byte-equiv guarantee."),
            ("CMP",    "CMP (DXT1)",
             "Lossy compressed, ~ 0.5 bytes/pixel (8x smaller than "
             "RGBA8). Demands 4x4 tile alignment; non-aligned images "
             "silently fall back to RGBA8."),
            ("RGB5A3", "RGB5A3",
             "Compact with 1-bit alpha + 4-bit alpha encoding, "
             "~ 2 bytes/pixel (4x smaller than RGBA8). Quantized."),
        ],
        default=_bm.DEFAULT_TARGET_FORMAT,
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
    if hasattr(bpy.types.WindowManager, "mkgp2_alias_new_name"):
        del bpy.types.WindowManager.mkgp2_alias_new_name
    if hasattr(bpy.types.WindowManager, "mkgp2_alias_new_target"):
        del bpy.types.WindowManager.mkgp2_alias_new_target
    if hasattr(bpy.types.Material, "mkgp2_target_format"):
        del bpy.types.Material.mkgp2_target_format
    bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
    bpy.types.TOPBAR_MT_file_export.remove(_menu_export)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
