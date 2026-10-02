# py-setuptools-scm native recipe

## Position in the hillclimb

`py-setuptools-scm@8.2.1` is the next ticket-08 Python bootstrap re-seat after
native `py-packaging` in the lean `py-torch` frontier:

```text
53  py-flit-core        3.12.0  python_pip  native
54  py-packaging        26.2    python_pip  native
55  py-setuptools-scm   8.2.1   python_pip  native
```

The focused reference graph for
`SPACK_ROOT_PKG='py-setuptools-scm@8.2.1 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib'`
has a 51-package lock and 56 build graph nodes. Spack still owns the DAG shape;
the native flip changes only `spack_py_setuptools_scm.build` to `native` and
re-exports `@py_setuptools_scm_native//:lib`. The generated link/runtime edges
remain Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-setuptools-scm-8.2.1-so3yrawsljehtiw2xy43in2dqw447gw7
```

Dependency prefixes from the same build environment:

```text
/vaso/cache/spack/opt/spack/linux-icelake/git-2.53.0-okp7t25swul5n7hnm7tpumtwwwybonlw
/vaso/cache/spack/opt/spack/linux-icelake/py-packaging-26.2-ay6pupbu4l5xddd2q7peiih7lu357c6e
/vaso/cache/spack/opt/spack/linux-icelake/py-pip-26.1.2-rjfumck4mkxpheunqnsc4okzhxxq53lv
/vaso/cache/spack/opt/spack/linux-icelake/py-setuptools-79.0.1-ahvs37zplvfrbijtnucmlc2upqxl5ijk
/vaso/cache/spack/opt/spack/linux-icelake/py-wheel-0.45.1-zkzfjve4om2w4y2nwjai4d2hp53vgnwt
/vaso/cache/spack/opt/spack/linux-icelake/python-venv-1.0-ab2pdnquesi6tr3e5jrieb2itp4jdbab
/vaso/cache/spack/opt/spack/linux-icelake/python-3.13.13-6u37x4adbcqbp247uvyjtokjsun4oewt
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-setuptools-scm-8.2.1-so3yrawsljehtiw2xy43in2dqw447gw7/.spack/repos/spack_repo/builtin/packages/py_setuptools_scm/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PySetuptoolsScm(PythonPackage)`
- selected build system: `python_pip`
- version: `8.2.1`
- selected variant: `+toml`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/s/setuptools_scm/setuptools_scm-8.2.1.tar.gz`
- source SHA256:
  `51cfdd1deefc9b8c08d1a61e940a59c4dec39eb6c285d33fa2f1b4be26c7874d`
- active dependencies for `@8.2.1 +toml`: `git` build/run,
  `py-packaging` build/run, `py-pip` build, `py-setuptools` build/run,
  `py-wheel` build, `python` build/run, and `python-venv` build/run
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
  --prefix=<py-setuptools-scm-prefix> \
  .
```

Spack supplies `pip`, `setuptools`, `wheel`, `packaging`, and `python-venv` by
putting their prefixes on `PYTHONPATH`, and it supplies `git` by putting the
selected prefix on `PATH`. The installed wheel metadata says:

```text
Generator: setuptools (8.2.1)
Root-Is-Purelib: true
Tag: py3-none-any
Requires-Dist: packaging>=20
Requires-Dist: setuptools
Requires-Dist: tomli>=1; python_version < "3.11"
Requires-Dist: typing-extensions; python_version < "3.10"
```

Generated installation metadata that embeds temporary stage paths or complete
wheel file lists, such as `direct_url.json`, `RECORD`, and bytecode, is not
part of the stable parity surface.

## Native build recipe

`native/py_setuptools_scm/py_setuptools_scm.bzl` mirrors that install method
directly:

```text
download and extract the exact setuptools_scm-8.2.1 source archive by SHA256
read @git_native//:prefix_path.txt
read @python_venv_native//:prefix_path.txt
derive PYTHON_ABI from PYTHON_VENV_PREFIX/bin/python3
read @py_packaging_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_setuptools_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
validate GIT_PREFIX/bin/git
validate PYTHON_VENV_PREFIX/bin/python3
validate PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI}
validate py-pip, py-setuptools, py-wheel, and py-packaging site-packages under
  lib/python${PYTHON_ABI}/site-packages
