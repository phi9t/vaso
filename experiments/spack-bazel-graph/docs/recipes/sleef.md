# SLEEF native recipe

## Position in the hillclimb

`sleef@3.8` is the vector math CMakePackage in the current full
`py-torch@2.14.0` CUDA closure with the human scope decisions applied:
`~magma`, `~mkldnn`, and `^py-networkx~default`. The cu129 and cu130 graph
captures both contain one `sleef` node at topological index 137, after
`py-pybind11` and before `valgrind`/`py-torch`.

Spack still owns the DAG shape. The native flip changes only
`spack_sleef.build` to `native` and re-exports `@sleef_native//:lib`; generated
dependency edges remain Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `Sleef(CMakePackage)`
- homepage and source remote: `https://github.com/shibatch/sleef`
- version: `3.8`
- source archive:
  `https://github.com/shibatch/sleef/archive/3.8.tar.gz`
- source SHA256:
  `a12ccd50f57083c530e1c76f10d52865defbd19fc9e2c85b483493065709874a`
- source strip prefix: `sleef-3.8`
- active CMake generator: `ninja`
- concrete graph dependencies: `cmake` and `ninja` as build dependencies,
  plus the rootfs toolchain leaves
- active CMake options: `SLEEF_BUILD_TESTS=OFF` and
  `CMAKE_POSITION_INDEPENDENT_CODE=ON`
- patches: none

The installed stable prefix surface is:

- `include/sleef.h`
- `lib/libsleef.a`
- `lib/libsleefgnuabi.a`
- `lib/pkgconfig/sleef.pc`
- `lib/cmake/sleef/sleefConfig.cmake`
- `lib/cmake/sleef/sleefConfigVersion.cmake`
- `lib/cmake/sleef/sleefTargets.cmake`
- `lib/cmake/sleef/sleefTargets-release.cmake`

There are no public shared libraries or executables in this concrete prefix.

## Native build recipe

`native/sleef/sleef.bzl` mirrors the CMakePackage install:

```text
download and extract the exact SLEEF 3.8 release archive by SHA256
read @cmake_native//:prefix_path.txt
read @ninja_native//:prefix_path.txt
validate CMAKE_PREFIX/bin/cmake
validate NINJA_PREFIX/bin/ninja-build
set PATH=<cmake-prefix>/bin:<ninja-prefix>/bin:$PATH
set CMAKE_PREFIX_PATH=<cmake-prefix>;<ninja-prefix>
run "$CMAKE_PREFIX/bin/cmake" -G Ninja with Spack's concrete CMake args
run "$NINJA_PREFIX/bin/ninja-build" -v
run "$NINJA_PREFIX/bin/ninja-build" install
validate the eight installed files and reject any produced .so
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so configure, build,
install, and dependency discovery run only inside the sealed CUDA rootfs. It
never searches for host Spack; CMake and Ninja are supplied by Bazel-native
prefixes.

The mechanism verifier reports this build channel:

```text
native/sleef/sleef.bzl: cmake: CMAKE_PREFIX, NINJA_PREFIX
```

## Prefix and behavior contract

The smoke target is:

```text
//synthetic:use_sleef_native
```

It depends on the generated `@spack_sleef//:lib` provider surface, includes
`<sleef.h>`, checks `sin(x)^2 + cos(x)^2`, and prints:

```text
sleef:3.8.0:1.000000000000
```

The parity target is:

```text
//synthetic:sleef_prefix_parity
```

It compares `@sleef_native//:prefix` against the hermetic Spack reference
prefix injected by `run.sh`, checking the installed header, both static
archives, prefix-normalized `sleef.pc`, CMake package metadata, and downstream
C link-and-run behavior with `-lsleef -lsleefgnuabi -lm`.

## ODR-sensitive provider invariant

This slice does not add protobuf, gRPC, Abseil, or Boost providers. Those
families must stay on one unified compatible concrete version family; the
generator rejects unqualified overrides and mixed concrete versions for those
ODR-sensitive providers.
