"""Verify the HSD bundle alias edit operators (Phase 2 v1):
add / remove / re-export round-trip via stashed `mkgp2_joint_aliases`.

Coverage:
  - Import HSD bundle, confirm initial alias dict
  - Add a new alias via scene.mkgp2_hsd_alias_add, confirm dict update
  - Adding a duplicate name overwrites with WARN
  - Bad target id is rejected
  - Remove an alias via scene.mkgp2_hsd_alias_remove, confirm dict shrink
  - Removing a non-existent alias is rejected (CANCELLED)
  - Export with the modified alias set lands the new alias in the .dat

  blender --background --python tools/test_addon_hsd_alias_edit.py
"""

import bpy
import json
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ADDON_DIR = r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\tools\blender"
VANILLA_BIN = r"C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files"
BASE_DAT_NAME = "MR_highway_short_A.dat"


def _ensure_bundle(addon, base_dat, out_dir):
    """Regenerate scene.json via M1+ csx so the bundle has the M2 GX
    metadata the unified exporter requires."""
    csx = addon._resolve_csx_path()
    dotnet = addon._resolve_dotnet_script()
    if not Path(csx).is_file() or dotnet is None:
        raise RuntimeError("dotnet-script / csx unavailable")
    proc = subprocess.run(
        [dotnet, csx, "--", str(base_dat), str(out_dir)],
        capture_output=True, text=True, timeout=240)
    if proc.returncode != 0:
        raise RuntimeError(f"csx failed: {proc.stderr[-500:]}")
    return str(Path(out_dir) / "scene.json")


def _activate_layer_for(coll):
    def find(layer_root, target):
        if layer_root.collection is target:
            return layer_root
        for ch in layer_root.children:
            hit = find(ch, target)
            if hit is not None:
                return hit
        return None
    lc = find(bpy.context.view_layer.layer_collection, coll)
    assert lc is not None
    bpy.context.view_layer.active_layer_collection = lc


def main():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import blender_addon_mkgp2_course as addon
    addon.register()
    addon.reload_modules()
    print("[test] addon registered")

    try:
        if not addon.HSDRAW_AVAILABLE:
            print("[test] SKIP: hsdraw not vendored")
            return
        if addon._resolve_dotnet_script() is None:
            print("[test] SKIP: dotnet-script not found")
            return
        base_dat = Path(VANILLA_BIN) / BASE_DAT_NAME
        if not base_dat.is_file():
            print(f"[test] SKIP: base .dat missing at {base_dat}")
            return
        addon._vanilla_bin_dir = lambda: VANILLA_BIN
        addon._output_bin_dir = lambda: tempfile.gettempdir()

        bundle_dir = tempfile.mkdtemp(prefix="mkgp2_alias_edit_")
        scene_json = _ensure_bundle(addon, base_dat, bundle_dir)
        print(f"[test] regenerated bundle: {scene_json}")

        # ---- Import & resolve bundle ---------------------------------
        result = bpy.ops.import_scene.mkgp2_hsd_json('EXEC_DEFAULT',
                                                     filepath=scene_json)
        assert result == {'FINISHED'}
        bundle = next((c for c in bpy.data.collections
                       if c.name.startswith("mkgp2:")), None)
        assert bundle is not None
        _activate_layer_for(bundle)
        before = json.loads(bundle["mkgp2_joint_aliases"])
        print(f"[test] before: {len(before)} aliases")

        # ---- A) Valid add ---------------------------------------------
        result = bpy.ops.scene.mkgp2_hsd_alias_add(
            'EXEC_DEFAULT',
            name="MR_highway_test_alias",
            target_id="jobj_3",
        )
        assert result == {'FINISHED'}, f"add: {result}"
        d = json.loads(bundle["mkgp2_joint_aliases"])
        assert d["MR_highway_test_alias"] == "jobj_3"
        assert len(d) == len(before) + 1
        print(f"[test] A add: {len(d)} aliases")

        # ---- B) Overwrite (warn but succeed) -------------------------
        result = bpy.ops.scene.mkgp2_hsd_alias_add(
            'EXEC_DEFAULT',
            name="MR_highway_test_alias",
            target_id="jobj_5",
        )
        assert result == {'FINISHED'}
        d = json.loads(bundle["mkgp2_joint_aliases"])
        assert d["MR_highway_test_alias"] == "jobj_5"
        assert len(d) == len(before) + 1  # didn't double-count
        print(f"[test] B overwrite: target now {d['MR_highway_test_alias']}")

        # ---- C) Bad target id (rejected) -----------------------------
        try:
            result = bpy.ops.scene.mkgp2_hsd_alias_add(
                'EXEC_DEFAULT',
                name="MR_highway_evil",
                target_id="jobj_999",
            )
        except RuntimeError:
            result = {'CANCELLED'}
        assert result == {'CANCELLED'}, f"bad target id should reject, got {result}"
        d = json.loads(bundle["mkgp2_joint_aliases"])
        assert "MR_highway_evil" not in d
        print("[test] C bad target id rejected")

        # ---- D) Empty name (rejected) -------------------------------
        try:
            result = bpy.ops.scene.mkgp2_hsd_alias_add(
                'EXEC_DEFAULT', name="   ", target_id="jobj_3",
            )
        except RuntimeError:
            result = {'CANCELLED'}
        assert result == {'CANCELLED'}
        print("[test] D empty name rejected")

        # ---- E) Remove existing -------------------------------------
        result = bpy.ops.scene.mkgp2_hsd_alias_remove(
            'EXEC_DEFAULT', name="MR_highway_test_alias",
        )
        assert result == {'FINISHED'}
        d = json.loads(bundle["mkgp2_joint_aliases"])
        assert "MR_highway_test_alias" not in d
        assert len(d) == len(before)
        print(f"[test] E remove: {len(d)} aliases")

        # ---- F) Remove non-existent (cancel) ------------------------
        try:
            result = bpy.ops.scene.mkgp2_hsd_alias_remove(
                'EXEC_DEFAULT', name="never_existed",
            )
        except RuntimeError:
            result = {'CANCELLED'}
        assert result == {'CANCELLED'}
        print("[test] F remove non-existent canceled")

        # ---- G) Add then export, verify .dat actually contains it ---
        if addon._resolve_dotnet_script() is None:
            print("[test] SKIP G: dotnet-script not found")
            addon.unregister()
            print("[test] PASS")
            return
        result = bpy.ops.scene.mkgp2_hsd_alias_add(
            'EXEC_DEFAULT',
            name="MR_highway_e2e_alias",
            target_id="jobj_4",
        )
        assert result == {'FINISHED'}
        with tempfile.TemporaryDirectory() as td:
            out_dat = os.path.join(td, "out.dat")
            result = bpy.ops.export_scene.mkgp2_hsd_json(
                'EXEC_DEFAULT', filepath=out_dat,
            )
            assert result == {'FINISHED'}
            assert os.path.isfile(out_dat)
            # Spot-check: the new alias name must literally appear in the
            # output bytes (HSDLib writes root names as plain ASCII at the
            # tail of the file). This is a cheap sanity check that doesn't
            # require re-loading via HSDLib.
            data = open(out_dat, "rb").read()
            assert b"MR_highway_e2e_alias" in data, \
                "new alias name missing from output .dat bytes"
            print(f"[test] G end-to-end: alias in .dat ({len(data)} bytes)")

        addon.unregister()
        print("[test] PASS")
    except Exception:
        traceback.print_exc()
        print("[test] FAIL")
        sys.exit(1)


main()
