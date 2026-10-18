# py-fonttools native recipe

## Position in the hillclimb

`py-fonttools@4.39.4` is a regular `PythonPackage` source build in the lean
`py-torch` frontier, re-seated on the decided `python@3.13.13` PyTorch line
after the `py-cycler` ticket-08 slice:

```text
94  py-cycler     0.12.1  python_pip  native
95  py-cython     3.2.4   python_pip  native
96  nvtx          3.3.0   generic     native
97  py-flit-core  3.12.0  python_pip  native
98  py-fonttools  4.39.4  python_pip  native
```

The focused reference graph for
`SPACK_ROOT_PKG='py-fonttools@4.39.4 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib'`
ends with 31 packages and a 36-node `build_graph.json`. Spack still owns the
DAG shape; the native flip changes only `spack_py_fonttools.build` to `native`
and re-exports `@py_fonttools_native//:lib`. The generated link/runtime edges
remain Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-fonttools-4.39.4-h3iurgavm43kna3icoxrakj3cj6jaawk
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
/vaso/cache/spack/opt/spack/linux-icelake/py-fonttools-4.39.4-h3iurgavm43kna3icoxrakj3cj6jaawk/.spack/repos/spack_repo/builtin/packages/py_fonttools/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyFonttools(PythonPackage)`
- selected build system: `python_pip`
- version: `4.39.4`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/f/fonttools/fonttools-4.39.4.zip`
- source SHA256:
  `dba8d7cdb8e2bac1b3da28c5ed5960de09e59a2fe7e63bb73f5a59e57b0430d2`
- dependencies: `python` and `python-venv` build/run, plus `py-pip`,
  `py-setuptools`, and `py-wheel` build inputs
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
  --prefix=<py-fonttools-prefix> \
  .
```

Spack supplies `pip` by putting the `py-pip` prefix on `PYTHONPATH`; the build
log reports:

```text
Using pip 26.1.2 from <py-pip-prefix>/lib/python3.13/site-packages/pip (python 3.13)
```

Pip builds an intermediate pure-Python wheel from the source tree, then
installs that wheel into the prefix. Generated installation metadata that
embeds temporary stage paths or full file lists, such as `direct_url.json`,
`RECORD`, and bytecode, is not part of the stable parity surface.

## Native build recipe

`native/py_fonttools/py_fonttools.bzl` mirrors that install method directly:

```text
download and extract the exact fonttools-4.39.4 source archive by SHA256
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
using lib/python${PYTHON_ABI}/site-packages
cd <source>
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate fontTools package files, dist-info metadata, console scripts, and ttx manpage
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so both the local wheel
build and the prefix install happen only inside the sealed CUDA rootfs. It
never searches `PATH` for Python or pip; Python comes from the Bazel-native
venv prefix and Python packaging tools come from Bazel-native prefixes on
`PYTHONPATH`.

The mechanism verifier reports the expected build channel:

```text
native/py_fonttools/py_fonttools.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_PIP_PREFIX, PY_SETUPTOOLS_PREFIX, PY_WHEEL_PREFIX
```

That verifier also checks every prefix-file input, `python -m pip` invocation
through `PYTHON_VENV_PREFIX`, `PY_PIP_PREFIX` exposure on `PYTHONPATH`, Spack's
no-network/no-build-isolation/no-deps flags, `--prefix`, and clearing
`PYTHONHOME`.

## Prefix and behavior contract

The stable prefix surface includes:

- package tree: `lib/python3.13/site-packages/fontTools`
- metadata and license: `fonttools-4.39.4.dist-info`
- console scripts: `bin/fonttools`, `bin/pyftmerge`, `bin/pyftsubset`,
  `bin/ttx`
- data: `share/man/man1/ttx.1`

Generated installation metadata that embeds the temporary Spack stage path or
complete wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is
not part of the stable parity surface. The package installs no ELF objects, so
the ABI axis is expected to be empty.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_fonttools_native
```

It resolves the native `py-fonttools`, `python@3.13`, `python-venv`,
`py-pip`, `py-setuptools`, and `py-wheel` prefix markers, derives the Python
ABI from the native Python prefix at runtime, asserts `3.13`, sets the native
Python library path and package `PYTHONPATH`, imports
`fontTools.ttLib.TTFont`, exercises `fontTools.misc.transform.Transform`,
verifies the package metadata version, runs `ttx --version`, and prints:

```text
py-fonttools:4.39.4:TTFont:<Transform [2 0 0 2 6 8]>
```

The parity target is:

```text
//synthetic:py_fonttools_prefix_parity
```

It compares `@py_fonttools_native//:prefix` against the hermetic Spack
reference prefix and covers:

- selected layout parity for stable package files and metadata under
  `lib/python3.13/site-packages`, scripts, and the `ttx` manpage;
- byte-identical representative package, metadata, license, and manpage files;
- executable presence parity for the four console scripts;
- matching `ttx --version` behavior;
- empty ELF ABI axis.

Current status: native `py-fonttools` is re-seated on `python@3.13.13` and
gated inside the hermetic CUDA insula.
The focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
  TMPDIR=$VASO_ESTATE_ROOT/agents/trae/tmp \
  VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
  BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
  SPACK_ROOT_PKG='py-fonttools@4.39.4 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib' \
  VASO_NATIVE=1 \
  VASO_SPACK_TIMEOUT=600 \
  VASO_EXTRA_TEST_TARGETS='//synthetic:use_py_fonttools_native,//synthetic:py_fonttools_prefix_parity,//tools:hermetic_native_deps_guard_test,//tools:native_dep_wiring_live_test,//tools:native_build_mechanism_guard_unit_test,//tools:python_abi_literal_guard_unit_test' \
  ./run.sh
```

The run log is
`$VASO_ESTATE_ROOT/agents/trae/logs/py-fonttools-rerun-20260929T053118Z.log`.
It used Bazel's `@spack_dist//:spack` inside rootfs mode `cuda-bundle`,
reported hermetic Spack version `1.2.2`, regenerated `spack_graph.lock.json`
with root `spack_py_fonttools`, regenerated a 36-node `build_graph.json` ending
in `python@3.13.13`, `python-venv@1.0`, `py-pip@26.1.2`,
`py-setuptools@79.0.1`, `py-wheel@0.45.1`, and `py-fonttools@4.39.4`, and
passed `//tools:native_build_mechanism_guard_unit_test`,
`//tools:hermetic_native_deps_guard_test`,
`//tools:native_dep_wiring_live_test`, `//synthetic:use_py_fonttools_native`,
and `//synthetic:py_fonttools_prefix_parity`.
The parity report had `"ok": true`, 17/17 selected layout/data paths, no
missing or extra candidate paths, byte-identical package files, metadata,
license, and manpage, matching `ttx --version` output `4.39.4`, and an empty
ELF ABI axis.
