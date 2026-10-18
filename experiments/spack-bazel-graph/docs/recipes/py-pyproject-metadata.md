# py-pyproject-metadata native recipe

## Position in the hillclimb

`py-pyproject-metadata@0.11.0` is the next `PythonPackage` source build after
native `py-pathspec` in the lean `py-torch` frontier's ticket-08 ABI re-seat
queue:

```text
92  py-mpmath              1.3.0   python_pip  native
93  py-networkx            3.6.1   python_pip  native
94  py-packaging           26.2    python_pip  native
95  py-pathspec            1.1.1   python_pip  native
96  py-pyproject-metadata  0.11.0  python_pip  native
97  py-pyyaml              6.0.3   python_pip  native
```

The focused reference graph for
`SPACK_ROOT_PKG='py-pyproject-metadata@0.11.0 ^python@3.13.13+...'` contains a
32-package lock and 37-node build graph. Spack still owns the DAG shape; the
native flip changes only `spack_py_pyproject_metadata.build` to `native` and
re-exports
`@py_pyproject_metadata_native//:lib`. The generated link/runtime edges remain
Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-pyproject-metadata-0.11.0-d7qaq56st23nrljazujkqzhznnl5dku6
```

Dependency prefixes from the same build environment:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-flit-core-3.12.0-vjkcb3p4z7cjbtr2bf7ky2w3n7vtwb7j
/vaso/cache/spack/opt/spack/linux-icelake/py-packaging-26.2-ay6pupbu4l5xddd2q7peiih7lu357c6e
/vaso/cache/spack/opt/spack/linux-icelake/py-pip-26.1.2-rjfumck4mkxpheunqnsc4okzhxxq53lv
/vaso/cache/spack/opt/spack/linux-icelake/py-wheel-0.45.1-zkzfjve4om2w4y2nwjai4d2hp53vgnwt
/vaso/cache/spack/opt/spack/linux-icelake/python-venv-1.0-ab2pdnquesi6tr3e5jrieb2itp4jdbab
/vaso/cache/spack/opt/spack/linux-icelake/python-3.13.13-6u37x4adbcqbp247uvyjtokjsun4oewt
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-pyproject-metadata-0.11.0-d7qaq56st23nrljazujkqzhznnl5dku6/.spack/repos/spack_repo/builtin/packages/py_pyproject_metadata/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyPyprojectMetadata(PythonPackage)`
- selected build system: `python_pip`
- version: `0.11.0`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/p/pyproject_metadata/pyproject_metadata-0.11.0.tar.gz`
- source SHA256:
  `c72fa49418bb7c5a10f25e050c418009898d1c051721d19f98a6fb6da59a66cf`
- active dependencies for `@0.11.0`: `py-flit-core@3.11:` build,
  `py-packaging@23.2:` build/run, `python@3.8:` build/run, plus
  PythonPackage machinery `python-venv`, `py-pip`, and `py-wheel`
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
  --prefix=<py-pyproject-metadata-prefix> \
  .
```

Spack supplies `pip`, `wheel`, `flit_core`, and the runtime `packaging`
dependency by putting their prefixes on `PYTHONPATH`. The installed wheel
metadata says:

```text
Generator: flit 3.12.0
Root-Is-Purelib: true
Tag: py3-none-any
Requires-Python: >=3.8
Requires-Dist: packaging>=23.2
```

Generated installation metadata that embeds temporary stage paths or complete
wheel file lists, such as `direct_url.json`, `RECORD`, and bytecode, is not
part of the stable parity surface.

## Native build recipe

`native/py_pyproject_metadata/py_pyproject_metadata.bzl` mirrors that install
method directly:

