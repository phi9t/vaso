# py-scikit-build-core native recipe

## Position in the hillclimb

`py-scikit-build-core@1.0.0` is the next `PythonPackage` source build after
native `py-fsspec` in the lean `py-torch` frontier:

```text
109  py-scikit-build-core  1.0.0  python_pip
```

The focused reference graph for `SPACK_ROOT_PKG='py-scikit-build-core'` has 64
build-graph nodes and 59 Spack lock packages. Spack still owns the DAG shape;
the native flip changes only `spack_py_scikit_build_core.build` to `native` and
re-exports `@py_scikit_build_core_native//:lib`. Generated dependency edges
remain Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-scikit-build-core-1.0.0-<recapture-pending>
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-scikit-build-core-1.0.0-<recapture-pending>/.spack/repos/spack_repo/builtin/packages/py_scikit_build_core/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyScikitBuildCore(PythonPackage)`
- selected build system: `python_pip`
- version: `1.0.0`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/29/e2/4c0431fe84f9d8c24c1fc77b27264f568c71724ca888f514766986f270b0/scikit_build_core-1.0.0.tar.gz`
- source SHA256:
  `b82a8b41dd66926b96096a61e8fc8df22214bbec437d251c0fda1bfb9d7df558`
- homepage: `https://github.com/scikit-build/scikit-build-core`
- license: `Apache-2.0`
- active variant: `pyproject=false`
- concrete build/run dependencies for Python 3.13:
  `cmake@3.31.11`, `python@3.13.13`, `py-packaging@26.2`,
  `py-pathspec@1.1.1`, and `python-venv@1.0`
- concrete build dependencies:
  `py-hatchling@1.29.0`, `py-hatch-vcs@0.5.0`, `py-pip@26.1.2`,
  and `py-wheel@0.45.1`
- Python-version-gated dependencies not present in this graph:
  `py-typing-extensions`, `py-tomli`, `py-importlib-resources`, and
  `py-exceptiongroup`
- patches: none

Spack's install path is the standard PythonPackage pip invocation:

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
  --prefix=<py-scikit-build-core-prefix> \
  .
```

The hermetic build log reported:

```text
Created wheel for scikit_build_core: filename=scikit_build_core-1.0.0-py3-none-any.whl
Successfully installed scikit_build_core-1.0.0
```

Generated wheel records, bytecode, and temporary build paths are not part of
the stable prefix contract.

## Native build recipe

`native/py_scikit_build_core/py_scikit_build_core.bzl` mirrors that install
method directly:

```text
download and extract the exact scikit_build_core-1.0.0 source archive by SHA256
read @cmake_native//:prefix_path.txt
read @python_venv_native//:prefix_path.txt
derive `PYTHON_ABI` from the native python-venv prefix
read @py_hatch_vcs_native//:prefix_path.txt
read @py_hatchling_native//:prefix_path.txt
read @py_packaging_native//:prefix_path.txt
read @py_pathspec_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_pluggy_native//:prefix_path.txt
read @py_setuptools_scm_native//:prefix_path.txt
read @py_setuptools_native//:prefix_path.txt
read @py_trove_classifiers_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
validate native cmake, python-venv, hatch-vcs, hatchling, packaging, pathspec, pip, pluggy, setuptools-scm, setuptools, trove-classifiers, and wheel prefixes under `lib/python${PYTHON_ABI}/site-packages`
clear PYTHONHOME
set CMAKE_EXECUTABLE from the native cmake prefix
set HATCH_METADATA_CLASSIFIERS_NO_VERIFY=1
set PATH from native cmake, pip, wheel, python-venv, and rootfs tool paths
set PYTHONPATH from native hatch-vcs, hatchling, packaging, pathspec, pip, pluggy, setuptools-scm, setuptools, trove-classifiers, wheel, and python-venv under `lib/python${PYTHON_ABI}/site-packages`
PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI} -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate scikit_build_core package modules, CMake resources, typed marker, metadata, entry points, and license
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the wheel build and
prefix install happen only inside the sealed CUDA rootfs. It never searches for
host Spack; all Spack facts come from the Bazel-vendored Spack run, and all
Python packaging tools come from Bazel-native prefixes.

The mechanism verifier is expected to report this build channel:

```text
native/py_scikit_build_core/py_scikit_build_core.bzl: python-pip-install: CMAKE_PREFIX, PYTHON_VENV_PREFIX, PY_HATCH_VCS_PREFIX, PY_HATCHLING_PREFIX, PY_PACKAGING_PREFIX, PY_PATHSPEC_PREFIX, PY_PIP_PREFIX, PY_PLUGGY_PREFIX, PY_SETUPTOOLS_SCM_PREFIX, PY_SETUPTOOLS_PREFIX, PY_TROVE_CLASSIFIERS_PREFIX, PY_WHEEL_PREFIX
```

## Prefix and behavior contract

The stable prefix surface is:

- package modules under `lib/python3.13/site-packages/scikit_build_core`
- resource files such as
  `lib/python3.13/site-packages/scikit_build_core/resources/scikit-build.schema.json`
  and vendored CMake `FindPython` helpers
- metadata, entry points, and license under
  `lib/python3.13/site-packages/scikit_build_core-1.0.0.dist-info`
- `lib/python3.13/site-packages/scikit_build_core/py.typed`

Generated installation metadata that embeds the temporary build path or complete
wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is not part
of the stable parity surface.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_scikit_build_core_native
```

It resolves native `py-scikit-build-core`, native Python, native Python venv,
native `py-packaging`, native `py-pathspec`, and native CMake. It imports the
package through the native Python venv, verifies the installed metadata version,
imports `pathspec`, and forces `scikit_build_core.cmake.CMake.default_search`
to discover CMake through the native `CMAKE_EXECUTABLE`.

The parity target is:

```text
//synthetic:py_scikit_build_core_prefix_parity
```

It compares `@py_scikit_build_core_native//:prefix` against the hermetic Spack
reference prefix and covers:

- selected layout parity for package modules, resources, `.dist-info` metadata,
  entry points, typed marker, and license;
- byte-identical representative package files and metadata;
- empty ELF ABI axis, since the package installs no native shared libraries.

Fresh proof on 2026-09-30 used rootfs mode `cuda-bundle`, Bazel-owned Spack
`1.2.2`, and
`SPACK_ROOT_PKG='py-scikit-build-core@1.0.0 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib'`.
The run regenerated root `spack_py_scikit_build_core`, a recapture-pending lock, and a
recapture-pending build graph. `//synthetic:py_scikit_build_core_prefix_parity` passed
with `ok=true` for all 18 selected `lib/python3.13/site-packages` paths, no
missing or extra candidate paths, byte-identical package files, metadata and
license, and an empty ELF ABI axis. `//synthetic:use_py_scikit_build_core_native`
passed with output `py-scikit-build-core:1.0.0:1.0.0:1.1.1:3.31.11`.
`//synthetic:curl_abi_parity`, `//tools:hermetic_native_deps_guard_test`, and
`//tools:native_build_mechanism_guard_unit_test` passed in the same insula run.

## ODR-sensitive provider invariant

This slice does not add protobuf, gRPC, Abseil, or Boost providers. Those
families must stay on one unified compatible concrete version family across
all companion packages before any native flip. The generator rejects
unqualified overrides and rejects mixed concrete family versions, including
protobuf/Python protobuf and gRPC/gRPC C++ pairings, so downstream consumers do
not combine ABI-incompatible providers.
