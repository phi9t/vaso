# py-flit-core native recipe

## Position in the hillclimb

`py-flit-core@3.12.0` is the next regular `PythonPackage` source build after
native `nvtx` in the lean `py-torch` frontier:

```text
95  py-cython     3.2.4   python_pip  native
96  nvtx          3.3.0   generic     native
97  py-flit-core  3.12.0  python_pip
```

The focused reference graph for
`SPACK_ROOT_PKG='py-flit-core@3.12.0 ^python@3.13.13+...'` contains a
30-package lock and 35-node build graph. Spack still owns the DAG shape; the
native flip changes only `spack_py_flit_core.build` to `native` and re-exports
`@py_flit_core_native//:lib`. The generated link/runtime edges remain
Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-flit-core-3.12.0-vjkcb3p4z7cjbtr2bf7ky2w3n7vtwb7j
```

Dependency prefixes from the same build environment:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-pip-26.1.2-rjfumck4mkxpheunqnsc4okzhxxq53lv
/vaso/cache/spack/opt/spack/linux-icelake/py-wheel-0.45.1-zkzfjve4om2w4y2nwjai4d2hp53vgnwt
/vaso/cache/spack/opt/spack/linux-icelake/python-venv-1.0-ab2pdnquesi6tr3e5jrieb2itp4jdbab
/vaso/cache/spack/opt/spack/linux-icelake/python-3.13.13-6u37x4adbcqbp247uvyjtokjsun4oewt
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-flit-core-3.12.0-vjkcb3p4z7cjbtr2bf7ky2w3n7vtwb7j/.spack/repos/spack_repo/builtin/packages/py_flit_core/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyFlitCore(PythonPackage)`
- selected build system: `python_pip`
- version: `3.12.0`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/f/flit-core/flit_core-3.12.0.tar.gz`
- source SHA256:
  `18f63100d6f94385c6ed57a72073443e1a71a4acb4339491615d0f16d6ff01b2`
- dependencies: `python` and `python-venv` build/run, plus `py-pip` and
  `py-wheel` build inputs
- install command:

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
  --prefix=<py-flit-core-prefix> \
  .
```

Spack supplies `pip` by putting the `py-pip` prefix on `PYTHONPATH`; the build
log reports:

```text
Using pip 26.1.2 from <py-pip-prefix>/lib/python3.13/site-packages/pip (python 3.13)
```

Pip builds an intermediate wheel from the source tree, then installs that
wheel into the prefix. Generated installation metadata that embeds temporary
stage paths or full file lists, such as `direct_url.json`, `RECORD`, and
bytecode, is not part of the stable parity surface.

## Native build recipe

`native/py_flit_core/py_flit_core.bzl` mirrors that install method directly:

```text
download and extract the exact flit_core-3.12.0 source archive by SHA256
read @python_venv_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
validate PYTHON_VENV_PREFIX/bin/python3
derive PYTHON_ABI from PYTHON_VENV_PREFIX/bin/python3
validate PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI}
validate PY_PIP_PREFIX/lib/python${PYTHON_ABI}/site-packages/pip
validate PY_WHEEL_PREFIX/lib/python${PYTHON_ABI}/site-packages/wheel
clear PYTHONHOME
set PYTHONPATH from native py-pip, py-wheel, and python-venv for PYTHON_ABI
cd <source>
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate flit_core files, vendored tomli files, metadata, and licenses
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the local wheel
build and prefix install happen only inside the sealed CUDA rootfs. It does not
use host Spack, host Python, or host pip.

The mechanism verifier reports the expected build channel:

```text
native/py_flit_core/py_flit_core.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_PIP_PREFIX, PY_WHEEL_PREFIX
```

That verifier checks every prefix-file input, the `python -m pip` invocation
through `PYTHON_VENV_PREFIX`, `PY_PIP_PREFIX` exposure on `PYTHONPATH`, Spack's
no-network/no-build-isolation/no-deps flags, `--prefix`, and clearing
`PYTHONHOME`.

## Prefix and behavior contract

The stable prefix surface includes:

- package tree: `lib/python3.13/site-packages/flit_core`
- vendored tomli tree: `flit_core/vendor/tomli`
- metadata and licenses: `flit_core-3.12.0.dist-info`

Generated installation metadata that embeds temporary stage paths or full file
lists, such as `direct_url.json`, `RECORD`, and bytecode, is not part of the
stable parity surface. The package installs no ELF objects, so the ABI axis is
expected to be empty.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_flit_core_native
```

It resolves the native `py-flit-core`, `python@3.13.13`, `python-venv`,
`py-pip`, and `py-wheel` prefix markers, asserts the runtime ABI is `3.13`,
imports `flit_core`, `flit_core.buildapi`, and vendored `tomli`, and prints:

```text
py-flit-core:3.12.0:3.12.0:flit_core.buildapi:flit_core.vendor.tomli
```

The parity target is:

```text
//synthetic:py_flit_core_prefix_parity
```

It compares `@py_flit_core_native//:prefix` against the hermetic Spack
reference prefix and covers:

- selected layout parity for package files, vendored files, metadata, and
  license files;
- byte-identical representative package, metadata, and license files;
- empty ELF ABI axis.

Current status: native `py-flit-core` is gated inside the hermetic CUDA insula.
The focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
HOME="$HOME" \
TMPDIR=$VASO_ESTATE_ROOT/agents/trae/tmp \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='py-flit-core@3.12.0 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_py_flit_core_native,//synthetic:py_flit_core_prefix_parity,//tools:hermetic_native_deps_guard_test,//tools:native_dep_wiring_live_test' \
VASO_SPACK_TIMEOUT=2400 \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside rootfs mode `cuda-bundle`,
reported hermetic Spack version `1.2.2`, regenerated a 30-package
`spack_graph.lock.json` with root `spack_py_flit_core`, and regenerated a
35-node `build_graph.json`. `//synthetic:py_flit_core_prefix_parity` passed
with `"ok": true`, 20/20 selected layout/data paths, byte-identical selected
package files and dist-info metadata under `lib/python3.13/site-packages`, and
an empty ELF ABI axis. `//synthetic:use_py_flit_core_native` passed with output
`py-flit-core:3.12.0:3.12.0:flit_core.buildapi:flit_core.vendor.tomli`.
`//tools:hermetic_native_deps_guard_test` passed with
`python ABI literal guard passed (158 files)`, and
`//tools:native_dep_wiring_live_test` passed with 15 remaining ticket-08
allowlisted mismatches.
