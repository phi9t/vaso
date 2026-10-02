# py-pluggy native recipe

## Position in the hillclimb

`py-pluggy@1.6.0` is the `PythonPackage` source build at lean `py-torch`
topo index 114. In ticket 08 it is re-seated on the decided
`python@3.13.13` line after the Python packaging bootstrap reaches native
`py-setuptools-scm`.

```text
112  py-cppy        1.3.1  python_pip  native
113  py-kiwisolver  1.5.0  python_pip  native
114  py-pluggy      1.6.0  python_pip
```

The focused reference graph for `SPACK_ROOT_PKG='py-pluggy'` ends with 57
nodes. Spack still owns the DAG shape; the native flip changes only
`spack_py_pluggy.build` to `native` and re-exports
`@py_pluggy_native//:lib`. The generated link/runtime edges remain
Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-pluggy-1.6.0-nqass3m46bgyyqt6yo5uikwncp7xs55g
```

Dependency prefixes from the same build environment:

```text
/vaso/cache/spack/opt/spack/linux-icelake/git-2.53.0-okp7t25swul5n7hnm7tpumtwwwybonlw
/vaso/cache/spack/opt/spack/linux-icelake/py-flit-core-3.12.0-vjkcb3p4z7cjbtr2bf7ky2w3n7vtwb7j
/vaso/cache/spack/opt/spack/linux-icelake/py-packaging-26.2-ay6pupbu4l5xddd2q7peiih7lu357c6e
/vaso/cache/spack/opt/spack/linux-icelake/py-pip-26.1.2-rjfumck4mkxpheunqnsc4okzhxxq53lv
/vaso/cache/spack/opt/spack/linux-icelake/py-setuptools-79.0.1-ahvs37zplvfrbijtnucmlc2upqxl5ijk
/vaso/cache/spack/opt/spack/linux-icelake/py-setuptools-scm-8.2.1-so3yrawsljehtiw2xy43in2dqw447gw7
/vaso/cache/spack/opt/spack/linux-icelake/py-wheel-0.45.1-zkzfjve4om2w4y2nwjai4d2hp53vgnwt
/vaso/cache/spack/opt/spack/linux-icelake/python-venv-1.0-ab2pdnquesi6tr3e5jrieb2itp4jdbab
/vaso/cache/spack/opt/spack/linux-icelake/python-3.13.13-6u37x4adbcqbp247uvyjtokjsun4oewt
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-pluggy-1.6.0-nqass3m46bgyyqt6yo5uikwncp7xs55g/.spack/repos/spack_repo/builtin/packages/py_pluggy/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyPluggy(PythonPackage)`
- selected build system: `python_pip`
- version: `1.6.0`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/p/pluggy/pluggy-1.6.0.tar.gz`
- source SHA256:
  `7dcc130b76258d33b90f61b658791dede3486c3e6bfb003ee5c9bfb396dd22f3`
- active build dependencies: `py-setuptools@65:`,
  `py-setuptools-scm@8:+toml`, `py-pip`, and `py-wheel`
- active build/run dependencies: `python@3.9:` and `python-venv`
- build-backend transitive dependencies captured explicitly by the native rule:
  `py-packaging` and `git`, because `py-setuptools-scm` imports packaging and
  shells through git on the source-version path
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
  --prefix=<py-pluggy-prefix> \
  .
```

Spack supplies `pip`, `setuptools`, `setuptools-scm`, `wheel`, `packaging`,
and `python-venv` by putting their prefixes on `PYTHONPATH`, and it supplies
`git` by putting the selected prefix on `PATH`.

Generated installation metadata that embeds temporary stage paths or complete
wheel file lists, such as `direct_url.json`, `RECORD`, and bytecode, is not
part of the stable parity surface.

## Native build recipe

`native/py_pluggy/py_pluggy.bzl` mirrors that install method directly:

```text
download and extract the exact pluggy-1.6.0 source archive by SHA256
read @git_native//:prefix_path.txt
read @python_venv_native//:prefix_path.txt
read @py_packaging_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_setuptools_native//:prefix_path.txt
read @py_setuptools_scm_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
validate GIT_PREFIX/bin/git
validate PYTHON_VENV_PREFIX/bin/python3
derive PYTHON_ABI from PYTHON_VENV_PREFIX/bin/python3
validate PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI}
validate py-pip, py-setuptools, py-setuptools-scm, py-wheel, and py-packaging under lib/python${PYTHON_ABI}/site-packages
clear PYTHONHOME
set PYTHONPATH from native py-pip, py-setuptools, py-setuptools-scm, py-wheel, py-packaging, and python-venv for PYTHON_ABI
set PATH from native git, pip, wheel, and python-venv prefixes
cd <source>
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate package files, dist-info metadata, typing marker, and license
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the wheel build and
prefix install happen only inside the sealed CUDA rootfs. It never searches for
Spack, Python packages, or git on the host; all inputs are Bazel-native prefix
markers.

