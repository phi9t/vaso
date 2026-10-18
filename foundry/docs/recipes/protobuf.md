# protobuf@21.12

## Position in the hillclimb

Hermetic Spack `protobuf@21.12` is the C++ protobuf provider selected for the
native PyTorch source target. Upstream PyTorch `v2.14.0` pins
`third_party/protobuf` to commit `f0dc78d7e6e331b8c6bb2d5283e06aa26883ca7c`,
which is protobuf tag `v3.21.12` and reports protoc/API version `3.21.12`. The
current hermetic Spack `py-torch` recipe graph may still resolve an older
recipe-side protobuf node; the native provider is exact-keyed as
`protobuf@21.12` and will not satisfy that older node silently.

```bash
SPACK_ROOT_PKG='py-torch cuda_arch=80,90,100 ^openblas~fortran ^font-util fonts:=encodings'
```

The focused all-Spack reference graph for this package has 22 nodes. The
concrete protobuf node has:

```text
build: cmake, compiler-wrapper, gcc, gmake
build/link: zlib-ng
link: gcc-runtime, glibc
```

`compiler-wrapper`, `gcc`, `gmake`, `gcc-runtime`, and `glibc` remain
toolchain/build-runtime infrastructure. The native provider consumes
`@cmake_native//:prefix_path.txt` and `@zlib_ng_native//:prefix_path.txt`.

## Hermetic Spack evidence

All evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this node.

Source provenance from upstream PyTorch and protobuf:

- hermetic Spack package key: `protobuf@21.12`
- PyTorch release tag: `v2.14.0`
- PyTorch release commit:
  `2b3ec34829036a65cd9d1398ea72a0167dc37470`
- PyTorch `third_party/protobuf` gitlink:
  `f0dc78d7e6e331b8c6bb2d5283e06aa26883ca7c`
- protobuf tag: `v3.21.12`
- upstream source URL:
  `https://github.com/protocolbuffers/protobuf/archive/v3.21.12.tar.gz`
- source SHA256:
  `930c2c3b5ecc6c9c12615cf5ad93f1cd6e12d0aba862b572e076259970ac3a53`
- the older Spack-selected `462964ed` ABI export patch is not applied for this
  version; the native rule keeps patching optional for older exact captures.

Concrete CMake arguments from the Spack recipe are:

```text
BUILD_SHARED_LIBS=ON
protobuf_BUILD_TESTS=OFF
CMAKE_POSITION_INDEPENDENT_CODE=ON
CMAKE_BUILD_TYPE=Release
generator=Unix Makefiles
source subdirectory=source root for `@21.12`
```

The hermetic Spack build environment also records compiler-wrapper target
flags:

```text
SPACK_TARGET_ARGS_CC='-march=icelake-client -mtune=icelake-client'
SPACK_TARGET_ARGS_CXX='-march=icelake-client -mtune=icelake-client'
```

Those flags are ABI-relevant for protobuf because GCC may emit or internalize
weak C++ template instantiations differently when target tuning changes. The
native recipe passes the same target flags through `CMAKE_C_FLAGS` and
`CMAKE_CXX_FLAGS` while using the same underlying `/usr/bin/gcc` and
`/usr/bin/g++` compilers that the Spack compiler wrapper delegates to.

The prefix-relevant installed surface is:

```text
bin/protoc
bin/protoc-3.21.12.0
include/google/protobuf/**
lib/libprotobuf.so -> libprotobuf.so.3.21.12.0
lib/libprotobuf-lite.so -> libprotobuf-lite.so.3.21.12.0
lib/libprotoc.so -> libprotoc.so.3.21.12.0
lib/pkgconfig/protobuf.pc
lib/pkgconfig/protobuf-lite.pc
lib/cmake/protobuf/**
```

The expected shared-library SONAMEs are `libprotobuf.so.3.21.12.0`,
`libprotobuf-lite.so.3.21.12.0`, and `libprotoc.so.3.21.12.0`.
`libprotobuf.so.3.21.12.0` needs `libz.so.1`, so the native build must consume
the Bazel-built zlib-ng prefix explicitly and must not discover rootfs zlib.

## Native build recipe

`native/protobuf/protobuf.bzl` mirrors Spack's CMakePackage flow inside the
CUDA insula:

```text
download protobuf v3.21.12
validate CMAKE_PREFIX/bin/cmake
validate ZLIB_PREFIX/include/zlib.h and ZLIB_PREFIX/lib/libz.so
export PATH=<cmake-prefix>/bin:$PATH
export CMAKE_PREFIX_PATH=<zlib-prefix>;<cmake-prefix>
export CMAKE_LIBRARY_PATH=<zlib-prefix>/lib
export CMAKE_INCLUDE_PATH=<zlib-prefix>/include
cmake -G "Unix Makefiles" <src>
  -DCMAKE_INSTALL_PREFIX=<prefix>
  -DCMAKE_INSTALL_RPATH_USE_LINK_PATH=ON
  -DCMAKE_INSTALL_RPATH=<prefix>/lib;<prefix>/lib64;<zlib-prefix>/lib
  -DCMAKE_C_COMPILER=/usr/bin/gcc
  -DCMAKE_CXX_COMPILER=/usr/bin/g++
  -DCMAKE_C_FLAGS="-march=icelake-client -mtune=icelake-client"
  -DCMAKE_CXX_FLAGS="-march=icelake-client -mtune=icelake-client"
  -DCMAKE_BUILD_TYPE=Release
  -DCMAKE_VERBOSE_MAKEFILE=ON
  -DCMAKE_POSITION_INDEPENDENT_CODE=ON
  -DBUILD_SHARED_LIBS=ON
  -Dprotobuf_BUILD_TESTS=OFF
  -DZLIB_ROOT=<zlib-prefix>
make
make install
remove libtool archives
emit prefix_path.txt
```

The mechanism verifier classifies this as `cmake` and reports:

```text
native/protobuf/protobuf.bzl: cmake: CMAKE_PREFIX, ZLIB_PREFIX
```

Both prefixes are mandatory Bazel `prefix_path.txt` inputs and are validated
before configure.

## Gates

The smoke targets are:

```text
//synthetic:use_protobuf_native
//synthetic:use_protobuf_prefix_native
```

The C++ smoke links against `@protobuf_native//:lib` and prints the compile-time
protobuf version. The prefix smoke checks `protoc`, headers, shared libraries,
pkg-config metadata, and CMake package metadata, then runs `protoc --version`.

`//synthetic:protobuf_abi_parity` compares the native prefix against the
hermetic Spack reference. It covers:

- layout for headers, shared libraries, pkg-config files, and selected CMake
  metadata;
- SONAME/exported-symbol parity for `libprotobuf`, `libprotobuf-lite`, and
  `libprotoc`;
- executable NEEDED parity and behavior for `bin/protoc`;
- downstream C++ link-and-run against `-lprotobuf -lz`, with zlib-ng supplied
  through explicit reference and candidate link prefixes.
