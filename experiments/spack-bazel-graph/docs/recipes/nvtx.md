# nvtx native recipe

## Position in the hillclimb

`nvtx@3.3.0+python` is the next node after native `py-cython` in the
Python 3.13 re-seat pass over the pruned lean `py-torch` frontier:

```text
85  py-charset-normalizer  3.4.4  python_pip  native
86  py-cython              3.2.4  python_pip  native
87  nvtx                   3.3.0  generic     native
```

The focused reference graph for `SPACK_ROOT_PKG='nvtx@3.3.0
^python@3.13.13'` has 37 nodes and ends with focused topo index 36. Spack still
owns the DAG shape; the native flip changes only
`spack_nvtx.build` to `native` and re-exports `@nvtx_native//:lib`. The
generated link/runtime edges remain Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/nvtx-3.3.0-drvucsc5hopo3yp3yklcgkq6khxapmxz
```

Dependency prefixes from the same build environment:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-cython-3.2.4-vdqgtwe3kxzopl2eumr4hl4tvohqmegi
/vaso/cache/spack/opt/spack/linux-icelake/py-pip-26.1.2-rjfumck4mkxpheunqnsc4okzhxxq53lv
/vaso/cache/spack/opt/spack/linux-icelake/py-setuptools-79.0.1-ahvs37zplvfrbijtnucmlc2upqxl5ijk
/vaso/cache/spack/opt/spack/linux-icelake/py-wheel-0.45.1-zkzfjve4om2w4y2nwjai4d2hp53vgnwt
/vaso/cache/spack/opt/spack/linux-icelake/python-venv-1.0-ab2pdnquesi6tr3e5jrieb2itp4jdbab
/vaso/cache/spack/opt/spack/linux-icelake/python-3.13.13-6u37x4adbcqbp247uvyjtokjsun4oewt
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/nvtx-3.3.0-drvucsc5hopo3yp3yklcgkq6khxapmxz/.spack/repos/spack_repo/builtin/packages/nvtx/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `Nvtx(Package, PythonExtension)`
- selected build system: `generic`
- version: `3.3.0`
- variant: `+python`
- source payload: GitHub release archive
  `https://github.com/NVIDIA/NVTX/archive/refs/tags/v3.3.0.tar.gz`
- source SHA256:
  `67d0cda2f9d19a89684592dab40c0bf2c2b13d5d588e51392076c0890a64b6c0`
- Spack patch: `nvtx-config.patch` adds a top-level `nvtx-config.cmake`
- dependencies: `python`, `python-venv`, `py-cython`, `py-pip`,
  `py-setuptools`, and `py-wheel`
- install command for the Python subpackage:

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
  --prefix=<nvtx-prefix> \
  .
```

Before pip, Spack copies `c/include` to `<prefix>/include`, installs
`c/CMakeLists.txt`, `c/nvtxImportedTargets.cmake`, `LICENSE.txt`, and the
patched `nvtx-config.cmake`, then runs pip from the source tree's `python/`
directory. The Spack patch also rewrites `python/setup.py` so extension modules
compile against the installed `<prefix>/include`.

## Native build recipe

`native/nvtx/nvtx.bzl` mirrors that install method directly:

```text
download and extract the exact NVTX-3.3.0 release archive by SHA256
read @python_313_native//:prefix_path.txt
read @python_venv_native//:prefix_path.txt
read @py_cython_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_setuptools_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
derive PYTHON_ABI from PYTHON_PREFIX/bin/python3
validate all six prefixes before invoking pip
copy c/include, c/CMakeLists.txt, c/nvtxImportedTargets.cmake, and LICENSE.txt
create the patched top-level nvtx-config.cmake
rewrite python/setup.py include_dirs to <prefix>/include
clear PYTHONHOME
set PYTHONPATH from native py-pip, py-setuptools, py-wheel, py-cython, and python-venv
set LD_LIBRARY_PATH, CFLAGS, and LDFLAGS from native Python and the nvtx prefix
cd <source>/python
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate headers, CMake files, Python files, metadata, and two extension modules
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the extension build
and prefix install happen only inside the sealed CUDA rootfs. It does not use
host Spack, host Python, or host pip.