```text
download and extract the exact pyproject_metadata-0.11.0 source archive by SHA256
read @python_venv_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
read @py_flit_core_native//:prefix_path.txt
read @py_packaging_native//:prefix_path.txt
validate PYTHON_VENV_PREFIX/bin/python3
derive PYTHON_ABI from PYTHON_VENV_PREFIX/bin/python3
validate PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI}
validate PY_PIP_PREFIX/lib/python${PYTHON_ABI}/site-packages/pip
validate PY_WHEEL_PREFIX/lib/python${PYTHON_ABI}/site-packages/wheel
validate PY_FLIT_CORE_PREFIX/lib/python${PYTHON_ABI}/site-packages/flit_core
validate PY_PACKAGING_PREFIX/lib/python${PYTHON_ABI}/site-packages/packaging
clear PYTHONHOME
set PYTHONPATH from native py-pip, py-wheel, py-flit-core, py-packaging, and python-venv for PYTHON_ABI
cd <source>
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate package files, dist-info metadata, and license
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the wheel build and
prefix install happen only inside the sealed CUDA rootfs. It never searches for
Python, pip, wheel, flit-core, or packaging on the host; the Python interpreter
comes from the Bazel-native venv prefix and Python package inputs come from
Bazel-native prefixes on `PYTHONPATH`. The Python ABI is derived from the
native venv interpreter and currently resolves to `3.13` for the PyTorch line.

The mechanism verifier reports the expected build channel:

```text
native/py_pyproject_metadata/py_pyproject_metadata.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_FLIT_CORE_PREFIX, PY_PACKAGING_PREFIX, PY_PIP_PREFIX, PY_WHEEL_PREFIX
```

That verifier checks every prefix-file input, the `python -m pip` invocation
through `PYTHON_VENV_PREFIX`, `PY_PIP_PREFIX` exposure on `PYTHONPATH`,
Spack's no-network/no-build-isolation/no-deps flags, `--prefix`, and clearing
`PYTHONHOME`.

## Prefix and behavior contract

The stable prefix surface includes:

- package tree: `lib/python3.13/site-packages/pyproject_metadata`
- metadata and license: `pyproject_metadata-0.11.0.dist-info`

The package installs no ELF objects, so the ABI axis is expected to be empty.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_pyproject_metadata_native
```

It resolves the native `py-pyproject-metadata`, `python@3.13.13`,
`python-venv`, `py-pip`, `py-flit-core`, `py-packaging`, and `py-wheel` prefix
markers, asserts the runtime ABI is `3.13`, sets the native Python library path
and package `PYTHONPATH`, imports `pyproject_metadata.StandardMetadata`, checks
version metadata, and parses a minimal project table with a `packaging`
dependency. Expected output:

```text
py-pyproject-metadata:0.11.0:demo-pkg:1.2.3:packaging
```

The parity target is:

```text
//synthetic:py_pyproject_metadata_prefix_parity
```

It compares `@py_pyproject_metadata_native//:prefix` against the hermetic Spack
reference prefix and covers:

- selected layout parity for stable package files, metadata, and license;
- byte-identical package modules and representative metadata;
- empty ELF ABI axis, since the package installs no native shared libraries.

Current status: native `py-pyproject-metadata` is gated inside the hermetic CUDA
insula. The focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
TMPDIR=$VASO_ESTATE_ROOT/agents/trae/tmp \
SPACK_ROOT_PKG='py-pyproject-metadata@0.11.0 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib' \
VASO_NATIVE=1 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//tools:hermetic_native_deps_guard_test,//tools:python_abi_literal_guard_unit_test,//synthetic:use_py_pyproject_metadata_native,//synthetic:py_pyproject_metadata_prefix_parity' \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside rootfs mode `cuda-bundle`,
reported hermetic Spack version `1.2.2`, regenerated a 32-package
`spack_graph.lock.json` with root `spack_py_pyproject_metadata`, passed
`//tools:hermetic_native_deps_guard_test`, passed
`//tools:python_abi_literal_guard_unit_test`, passed
`//synthetic:use_py_pyproject_metadata_native`, and passed
`//synthetic:py_pyproject_metadata_prefix_parity` with selected stable files
under `lib/python3.13/site-packages` byte-identical to the Spack reference and
an empty ELF ABI axis.

Full proof log:
`$VASO_ESTATE_ROOT/agents/trae/logs/py-pyproject-metadata-insula-proof-full-20260929T194023Z.log`
