# py-pyparsing native recipe

## Position in the hillclimb

`py-pyparsing@3.3.2` is the next `PythonPackage` source build after native
`py-ply` in the lean `py-torch` frontier:

```text
106  py-pathspec          1.1.1   python_pip  native
107  py-ply               3.11    python_pip  native
108  py-pyparsing         3.3.2   python_pip
109  py-pyproject-metadata 0.11.0 python_pip
```

The focused reference graph for `SPACK_ROOT_PKG='py-pyparsing@3.3.2'` ends
with 36 nodes. Spack still owns the DAG shape; the native flip changes only
`spack_py_pyparsing.build` to `native` and re-exports
`@py_pyparsing_native//:lib`. The generated link/runtime edges remain
Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-pyparsing-3.3.2-of43ld57t4324hvutmiqef5lklbog46r
```

Dependency prefixes from the same build environment:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-flit-core-3.12.0-gb3ixsfh6lsmbaci2vnlcu7oxgykog47
/vaso/cache/spack/opt/spack/linux-icelake/py-pip-26.1.2-uhzkdfzfb2i6lrqfrzd6arodvbwn7vwy
/vaso/cache/spack/opt/spack/linux-icelake/py-wheel-0.45.1-yzp43gtv6u2kqaxiyeit3jvutyu7mxub
/vaso/cache/spack/opt/spack/linux-icelake/python-venv-1.0-aiafo5jstbj56qrf2ezsa2stsdb4po43
/vaso/cache/spack/opt/spack/linux-icelake/python-3.14.5-slorb5hlbuw4ew2hjum37l3xm7gf74s6
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-pyparsing-3.3.2-of43ld57t4324hvutmiqef5lklbog46r/.spack/repos/spack_repo/builtin/packages/py_pyparsing/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyPyparsing(PythonPackage)`
- selected build system: `python_pip`
- version: `3.3.2`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/p/pyparsing/pyparsing-3.3.2.tar.gz`
- source SHA256:
  `c777f4d763f140633dcb6d8a3eda953bf7a214dc4eff598413c070bcdc117cbc`
- active dependencies for `@3.3.2`: `py-flit-core@3.2:3` build,
  `python@3.9:` build/run, plus PythonPackage machinery `python-venv`,
  `py-pip`, and `py-wheel`
- inactive historical dependency branch: `py-setuptools` is used only for
  `@:3.0.8`
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
  --prefix=<py-pyparsing-prefix> \
  .
```

Spack supplies `pip`, `wheel`, and `flit_core` by putting their prefixes on
`PYTHONPATH`. The installed wheel metadata says:

```text
Generator: flit 3.12.0
Root-Is-Purelib: true
Tag: py3-none-any
Requires-Python: >=3.9
```

Generated installation metadata that embeds temporary stage paths or complete
wheel file lists, such as `direct_url.json`, `RECORD`, and bytecode, is not
part of the stable parity surface.

## Native build recipe

`native/py_pyparsing/py_pyparsing.bzl` mirrors that install method directly:

```text
download and extract the exact pyparsing-3.3.2 source archive by SHA256
read @python_venv_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
read @py_flit_core_native//:prefix_path.txt
validate PYTHON_VENV_PREFIX/bin/python3
validate PY_PIP_PREFIX/lib/python3.14/site-packages/pip
validate PY_WHEEL_PREFIX/lib/python3.14/site-packages/wheel
validate PY_FLIT_CORE_PREFIX/lib/python3.14/site-packages/flit_core
clear PYTHONHOME
set PYTHONPATH from native py-pip, py-wheel, py-flit-core, and python-venv
cd <source>
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate package files, dist-info metadata, and license
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the wheel build and
prefix install happen only inside the sealed CUDA rootfs. It never searches for
Python, pip, wheel, or flit-core on the host; the Python interpreter comes from
the Bazel-native venv prefix and Python package inputs come from Bazel-native
prefixes on `PYTHONPATH`.

The mechanism verifier reports the expected build channel:

```text
native/py_pyparsing/py_pyparsing.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_FLIT_CORE_PREFIX, PY_PIP_PREFIX, PY_WHEEL_PREFIX
```

That verifier checks every prefix-file input, the `python -m pip` invocation
through `PYTHON_VENV_PREFIX`, `PY_PIP_PREFIX` exposure on `PYTHONPATH`,
Spack's no-network/no-build-isolation/no-deps flags, `--prefix`, and clearing
`PYTHONHOME`.

## Prefix and behavior contract

The stable prefix surface includes:

- package tree: `lib/python3.14/site-packages/pyparsing`
- metadata and license: `pyparsing-3.3.2.dist-info`

The package installs no ELF objects, so the ABI axis is expected to be empty.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_pyparsing_native
```

It resolves the native `py-pyparsing`, `python`, `python-venv`, `py-pip`,
`py-flit-core`, and `py-wheel` prefix markers, sets the native Python library
path and package `PYTHONPATH`, imports `pyparsing`, checks version metadata,
and parses a named assignment grammar. Expected output:

```text
py-pyparsing:3.3.2:answer:42
```

The parity target is:

```text
//synthetic:py_pyparsing_prefix_parity
```

It compares `@py_pyparsing_native//:prefix` against the hermetic Spack
reference prefix and covers:

- selected layout parity for stable package files, metadata, and license;
- byte-identical package modules and representative metadata;
- empty ELF ABI axis, since the package installs no native shared libraries.

Current status: native `py-pyparsing` is gated inside the hermetic CUDA insula.
The focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='py-pyparsing@3.3.2' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_EXTRA_TEST_TARGETS='//tools:hermetic_native_deps_guard_test,//synthetic:use_py_pyparsing_native,//synthetic:py_pyparsing_prefix_parity' \
VASO_SPACK_TIMEOUT=2400 \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside rootfs mode `cuda-bundle`,
reported hermetic Spack version `1.2.2`, regenerated `spack_graph.lock.json`
with root `spack_py_pyparsing`, passed `//tools:hermetic_native_deps_guard_test`,
passed `//synthetic:use_py_pyparsing_native`, and passed
`//synthetic:py_pyparsing_prefix_parity`.
