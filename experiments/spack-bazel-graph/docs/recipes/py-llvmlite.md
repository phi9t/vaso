# py-llvmlite native build-action skeleton

## Position in the hillclimb

`py-llvmlite@0.47.0` is the next lean `py-torch` frontier node after native
`pthreadpool`:

```text
143  psimd          2020-05-17  cmake       native
144  pthreadpool    2023-08-29  cmake       native
145  py-llvmlite    0.47.0      python_pip  spack
```

The focused reference graph for
`SPACK_ROOT_PKG='py-llvmlite@0.47.0 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib'`
was generated with `--fresh` and has 52 nodes. It contains only
`python@3.13.13` on the Python axis and ends with:

```text
44  python-venv     1.0     generic     spack
45  py-pip          26.1.2  generic     spack
46  py-setuptools   82.0.1  generic     spack
47  py-wheel        0.45.1  generic     spack
48  re2c            4.4     autotools   spack
49  ninja           1.13.2  generic     spack
50  llvm            20.1.8  cmake       spack
51  py-llvmlite     0.47.0  python_pip  spack
```

This checkpoint captures the recipe and a dry-run native build-action skeleton
only. It does not flip `py-llvmlite` in `native_overrides.json`, because the
dependency boundary still includes `llvm`, whose full native build and ABI gate
remain token-gated.

## Hermetic Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout. The
vendored distribution is upstream Spack `v1.2.2`.

The focused graph records the concrete node as:

```text
py-llvmlite@0.47.0
build_system=python_pip
status=spack
spack_hash=rdoxkxtisrjx3esmls7xmrdebl5bavze
```

The selected Python node in that focused graph is:

```text
python@3.13.13
spack_hash=micqjwdjbkfwhcggzvsshifmka7h7z64
```

The lean PyTorch graph records the frontier node as:

```text
py-llvmlite@0.47.0
build_system=python_pip
status=spack
spack_hash=x5ree3czwtiyhiil36kftaavfpxalzh6
```

The hermetic Spack recipe facts are:

- package class: `PyLlvmlite(PythonPackage)`
- selected build system: `python_pip`
- version: `0.47.0`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/l/llvmlite/llvmlite-0.47.0.tar.gz`
- source SHA256:
  `62031ce968ec74e95092184d4b0e857e444f8fdff0b8f9213707699570c33ccc`
- build inputs: `cmake`, `binutils` on Linux, `py-setuptools`, C compiler, and
  C++ compiler
- build/run inputs: `python@3.10:3.14` for `@0.46:`
- LLVM compatibility: `llvm@20` for `@0.45:`
- selected migration line: `python@3.13.13`

The focused dependency boundary is:

```text
build: binutils, cmake, compiler-wrapper, gcc, llvm, py-pip, py-setuptools,
       py-wheel, python, python-venv
