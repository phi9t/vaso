# py-cython native recipe

## Position in the hillclimb

`py-cython@3.2.4` is the next live Python-bound native provider in the pruned
`py-torch` frontier after ticket 14:

```text
86  py-cython  3.2.4  python_pip  native
87  nvtx       3.3.0  generic
97  py-pyyaml  6.0.3  python_pip
```

The focused reference graph for
`SPACK_ROOT_PKG='py-cython@3.2.4 ^python@3.13.13+...'` resolves
`root=spack_py_cython`, writes a 31-package lock, and writes a 36-node build
graph ending in:

```text
python@3.13.13 -> python-venv@1.0 -> py-pip@26.1.2
-> py-setuptools@79.0.1 -> py-wheel@0.45.1 -> py-cython@3.2.4
```

Spack still owns the DAG shape; the native flip changes only
`spack_py_cython.build` to `native` and re-exports `@py_cython_native//:lib`.
The generated link/runtime edges remain Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-cython-3.2.4-vdqgtwe3kxzopl2eumr4hl4tvohqmegi
```

Dependency prefixes from the same build environment:

```text
/vaso/cache/spack/opt/spack/linux-icelake/python-3.13.13-6u37x4adbcqbp247uvyjtokjsun4oewt
/vaso/cache/spack/opt/spack/linux-icelake/python-venv-1.0-ab2pdnquesi6tr3e5jrieb2itp4jdbab
/vaso/cache/spack/opt/spack/linux-icelake/py-pip-26.1.2-rjfumck4mkxpheunqnsc4okzhxxq53lv
/vaso/cache/spack/opt/spack/linux-icelake/py-setuptools-79.0.1-ahvs37zplvfrbijtnucmlc2upqxl5ijk
/vaso/cache/spack/opt/spack/linux-icelake/py-wheel-0.45.1-zkzfjve4om2w4y2nwjai4d2hp53vgnwt
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-cython-3.2.4-vdqgtwe3kxzopl2eumr4hl4tvohqmegi/.spack/repos/spack_repo/builtin/packages/py_cython/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyCython(PythonPackage)`
- selected build system: `python_pip`
- version: `3.2.4`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/c/cython/cython-3.2.4.tar.gz`
- source SHA256:
  `84226ecd313b233da27dc2eb3601b4f222b8209c3a7216d8733b031da1dc64e6`
- dependencies: `python` build/link/run, `python-venv` build/run,
  `py-setuptools` build/run, plus `py-pip` and `py-wheel` build inputs
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
  --prefix=<py-cython-prefix> \
  .
```

Spack supplies `pip` by putting the `py-pip` prefix on `PYTHONPATH`; the build
log reports:

```text
Using pip 26.1.2 from <py-pip-prefix>/lib/python3.13/site-packages/pip (python 3.13)
```

Pip builds an intermediate wheel from the source tree, then installs that
wheel into the prefix. Generated installation metadata that embeds temporary
stage paths or full file lists, such as `direct_url.json`, `RECORD`, and
bytecode, is not part of the stable parity surface.

## Native build recipe

`native/py_cython/py_cython.bzl` mirrors that install method directly:

```text
download and extract the exact cython-3.2.4 source archive by SHA256
read @python_313_native//:prefix_path.txt
read @python_venv_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_setuptools_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
derive PYTHON_ABI from PYTHON_PREFIX/bin/python3
validate PYTHON_PREFIX/bin/python${PYTHON_ABI}
validate PYTHON_PREFIX/include/python${PYTHON_ABI} plus lib
validate PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI}
validate PY_PIP_PREFIX/lib/python${PYTHON_ABI}/site-packages/pip
validate PY_SETUPTOOLS_PREFIX/lib/python${PYTHON_ABI}/site-packages/setuptools
validate PY_WHEEL_PREFIX/lib/python${PYTHON_ABI}/site-packages/wheel
compute importlib.machinery.EXTENSION_SUFFIXES[0] from native Python
clear PYTHONHOME
set PYTHONPATH from native py-pip, py-setuptools, py-wheel, and python-venv
for lib/python${PYTHON_ABI}/site-packages
set LD_LIBRARY_PATH, CFLAGS, and LDFLAGS from native Python
cd <source>
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate Cython package files, scripts, metadata, and 16 extension modules
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the C extension
build and prefix install happen only inside the sealed CUDA rootfs. It never
searches for Python or pip on the host; Python comes from Bazel-native Python
prefixes and Python packaging tools come from Bazel-native prefixes on
`PYTHONPATH`.

