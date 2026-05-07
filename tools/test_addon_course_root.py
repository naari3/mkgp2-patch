"""Verify T2c — coordinate root system produces byte-identical
exports regardless of where the user moves the root.

Pipeline:
  1) Import a vanilla course as collection A.
  2) Export A -> baseline .bin set.
  3) Import the same course as collection B, add a course root, drag
     it to (1234, 567, -89) and rotate it 30 degrees.
  4) Export B with root frozen -> compare to baseline byte-for-byte.

  blender --background --python tools/test_addon_course_root.py
"""

import bpy
import math
import sys
import tempfile
import traceback
from pathlib import Path
import mathutils

ADDON_DIR = r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\tools\blender"
PATCH_DIR = Path(r"C:\Users\naari\src\github.com\naari3\mkgp2-patch\features\cup_page3\files")


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


def _import_course(name):
    bpy.ops.scene.mkgp2_import_course(
        'EXEC_DEFAULT',
        name=name,
        collision_path=str(PATCH_DIR / "grd_short.bin"),
        line_path=str(PATCH_DIR / "test_course_short_line.bin"),
        auto_f_path="",
        auto_r_path="",
    )
    return bpy.data.collections[name]


def _export_to(coll, td):
    coll["mkgp2_bin_dir"] = td
    _activate_layer_for(coll)
    result = bpy.ops.scene.mkgp2_export_course('EXEC_DEFAULT')
    assert result == {'FINISHED'}, f"export result: {result}"


def _gather_files(td):
    out = {}
    for p in sorted(Path(td).glob("*.bin")):
        out[p.name] = p.read_bytes()
    return out


def main():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import blender_addon_mkgp2_course as addon
    addon.register()
    addon.reload_modules()
    print("[test] addon registered")

    try:
        # ---- 1+2) Baseline: course A, no root, export ----------------
        coll_a = _import_course("course_a")
        with tempfile.TemporaryDirectory() as td_a:
            _export_to(coll_a, td_a)
            baseline = _gather_files(td_a)
            assert "grd_short.bin" in baseline
            assert "test_course_short_line.bin" in baseline
            print(f"[test] baseline: {sorted(baseline.keys())} "
                  f"sizes={[len(v) for v in baseline.values()]}")

        # Tear down course_a so course_b can re-use the same object names
        # without the ".001" suffix (which would derail
        # `name.endswith('_line')` resolution).
        for o in list(coll_a.all_objects):
            bpy.data.objects.remove(o, do_unlink=True)
        bpy.data.collections.remove(coll_a)

        # ---- 3) Course B with a moved + rotated course root ---------
        coll_b = _import_course("course_b")
        _activate_layer_for(coll_b)
        result = bpy.ops.mkgp2.add_course_root()
        assert result == {'FINISHED'}, f"add_course_root: {result}"

        root = next(o for o in coll_b.objects if o.get("mkgp2_course_root"))
        assert root is not None
        # Confirm parenting actually happened
        n_kids = sum(1 for o in coll_b.objects
                     if o.parent is root and o is not root)
        assert n_kids >= 2, f"expected >=2 children parented, got {n_kids}"
        print(f"[test] root '{root.name}' parents {n_kids} top-level child(ren)")

        # Idempotent: second invocation must not create another root
        bpy.ops.mkgp2.add_course_root()
        roots = [o for o in coll_b.objects if o.get("mkgp2_course_root")]
        assert len(roots) == 1, f"expected 1 root, got {len(roots)}"

        # Drag the root: translate + rotate (deliberately ugly numbers
        # to catch any axis-swap bug)
        rot = mathutils.Euler((math.radians(30), 0, 0), 'XYZ').to_matrix().to_4x4()
        trans = mathutils.Matrix.Translation((1234.0, 567.0, -89.0))
        root.matrix_world = trans @ rot
        bpy.context.view_layer.update()

        # Sanity: a child's matrix_world should now reflect the root.
        child = next(o for o in coll_b.objects
                     if o.parent is root and o.type == 'MESH')
        child_local = child.matrix_local.copy()
        # After update, world should be rooted_xform @ matrix_local
        expected = root.matrix_world @ child_local
        actual = child.matrix_world
        for i in range(4):
            for j in range(4):
                assert abs(expected[i][j] - actual[i][j]) < 1e-4, \
                    f"child world differs from root @ local at [{i}][{j}]"
        print(f"[test] root drag propagates to children "
              f"(child '{child.name}' world reflects offset)")

        # ---- 4) Export B and compare ---------------------------------
        with tempfile.TemporaryDirectory() as td_b:
            _export_to(coll_b, td_b)
            shifted = _gather_files(td_b)

        # Baseline must be present and match shifted byte-for-byte
        assert sorted(baseline) == sorted(shifted), \
            f"file sets differ: A={sorted(baseline)} B={sorted(shifted)}"
        for name in baseline:
            if baseline[name] != shifted[name]:
                # Diff to first byte
                a, b = baseline[name], shifted[name]
                first = next((i for i in range(min(len(a), len(b)))
                              if a[i] != b[i]), -1)
                raise AssertionError(
                    f"{name} differs: lenA={len(a)} lenB={len(b)} "
                    f"first byte mismatch at offset {first}"
                )
        print(f"[test] T2c byte-identical export confirmed for "
              f"{len(baseline)} file(s) despite root offset+rotation")

        # ---- 5) Root world matrix must be restored after export -----
        for i in range(4):
            for j in range(4):
                v = (trans @ rot)[i][j]
                assert abs(root.matrix_world[i][j] - v) < 1e-4, \
                    f"root not restored at [{i}][{j}]: " \
                    f"{root.matrix_world[i][j]} vs {v}"
        print("[test] root matrix_world preserved through export")

        addon.unregister()
        print("[test] PASS")
    except Exception:
        traceback.print_exc()
        print("[test] FAIL")
        sys.exit(1)


main()
