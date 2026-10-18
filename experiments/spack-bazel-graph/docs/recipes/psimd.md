# psimd native recipe

## Position in the hillclimb

`psimd@2020-05-17` is the next CMakePackage source build after native
HarfBuzz in the lean `py-torch` frontier:

```text
142  harfbuzz  11.5.1      meson  native
143  psimd     2020-05-17  cmake
144  pthreadpool           cmake
```

The focused reference graph for `SPACK_ROOT_PKG='psimd'` has 37 build-graph
nodes and ends in `python`, `re2c`, `ninja`, then `psimd`. Spack still owns the
DAG shape; the native flip changes only `spack_psimd.build` to `native` and
re-exports `@psimd_native//:lib`.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout. The
vendored distribution observed by the self-check is upstream Spack `v1.2.2`.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/psimd-2020-05-17-tupsltfk2yr6pyqia53dw2ncchf2g2tx
```

Hermetic recipe path observed from the Bazel-owned Spack package repository:

```text
/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/psimd/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `Psimd(CMakePackage)`
- homepage and git remote: `https://github.com/Maratyszcza/psimd`
- version: `2020-05-17`
- source commit:
  `072586a71b55b7f8c584153d223e95687148a900`
- source archive used by the native rule:
  `https://github.com/Maratyszcza/psimd/archive/072586a71b55b7f8c584153d223e95687148a900.tar.gz`
- source SHA256:
  `f6c4dab91ae9a03b3019e7cab0572743afd0e1b6e75b97fcca50259c737c924e`
- CMake generator: `Ninja`
- build dependencies: C, CXX, CMake `@2.8.12:`, and Ninja via generator

The concrete Spack configure command used hermetic CMake and Ninja prefixes:

```text
cmake -G Ninja \
  -DCMAKE_INSTALL_PREFIX:STRING=<psimd-prefix> \
  -DCMAKE_INSTALL_RPATH_USE_LINK_PATH:BOOL=ON \
  -DCMAKE_INSTALL_RPATH:STRING=<psimd-prefix>/lib;<psimd-prefix>/lib64 \
  -DCMAKE_PREFIX_PATH:STRING=<cmake-prefix>;<compiler-wrapper-prefix>;<gcc-runtime-prefix>;<ninja-prefix> \
  -DCMAKE_BUILD_TYPE:STRING=Release \
  -DCMAKE_INTERPROCEDURAL_OPTIMIZATION:BOOL=OFF \
  -DCMAKE_POLICY_DEFAULT_CMP0090:STRING=NEW \
  -DCMAKE_FIND_USE_PACKAGE_REGISTRY:BOOL=OFF \
  -DCMAKE_EXPORT_COMPILE_COMMANDS:BOOL=ON
```

The build phase reports `ninja: no work to do.` because psimd is header-only.
The installed stable prefix surface is:

- `include/psimd.h`

There are no public shared libraries, static libraries, executables,
pkg-config files, or CMake package files in this concrete prefix.

## Native build recipe

`native/psimd/psimd.bzl` mirrors the CMakePackage install:

```text
download and extract the exact psimd commit archive by SHA256
read @cmake_native//:prefix_path.txt
read @ninja_native//:prefix_path.txt
validate CMAKE_PREFIX/bin/cmake
validate NINJA_PREFIX/bin/ninja-build
set PATH=<cmake-prefix>/bin:<ninja-prefix>/bin:$PATH
set CMAKE_PREFIX_PATH=<cmake-prefix>;<ninja-prefix>
run "$CMAKE_PREFIX/bin/cmake" -G Ninja with Spack's concrete CMake args
run "$NINJA_PREFIX/bin/ninja-build" -v
run "$NINJA_PREFIX/bin/ninja-build" install
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so configure, build,
install, and dependency discovery run only inside the sealed CUDA rootfs. It
never searches for host Spack; all Spack facts come from the Bazel-vendored
Spack run, and CMake plus Ninja are supplied by Bazel-native prefixes.

The mechanism verifier reports this build channel:

```text
native/psimd/psimd.bzl: cmake: CMAKE_PREFIX, NINJA_PREFIX
```

`cmake` is the mechanism-specific guard for this native provider. It requires
explicit `*_prefix_file` attrs, shell-side prefix checks, and dependency
injection through CMake/PATH channels rather than ambient host discovery.

## Prefix and behavior gate

The smoke target is:

```text
//synthetic:use_psimd_native
```

It depends on the generated `@spack_psimd//:lib` provider surface, includes
`<psimd.h>`, round-trips four `float` lanes through `psimd_load_f32` and
`psimd_store_f32`, and prints:

```text
psimd:1.0:-2.0:3.5:8.0
```

The parity target is:

```text
//synthetic:psimd_prefix_parity
```

It compares `@psimd_native//:prefix` against the hermetic Spack reference
prefix with `--data-path include/psimd.h`. The ABI axis is intentionally empty
because this concrete prefix has no libraries or executables.

Focused verification passed inside the CUDA 12.9.1 insula with
`VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT`,
`SPACK_ROOT_PKG=psimd`, `VASO_NATIVE=1`, and Bazel 9.2.0:

- `//synthetic:spack_selfcheck` reported
  `hermetic spack (Bazel-owned) version: 1.2.2`.
- `//tools:hermetic_native_deps_guard_test` reported
  `native/psimd/psimd.bzl: cmake: CMAKE_PREFIX, NINJA_PREFIX`.
- `//synthetic:use_psimd_native` passed with
  `psimd:1.0:-2.0:3.5:8.0`.
- `//synthetic:psimd_prefix_parity` passed against
  `/vaso/cache/spack/opt/spack/linux-icelake/psimd-2020-05-17-tupsltfk2yr6pyqia53dw2ncchf2g2tx`.
  The candidate prefix had exactly one layout path, `include/psimd.h`; its
  SHA256 matched the reference
  `e33f1b932b322820ab8c8e0c85ca8f508b1d578e9551c45f8caf2f3b7a79ab5e`, and
  the downstream link-and-run output matched the reference.