The mechanism verifier reports the expected build channel:

```text
native/nvtx/nvtx.bzl: python-pip-install: PYTHON_PREFIX, PYTHON_VENV_PREFIX, PY_CYTHON_PREFIX, PY_PIP_PREFIX, PY_SETUPTOOLS_PREFIX, PY_WHEEL_PREFIX
```

That verifier checks every prefix-file input, the `python -m pip` invocation
through `PYTHON_VENV_PREFIX`, `PY_PIP_PREFIX` exposure on `PYTHONPATH`, Spack's
no-network/no-build-isolation/no-deps flags, `--prefix`, and clearing
`PYTHONHOME`.

## Prefix and behavior contract

The stable prefix surface includes:

- header tree: `include/nvtx3`
- CMake files: `CMakeLists.txt`, `nvtxImportedTargets.cmake`,
  `nvtx-config.cmake`
- license: `LICENSE.txt`
- Python package tree: `lib/python3.13/site-packages/nvtx`
- metadata: `nvtx-0.2.14a1.dist-info`
- CPython extension modules:
  `nvtx/_lib/lib.cpython-313-x86_64-linux-gnu.so` and
  `nvtx/_lib/profiler.cpython-313-x86_64-linux-gnu.so`

Generated installation metadata that embeds temporary stage paths or full file
lists, such as `direct_url.json`, `RECORD`, and bytecode, is not part of the
stable parity surface.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_nvtx_native
```

It resolves the native `nvtx`, `python`, `python-venv`, `py-cython`,
`py-pip`, `py-setuptools`, and `py-wheel` prefix markers, imports `nvtx` and
both compiled extension modules, exercises `nvtx.annotate`, and compiles a C
translation unit against `include/nvtx3/nvToolsExt.h`. It prints:

```text
nvtx:0.2.14a1:.cpython-313-x86_64-linux-gnu.so:annotate:nvtx._lib.lib:nvtx._lib.profiler
```

The parity target is:

```text
//synthetic:nvtx_prefix_parity
```

It compares `@nvtx_native//:prefix` against the hermetic Spack reference prefix
and covers:

- selected layout parity for C headers, CMake files, Python files, metadata,
  and extension modules;
- byte-identical representative C/CMake/Python files and metadata;
- NEEDED, SONAME, and exported-symbol parity for both CPython extension
  modules.

Current status: native `nvtx` is gated inside the hermetic CUDA insula. The
focused verification command was:

```bash
TMPDIR=$VASO_ESTATE_ROOT/agents/trae/tmp \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='nvtx@3.3.0 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib' \
VASO_NATIVE=1 \
VASO_SPACK_TIMEOUT=600 \
VASO_EXTRA_TEST_TARGETS='//synthetic:nvtx_prefix_parity,//synthetic:use_nvtx_native,//tools:hermetic_native_deps_guard_test,//tools:native_dep_wiring_live_test' \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside rootfs mode `cuda-bundle`,
reported hermetic Spack version `1.2.2`, regenerated `spack_graph.lock.json`
with root `spack_nvtx` and a 37-node build graph ending in
`python@3.13.13`, `python-venv@1.0`, `py-pip@26.1.2`,
`py-setuptools@79.0.1`, `py-wheel@0.45.1`, `py-cython@3.2.4`, and
`nvtx@3.3.0`. It passed `//tools:hermetic_native_deps_guard_test`,
`//tools:native_dep_wiring_live_test`, `//synthetic:use_nvtx_native`, and
`//synthetic:nvtx_prefix_parity`; the full log is
`$VASO_ESTATE_ROOT/agents/trae/logs/nvtx-rerun-20260929T080023Z.log`.
