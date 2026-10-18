# py-mpmath native recipe

## Position in the hillclimb

`py-mpmath@1.3.0` is a pure `PythonPackage` source build in the pruned lean
`py-torch` frontier:

```text
90  py-markupsafe  3.0.3  python_pip  native
91  py-jinja2      3.1.6  python_pip  native
92  py-mpmath      1.3.0  python_pip  native
```

The focused reference graph for `SPACK_ROOT_PKG='py-mpmath@1.3.0'` ends with
36 nodes. Spack still owns the DAG shape; the native flip changes only
`spack_py_mpmath.build` to `native` and re-exports
`@py_mpmath_native//:lib`. The generated link/runtime edges remain
Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-mpmath-1.3.0-46bsq6x6xfgkhcuxekzjcno7dfn7gvda
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
/vaso/cache/spack/opt/spack/linux-icelake/py-mpmath-1.3.0-46bsq6x6xfgkhcuxekzjcno7dfn7gvda/.spack/repos/spack_repo/builtin/packages/py_mpmath/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyMpmath(PythonPackage)`
- selected build system: `python_pip`
- version: `1.3.0`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/m/mpmath/mpmath-1.3.0.tar.gz`
- source SHA256:
  `7a28eb2a9774d00c7bc92411c19a89209d5da7c4c9a9e227be8330a23a25b91f`
- dependencies for `@1.3.0`: `py-setuptools` build,
  `py-setuptools@36.7.0:` build, plus PythonPackage machinery
  `python`, `python-venv`, `py-pip`, and `py-wheel`
- inactive dependency branch: `py-setuptools-scm@1.7.0:` applies only to
  `@1.2.0:1.2`
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
  --prefix=<py-mpmath-prefix> \
  .
```

Spack supplies `pip`, `setuptools`, and `wheel` by putting their prefixes on
`PYTHONPATH`. The installed wheel metadata says:

```text
Generator: setuptools (79.0.1)
Root-Is-Purelib: true
Tag: py3-none-any
```

Generated installation metadata that embeds temporary stage paths or complete
wheel file lists, such as `direct_url.json`, `RECORD`, and bytecode, is not
part of the stable parity surface.

## Native build recipe

`native/py_mpmath/py_mpmath.bzl` mirrors that install method directly:

```text
download and extract the exact mpmath-1.3.0 source archive by SHA256
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
cd <source>
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate package files, dist-info metadata, and license
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the wheel build and
prefix install happen only inside the sealed CUDA rootfs. It never searches for
Python, pip, setuptools, or wheel on the host; the Python interpreter comes from
the Bazel-native venv prefix and Python package inputs come from Bazel-native
prefixes on `PYTHONPATH`. The Python ABI is derived from the native venv
interpreter and currently resolves to `3.13` for the PyTorch line.

The mechanism verifier reports the expected build channel:

```text
native/py_mpmath/py_mpmath.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_PIP_PREFIX, PY_SETUPTOOLS_PREFIX, PY_WHEEL_PREFIX
```

That verifier checks every prefix-file input, the `python -m pip` invocation
through `PYTHON_VENV_PREFIX`, `PY_PIP_PREFIX` exposure on `PYTHONPATH`,
Spack's no-network/no-build-isolation/no-deps flags, `--prefix`, and clearing
`PYTHONHOME`.

## Prefix and behavior contract

The stable prefix surface includes:

- package tree: `lib/python3.13/site-packages/mpmath`
- metadata and license: `mpmath-1.3.0.dist-info`

The package installs no ELF objects, so the ABI axis is expected to be empty.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_mpmath_native
```

It resolves the native `py-mpmath`, `python_313`, `python-venv`, `py-pip`,
`py-setuptools`, and `py-wheel` prefix markers, sets the native Python library
path and package `PYTHONPATH`, derives and checks Python ABI `3.13`, imports
`mpmath`, checks version metadata, and computes representative
arbitrary-precision constants. Expected output:

```text
py-mpmath:1.3.0:3.141592653589793238462643:1.414213562373095048801689:1.644934066848226436472415
```

The parity target is:

```text
//synthetic:py_mpmath_prefix_parity
```

It compares `@py_mpmath_native//:prefix` against the hermetic Spack reference
prefix and covers:

- selected layout parity for stable package files, metadata, and license;
- byte-identical package modules and representative metadata;
- empty ELF ABI axis, since the package installs no native shared libraries.

Current status: native `py-mpmath` is gated inside the hermetic CUDA insula.
The focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
TMPDIR=$VASO_ESTATE_ROOT/agents/trae/tmp \
SPACK_ROOT_PKG='py-mpmath@1.3.0 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib' \
VASO_NATIVE=1 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//tools:hermetic_native_deps_guard_test,//tools:python_abi_literal_guard_unit_test,//synthetic:use_py_mpmath_native,//synthetic:py_mpmath_prefix_parity' \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside rootfs mode `cuda-bundle`,
reported hermetic Spack version `1.2.2`, regenerated `spack_graph.lock.json`
with root `spack_py_mpmath`, passed
`//tools:hermetic_native_deps_guard_test`, passed
`//tools:python_abi_literal_guard_unit_test`, passed
`//synthetic:use_py_mpmath_native`, and passed
`//synthetic:py_mpmath_prefix_parity` with selected stable files under
`lib/python3.13/site-packages` byte-identical to the Spack reference and an
empty ELF ABI axis.

Full proof log:
`$VASO_ESTATE_ROOT/agents/trae/logs/py-mpmath-insula-proof-full-20260929T191459Z.log`
