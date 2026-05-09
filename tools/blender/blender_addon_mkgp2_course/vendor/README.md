# Vendored binary extensions

The addon's HSD pipeline (`MKGP2_OT_ImportHSD` / `MKGP2_OT_ExportHSD`)
runs entirely on the Rust [`hsdraw`](https://github.com/naari3/hsdraw)
crate's PyO3 binding. There is no dotnet-script / HSDLib fallback;
both directions read and write `.dat` directly via `hsdraw.parse_dat`,
`hsdraw.export_scene_json`, `hsdraw.gx_decode`, and the writer
helpers (`Dat.alloc_scene_data` etc.). The native extension is
shipped as platform-specific wheels extracted into `vendor/<platform>/`.

Layout:

```
vendor/
  README.md                       (this file)
  windows_x86_64/
    hsdraw/                       extracted wheel package
      __init__.py
      hsdraw.pyd                  the abi3 native extension
    hsdraw-0.0.1.dist-info/
  linux_x86_64/                   (not yet shipped)
  linux_aarch64/                  (not yet shipped)
  macos_x86_64/                   (not yet shipped)
  macos_arm64/                    (not yet shipped)
```

The `__init__.py` of the addon adds the matching `vendor/<platform>/`
to `sys.path` before any other import, so `import hsdraw` resolves to
the vendored copy. abi3-py37 wheels mean any CPython 3.7+ (including
Blender 4.x's bundled 3.11) can load it without rebuilding.

## Re-building / refreshing the wheel

Until the upstream `hsdraw` repo cuts a release, vendor refresh is
manual:

```bash
# in hsdraw repo (currently expected at ~/src/github.com/naari3/hsdraw/)
python -m maturin build --release --strip --features pyo3/extension-module

# extract the produced wheel into the matching vendor dir
python - <<'PY'
import zipfile
src = r"C:/Users/.../hsdraw/target/wheels/hsdraw-*.whl"  # latest
dst = r"C:/.../mkgp2-patch/tools/blender/blender_addon_mkgp2_course/vendor/windows_x86_64"
import glob, os, shutil
src = glob.glob(src)[-1]
shutil.rmtree(os.path.join(dst, "hsdraw"), ignore_errors=True)
shutil.rmtree(os.path.join(dst, "hsdraw-0.0.1.dist-info"), ignore_errors=True)
with zipfile.ZipFile(src) as z:
    z.extractall(dst)
PY
```

## What if hsdraw is missing for your platform?

If `import hsdraw` fails (the platform-specific wheel hasn't been
shipped yet, or the `.pyd` is locked because Blender is open while
you swap it in), the HSD operators report `SKIP: hsdraw not vendored`
and refuse to run. There is no dotnet-script / HSDLib fallback any
more (M3-era retirement, see CLAUDE.md). Build a wheel for your host
following the *Re-building / refreshing the wheel* section above.

The `tools/hsd/*.csx` files still ship in the parent repo and are
useful as a parity oracle (= ground truth for hsdraw round-trip
tests), but the Blender addon never invokes them.
