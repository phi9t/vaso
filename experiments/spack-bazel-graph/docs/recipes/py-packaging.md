# py-packaging native recipe

## Position in the hillclimb

`py-packaging@26.2` is the next `PythonPackage` source build after native
`py-flit-core` on the ticket-08 Python bootstrap path. In the lean `py-torch`
frontier it appears after native `py-mpmath`:

```text
103  py-jinja2     3.1.6  python_pip  native
104  py-mpmath     1.3.0  python_pip  native
105  py-packaging  26.2   python_pip
```

The focused reference graph for
`SPACK_ROOT_PKG='py-packaging@26.2 ^python@3.13.13+...'` contains a 31-package
lock and 36-node build graph. Spack still owns the DAG shape; the native flip
changes only `spack_py_packaging.build` to `native` and re-exports
`@py_packaging_native//:lib`. The generated link/runtime edges remain
Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-packaging-26.2-ay6pupbu4l5xddd2q7peiih7lu357c6e
```

Dependency prefixes from the same build environment:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-flit-core-3.12.0-vjkcb3p4z7cjbtr2bf7ky2w3n7vtwb7j
/vaso/cache/spack/opt/spack/linux-icelake/py-pip-26.1.2-rjfumck4mkxpheunqnsc4okzhxxq53lv
/vaso/cache/spack/opt/spack/linux-icelake/py-wheel-0.45.1-zkzfjve4om2w4y2nwjai4d2hp53vgnwt
/vaso/cache/spack/opt/spack/linux-icelake/python-venv-1.0-ab2pdnquesi6tr3e5jrieb2itp4jdbab
/vaso/cache/spack/opt/spack/linux-icelake/python-3.13.13-6u37x4adbcqbp247uvyjtokjsun4oewt
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-packaging-26.2-ay6pupbu4l5xddd2q7peiih7lu357c6e/.spack/repos/spack_repo/builtin/packages/py_packaging/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyPackaging(PythonPackage)`
- selected build system: `python_pip`
- version: `26.2`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/p/packaging/packaging-26.2.tar.gz`
- source SHA256:
  `ff452ff5a3e828ce110190feff1178bb1f2ea2281fa2075aadb987c2fb221661`
- dependencies for `@26.2`: `py-flit-core@3.12:` build,
  `python@3.7:` build/run, plus PythonPackage machinery `python-venv`,
  `py-pip`, and `py-wheel`
- inactive historical dependency branches: older releases use setuptools,
  pyparsing, six, or attrs; none are active for `@26.2`
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
  --prefix=<py-packaging-prefix> \
  .
```

Spack supplies `pip`, `wheel`, and `flit_core` by putting their prefixes on
`PYTHONPATH`. The installed wheel metadata says:

```text
Generator: flit 3.12.0
Root-Is-Purelib: true
Tag: py3-none-any
Requires-Python: >=3.8
```

Generated installation metadata that embeds temporary stage paths or complete
wheel file lists, such as `direct_url.json`, `RECORD`, and bytecode, is not
part of the stable parity surface.

## Native build recipe

`native/py_packaging/py_packaging.bzl` mirrors that install method directly:

```text
download and extract the exact packaging-26.2 source archive by SHA256
read @python_venv_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
read @py_flit_core_native//:prefix_path.txt
validate PYTHON_VENV_PREFIX/bin/python3
derive PYTHON_ABI from PYTHON_VENV_PREFIX/bin/python3
validate PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI}
validate PY_PIP_PREFIX/lib/python${PYTHON_ABI}/site-packages/pip
validate PY_WHEEL_PREFIX/lib/python${PYTHON_ABI}/site-packages/wheel
validate PY_FLIT_CORE_PREFIX/lib/python${PYTHON_ABI}/site-packages/flit_core
clear PYTHONHOME
set PYTHONPATH from native py-pip, py-wheel, py-flit-core, and python-venv for PYTHON_ABI
cd <source>
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate package files, dist-info metadata, and licenses
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the wheel build and
prefix install happen only inside the sealed CUDA rootfs. It never searches for
Python, pip, wheel, or flit-core on the host; the Python interpreter comes from
the Bazel-native venv prefix and Python package inputs come from Bazel-native
prefixes on `PYTHONPATH`.

The mechanism verifier reports the expected build channel:

```text
native/py_packaging/py_packaging.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_FLIT_CORE_PREFIX, PY_PIP_PREFIX, PY_WHEEL_PREFIX
```

That verifier checks every prefix-file input, the `python -m pip` invocation
through `PYTHON_VENV_PREFIX`, `PY_PIP_PREFIX` exposure on `PYTHONPATH`,
Spack's no-network/no-build-isolation/no-deps flags, `--prefix`, and clearing
`PYTHONHOME`.

## Prefix and behavior contract

The stable prefix surface includes:

- package tree: `lib/python3.13/site-packages/packaging`
- metadata and licenses: `packaging-26.2.dist-info`

The package installs no ELF objects, so the ABI axis is expected to be empty.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_packaging_native
```

It resolves the native `py-packaging`, `python@3.13.13`, `python-venv`,
`py-pip`, `py-flit-core`, and `py-wheel` prefix markers, asserts the runtime
ABI is `3.13`, sets the native Python library path and package `PYTHONPATH`,
imports `packaging`, checks version metadata, evaluates a version comparison,
evaluates a specifier set, and inspects the active interpreter tag. Expected
output:

```text
py-packaging:26.2:1:True:cp313
```

The parity target is:

```text
//synthetic:py_packaging_prefix_parity
```

It compares `@py_packaging_native//:prefix` against the hermetic Spack
reference prefix and covers:

- selected layout parity for stable package files, metadata, and licenses;
- byte-identical package modules and representative metadata;
- empty ELF ABI axis, since the package installs no native shared libraries.

Current status: native `py-packaging` is gated inside the hermetic CUDA insula.
The focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
HOME="$HOME" \
TMPDIR=$VASO_ESTATE_ROOT/agents/trae/tmp \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='py-packaging@26.2 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_py_packaging_native,//synthetic:py_packaging_prefix_parity,//tools:hermetic_native_deps_guard_test,//tools:native_dep_wiring_live_test' \
VASO_SPACK_TIMEOUT=2400 \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside rootfs mode `cuda-bundle`,
reported hermetic Spack version `1.2.2`, regenerated a 31-package
`spack_graph.lock.json` with root `spack_py_packaging`, and regenerated a
36-node `build_graph.json`. `//synthetic:py_packaging_prefix_parity` passed
with `"ok": true`, 16/16 selected layout/data paths, byte-identical selected
package files and dist-info metadata under `lib/python3.13/site-packages`, and
an empty ELF ABI axis. `//synthetic:use_py_packaging_native` passed with output
`py-packaging:26.2:1:True:cp313`. `//tools:hermetic_native_deps_guard_test`
passed with `python ABI literal guard passed (158 files)`, and
`//tools:native_dep_wiring_live_test` passed with 15 remaining ticket-08
allowlisted mismatches.