The mechanism verifier reports the expected build channel:

```text
native/py_pluggy/py_pluggy.bzl: python-pip-install: GIT_PREFIX, PYTHON_VENV_PREFIX, PY_PACKAGING_PREFIX, PY_PIP_PREFIX, PY_SETUPTOOLS_PREFIX, PY_SETUPTOOLS_SCM_PREFIX, PY_WHEEL_PREFIX
```

That verifier checks every prefix-file input, the `python -m pip` invocation
through `PYTHON_VENV_PREFIX`, `PY_PIP_PREFIX` exposure on `PYTHONPATH`,
Spack's no-network/no-build-isolation/no-deps flags, `--prefix`, and clearing
`PYTHONHOME`.

## Prefix and behavior contract

The stable prefix surface includes:

- package tree: `lib/python3.13/site-packages/pluggy`
- typing marker: `lib/python3.13/site-packages/pluggy/py.typed`
- metadata and license: `lib/python3.13/site-packages/pluggy-1.6.0.dist-info`

The package installs no ELF objects, so the ABI axis is expected to be empty.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_pluggy_native
```

It resolves the native `py-pluggy`, `python@3.13.13`, `python-venv`, `py-packaging`,
`py-pip`, `py-setuptools`, `py-setuptools-scm`, and `py-wheel` prefix markers,
derives and asserts ABI `3.13` from `@python_313_native`, sets the native
Python library path and package `PYTHONPATH`, imports `pluggy`, checks version
metadata, registers a hook spec and implementation, and expects:

```text
py-pluggy:1.6.0:12
```

The parity target is:

```text
//synthetic:py_pluggy_prefix_parity
```

It compares `@py_pluggy_native//:prefix` against the hermetic Spack reference
prefix and covers:

- selected layout parity for stable package files, metadata, typing marker, and
  license;
- byte-identical package modules and representative metadata;
- empty ELF ABI axis, since the package installs no native shared libraries.

Current status: native `py-pluggy` is gated inside the hermetic CUDA insula.
The focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='py-pluggy@1.6.0 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_FORCE_FETCH_REPOS='@py_pluggy_native' \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_py_pluggy_native,//synthetic:py_pluggy_prefix_parity,//tools:hermetic_native_deps_guard_test,//tools:native_dep_wiring_live_test' \
VASO_SPACK_TIMEOUT=2400 \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside rootfs mode `cuda-bundle`,
reported hermetic Spack version `1.2.2`, regenerated `spack_graph.lock.json`
with root `spack_py_pluggy`, produced a 52-package lock and 57-node build graph
with tail `git@2.53.0`, `python@3.13.13`, `python-venv@1.0`,
`py-pip@26.1.2`, `py-setuptools@79.0.1`, `py-wheel@0.45.1`,
`py-flit-core@3.12.0`, `py-packaging@26.2`, `py-setuptools-scm@8.2.1`,
and `py-pluggy@1.6.0`, installed `pluggy-1.6.0` successfully, passed
`//synthetic:use_py_pluggy_native` with output `py-pluggy:1.6.0:12`, passed
`//synthetic:py_pluggy_prefix_parity` with top-level `"ok": true`, 15/15
selected layout paths under `lib/python3.13/site-packages`, no missing or extra
candidate paths, and empty ELF ABI axis, passed
`//tools:hermetic_native_deps_guard_test` with
`python ABI literal guard passed (158 files)`, and passed
`//tools:native_dep_wiring_live_test` with
`native dependency wiring consistent (15 allowlisted mismatches)`.
