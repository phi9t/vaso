# FXdiv native recipe

## Position in the hillclimb

`fxdiv@2020-04-17` is the next CMakePackage source build after native `fp16`
in the lean `py-torch` frontier:

```text
132  cpuinfo  2025-11-14  cmake  native
133  fp16     2020-05-14  cmake  native
134  fxdiv    2020-04-17  cmake
```

The focused reference graph for `SPACK_ROOT_PKG='fxdiv'` has 37 build-graph
nodes and ends in `python`, `re2c`, `ninja`, then `fxdiv`. Spack still owns the
DAG shape; the native flip changes only `spack_fxdiv.build` to `native` and
re-exports `@fxdiv_native//:lib`.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout. The
vendored distribution is upstream Spack `v1.2.2`.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/fxdiv-2020-04-17-ekuzonkcswbcvwxgyoyvfregs2fmdqnx
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/fxdiv-2020-04-17-ekuzonkcswbcvwxgyoyvfregs2fmdqnx/.spack/repos/spack_repo/builtin/packages/fxdiv/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `Fxdiv(CMakePackage)`
- homepage and git remote: `https://github.com/Maratyszcza/FXdiv`
- version: `2020-04-17`
- source commit:
  `b408327ac2a15ec3e43352421954f5b1967701d1`
- source archive used by the native rule:
  `https://github.com/Maratyszcza/FXdiv/archive/b408327ac2a15ec3e43352421954f5b1967701d1.tar.gz`
- source SHA256:
  `9ccf554541666b5c089ad5dd465141d671c99971f36d72f313652f5c49ffce14`
- CMake generator: `Ninja`
- build dependencies: C, CXX, CMake `@3.5:`, Python, and Ninja via generator
- concrete CMake args:
  `FXDIV_BUILD_TESTS=OFF`,
  `FXDIV_BUILD_BENCHMARKS=OFF`

The concrete Spack configure command used hermetic CMake, Ninja, and Python
prefixes:

```text
cmake -G Ninja \
  -DCMAKE_INSTALL_PREFIX:STRING=<fxdiv-prefix> \
  -DCMAKE_INSTALL_RPATH_USE_LINK_PATH:BOOL=ON \
  -DCMAKE_INSTALL_RPATH:STRING=<fxdiv-prefix>/lib;<fxdiv-prefix>/lib64 \
  -DCMAKE_PREFIX_PATH:STRING=<cmake-prefix>;<compiler-wrapper-prefix>;<gcc-runtime-prefix>;<ninja-prefix>;<python-prefix> \
  -DCMAKE_BUILD_TYPE:STRING=Release \
  -DCMAKE_INTERPROCEDURAL_OPTIMIZATION:BOOL=OFF \
  -DCMAKE_POLICY_DEFAULT_CMP0090:STRING=NEW \
  -DCMAKE_FIND_USE_PACKAGE_REGISTRY:BOOL=OFF \
  -DCMAKE_EXPORT_COMPILE_COMMANDS:BOOL=ON \
  -DPYTHON_EXECUTABLE:PATH=<python-prefix>/bin/python3 \
  -DPython_EXECUTABLE:PATH=<python-prefix>/bin/python3 \
  -DPython3_EXECUTABLE:PATH=<python-prefix>/bin/python3 \
  -DFXDIV_BUILD_TESTS:BOOL=OFF \
  -DFXDIV_BUILD_BENCHMARKS:BOOL=OFF
```

The build phase reports `ninja: no work to do.` because FXdiv is header-only.
The installed stable prefix surface is:

- `include/fxdiv.h`

There are no public shared libraries, static libraries, executables,
pkg-config files, or CMake package files in this concrete prefix.

## Native build recipe

`native/fxdiv/fxdiv.bzl` mirrors the CMakePackage install:

```text
download and extract the exact FXdiv commit archive by SHA256
read @cmake_native//:prefix_path.txt
read @ninja_native//:prefix_path.txt
read @python_313_native//:prefix_path.txt
validate CMAKE_PREFIX/bin/cmake
validate NINJA_PREFIX/bin/ninja-build
validate PYTHON_PREFIX/bin/python3
set PATH=<cmake-prefix>/bin:<ninja-prefix>/bin:<python-prefix>/bin:$PATH
set CMAKE_PREFIX_PATH=<cmake-prefix>;<ninja-prefix>;<python-prefix>
run "$CMAKE_PREFIX/bin/cmake" -G Ninja with Spack's concrete CMake args
run "$NINJA_PREFIX/bin/ninja-build" -v
run "$NINJA_PREFIX/bin/ninja-build" install
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so configure, build,
install, and dependency discovery run only inside the sealed CUDA rootfs. It
never searches for host Spack; all Spack facts come from the Bazel-vendored
Spack run, and CMake, Ninja, and Python are supplied by Bazel-native prefixes.

The mechanism verifier reports this build channel:

```text
native/fxdiv/fxdiv.bzl: cmake: CMAKE_PREFIX, NINJA_PREFIX, PYTHON_PREFIX
```

`cmake` is the mechanism-specific guard for this native provider. It requires
explicit `*_prefix_file` attrs, shell-side prefix checks, and dependency
injection through CMake/PATH channels rather than ambient host discovery.

## Prefix and behavior gate

The smoke target is:

```text
//synthetic:use_fxdiv_native
```

It depends on the generated `@spack_fxdiv//:lib` provider surface, includes
`<fxdiv.h>`, divides `100` by `7` through the initialized FXdiv divisor, and
prints:

```text
fxdiv:14:2
```

The parity target is:

```text
//synthetic:fxdiv_prefix_parity
```

It compares `@fxdiv_native//:prefix` against the hermetic Spack reference
prefix and covers:

- the complete one-header installed layout;
- byte-identical public header content;
- empty shared-library ABI axis;
- downstream C link-and-run output through the generated `@spack_fxdiv`
  provider.

## ODR-sensitive provider invariant

This slice does not add protobuf, gRPC, Abseil, or Boost providers. Those
families must stay on one unified compatible concrete version family across
all companion packages before any native flip. The generator rejects
unqualified overrides and rejects mixed concrete family versions, including
protobuf/Python protobuf and gRPC/gRPC C++ pairings.