clear PYTHONHOME
set PYTHONPATH from native py-pip, py-setuptools, py-wheel, py-packaging, and
  python-venv prefixes under lib/python${PYTHON_ABI}/site-packages
set PATH from native git, pip, wheel, and python-venv prefixes
cd <source>
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate package files, dist-info metadata, entry points, and license
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the wheel build and
prefix install happen only inside the sealed CUDA rootfs. It never searches for
Python, pip, setuptools, wheel, packaging, or git on the host; all inputs are
Bazel-native prefixes.

The mechanism verifier reports the expected build channel:

```text
native/py_setuptools_scm/py_setuptools_scm.bzl: python-pip-install: GIT_PREFIX, PYTHON_VENV_PREFIX, PY_PACKAGING_PREFIX, PY_PIP_PREFIX, PY_SETUPTOOLS_PREFIX, PY_WHEEL_PREFIX
```

That verifier checks every prefix-file input, the `python -m pip` invocation
through `PYTHON_VENV_PREFIX`, `PY_PIP_PREFIX` exposure on `PYTHONPATH`,
Spack's no-network/no-build-isolation/no-deps flags, `--prefix`, and clearing
`PYTHONHOME`.

## Prefix and behavior contract

The stable prefix surface includes:

- package tree: `lib/python3.13/site-packages/setuptools_scm`
- metadata, entry points, and license:
  `lib/python3.13/site-packages/setuptools_scm-8.2.1.dist-info`

The package installs no ELF objects, so the ABI axis is expected to be empty.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_setuptools_scm_native
```

It resolves the native `py-setuptools-scm`, `git`, `python_313`,
`python-venv`, `py-packaging`, `py-pip`, `py-setuptools`, and `py-wheel` prefix
markers, derives and asserts Python ABI `3.13`, sets the native Python library
path and package `PYTHONPATH`, imports
`setuptools_scm`, creates a temporary git repository, tags it `v1.2.3`, and
checks that `get_version()` returns the tag-derived version through the native
git executable. Expected output:

```text
py-setuptools-scm:8.2.1:1.2.3:git
```

The parity target is:

```text
//synthetic:py_setuptools_scm_prefix_parity
```

It compares `@py_setuptools_scm_native//:prefix` against the hermetic Spack
reference prefix and covers:

- selected layout parity for stable package files, metadata, entry points, and
  license;
- byte-identical package modules and representative metadata;
- empty ELF ABI axis, since the package installs no native shared libraries.

Current status: native `py-setuptools-scm` is gated inside the hermetic CUDA
insula. The focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
HOME="$HOME" \
USER="${USER:-<user>}" \
LOGNAME="${LOGNAME:-<user>}" \
TMPDIR=$VASO_ESTATE_ROOT/agents/trae/tmp \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='py-setuptools-scm@8.2.1 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_py_setuptools_scm_native,//synthetic:py_setuptools_scm_prefix_parity,//tools:hermetic_native_deps_guard_test,//tools:native_dep_wiring_live_test' \
VASO_SPACK_TIMEOUT=2400 \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside rootfs mode `cuda-bundle`,
reported hermetic Spack version `1.2.2`, regenerated `spack_graph.lock.json`
with root `spack_py_setuptools_scm` and reference prefix
`/vaso/cache/spack/opt/spack/linux-icelake/py-setuptools-scm-8.2.1-so3yrawsljehtiw2xy43in2dqw447gw7`,
passed `//synthetic:use_py_setuptools_scm_native`, passed
`//synthetic:py_setuptools_scm_prefix_parity` with `"ok": true` for 21
selected `lib/python3.13/site-packages` layout/data paths and an empty ELF ABI
axis, passed `//tools:hermetic_native_deps_guard_test`, and passed
`//tools:native_dep_wiring_live_test` with 15 remaining ticket-08 allowlisted
mismatches.
