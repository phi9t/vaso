# py-cycler native recipe

## Position in the hillclimb

`py-cycler@0.12.1` is the next regular `PythonPackage` source build after
native `py-charset-normalizer` in the lean `py-torch` frontier, re-seated on
the decided `python@3.13.13` PyTorch line:

```text
91  py-calver              2025.10.20  python_pip  native
92  py-certifi             2026.2.25   python_pip  native
93  py-charset-normalizer  3.4.4       python_pip  native
94  py-cycler              0.12.1      python_pip  native
```

The focused reference graph for
`SPACK_ROOT_PKG='py-cycler@0.12.1 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib'`
ends with 31 packages and a 36-node `build_graph.json`. Spack still owns the
DAG shape; the native flip changes only `spack_py_cycler.build` to `native`
and re-exports `@py_cycler_native//:lib`. The generated link/runtime edges
remain Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-cycler-0.12.1-ejizh7bsy72jur5gq4vfwesfgpwvo2hu
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
/vaso/cache/spack/opt/spack/linux-icelake/py-cycler-0.12.1-ejizh7bsy72jur5gq4vfwesfgpwvo2hu/.spack/repos/spack_repo/builtin/packages/py_cycler/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyCycler(PythonPackage)`
- selected build system: `python_pip`
- version: `0.12.1`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/c/cycler/cycler-0.12.1.tar.gz`
- source SHA256:
  `88bb128f02ba341da8ef447245a9e138fae777f6a23943da4540077d3601eb1c`
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
  --prefix=<py-cycler-prefix> \
  .
```

Spack supplies `pip` by putting the `py-pip` prefix on `PYTHONPATH`. The
3.13 parity proof also validates the upstream `py-pip` provider's own
behavior as:

```text
pip 26.1.2 from ${ABI_PARITY_PREFIX}/lib/python3.13/site-packages/pip (python 3.13)
```

Pip builds an intermediate wheel from the source tree, then installs that
wheel into the prefix. The generated wheel hash is not stable across build
directories and is not part of the prefix contract.

## Native build recipe

`native/py_cycler/py_cycler.bzl` mirrors that install method directly:

```text
download and extract the exact cycler-0.12.1 source archive by SHA256
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
cd <source>
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate cycler package files and dist-info metadata under lib/python${PYTHON_ABI}/site-packages
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so both the local wheel
build and the prefix install happen only inside the sealed CUDA rootfs. It
never searches `PATH` for Python or pip; Python comes from the Bazel-native
venv prefix and Python packaging tools come from Bazel-native prefixes on
`PYTHONPATH`.

The mechanism verifier reports the expected build channel:

```text
native/py_cycler/py_cycler.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_PIP_PREFIX, PY_SETUPTOOLS_PREFIX, PY_WHEEL_PREFIX
```

That verifier also checks every prefix-file input, `python -m pip` invocation
through `PYTHON_VENV_PREFIX`, `PY_PIP_PREFIX` exposure on `PYTHONPATH`, Spack's
no-network/no-build-isolation/no-deps flags, `--prefix`, and clearing
`PYTHONHOME`.

## Prefix and behavior contract

The ABI-relevant prefix surface is a pure Python package/data prefix:

- package tree: `cycler`
- marker file: `cycler/py.typed`
- metadata: `cycler-0.12.1.dist-info`

Generated installation metadata that embeds the temporary Spack stage path or
complete wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is
not part of the stable parity surface.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_cycler_native
```

It resolves the native `py-cycler`, `python@3.13`, `python-venv`, `py-pip`,
`py-setuptools`, and `py-wheel` prefix markers, asserts the interpreter ABI is
`3.13`, sets the native Python library path and package `PYTHONPATH`, imports
`cycler`, verifies both the package `__version__` and normalized distribution
metadata version, composes two deterministic cyclers, and prints:

```text
py-cycler:0.12.1:0.12.1:2:r:-
```

The parity target is:

```text
//synthetic:py_cycler_prefix_parity
```

It compares `@py_cycler_native//:prefix` against the hermetic Spack reference
prefix and covers:

- selected layout parity for eight stable package files and `.dist-info`
  metadata files under `lib/python3.13/site-packages`;
- byte-identical package files and representative metadata files;
- empty ELF ABI axis, since the package installs no native shared libraries.

Current status: native `py-cycler` is gated inside the hermetic CUDA insula.
The focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
  TMPDIR=$VASO_ESTATE_ROOT/agents/trae/tmp \
  BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
  VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
  SPACK_ROOT_PKG='py-cycler@0.12.1 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib' \
  VASO_NATIVE=1 \
  VASO_FORMAL=0 \
  VASO_SPACK_TIMEOUT=1800 \
  VASO_EXTRA_TEST_TARGETS='//synthetic:use_py_cycler_native,//synthetic:py_cycler_prefix_parity,//tools:hermetic_native_deps_guard_test,//tools:native_dep_wiring_live_test' \
  ./run.sh
```

The run log is
`$VASO_ESTATE_ROOT/agents/trae/logs/py-cycler-rerun-20260929T051439Z.log`.
It used Bazel's `@spack_dist//:spack` inside rootfs mode `cuda-bundle`,
reported hermetic Spack version `1.2.2`, regenerated `spack_graph.lock.json`
with root `spack_py_cycler`, regenerated a 36-node `build_graph.json` ending
in `python@3.13.13`, `python-venv@1.0`, `py-pip@26.1.2`,
`py-setuptools@79.0.1`, `py-wheel@0.45.1`, and `py-cycler@0.12.1`, passed
`//tools:native_build_mechanism_guard_unit_test`,
`//tools:hermetic_native_deps_guard_test`,
`//tools:native_dep_wiring_live_test`, `//synthetic:use_py_cycler_native`, and
`//synthetic:py_cycler_prefix_parity`.
