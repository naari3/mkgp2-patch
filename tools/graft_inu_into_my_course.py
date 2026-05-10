"""Graft the inu (= dog) mesh from MR_highway_short_A_inu_aliased.dat into
my_course.dat as a child JObj of `my_course_joint`.

Why this exists
---------------
The vis: → my_course.dat promote pipeline (`_promote_vis_to_hsd.py`) only
emits the meshes it sees in the `vis:my_course` Blender collection.  The
inu (a 155-DObj textured asset extracted from a separate modded MR_highway
.dat) lives outside that collection — running promote will overwrite
my_course.dat without it.

This script re-applies the inu graft on top of the latest my_course.dat,
in a fully self-contained way (no Blender required, just hsdraw):

  1. Parse MR_highway_short_A_inu_aliased.dat (= the user's existing
     "MR_highway with inu added" mod, used as the inu source).
  2. Take the FIRST child JObj of root `MR_highway_inu_joint` (= the
     155-DObj 0x001c0000 leaf carrying the inu mesh + textures).
  3. Detach that JObj's `.next` chain so the original MR_highway road
     copies aren't pulled along (the source root chains them as siblings;
     we want only the dog).
  4. Parse my_course.dat (= the user's promoted course, possibly freshly
     re-emitted by the vis: pipeline so this script needs to be rerun).
  5. Replace `my_course_joint`'s child chain with the cleaned inu JObj,
     so the inu becomes the sole sub-tree under my_course_joint while
     my_course's own DObjs (still attached directly to my_course_joint)
     keep rendering.
  6. Write my_course.dat back.

Coordinates: the inu mesh's local TRS is identity (tx=ty=tz=0,
sx=sy=sz=1), so it lands at my_course_joint's world origin — same place
it appears in the MR_highway+inu source.  If it ends up off-map relative
to my_course's start gate, set `inu_mesh.tx/ty/tz` here before grafting.

Run
---
    PYTHONIOENCODING=utf-8 python tools/graft_inu_into_my_course.py

Then `bash build.sh` to sync the .dat into the Riivolution mod dir
(`features/cup_page3/files/*.dat` → `<Dolphin>/Load/Riivolution/mkgp2_patch/`).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(
    0,
    str(REPO_ROOT / "tools" / "blender" / "blender_addon_mkgp2_course"
        / "vendor" / "windows_x86_64"),
)

import hsdraw  # noqa: E402

INU_SRC = Path(r"C:\Users\naari\Documents\blender\MR_highway_short_A_inu_aliased.dat")
INU_ROOT = "MR_highway_inu_joint"
MYC_DAT = REPO_ROOT / "features" / "cup_page3" / "files" / "my_course.dat"
MYC_ROOT = "my_course_joint"


def _find_root(dat: "hsdraw.Dat", name: str) -> "hsdraw.JObj":
    for r in dat.roots():
        if r.name == name:
            return hsdraw.JObj.from_struct(r.data)
    raise SystemExit(f"root {name!r} not found")


def _count_chain_dobjs(jobj) -> int:
    """Count DObjs reachable from `jobj` and its descendants (child + next)."""
    visited = {}

    def collect(j):
        if j is None:
            return
        raw = bytes(j.as_struct().raw())
        if raw in visited:
            return
        visited[raw] = j
        try:
            collect(j.child)
        except Exception:
            pass
        try:
            collect(j.next)
        except Exception:
            pass

    collect(jobj)
    n = 0
    for j in visited.values():
        for off, ref in j.as_struct().references():
            if off == 16:
                d = hsdraw.DObj.from_struct(ref)
                while d is not None:
                    n += 1
                    d = d.next
    return n


def main() -> None:
    if not INU_SRC.exists():
        raise SystemExit(f"inu source missing: {INU_SRC}")
    if not MYC_DAT.exists():
        raise SystemExit(f"my_course.dat missing: {MYC_DAT}")

    print(f"inu source : {INU_SRC.name}")
    print(f"my_course  : {MYC_DAT.relative_to(REPO_ROOT)}")

    # 1) Get inu mesh JObj, detach its next chain so MR_highway road copies
    #    (= siblings of the inu mesh in the source's child.next chain)
    #    aren't pulled along.
    inu_dat = hsdraw.parse_dat(INU_SRC.read_bytes())
    inu_root = _find_root(inu_dat, INU_ROOT)
    inu_mesh = inu_root.child
    if inu_mesh is None:
        raise SystemExit(f"{INU_ROOT} has no child JObj (= inu mesh)")
    print(f"  inu_mesh    : flags=0x{inu_mesh.flags:08x}  "
          f"trs=({inu_mesh.tx}, {inu_mesh.ty}, {inu_mesh.tz})")
    inu_mesh.set_next(None)

    # 2) Open my_course.dat and replace my_course_joint's child chain.
    myc_dat = hsdraw.parse_dat(MYC_DAT.read_bytes())
    myc_root = _find_root(myc_dat, MYC_ROOT)
    print(f"  myc_root    : flags=0x{myc_root.flags:08x}  "
          f"DObjs(direct)={_count_chain_dobjs(myc_root) - _count_chain_dobjs(myc_root.child) if myc_root.child else _count_chain_dobjs(myc_root)}")

    myc_root.set_child(inu_mesh)

    # 3) Write back.
    out = bytes(myc_dat.write())
    MYC_DAT.write_bytes(out)
    n = _count_chain_dobjs(myc_root)
    print(f"\nwrote {MYC_DAT.name}: {len(out):,} bytes  "
          f"({n} reachable DObjs under {MYC_ROOT})")
    print("  → run `bash build.sh` to sync into Riivolution mod dir.")


if __name__ == "__main__":
    main()
