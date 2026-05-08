# Vendored binary extensions

The addon's `MKGP2_OT_ExportHSD` operator can use a Rust path
([`hsdraw`](https://github.com/naari3/hsdraw), HSDLib parity) instead
of the dotnet-script + HSDLib path. The Rust path is shipped as
platform-specific wheels extracted into `vendor/<platform>/`.

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

## Falling back to csx

If `hsdraw` is not vendored for your platform (or import fails for
any reason), the operator falls back to running
`tools/hsd/hsd_import_from_blender.csx` via dotnet-script. Set the
addon preference *HSD writer backend* to `csx` to opt in to that
path explicitly.
