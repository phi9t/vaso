# py-numpy native recipe

## Position in the hillclimb

`py-numpy@2.4.6` is the current full PyTorch closure Python extension build
after native `py-meson-python@0.19.0` in FC-10's selective replay.

The current CE-9 full graphs use `py-torch@2.14.0` with `~magma ~mkldnn` and
`^py-networkx~default`. The native flip changes only `spack_py_numpy.build` to
`native` and re-exports `@py_numpy_native//:lib`; generated dependency edges
remain Spack-derived.

## Spack Evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyNumpy(PythonPackage)`
- selected build system: `python_pip`
- version: `2.4.6`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/n/numpy/numpy-2.4.6.tar.gz`
- source SHA256:
  `f3a3570c4a2a16746ac2c31a7c7c7b0c186b95ce902e33db6f28094ed7387dda`
- Spack patch: `check_executables.patch` for `@1.20.0:`, copied into
  `native/py_numpy/check_executables.patch`
- Spack patch SHA256:
  `873745d7b547857fcfec9cae90b09c133b42a4f0c23b6c2d84cf37e2dd816604`
- active dependencies: `openblas`, `py-cython`, `py-meson-python`,
  `py-pip`, `py-wheel`, `python`, `python-venv`, plus compiler/rootfs leaves

Spack's install path is the standard PythonPackage pip invocation with NumPy's
Meson config settings:

```text
<python-venv-prefix>/bin/python3 -m pip \
  -vvv \
  --no-input \
  --no-cache-dir \
  --disable-pip-version-check \
  install \
  --no-deps \
  --ignore-installed \
  --no-build-isolation \
  --no-warn-script-location \
  --no-index \
  --prefix=<py-numpy-prefix> \
  --config-settings=builddir=build \
  --config-settings=setup-args=-Dblas=openblas \
  --config-settings=setup-args=-Dlapack=openblas \
  --config-settings=setup-args=-Duse-ilp64=false \
  .
```

## Native Build Recipe

`native/py_numpy/py_numpy.bzl` mirrors that install method directly:

```text
download and extract the exact numpy-2.4.6 source archive by SHA256
apply native/py_numpy/check_executables.patch
read @meson_native//:prefix_path.txt
read @ninja_native//:prefix_path.txt
read @openblas_native//:prefix_path.txt
read @pkgconf_native//:prefix_path.txt
read @py_cython_native//:prefix_path.txt
read @py_meson_python_native//:prefix_path.txt
read @py_packaging_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_pyproject_metadata_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
read @python_313_native//:prefix_path.txt
read @python_venv_native//:prefix_path.txt
derive `PYTHON_ABI` from the native Python prefix
validate native OpenBLAS, pkgconf, Python, Python venv, Cython, meson-python, packaging, pip, pyproject-metadata, and wheel prefixes
clear PYTHONHOME and set PYTHONNOUSERSITE
set PATH from native build tools and Python packaging prefixes
set PYTHONPATH from native Python package prefixes under `lib/python${PYTHON_ABI}/site-packages`
set PKG_CONFIG, PKG_CONFIG_PATH, PKG_CONFIG_LIBDIR, LD_LIBRARY_PATH, include flags, and linker rpaths from native prefixes
PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI} -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> \
  --config-settings=builddir=build \
  --config-settings=setup-args=-Dblas=openblas \
  --config-settings=setup-args=-Dlapack=openblas \
  --config-settings=setup-args=-Duse-ilp64=false .
validate package modules, executable scripts, dist-info metadata, and OpenBLAS linkage
run a small NumPy matmul/determinant import smoke
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the extension build
and prefix install happen only inside the sealed CUDA rootfs. It never searches
for host Spack; all build and Python packaging inputs come from Bazel-native
prefixes.

## Prefix and Behavior Contract

The stable prefix surface is:

- scripts `bin/f2py` and `bin/numpy-config`
- package modules under `lib/python3.13/site-packages/numpy`
- NumPy headers under `lib/python3.13/site-packages/numpy/_core/include`
- CPython extension modules under `numpy/_core`, `numpy/linalg`, and
  `numpy/random`
- metadata under `lib/python3.13/site-packages/numpy-2.4.6.dist-info`

## ABI and Behavior Gate

The smoke target is:

```text
//synthetic:use_py_numpy_native
```

It resolves the native NumPy, Python, Python venv, and OpenBLAS prefixes,
imports NumPy, runs a small matrix multiply and determinant, checks metadata
version, and verifies the build configuration reports OpenBLAS. Expected
output:

```text
py-numpy:2.4.6:numpy._core._multiarray_umath:19.0:-2.0:openblas
```

The parity target is:

```text
//synthetic:py_numpy_prefix_parity
```

It compares `@py_numpy_native//:prefix` against the hermetic Spack reference
prefix injected by `run.sh`, checks selected scripts, headers, metadata and
extension modules, and runs explicit ELF ABI parity for the main NumPy
extension modules. Fresh proof is recorded in
`.scratch/pytorch-frontier-convergence/issues/10-resume-py-frontier.md`.
