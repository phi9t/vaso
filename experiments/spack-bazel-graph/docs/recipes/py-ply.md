# py-ply native recipe

## Position in the hillclimb

`py-ply@3.11` is the next `PythonPackage` source build after native
`py-pathspec` in the lean `py-torch` frontier:

```text
105  py-packaging  26.2   python_pip  native
106  py-pathspec   1.1.1  python_pip  native
107  py-ply        3.11   python_pip
```

The focused reference graph for `SPACK_ROOT_PKG='py-ply@3.11'` ends with 36
nodes. Spack still owns the DAG shape; the native flip changes only
`spack_py_ply.build` to `native` and re-exports `@py_ply_native//:lib`. The
generated link/runtime edges remain Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-ply-3.11-vbrmdb4tlmgr23ufzb53hjlmhjqulucr
```

Dependency prefixes from the same build environment:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-setuptools-79.0.1-amtimju3srqbl5rrw23fdcf4fyus2bhi
/vaso/cache/spack/opt/spack/linux-icelake/py-pip-26.1.2-uhzkdfzfb2i6lrqfrzd6arodvbwn7vwy
/vaso/cache/spack/opt/spack/linux-icelake/py-wheel-0.45.1-yzp43gtv6u2kqaxiyeit3jvutyu7mxub
/vaso/cache/spack/opt/spack/linux-icelake/python-venv-1.0-aiafo5jstbj56qrf2ezsa2stsdb4po43
/vaso/cache/spack/opt/spack/linux-icelake/python-3.14.5-slorb5hlbuw4ew2hjum37l3xm7gf74s6
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-ply-3.11-vbrmdb4tlmgr23ufzb53hjlmhjqulucr/.spack/repos/spack_repo/builtin/packages/py_ply/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyPly(PythonPackage)`
- selected build system: `python_pip`
- version: `3.11`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/p/ply/ply-3.11.tar.gz`
- source SHA256:
  `00c7c1aaa88358b9c765b6d3000c6eec0ba42abca5351b095321aef446081da3`
- dependencies for `@3.11`: `py-setuptools` build, plus PythonPackage
  machinery `python-venv`, `py-pip`, and `py-wheel`
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
  --prefix=<py-ply-prefix> \
  .
```

Spack supplies `pip`, `setuptools`, and `wheel` by putting their prefixes on
`PYTHONPATH`. The installed wheel metadata says:

```text
Generator: setuptools (79.0.1)
Root-Is-Purelib: true
Tag: py2-none-any
Tag: py3-none-any
```

Generated installation metadata that embeds temporary stage paths or complete
wheel file lists, such as `direct_url.json`, `RECORD`, and bytecode, is not
part of the stable parity surface.

## Native build recipe

`native/py_ply/py_ply.bzl` mirrors that install method directly:

```text
download and extract the exact ply-3.11 source archive by SHA256
read @python_venv_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_setuptools_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
validate PYTHON_VENV_PREFIX/bin/python3
validate PY_PIP_PREFIX/lib/python3.14/site-packages/pip
validate PY_SETUPTOOLS_PREFIX/lib/python3.14/site-packages/setuptools
validate PY_WHEEL_PREFIX/lib/python3.14/site-packages/wheel
clear PYTHONHOME
set PYTHONPATH from native py-pip, py-setuptools, py-wheel, and python-venv
cd <source>
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate package files and dist-info metadata
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the wheel build and
prefix install happen only inside the sealed CUDA rootfs. It never searches for
Python, pip, setuptools, or wheel on the host; the Python interpreter comes from
the Bazel-native venv prefix and Python package inputs come from Bazel-native
prefixes on `PYTHONPATH`.

The mechanism verifier reports the expected build channel:

```text
native/py_ply/py_ply.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_PIP_PREFIX, PY_SETUPTOOLS_PREFIX, PY_WHEEL_PREFIX
```

That verifier checks every prefix-file input, the `python -m pip` invocation
through `PYTHON_VENV_PREFIX`, `PY_PIP_PREFIX` exposure on `PYTHONPATH`,
Spack's no-network/no-build-isolation/no-deps flags, `--prefix`, and clearing
`PYTHONHOME`.

## Prefix and behavior contract

The stable prefix surface includes:

- package tree: `lib/python3.14/site-packages/ply`
- metadata: `ply-3.11.dist-info`

The package installs no ELF objects, so the ABI axis is expected to be empty.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_ply_native
```

It resolves the native `py-ply`, `python`, `python-venv`, `py-pip`,
`py-setuptools`, and `py-wheel` prefix markers, sets the native Python library
path and package `PYTHONPATH`, imports `ply.lex` and `ply.yacc`, and parses a
small expression grammar. Expected output:

```text
py-ply:3.11:7
```

The parity target is:

```text
//synthetic:py_ply_prefix_parity
```

It compares `@py_ply_native//:prefix` against the hermetic Spack reference
prefix and covers:

- selected layout parity for stable package files and metadata;
- byte-identical package modules and representative metadata;
- empty ELF ABI axis, since the package installs no native shared libraries.

Current status: native `py-ply` is gated inside the hermetic CUDA insula. The
focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='py-ply@3.11' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_EXTRA_TEST_TARGETS='//tools:hermetic_native_deps_guard_test,//synthetic:use_py_ply_native,//synthetic:py_ply_prefix_parity' \
VASO_SPACK_TIMEOUT=2400 \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside rootfs mode `cuda-bundle`,
reported hermetic Spack version `1.2.2`, regenerated `spack_graph.lock.json`
with root `spack_py_ply`, passed `//tools:hermetic_native_deps_guard_test`,
passed `//synthetic:use_py_ply_native`, and passed
`//synthetic:py_ply_prefix_parity`.