link/run: gcc-runtime, glibc, llvm, python, python-venv
```

`compiler-wrapper`, `gcc`, `gcc-runtime`, and `glibc` remain toolchain/runtime
infrastructure. The native skeleton consumes explicit Bazel prefix files for
the package-level dependencies: binutils, CMake, LLVM, Python, python-venv,
py-pip, py-setuptools, and py-wheel.

Spack's recipe sets these llvmlite-specific build environment variables for
the non-Fujitsu compiler path:

```text
CXX_FLTO_FLAGS=-flto -fPIC
LD_FLTO_FLAGS=-Wl,--exclude-libs=ALL
```

## Upstream build interface

The llvmlite source build is a Python package install that compiles the native
`llvmlite.binding` bridge through CMake:

```text
setup.py -> ffi/build.py -> cmake -G "Unix Makefiles" -> libllvmlite.so
```

For `0.47.0`, upstream `ffi/CMakeLists.txt` requires LLVM 20 through
`find_package(LLVM REQUIRED CONFIG)`, defaults to static LLVM linkage
(`LLVMLITE_SHARED=OFF`), and emits:

```text
lib/python3.13/site-packages/llvmlite/binding/libllvmlite.so
```

The native build must therefore pin `LLVM_CONFIG`, `LLVM_DIR`, and
`CMAKE_PREFIX_PATH` to the Spack-selected LLVM prefix and disable CMake's system
package registries and system search path. The Python ABI is derived from the
selected `python_prefix/bin/python3` interpreter and must be `3.13`.

## Native skeleton

`native/py_llvmlite/py_llvmlite.bzl` is intentionally gated:

- it refuses repository evaluation outside the hermetic insula;
- it reads every dependency through mandatory Bazel `*_prefix_file` labels;
- it writes `build_plan.json` through `native/py_llvmlite/plan.py`;
- the plan records prefix inputs, `PYTHON_ABI`, `PATH`, `PYTHONPATH`,
  `LD_LIBRARY_PATH`, `LLVM_CONFIG`, `LLVM_DIR`, `CMAKE`, `CMAKE_PREFIX_PATH`,
  `CMAKE_ARGS`, compiler/linker flags, pip flags, and the emitted Python prefix
  layout;
- without `VASO_NATIVE_PY_LLVMLITE_TOKEN=build-native-py-llvmlite`, it emits
  only the plan and reviewable `build.sh`, and does not build llvmlite;
- with the token, it still refuses the full build in this checkpoint until LLVM
  has a reviewed native provider or an explicitly accepted Spack-external
  boundary and the prefix/ABI gates are wired.

The future execute path will replay Spack's PythonPackage install method:

```text
PYTHON_VENV_PREFIX/bin/python3 -m pip \
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
  --prefix=<py-llvmlite-prefix> \
  .
```

The mechanism verifier reports the expected build channel:

```text
native/py_llvmlite/py_llvmlite.bzl: python-pip-install: BINUTILS_PREFIX, CMAKE_PREFIX, LLVM_PREFIX, PYTHON_PREFIX, PYTHON_VENV_PREFIX, PY_PIP_PREFIX, PY_SETUPTOOLS_PREFIX, PY_WHEEL_PREFIX
```

That verifier checks the explicit prefix inputs, the `python -m pip` invocation
through `PYTHON_VENV_PREFIX`, `PY_PIP_PREFIX` exposure on `PYTHONPATH`, Spack's
no-network/no-build-isolation/no-deps flags, `--prefix`, and the package's
CMake/LLVM prefix wiring.

Focused graph-only verification ran inside the CUDA insula with Bazel-owned
Spack v1.2.2, `--graph-only`, `--graph-no-prefix`, and `--fresh`; it wrote
`py_llvmlite_build_graph.json` with 52 nodes and only `python@3.13.13`. The
captured log is:

```text
$VASO_ESTATE_ROOT/agents/trae/logs/py-llvmlite-graph-313-fresh-20260930T031608Z.log
```

## Prefix and ABI contract

The planned stable prefix surface includes:

- package tree:
  `lib/python3.13/site-packages/llvmlite`
- native bridge:
  `lib/python3.13/site-packages/llvmlite/binding/libllvmlite.so`
- CPython stub extension:
  `lib/python3.13/site-packages/llvmlite/binding/_stub.cpython-313-x86_64-linux-gnu.so`
- metadata:
  `lib/python3.13/site-packages/llvmlite-0.47.0.dist-info`

Because the native libraries live under `site-packages`, the ABI gate must use
explicit `--elf-path` coverage for the Python extension surfaces instead of
only scanning top-level `lib` directories. The pending gate must compare layout,
NEEDED libraries, SONAME/null-SONAME state, exported symbols for
`libllvmlite.so` and the CPython stub extension, import behavior, and a minimal
LLVM-backed binding smoke.

## Planned native flip

Before adding `py-llvmlite` to `native_overrides.json`, the slice needs:

- the LLVM boundary resolved by a token-authorized native LLVM build or an
  explicit Spack-external acceptance for the LLVM prefix;
- the token-authorized `py-llvmlite` build inside the CUDA insula;
- layout and ABI parity against the hermetic Spack reference prefix;
- a downstream import/link smoke that uses the generated `@spack_py_llvmlite`
  provider;
- registration as a Spack external only after parity is green.

## ODR-sensitive provider invariant

This slice does not add protobuf, gRPC, Abseil, or Boost providers. Those
families must stay on one unified compatible concrete version family across all
companion packages before any native flip.
