# cmake@3.31.11

## Position in the hillclimb

`cmake` is the py-torch frontier node immediately after `curl`. In the
captured `SPACK_ROOT_PKG=py-torch` graph it appears as:

```text
61  cmake  3.31.11  generic
```

The focused reference graph used for this migration is:

```bash
SPACK_ROOT_PKG='cmake@3.31.11 +ownlibs +ncurses ~doc ~qtgui'
```

That run seats the CUDA 12.9.1 insula, runs Bazel's vendored
`@spack_dist//:spack`, and writes `cmake_spack_graph.lock.json` plus
`cmake_build_graph.json`. The reference lock keeps `spack_cmake` as
`build: "spack"` and records link deps on `spack_curl`, `spack_ncurses`, and
`spack_zlib_ng`.

## Hermetic Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path in the isolated estate:

```text
/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/cmake/package.py
```

Source provenance from the Spack recipe:

- package class: `Cmake(Package)`
- Spack build-system bucket: `generic`
- concrete Linux build mechanism: CMake bootstrap script, then `make install`
- upstream source URL: `https://github.com/Kitware/CMake/releases/download/v3.31.11/cmake-3.31.11.tar.gz`
- SHA256: `c0a3b3f2912b2166f522d5010ffb6029d8454ee635f5ad7a3247e0be7f9a15c9`
- concrete variants: `+ownlibs +ncurses ~doc ~qtgui build_type=Release`
- dependency channels: `curl`, `ncurses`, and `zlib-api`

The concrete focused `cmake@3.31.11` node has:

```text
build: compiler-wrapper, curl, gcc, gmake, ncurses, zlib-ng
link: curl, gcc-runtime, glibc, ncurses, zlib-ng
```

The hermetic Spack build log shows this bootstrap vector:

```text
./bootstrap --prefix=<cmake-prefix> \
  --no-system-libs \
  --system-curl \
  --no-qt-gui \
  -- \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMake_TEST_INSTALL=OFF \
  -DBUILD_CursesDialog=ON \
  -DBUILD_QtDialog=OFF \
  -DCMAKE_INSTALL_RPATH_USE_LINK_PATH=ON \
  -DCMAKE_INSTALL_RPATH=<cmake-prefix>/lib;<cmake-prefix>/lib64 \
  -DCMAKE_PREFIX_PATH=<compiler-wrapper>;<curl-prefix>;<gmake-prefix>;<ncurses-prefix>;<nghttp2-prefix>;<openssl-prefix>;<zlib-ng-prefix>;<gcc-runtime-prefix>
```

Relevant configure findings from the hermetic reference:

```text
-- Found ZLIB: <zlib-ng-prefix>/lib/libz.so (found version "1.3.1")
-- Found CURL: <curl-prefix>/lib/libcurl.so (found version "8.20.0")
-- Found Curses: <ncurses-prefix>/lib/libncurses.so
```

The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/cmake-3.31.11-hv4asdytpxoykwihgig5q5bngfh25yrb
```

The ABI/behavior-relevant installed surface for this migration is a tool
prefix, not a C library:

```text
bin/cmake
bin/ctest
bin/cpack
bin/ccmake
share/aclocal/cmake.m4
doc/cmake-3.31/Copyright.txt
share/cmake-3.31/Modules/CMakeCInformation.cmake
share/cmake-3.31/Modules/FindCURL.cmake
share/cmake-3.31/Help/command/project.rst
```

The Spack package intentionally exposes empty `libs` and `headers` properties.
The native provider keeps that shape: `@cmake_native//:lib` is an empty
`cc_library`, while `@cmake_native//:prefix` and `prefix_path.txt` carry the
installed tool prefix.

## Native build recipe

`native/cmake/cmake.bzl` mirrors the concrete Spack bootstrap flow:

