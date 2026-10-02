# py-pyyaml native recipe

## Position in the hillclimb

`py-pyyaml@6.0.3` is the `PythonPackage` source build at pruned lean
`py-torch` topo index 97, after native `py-pyproject-metadata`:

```text
95  py-pathspec            1.1.1   python_pip  native
96  py-pyproject-metadata  0.11.0  python_pip  native
97  py-pyyaml              6.0.3   python_pip
98  py-setuptools-scm      8.2.1   python_pip  native
```

The focused reference graph for `SPACK_ROOT_PKG='py-pyyaml@6.0.3'` ends with
38 nodes. Spack still owns the DAG shape; the native flip changes only
`spack_py_pyyaml.build` to `native` and re-exports `@py_pyyaml_native//:lib`.
The generated link/runtime edges remain Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-pyyaml-6.0.3-e2cpgcjptuueolkmpiatqcjgx5hvgvit
```

Dependency prefixes from the same build environment:

```text
/vaso/cache/spack/opt/spack/linux-icelake/libyaml-0.2.5-o6r5nqmgjp7otoihpvvlhi2gkur7ldwn
/vaso/cache/spack/opt/spack/linux-icelake/py-cython-3.2.4-vdqgtwe3kxzopl2eumr4hl4tvohqmegi
/vaso/cache/spack/opt/spack/linux-icelake/py-pip-26.1.2-rjfumck4mkxpheunqnsc4okzhxxq53lv
/vaso/cache/spack/opt/spack/linux-icelake/py-setuptools-79.0.1-ahvs37zplvfrbijtnucmlc2upqxl5ijk
/vaso/cache/spack/opt/spack/linux-icelake/py-wheel-0.45.1-zkzfjve4om2w4y2nwjai4d2hp53vgnwt
/vaso/cache/spack/opt/spack/linux-icelake/python-venv-1.0-ab2pdnquesi6tr3e5jrieb2itp4jdbab
/vaso/cache/spack/opt/spack/linux-icelake/python-3.13.13-6u37x4adbcqbp247uvyjtokjsun4oewt
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-pyyaml-6.0.3-e2cpgcjptuueolkmpiatqcjgx5hvgvit/.spack/repos/spack_repo/builtin/packages/py_pyyaml/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyPyyaml(PythonPackage)`
- selected build system: `python_pip`
- version: `6.0.3`
- selected variant: `+libyaml`
- source payload: PyPI source archive
  `https://pypi.io/packages/source/p/pyyaml/pyyaml-6.0.3.tar.gz`
- source SHA256:
  `d76623373421df22fb4cf8817020cbb7ef15c725b9d5e45f17e189bfc384190f`
- active dependencies for `@6.0.3 +libyaml`: `libyaml` link,
  `py-cython` build, `py-pip` build, `py-setuptools@62:` build,
  `py-wheel` build, `python` build/link/run, and `python-venv` build/run
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
  --prefix=<py-pyyaml-prefix> \
  --config-settings=--global-option=--with-libyaml \
  .
```

Spack supplies `pip`, `setuptools`, `wheel`, `Cython`, and `python-venv` by
putting their prefixes on `PYTHONPATH`. It supplies `libyaml` through
`CFLAGS=-I<libyaml>/include`, `LDFLAGS=-L<libyaml>/lib
-Wl,-rpath,<libyaml>/lib`, and `CYTHON_FORCE_REGEN=1`.

The installed wheel metadata says:

```text
Generator: setuptools (79.0.1)
Root-Is-Purelib: false
Tag: cp313-cp313-linux_x86_64
Requires-Python: >=3.8
```

Generated installation metadata that embeds temporary stage paths or complete
wheel file lists, such as `direct_url.json`, `RECORD`, and bytecode, is not
part of the stable parity surface.

## Native build recipe

`native/py_pyyaml/py_pyyaml.bzl` mirrors that install method directly:

```text
download and extract the exact pyyaml-6.0.3 source archive by SHA256
read @libyaml_native//:prefix_path.txt
read @python_313_native//:prefix_path.txt
read @python_venv_native//:prefix_path.txt
read @py_cython_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_setuptools_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
validate LIBYAML_PREFIX/include/yaml.h and LIBYAML_PREFIX/lib
derive PYTHON_ABI from PYTHON_PREFIX/bin/python3
validate PYTHON_PREFIX/bin/python${PYTHON_ABI}, include/python${PYTHON_ABI}, and lib
validate PYTHON_VENV_PREFIX/bin/python3 and bin/python${PYTHON_ABI}
validate py-pip, py-setuptools, py-wheel, and py-cython site-packages under lib/python${PYTHON_ABI}/site-packages
clear PYTHONHOME
set PYTHONPATH from native py-pip, py-setuptools, py-wheel, py-cython, and python-venv under lib/python${PYTHON_ABI}/site-packages
set LD_LIBRARY_PATH from native python and libyaml
set CYTHON_FORCE_REGEN=1
set CFLAGS/LDFLAGS from native libyaml and python${PYTHON_ABI} prefixes
cd <source>
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> \
  --config-settings=--global-option=--with-libyaml .
