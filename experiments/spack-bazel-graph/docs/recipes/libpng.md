# libpng@1.6.58

## Position in the hillclimb

`libpng` is the py-torch frontier node immediately after native
`libjpeg-turbo`. In the captured `SPACK_ROOT_PKG=py-torch` graph it appears as:

```text
66  libpng  1.6.58  cmake
```

The focused reference graph used for this migration is:

```bash
SPACK_ROOT_PKG='libpng@1.6.58'
```

The reference and native runs used Bazel's vendored `@spack_dist//:spack`
inside the isolated CUDA 12.9.1 insula. The native run flips `spack_libpng` to
`@libpng_native//:lib` without changing the Spack DAG edges.

## Hermetic Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path observed in the isolated estate:

```text
/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/libpng/package.py
```

Source provenance from the Spack recipe:

- package class: `Libpng(CMakePackage)`
- Spack build-system bucket: `cmake`
- upstream source URL:
  `https://prdownloads.sourceforge.net/libpng/libpng-1.6.58.tar.xz`
- SHA256: `28eb403f51f0f7405249132cecfe82ea5c0ef97f1b32c5a65828814ae0d34775`
- concrete variants: `libs=shared,static`, `~pic`,
  `build_type=Release`, `generator=make`, `~ipo`
- build dependency channel: `cmake@3.14:`
- link dependency channel: `zlib-api`, concretized to native `zlib-ng`
- concrete CMake args:
  `-DCMAKE_CXX_FLAGS=-I<zlib-ng>/include -DZLIB_ROOT=<zlib-ng-prefix> -DPNG_SHARED=ON -DPNG_STATIC=ON -DCMAKE_POSITION_INDEPENDENT_CODE=OFF -DZLIB_LIBRARY=<zlib-ng-prefix>/lib/libz.so`

The focused `libpng@1.6.58` node has:

```text
build: cmake, compiler-wrapper, gcc, gmake
link: gcc-runtime, glibc, zlib-ng
```

The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/libpng-1.6.58-6sqofp65h6ibavvejpiwd2g3tmdjkw3d
```

The ABI/prefix-relevant installed surface is:

```text
bin/libpng-config
bin/libpng16-config
bin/png-fix-itxt
bin/pngfix
include/png.h
include/pngconf.h
include/pnglibconf.h
include/libpng16/png.h
include/libpng16/pngconf.h
include/libpng16/pnglibconf.h
lib/libpng16.so -> libpng16.so.16 -> libpng16.so.16.58.0
lib/libpng.so -> libpng16.so
lib/libpng.a -> libpng16.a
lib/libpng16.a
lib/pkgconfig/libpng.pc
lib/pkgconfig/libpng16.pc
lib/cmake/PNG/
lib/libpng/
share/man/man3/libpng.3
share/man/man3/libpngpf.3
share/man/man5/png.5
```

## Native build recipe

`native/libpng/libpng.bzl` mirrors the concrete Spack CMake flow:

```text
download libpng-1.6.58.tar.xz
validate native CMake and zlib-ng prefixes from Bazel prefix files
export PATH=<cmake>/bin:$PATH
export CMAKE_PREFIX_PATH=<cmake>;<zlib-ng>
export CMAKE_LIBRARY_PATH=<zlib-ng>/lib
export CMAKE_INCLUDE_PATH=<zlib-ng>/include
cmake -G "Unix Makefiles" \
  -DCMAKE_INSTALL_PREFIX=<prefix> \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF \
  -DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF \
  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
  -DCMAKE_CXX_FLAGS=-I<zlib-ng>/include \
  -DZLIB_ROOT=<zlib-ng-prefix> \
  -DZLIB_LIBRARY=<zlib-ng-prefix>/lib/libz.so \
  -DPNG_SHARED=ON \
  -DPNG_STATIC=ON \
  -DCMAKE_POSITION_INDEPENDENT_CODE=OFF
make
make install
emit prefix_path.txt
```

The native provider exposes:

- `@libpng_native//:prefix` for the installed prefix filegroup;
- `@libpng_native//:prefix_path.txt` for downstream native repository rules;
- `@libpng_native//:lib` with the same public link surface as Spack:
  `png16` plus the native zlib-ng dependency.

The corresponding mechanism verifier is the CMake dependency-prefix case:

```text
native/libpng/libpng.bzl: cmake: CMAKE_PREFIX, ZLIB_PREFIX
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, consumes
`@cmake_native//:prefix_path.txt` and `@zlib_ng_native//:prefix_path.txt`,
validates `CMAKE_PREFIX/bin/cmake`, `ZLIB_PREFIX/include/zlib.h`, and
`ZLIB_PREFIX/lib/libz.so`, and threads dependency lookup through
`CMAKE_PREFIX_PATH`, `CMAKE_LIBRARY_PATH`, `CMAKE_INCLUDE_PATH`, `ZLIB_ROOT`,
and `ZLIB_LIBRARY`.

## Gates

The smoke target is:

```text
//synthetic:use_libpng_native
```

It compiles a C consumer against `@libpng_native//:lib`, includes `<png.h>`,
creates a write/info struct pair, checks runtime/header version agreement, and
prints:

```text
libpng:1.6.58:header=10658
```

`//synthetic:libpng_abi_parity` compares the native prefix against the hermetic
Spack reference. It covers:

- 22 installed layout paths;
- SONAME and exported-symbol parity for `libpng16.so.16.58.0`;
- static archive member and symbol parity for `libpng.a` and `libpng16.a`;
- prefix-normalized pkg-config metadata;
- byte-equivalent CMake package metadata under `lib/cmake/PNG` and
  `lib/libpng`;
- executable presence and `--version` behavior for `libpng-config` and
  `libpng16-config`;
- downstream C link-and-run output for both native and reference prefixes.

Current status: native libpng is gated inside the hermetic CUDA insula. The
focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='libpng@1.6.58' \
VASO_LOCK_OUT=/workspace/experiment/libpng_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/libpng_native_build_graph.json \
VASO_NATIVE=1 \
VASO_FORCE_FETCH_REPOS='@libpng_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_libpng_native' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

The run used Bazel's `@spack_dist//:spack` inside the isolated CUDA 12.9.1
insula, reported hermetic Spack version `1.2.2`, passed
`//tools:hermetic_spack_guard_test`, passed
`//tools:hermetic_native_deps_guard_test`, passed
`//tools:native_build_mechanism_guard_unit_test`, passed
`//synthetic:use_libpng_native`, and passed
`//synthetic:libpng_abi_parity`.

The parity JSON reported `ok: true`: 22 layout paths, SONAME
`libpng16.so.16`, 256 exported dynamic symbols for
`libpng16.so.16.58.0`, static archive member/symbol parity for `libpng.a` and
`libpng16.a` with 391 symbols, pkg-config and CMake metadata parity,
`libpng-config --version` and `libpng16-config --version` both matching
`1.6.58`, and downstream C output matching
`libpng:1.6.58:header=10658`.