```text
download cmake-3.31.11.tar.gz
validate CURL_PREFIX, NCURSES_PREFIX, and ZLIB_PREFIX
export PATH=<curl-prefix>/bin:<ncurses-prefix>/bin:$PATH
export PKG_CONFIG_PATH=<curl-prefix>/lib/pkgconfig:<ncurses-prefix>/lib/pkgconfig:<zlib-prefix>/lib/pkgconfig
export CMAKE_PREFIX_PATH=<curl-prefix>;<ncurses-prefix>;<zlib-prefix>
export CMAKE_LIBRARY_PATH=<curl-libdir>;<ncurses-libdir>;<zlib-libdir>
export CMAKE_INCLUDE_PATH=<curl-include>;<ncurses-include>;<zlib-include>
export LD_LIBRARY_PATH=<curl-libdir>:<ncurses-libdir>:<zlib-libdir>:$LD_LIBRARY_PATH
export LD_RUN_PATH=<curl-libdir>:<zlib-libdir>:<ncurses-libdir>
./bootstrap --prefix=<prefix> --parallel=<jobs> --no-system-libs --system-curl --no-qt-gui -- ...
make
make install
remove *.la
```

The native rule consumes `@curl_native//:prefix_path.txt`,
`@ncurses_native//:prefix_path.txt`, and
`@zlib_ng_native//:prefix_path.txt`. It validates:

- `CURL_PREFIX/bin/curl`
- `CURL_PREFIX/include/curl/curl.h`
- `NCURSES_PREFIX/include/ncurses.h`
- `ZLIB_PREFIX/include/zlib.h`
- `libcurl.so`, `libncurses.so`, `libcurses.so`, and `libz.so`

The dependency lookup is threaded through CMake channels rather than host
discovery: `CMAKE_PREFIX_PATH`, `CMAKE_LIBRARY_PATH`, `CMAKE_INCLUDE_PATH`,
`PKG_CONFIG_PATH`, `LD_LIBRARY_PATH`, and explicit
`-DCMAKE_INSTALL_RPATH=...`.

The corresponding mechanism verifier is the CMake dependency-prefix case:

```text
native/cmake/cmake.bzl: cmake: CURL_PREFIX, NCURSES_PREFIX, ZLIB_PREFIX
```

This is deliberately recorded as a `cmake` native mechanism even though the
Spack graph bucket is `generic`, because the concrete build action is CMake's
bootstrap/CMake-cache path.

## Gates

The smoke target is:

```text
//synthetic:use_cmake_native
```

It runs the native `cmake`, `ctest`, `cpack`, and `ccmake` binaries, then uses
the native CMake prefix to configure, build, and test a tiny C project. Expected
stdout:

```text
cmake:3.31.11:ok
```

`//synthetic:cmake_prefix_parity` compares the native prefix against the
hermetic Spack reference. Because CMake exposes no public C ABI, this is a
selected prefix/behavior parity gate rather than a library link-and-run gate.
It covers:

- selected installed data files listed above, byte-identical by SHA256;
- executable dynamic dependency parity for `bin/cmake`, `bin/ctest`,
  `bin/cpack`, and `bin/ccmake`;
- matching `--version` behavior for all four executables;
- the empty shared-library ABI axis expected for this tool prefix.

Current status: native CMake is gated inside the hermetic CUDA insula. The
focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='cmake@3.31.11 +ownlibs +ncurses ~doc ~qtgui' \
VASO_NATIVE=1 \
VASO_LOCK_OUT=/workspace/experiment/cmake_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/cmake_native_build_graph.json \
VASO_FORCE_FETCH_REPOS='@cmake_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_cmake_native //tools:hermetic_native_deps_guard_test' \
VASO_SPACK_TIMEOUT=3600 \
./run.sh
```

The run used Bazel's `@spack_dist//:spack` inside the isolated CUDA 12.9.1
insula, reported hermetic Spack version `1.2.2`, passed
`//tools:hermetic_spack_guard_test`, passed
`//tools:hermetic_native_deps_guard_test`, passed
`//synthetic:use_cmake_native`, and passed `//synthetic:cmake_prefix_parity`.

The parity JSON reported `ok: true`: nine selected layout paths, byte-identical
selected data files, empty shared-library ABI axis, matching executable NEEDED
sets for `cmake`, `ctest`, `cpack`, and `ccmake`, and matching version output
for all four executables.
