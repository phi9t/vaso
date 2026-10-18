# py-pip native recipe

## Position in the hillclimb

`py-pip@26.1.2` is the first Python package-manager node after the native
`python-venv` prefix in the lean `py-torch` frontier:

```text
87  python-venv  1.0     generic  native
88  py-pip       26.1.2  generic
89  py-setuptools 79.0.1 generic
```

The focused reference graph for `SPACK_ROOT_PKG='py-pip@26.1.2'` ends with
33 nodes. Spack still owns the DAG shape; the native flip changes only
`spack_py_pip.build` to `native` and re-exports `@py_pip_native//:lib`. Its
graph edges remain the Spack-derived build/run dependencies on `python` and
`python-venv`.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-pip-26.1.2-rjfumck4mkxpheunqnsc4okzhxxq53lv
```

Dependency prefixes from the same lock:

```text
/vaso/cache/spack/opt/spack/linux-icelake/python-3.13.13-6u37x4adbcqbp247uvyjtokjsun4oewt
/vaso/cache/spack/opt/spack/linux-icelake/python-venv-1.0-ab2pdnquesi6tr3e5jrieb2itp4jdbab
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-pip-26.1.2-rjfumck4mkxpheunqnsc4okzhxxq53lv/.spack/repos/spack_repo/builtin/packages/py_pip/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: generic Python extension package
- selected build system: generic package install method
- version: `26.1.2`
- source payload: upstream wheel
  `https://files.pythonhosted.org/packages/py3/p/pip/pip-26.1.2-py3-none-any.whl`
- wheel SHA256:
  `382ff9f685ee3bc25864f820aa50505825f10f5458ffff07e30a6d96e5715cab`
- dependencies: `python` and `python-venv`, both represented in the concrete
  graph as build/run dependencies
- install command shape:

```text
<python-venv-prefix>/bin/python3 <stage>/pip-26.1.2-py3-none-any.whl/pip \
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
  --prefix=<py-pip-prefix> \
  <wheel>
```

## Native build recipe

`native/py_pip/py_pip.bzl` mirrors that install path directly:

```text
download the exact pip-26.1.2 wheel by SHA256
read @python_313_native//:prefix_path.txt
read @python_venv_native//:prefix_path.txt
derive PYTHON_ABI from PYTHON_PREFIX/bin/python3
validate PYTHON_PREFIX/bin/python${PYTHON_ABI}
validate PYTHON_PREFIX/include/python${PYTHON_ABI}
validate PYTHON_VENV_PREFIX/bin/python3
validate PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI}
clear PYTHONHOME and PYTHONPATH
PYTHON_PREFIX/bin/python${PYTHON_ABI} -m zipfile -e <wheel> unpacked-wheel
PYTHON_VENV_PREFIX/bin/python3 unpacked-wheel/pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> <wheel>
validate bin/pip, bin/pip3, bin/pip${PYTHON_ABI}, pip package, and dist-info metadata
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so both wheel
extraction and installation happen inside the sealed CUDA rootfs. It never
searches `PATH` for Python or pip; both interpreters come from Bazel-native
prefix marker files.

The mechanism verifier reports the expected build channel:

```text
native/py_pip/py_pip.bzl: python-bootstrap-pip: PYTHON_PREFIX, PYTHON_VENV_PREFIX
```

That verifier also checks the two-prefix contract, wheel extraction through
`PYTHON_PREFIX`, pip invocation through `PYTHON_VENV_PREFIX`, Spack's
no-network/no-build-isolation/no-deps flags, `--prefix`, and clearing
`PYTHONHOME`.

## Prefix and behavior contract

The ABI-relevant prefix surface is a Python-package prefix, with no C link
library of its own:

- console scripts: `bin/pip`, `bin/pip3`, and `bin/pip3.13`
- package tree: `lib/python3.13/site-packages/pip`
- metadata: `lib/python3.13/site-packages/pip-26.1.2.dist-info`

The console scripts embed the absolute py-pip prefix and execute through the
`python-venv` dependency interpreter. The parity verifier normalizes generated
prefix values before comparing those scripts while still requiring the same
files, executable presence, and runtime behavior.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_pip_native
```

It resolves the native `py-pip`, `python`, and `python-venv` prefix markers,
sets the native Python library path and py-pip `site-packages`, confirms
`pip.__version__ == 26.1.2`, confirms the interpreter is still a venv, verifies
`pip3.13 --version`, and prints:

```text
py-pip:26.1.2:True
```

The parity target is:

```text
//synthetic:py_pip_prefix_parity
```

It compares `@py_pip_native//:prefix` against the hermetic Spack reference
prefix and covers:

- selected layout parity for console scripts, package entry modules, and stable
  `.dist-info` metadata;
- prefix-normalized `bin/pip`, `bin/pip3`, and `bin/pip3.13` script parity;
- executable presence/NEEDED parity for `bin/pip3.13`;
- runtime behavior parity for `pip3.13 --version`;
- runtime installed-package parity for `pip list --format=freeze`.

Current status: native `py-pip` is gated inside the hermetic CUDA insula. The
focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='py-pip@26.1.2 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_FORCE_FETCH_REPOS=@py_pip_native \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_py_pip_native,//synthetic:py_pip_prefix_parity,//tools:hermetic_native_deps_guard_test,//tools:native_dep_wiring_live_test' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside rootfs mode `cuda-bundle`,
reported hermetic Spack version `1.2.2`, regenerated
`spack_graph.lock.json` with root `spack_py_pip`, and wrote a 33-node
`build_graph.json` whose tail is `python@3.13.13`, `python-venv@1.0`, and
`py-pip@26.1.2`. It passed `//tools:hermetic_native_deps_guard_test`,
`//tools:native_dep_wiring_live_test`, `//synthetic:use_py_pip_native`, and
`//synthetic:py_pip_prefix_parity`. The parity gate reported `"ok": true`,
11/11 layout parity, prefix-normalized parity for `bin/pip`, `bin/pip3`, and
`bin/pip3.13`, byte-identical selected package and dist-info files, matching
`pip3.13 --version` output, and matching `pip list --format=freeze` output
`pip==26.1.2`.
