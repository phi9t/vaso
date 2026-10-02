# py-calver native recipe

## Position in the hillclimb

`py-calver@2025.10.20` is the first regular `PythonPackage` source build after
the Python packaging bootstrap trio in the lean `py-torch` frontier:

```text
88  py-pip        26.1.2      generic     native
89  py-setuptools 79.0.1      generic     native
90  py-wheel      0.45.1      generic     native
91  py-calver     2025.10.20  python_pip
```

The focused reference graph for
`SPACK_ROOT_PKG='py-calver@2025.10.20 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib'`
ends with 36 nodes. Spack still owns the DAG shape; the native flip changes only
`spack_py_calver.build` to `native` and re-exports
`@py_calver_native//:lib`. The generated link/runtime edges remain
Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-calver-2025.10.20-iyr5n23xmhv56wyo5oqsplg742arv4rh
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
/vaso/cache/spack/opt/spack/linux-icelake/py-calver-2025.10.20-iyr5n23xmhv56wyo5oqsplg742arv4rh/.spack/repos/spack_repo/builtin/packages/py_calver/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyCalver(PythonPackage)`
- selected build system: `python_pip`
- version: `2025.10.20`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/c/calver/calver-2025.10.20.tar.gz`
- source SHA256:
  `c98b376c2424642224d456b2f70c51402343e008c63d204634665e1a2a2835f5`
- dependencies: `python` build/run, plus `py-pip`, `py-setuptools`, and
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
  --prefix=<py-calver-prefix> \
  .
```

Spack supplies `pip` by putting the `py-pip` prefix on `PYTHONPATH`; the build
log reports:

```text
Using pip 26.1.2 from <py-pip-prefix>/lib/python3.13/site-packages/pip (python 3.13)
```

Pip builds an intermediate wheel from the source tree, then installs that
wheel into the prefix. The generated wheel hash is not stable across build
directories and is not part of the prefix contract.

## Native build recipe

`native/py_calver/py_calver.bzl` mirrors that install method directly:

```text
download and extract the exact calver-2025.10.20 source archive by SHA256
read @python_venv_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_setuptools_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
validate PYTHON_VENV_PREFIX/bin/python3
derive PYTHON_ABI from PYTHON_VENV_PREFIX/bin/python3
validate PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI}
validate PY_PIP_PREFIX/lib/python${PYTHON_ABI}/site-packages/pip
validate PY_SETUPTOOLS_PREFIX/lib/python${PYTHON_ABI}/site-packages/setuptools
validate PY_WHEEL_PREFIX/lib/python${PYTHON_ABI}/site-packages/wheel
clear PYTHONHOME
set PYTHONPATH from native py-pip, py-setuptools, py-wheel, and python-venv
for PYTHON_ABI
cd <source>
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate calver package files and dist-info metadata
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so both the local wheel
build and the prefix install happen only inside the sealed CUDA rootfs. It
never searches `PATH` for Python or pip; Python comes from the Bazel-native
venv prefix and Python packaging tools come from Bazel-native prefixes on
`PYTHONPATH`.

The mechanism verifier reports the expected build channel:

```text
native/py_calver/py_calver.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_PIP_PREFIX, PY_SETUPTOOLS_PREFIX, PY_WHEEL_PREFIX
```

That verifier also checks every prefix-file input, `python -m pip` invocation
through `PYTHON_VENV_PREFIX`, `PY_PIP_PREFIX` exposure on `PYTHONPATH`, Spack's
no-network/no-build-isolation/no-deps flags, `--prefix`, and clearing
`PYTHONHOME`.

## Prefix and behavior contract

The ABI-relevant prefix surface is a pure Python package prefix:

- package tree: `calver`
- metadata: `calver-2025.10.20.dist-info`
- setuptools entry point: `use_calver = calver.integration:version`

Generated installation metadata that embeds the temporary Spack stage path or
complete wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is
not part of the stable parity surface.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_calver_native
```

It resolves the native `py-calver`, `python`, `python-venv`, `py-pip`,
`py-setuptools`, and `py-wheel` prefix markers, sets the native Python library
path and package `PYTHONPATH`, imports `calver`, verifies the installed version
through `importlib.metadata`, checks deterministic calendar-version generation
with `SOURCE_DATE_EPOCH=0`, and prints:

```text
py-calver:2025.10.20:calver:1970.01.01
```

The parity target is:

```text
//synthetic:py_calver_prefix_parity
```

It compares `@py_calver_native//:prefix` against the hermetic Spack reference
prefix and covers:

- selected layout parity for stable package files and `.dist-info` metadata;
- byte-identical package entry modules and representative metadata files;
- empty ELF ABI axis, since the package installs no native shared libraries.

Current status: native `py-calver` is gated inside the hermetic CUDA insula.
The focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='py-calver@2025.10.20 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_FORCE_FETCH_REPOS='@py_calver_native' \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_py_calver_native,//synthetic:py_calver_prefix_parity,//tools:hermetic_native_deps_guard_test,//tools:native_dep_wiring_live_test' \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside rootfs mode `cuda-bundle`,
reported hermetic Spack version `1.2.2`, regenerated `spack_graph.lock.json`
with root `spack_py_calver` and a 31-package lock, regenerated a 36-node
`build_graph.json` tail `python@3.13.13`, `python-venv@1.0`,
`py-pip@26.1.2`, `py-setuptools@79.0.1`, `py-wheel@0.45.1`,
`py-calver@2025.10.20`, passed `//tools:hermetic_native_deps_guard_test`,
passed `//tools:native_dep_wiring_live_test`, passed
`//synthetic:use_py_calver_native`, and passed
`//synthetic:py_calver_prefix_parity`.