compute the CPython extension suffix from the native interpreter
validate yaml package files, _yaml package, C extension, dist-info metadata, and license under lib/python${PYTHON_ABI}/site-packages
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the Cython rebuild,
C extension compile/link, wheel build, and prefix install happen only inside the
sealed CUDA rootfs. It never searches for Python, pip, setuptools, wheel,
Cython, or libyaml on the host; all inputs are Bazel-native prefixes.

The mechanism verifier reports the expected build channel:

```text
native/py_pyyaml/py_pyyaml.bzl: python-pip-install: LIBYAML_PREFIX, PYTHON_PREFIX, PYTHON_VENV_PREFIX, PY_CYTHON_PREFIX, PY_PIP_PREFIX, PY_SETUPTOOLS_PREFIX, PY_WHEEL_PREFIX
```

That verifier checks every prefix-file input, the `python -m pip` invocation
through `PYTHON_VENV_PREFIX`, `PY_PIP_PREFIX` exposure on `PYTHONPATH`,
Spack's no-network/no-build-isolation/no-deps flags, `--prefix`, and clearing
`PYTHONHOME`.

## Prefix and behavior contract

The stable prefix surface includes:

- package trees: `lib/python3.13/site-packages/yaml` and
  `lib/python3.13/site-packages/_yaml`
- C extension: `lib/python3.13/site-packages/yaml/_yaml.cpython-313-x86_64-linux-gnu.so`
- metadata and license: `pyyaml-6.0.3.dist-info`

The C extension is not itself SONAME-bearing. Its dynamic dependency surface is
`libyaml-0.so.2` and `libc.so.6`; the `RUNPATH` points at the selected libyaml
prefix.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_pyyaml_native
```

It resolves the native `py-pyyaml`, `libyaml`, `python`, `python-venv`,
`py-cython`, `py-pip`, `py-setuptools`, and `py-wheel` prefix markers, sets the
native Python library path and package `PYTHONPATH`, imports `yaml` and
`yaml.cyaml`, checks version metadata, and parses/dumps a mapping through
`yaml.CLoader` and `yaml.CDumper`. Expected output:

```text
py-pyyaml:6.0.3:True:CDumper:3
```

The parity target is:

```text
//synthetic:py_pyyaml_prefix_parity
```

It compares `@py_pyyaml_native//:prefix` against the hermetic Spack reference
prefix and covers:

- selected layout parity for stable package files, metadata, and license;
- byte-identical package modules and representative metadata;
- explicit ELF parity for `_yaml.cpython-313-x86_64-linux-gnu.so`, including
  matching `NEEDED` entries and exported symbols.

Current status: native `py-pyyaml` is gated inside the hermetic CUDA insula.
The focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='py-pyyaml@6.0.3' \
  '^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib' \
VASO_NATIVE=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:py_pyyaml_prefix_parity,//synthetic:use_py_pyyaml_native,//tools:hermetic_native_deps_guard_test,//tools:native_dep_wiring_live_test,//tools:migration_ledger_check_live_test,//tools:migration_ledger_check_unit_test' \
VASO_SPACK_TIMEOUT=1200 \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside rootfs mode `cuda-bundle`,
reported hermetic Spack version `1.2.2`, regenerated `spack_graph.lock.json`
with root `spack_py_pyyaml`, a 33-package lock, and a 38-node build graph tail
`python@3.13.13`, `python-venv@1.0`, `py-pip@26.1.2`,
`py-setuptools@79.0.1`, `py-wheel@0.45.1`, `py-cython@3.2.4`,
`py-pyyaml@6.0.3`. `//synthetic:py_pyyaml_prefix_parity` passed with
`"ok": true`, 25/25 stable layout/data paths, and explicit ELF
NEEDED/SONAME/exported-symbol parity for
`lib/python3.13/site-packages/yaml/_yaml.cpython-313-x86_64-linux-gnu.so`.
`//synthetic:use_py_pyyaml_native` passed with output
`py-pyyaml:6.0.3:True:CDumper:3`; the guard targets passed in the same insula
run. Full log:
`$VASO_ESTATE_ROOT/agents/trae/logs/py-pyyaml-rerun-20260929T082631Z.log`.
