# py-gast native recipe

## Position in the hillclimb

`py-gast@0.6.0` is a regular `PythonPackage` source build in the lean
`py-torch` frontier, re-seated on the decided `python@3.13.13` PyTorch line
after the `py-fonttools` ticket-08 slice:

```text
97  py-flit-core  3.12.0  python_pip  native
98  py-fonttools  4.39.4  python_pip  native
99  py-gast       0.6.0   python_pip  native
```

The focused reference graph for
`SPACK_ROOT_PKG='py-gast@0.6.0 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib'`
ends with 31 packages and a 36-node `build_graph.json`. Spack still owns the
DAG shape; the native flip changes only `spack_py_gast.build` to `native` and
re-exports `@py_gast_native//:lib`. The generated link/runtime edges remain
Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-gast-0.6.0-z5lwihe7nljygllyvnxpf47jgptqukct
```

Dependency prefixes from the same build environment:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-pip-26.1.2-rjfumck4mkxpheunqnsc4okzhxxq53lv
/vaso/cache/spack/opt/spack/linux-icelake/py-setuptools-79.0.1-ahvs37zplvfrbijtnucmlc2upqxl5ijk
/vaso/cache/spack/opt/spack/linux-icelake/py-wheel-0.45.1-zkzfjve4om2w4y2nwjai4d2hp53vgnwt
/vaso/cache/spack/opt/spack/linux-icelake/python-venv-1.0-ab2pdnquesi6tr3e5jrieb2itp4jdbab
/vaso/cache/spack/opt/spack/linux-icelake/python-3.13.13-6u37x4adbcqbp247uvyjtokjsun4oewt
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-gast-0.6.0-z5lwihe7nljygllyvnxpf47jgptqukct/.spack/repos/spack_repo/builtin/packages/py_gast/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyGast(PythonPackage)`
- selected build system: `python_pip`
- version: `0.6.0`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/g/gast/gast-0.6.0.tar.gz`
- source SHA256:
  `88fc5300d32c7ac6ca7b515310862f71e6fdf2c029bbec7c66c0f5dd47b6b1fb`
- package.py dependency: `py-setuptools` build input
- concretized PythonPackage machinery: `python`, `python-venv`, `py-pip`,
  `py-setuptools`, and `py-wheel`
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
  --prefix=<py-gast-prefix> \
  .
```

Spack supplies `pip` by putting the `py-pip` prefix on `PYTHONPATH`; the build
log reports:

```text
Using pip 26.1.2 from <py-pip-prefix>/lib/python3.13/site-packages/pip (python 3.13)
```

Pip builds an intermediate pure-Python wheel from the source tree, then
installs that wheel into the prefix. Generated installation metadata that
embeds temporary stage paths or full file lists, such as `direct_url.json`,
`RECORD`, and bytecode, is not part of the stable parity surface.

## Native build recipe

`native/py_gast/py_gast.bzl` mirrors that install method directly:

```text
download and extract the exact gast-0.6.0 source archive by SHA256
read @python_venv_native//:prefix_path.txt
derive PYTHON_ABI from PYTHON_VENV_PREFIX/bin/python3
read @py_pip_native//:prefix_path.txt
read @py_setuptools_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
validate PYTHON_VENV_PREFIX/bin/python3
validate PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI}
validate PY_PIP_PREFIX/lib/python${PYTHON_ABI}/site-packages/pip
validate PY_SETUPTOOLS_PREFIX/lib/python${PYTHON_ABI}/site-packages/setuptools
validate PY_WHEEL_PREFIX/lib/python${PYTHON_ABI}/site-packages/wheel
clear PYTHONHOME
set PYTHONPATH from native py-pip, py-setuptools, py-wheel, and python-venv
using lib/python${PYTHON_ABI}/site-packages
cd <source>
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate gast package files, dist-info metadata, and license
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so both the local wheel
build and the prefix install happen only inside the sealed CUDA rootfs. It
never searches `PATH` for Python or pip; Python comes from the Bazel-native
venv prefix and Python packaging tools come from Bazel-native prefixes on
`PYTHONPATH`.

The mechanism verifier reports the expected build channel:

```text
native/py_gast/py_gast.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_PIP_PREFIX, PY_SETUPTOOLS_PREFIX, PY_WHEEL_PREFIX
```

That verifier also checks every prefix-file input, `python -m pip` invocation
through `PYTHON_VENV_PREFIX`, `PY_PIP_PREFIX` exposure on `PYTHONPATH`, Spack's
no-network/no-build-isolation/no-deps flags, `--prefix`, and clearing
`PYTHONHOME`.

## Prefix and behavior contract

The stable prefix surface includes:

- package tree: `lib/python3.13/site-packages/gast`
- metadata and license: `gast-0.6.0.dist-info`

Generated installation metadata that embeds the temporary Spack stage path or
complete wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is
not part of the stable parity surface. The package installs no ELF objects, so
the ABI axis is expected to be empty.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_gast_native
```

It resolves the native `py-gast`, `python@3.13`, `python-venv`, `py-pip`,
`py-setuptools`, and `py-wheel` prefix markers, derives the Python ABI from the
native Python prefix at runtime, asserts `3.13`, sets the native Python library
path and package `PYTHONPATH`, imports `gast`, verifies package metadata, round
trips a simple Python AST through `gast.ast_to_gast` and `gast.gast_to_ast`,
and prints:

```text
py-gast:0.6.0:Assign:Add:x = 1 + 2
```

The parity target is:

```text
//synthetic:py_gast_prefix_parity
```

It compares `@py_gast_native//:prefix` against the hermetic Spack reference
prefix and covers:

- selected layout parity for stable package files and metadata under
  `lib/python3.13/site-packages`;
- byte-identical package modules, representative metadata, and license files;
- empty ELF ABI axis, since the package installs no native shared libraries.

Current status: native `py-gast` is re-seated on `python@3.13.13` and gated
inside the hermetic CUDA insula. The focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
  TMPDIR=$VASO_ESTATE_ROOT/agents/trae/tmp \
  VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
  BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
  SPACK_ROOT_PKG='py-gast@0.6.0 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib' \
  VASO_NATIVE=1 \
  VASO_SPACK_TIMEOUT=600 \
  VASO_EXTRA_TEST_TARGETS='//synthetic:use_py_gast_native,//synthetic:py_gast_prefix_parity,//tools:hermetic_native_deps_guard_test,//tools:native_dep_wiring_live_test,//tools:native_build_mechanism_guard_unit_test,//tools:python_abi_literal_guard_unit_test' \
  ./run.sh
```

The run log is
`$VASO_ESTATE_ROOT/agents/trae/logs/py-gast-rerun-20260929T054516Z.log`.
It used Bazel's `@spack_dist//:spack` inside rootfs mode `cuda-bundle`,
reported hermetic Spack version `1.2.2`, regenerated `spack_graph.lock.json`
with root `spack_py_gast`, regenerated a 36-node `build_graph.json` ending in
`python@3.13.13`, `python-venv@1.0`, `py-pip@26.1.2`,
`py-setuptools@79.0.1`, `py-wheel@0.45.1`, and `py-gast@0.6.0`, and passed
`//tools:native_build_mechanism_guard_unit_test`,
`//tools:hermetic_native_deps_guard_test`,
`//tools:native_dep_wiring_live_test`, `//synthetic:use_py_gast_native`, and
`//synthetic:py_gast_prefix_parity`.
The parity report had `"ok": true`, 11/11 selected layout/data paths, no
missing or extra candidate paths, byte-identical package files, metadata, and
license, and an empty ELF ABI axis.
