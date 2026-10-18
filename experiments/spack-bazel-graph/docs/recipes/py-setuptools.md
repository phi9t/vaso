# py-setuptools native recipe

## Position in the hillclimb

`py-setuptools@79.0.1` is the first Python package installed by an already
available `pip` provider in the lean `py-torch` frontier:

```text
88  py-pip        26.1.2  generic  native
89  py-setuptools 79.0.1  generic
90  py-wheel      0.45.1  generic
```

The focused reference graph for `SPACK_ROOT_PKG='py-setuptools@79.0.1'` ends
with 34 nodes. Spack still owns the DAG shape; the native flip changes only
`spack_py_setuptools.build` to `native` and re-exports
`@py_setuptools_native//:lib`. The generated link/runtime edges remain
Spack-derived; `py-pip` is consumed by the native repository rule as a build
tool prefix because Spack's dependency is build-only.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-setuptools-79.0.1-ahvs37zplvfrbijtnucmlc2upqxl5ijk
```

Dependency prefixes from the same build environment:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-pip-26.1.2-rjfumck4mkxpheunqnsc4okzhxxq53lv
/vaso/cache/spack/opt/spack/linux-icelake/python-venv-1.0-ab2pdnquesi6tr3e5jrieb2itp4jdbab
/vaso/cache/spack/opt/spack/linux-icelake/python-3.13.13-6u37x4adbcqbp247uvyjtokjsun4oewt
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-setuptools-79.0.1-ahvs37zplvfrbijtnucmlc2upqxl5ijk/.spack/repos/spack_repo/builtin/packages/py_setuptools/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PySetuptools(Package, PythonExtension)`
- selected build system: generic package install method
- version: `79.0.1`
- source payload: upstream wheel
  `https://files.pythonhosted.org/packages/py3/s/setuptools/setuptools-79.0.1-py3-none-any.whl`
- wheel SHA256:
  `e147c0549f27767ba362f9da434eab9c5dc0045d5304feb602a0af001089fc51`
- dependencies: `python` build/run and `py-pip` build
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
  --prefix=<py-setuptools-prefix> \
  <wheel>
```

Spack supplies `pip` by putting the `py-pip` prefix on `PYTHONPATH`; the build
log reports:

```text
Using pip 26.1.2 from <py-pip-prefix>/lib/python3.13/site-packages/pip (python 3.13)
```

## Native build recipe

`native/py_setuptools/py_setuptools.bzl` mirrors that install method directly:

```text
download the exact setuptools-79.0.1 wheel by SHA256
read @python_venv_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
validate PYTHON_VENV_PREFIX/bin/python3
derive PYTHON_ABI from PYTHON_VENV_PREFIX/bin/python3
validate PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI}
validate PY_PIP_PREFIX/lib/python${PYTHON_ABI}/site-packages/pip
clear PYTHONHOME
set PYTHONPATH from the native py-pip and python-venv prefixes for PYTHON_ABI
PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI} -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> <wheel>
validate setuptools, pkg_resources, _distutils_hack, distutils-precedence.pth,
and dist-info metadata
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the wheel
installation happens only inside the sealed CUDA rootfs. It never searches
`PATH` for Python or pip; Python comes from the Bazel-native venv prefix and
pip comes from the Bazel-native `py-pip` prefix on `PYTHONPATH`.

The mechanism verifier reports the expected build channel:

```text
native/py_setuptools/py_setuptools.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_PIP_PREFIX
```

That verifier also checks the two-prefix contract, `python -m pip` invocation
through `PYTHON_VENV_PREFIX`, `PY_PIP_PREFIX` exposure on `PYTHONPATH`, Spack's
no-network/no-build-isolation/no-deps flags, `--prefix`, and clearing
`PYTHONHOME`.

## Prefix and behavior contract

The ABI-relevant prefix surface is a Python-package prefix, with no C link
library and no console executable of its own:

- package trees: `setuptools`, `pkg_resources`, and `_distutils_hack`
- import hook marker: `distutils-precedence.pth`
- metadata: `setuptools-79.0.1.dist-info`

Generated installation metadata that embeds the temporary Spack stage path or
complete wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is
not part of the stable parity surface.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_setuptools_native
```

It resolves the native `py-setuptools`, `python`, `python-venv`, and `py-pip`
prefix markers, asserts the derived ABI is `3.13`, sets the native Python
library path and package `PYTHONPATH`,
imports `setuptools`, `pkg_resources`, and `_distutils_hack`, verifies the
installed version through `importlib.metadata`, and prints:

```text
py-setuptools:79.0.1:79.0.1:setuptools:_distutils_hack
```

The parity target is:

```text
//synthetic:py_setuptools_prefix_parity
```

It compares `@py_setuptools_native//:prefix` against the hermetic Spack
reference prefix and covers:

- selected layout parity for stable package files and `.dist-info` metadata;
- byte-identical package entry modules and representative metadata files;
- empty ELF ABI axis, since the wheel installs no native shared libraries.

Current status: native `py-setuptools` is gated inside the hermetic CUDA
insula. The focused verification command was:

```bash
SPACK_ROOT_PKG='py-setuptools@79.0.1 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib' \
VASO_NATIVE=1 \
VASO_SPACK_TIMEOUT=600 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_py_setuptools_native,//synthetic:py_setuptools_prefix_parity,//tools:hermetic_native_deps_guard_test,//tools:native_dep_wiring_live_test' \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside rootfs mode `cuda-bundle`,
reported hermetic Spack version `1.2.2`, regenerated a 29-package
`spack_graph.lock.json` with root `spack_py_setuptools` and a 34-node
`build_graph.json` tail `python@3.13.13`, `python-venv@1.0`, `py-pip@26.1.2`,
`py-setuptools@79.0.1`, passed `//tools:hermetic_native_deps_guard_test`,
passed `//tools:native_dep_wiring_live_test` with 15 remaining ticket-08
allowlisted mismatches, passed
`//synthetic:use_py_setuptools_native`, and passed
`//synthetic:py_setuptools_prefix_parity` with `"ok": true` over the selected
`lib/python3.13/site-packages` layout.