The mechanism verifier reports the expected build channel:

```text
native/py_cython/py_cython.bzl: python-pip-install: PYTHON_PREFIX, PYTHON_VENV_PREFIX, PY_PIP_PREFIX, PY_SETUPTOOLS_PREFIX, PY_WHEEL_PREFIX
```

That verifier checks every prefix-file input, the `python -m pip` invocation
through `PYTHON_VENV_PREFIX`, `PY_PIP_PREFIX` exposure on `PYTHONPATH`, Spack's
no-network/no-build-isolation/no-deps flags, `--prefix`, and clearing
`PYTHONHOME`.

## Prefix and behavior contract

The stable prefix surface includes:

- scripts: `bin/cython`, `bin/cythonize`, `bin/cygdb`
- package entry: `cython.py`
- package tree: `Cython`
- metadata: `cython-3.2.4.dist-info`
- CPython extension modules:
  `Cython/Compiler/{Code,FlowControl,FusedNode,LineTable,Parsing,Scanning,Visitor}`,
  `Cython/Plex/{Actions,DFA,Machines,Scanners,Transitions}`,
  `Cython/Runtime/refnanny`, `Cython/StringIOTree`, `Cython/Tempita/_tempita`,
  and `Cython/Utils`

The extension modules live under `site-packages`, not top-level `lib` or
`lib64`. `tools/abi_parity.py` therefore supports `--elf-path` so Python
package native flips can compare NEEDED libraries, SONAME, and exported
symbols for explicit extension-module paths.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_cython_native
```

It resolves the native `py-cython`, `python`, `python-venv`, `py-pip`,
`py-setuptools`, and `py-wheel` prefix markers, sets the native Python library
path and package `PYTHONPATH`, imports Cython and representative compiled
modules, asserts the ABI is `3.13`, asserts the extension suffix contains
`cpython-313`, runs `bin/cython -3` on a tiny `.pyx`, verifies
`cython --version`, and prints:

```text
py-cython:3.2.4:3.2.4:.cpython-313-x86_64-linux-gnu.so:Cython.Compiler.Parsing:Cython.Compiler.Visitor
```

The parity target is:

```text
//synthetic:py_cython_prefix_parity
```

It compares `@py_cython_native//:prefix` against the hermetic Spack reference
prefix and covers:

- selected layout parity for stable scripts, package files, metadata, and
  extension modules;
- byte-identical representative package files and metadata files;
- executable NEEDED parity for `bin/cython`, `bin/cythonize`, and `bin/cygdb`;
- `bin/cython --version` behavior parity;
- NEEDED, SONAME, and exported-symbol parity for all 16 CPython extension
  modules.

Current status: native `py-cython` is re-seated on `python@3.13.13` and gated
inside the hermetic CUDA insula. The focused verification used rootfs mode
`cuda-bundle`, Bazel-owned Spack `1.2.2`, and log:

```text
$VASO_ESTATE_ROOT/agents/trae/logs/py-cython-rerun-20260929T073159Z.log
```

It ran:

```text
SPACK_ROOT_PKG='py-cython@3.2.4 ^python@3.13.13+...'
VASO_NATIVE=1
VASO_EXTRA_TEST_TARGETS='//synthetic:py_cython_prefix_parity,//synthetic:use_py_cython_native,//tools:hermetic_native_deps_guard_test,//tools:native_dep_wiring_live_test'
./run.sh
```

The run finished with `RC=0`. `//synthetic:use_py_cython_native` printed the
3.13 extension suffix shown above. `//synthetic:py_cython_prefix_parity`
passed with `"ok": true`, 33 selected layout/data paths under
`lib/python3.13/site-packages`, no missing or extra candidate paths,
byte-identical representative package and metadata files, `bin/cython
--version` parity, and NEEDED/SONAME/exported-symbol parity for all 16
`*.cpython-313-x86_64-linux-gnu.so` extension modules. The shared guards also
passed; `//tools:native_dep_wiring_live_test` now reports 7 remaining
ticket-08 allowlisted wiring mismatches.
