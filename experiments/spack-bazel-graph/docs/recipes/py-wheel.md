# py-wheel native recipe

## Position in the hillclimb

`py-wheel@0.45.1` is the next Python package installed by the already-native
`py-pip` provider in the lean `py-torch` frontier, re-seated on the decided
`python@3.13.13` PyTorch line:

```text
88  py-pip        26.1.2  generic  native
89  py-setuptools 79.0.1  generic  native
90  py-wheel      0.45.1  generic  native
```

The focused reference graph for `SPACK_ROOT_PKG='py-wheel@0.45.1'` ends with
34 nodes. Spack still owns the DAG shape; the native flip changes only
`spack_py_wheel.build` to `native` and re-exports `@py_wheel_native//:lib`.
The generated link/runtime edges remain Spack-derived; `py-pip` is consumed by
the native repository rule as a build tool prefix because Spack's dependency is
build-only.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-wheel-0.45.1-zkzfjve4om2w4y2nwjai4d2hp53vgnwt
```

Dependency prefixes from the same build environment:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-pip-26.1.2-rjfumck4mkxpheunqnsc4okzhxxq53lv
/vaso/cache/spack/opt/spack/linux-icelake/python-venv-1.0-ab2pdnquesi6tr3e5jrieb2itp4jdbab
/vaso/cache/spack/opt/spack/linux-icelake/python-3.13.13-6u37x4adbcqbp247uvyjtokjsun4oewt
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-wheel-0.45.1-zkzfjve4om2w4y2nwjai4d2hp53vgnwt/.spack/repos/spack_repo/builtin/packages/py_wheel/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyWheel(Package, PythonExtension)`
- selected build system: generic package install method
- version: `0.45.1`
- source payload: upstream wheel
  `https://files.pythonhosted.org/packages/py3/w/wheel/wheel-0.45.1-py3-none-any.whl`
- wheel SHA256:
  `708e7481cc80179af0e556bbf0cc00b8444c7321e2700b8d8580231d13017248`
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
  --prefix=<py-wheel-prefix> \
  <wheel>
```

Spack supplies `pip` by putting the `py-pip` prefix on `PYTHONPATH`; the build
log reports:

```text
Using pip 26.1.2 from <py-pip-prefix>/lib/python3.13/site-packages/pip (python 3.13)
```

## Native build recipe

`native/py_wheel/py_wheel.bzl` mirrors that install method directly:

```text
download the exact wheel-0.45.1 wheel by SHA256
read @python_venv_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
validate PYTHON_VENV_PREFIX/bin/python3
derive PYTHON_ABI from PYTHON_VENV_PREFIX/bin/python3
validate PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI}
validate PY_PIP_PREFIX/lib/python${PYTHON_ABI}/site-packages/pip
clear PYTHONHOME
set PYTHONPATH from the native py-pip and python-venv prefixes for PYTHON_ABI
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> <wheel>
validate bin/wheel, the wheel package, and dist-info metadata
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the wheel
installation happens only inside the sealed CUDA rootfs. It never searches
`PATH` for Python or pip; Python comes from the Bazel-native venv prefix and
pip comes from the Bazel-native `py-pip` prefix on `PYTHONPATH`.

The mechanism verifier reports the expected build channel:

```text
native/py_wheel/py_wheel.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_PIP_PREFIX
```

That verifier also checks the two-prefix contract, `python -m pip` invocation
through `PYTHON_VENV_PREFIX`, `PY_PIP_PREFIX` exposure on `PYTHONPATH`, Spack's
no-network/no-build-isolation/no-deps flags, `--prefix`, and clearing
`PYTHONHOME`.

## Prefix and behavior contract

The ABI-relevant prefix surface is a Python-package prefix with one console
script:

- executable: `bin/wheel`
- package tree: `wheel`
- metadata: `wheel-0.45.1.dist-info`

Generated installation metadata that embeds the temporary Spack stage path or
complete wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is
not part of the stable parity surface.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_wheel_native
```

It resolves the native `py-wheel`, `python`, `python-venv`, and `py-pip` prefix
markers, asserts the derived ABI is `3.13`, sets the native Python library path
and package `PYTHONPATH`, imports `wheel`, verifies the installed version
through `importlib.metadata`, validates `bin/wheel version`, and prints:

```text
py-wheel:0.45.1:0.45.1:wheel
```

The parity target is:

```text
//synthetic:py_wheel_prefix_parity
```

It compares `@py_wheel_native//:prefix` against the hermetic Spack reference
prefix and covers:

- selected layout parity for stable package files, `.dist-info` metadata, and
  `bin/wheel`;
- byte-identical package entry modules and representative metadata files;
- executable behavior parity for `bin/wheel version`;
- empty ELF ABI axis, since the wheel installs no native shared libraries.

Current status: native `py-wheel` is gated inside the hermetic CUDA insula. The
focused verification command is:

```bash
SPACK_ROOT_PKG='py-wheel@0.45.1 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib' \
VASO_NATIVE=1 \
VASO_SPACK_TIMEOUT=600 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
TMPDIR=$VASO_ESTATE_ROOT/agents/trae/tmp \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_py_wheel_native,//synthetic:py_wheel_prefix_parity,//tools:hermetic_native_deps_guard_test,//tools:native_dep_wiring_live_test' \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside rootfs mode `cuda-bundle`,
reported hermetic Spack version `1.2.2`, regenerated a 29-package
`spack_graph.lock.json` with root `spack_py_wheel` and a 34-node
`build_graph.json` tail `python@3.13.13`, `python-venv@1.0`,
`py-pip@26.1.2`, `py-wheel@0.45.1`, passed
`//tools:hermetic_native_deps_guard_test`, passed
`//tools:native_dep_wiring_live_test` with 15 remaining ticket-08 allowlisted
mismatches, passed `//synthetic:use_py_wheel_native`, and passed
`//synthetic:py_wheel_prefix_parity` with `"ok": true` over the selected
`lib/python3.13/site-packages` layout and prefix-normalized `bin/wheel`.
