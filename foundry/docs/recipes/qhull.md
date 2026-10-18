# qhull@2020.2

## Position in the hillclimb

`qhull@2020.2` is the next not-yet-native CMakePackage frontier node after
native `protobuf@3.13.0` in the lean `py-torch` graph. The full graph keeps the
lean font topology:

```bash
SPACK_ROOT_PKG='py-torch cuda_arch=80,90,100 ^openblas~fortran ^font-util fonts:=encodings'
```

The focused all-Spack qhull reference graph has 22 nodes. The concrete qhull
node has:

```text
build: cmake, compiler-wrapper, gcc, gmake
link: gcc-runtime, glibc
```

`compiler-wrapper`, `gcc`, `gmake`, `gcc-runtime`, and `glibc` remain
toolchain/build-runtime infrastructure. The native provider consumes
`@cmake_native//:prefix_path.txt` as its only package-specific build-driver
prefix.

## Hermetic Spack evidence

All evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this node.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/qhull-2020.2-lxkxdqq7fzens773py6urzbhsi7pblgx
```

Source provenance from the archived hermetic Spack recipe:

- package class: `Qhull(CMakePackage)`
- upstream source URL:
  `https://github.com/qhull/qhull/archive/refs/tags/2020.2.tar.gz`
- source SHA256:
  `59356b229b768e6e2b09a701448bfa222c37b797a84f87f864f97462d8dbc7c5`
- no patches for `@2020.2`

Concrete CMake arguments from the Spack build output are:

```text
generator=Unix Makefiles
BUILD_SHARED_LIBS=ON
CMAKE_BUILD_TYPE=Release
CMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF
CMAKE_POLICY_DEFAULT_CMP0090=NEW
CMAKE_FIND_USE_PACKAGE_REGISTRY=OFF
CMAKE_EXPORT_COMPILE_COMMANDS=ON
```

The hermetic Spack build environment records compiler-wrapper target flags:

```text
SPACK_CC=/usr/bin/gcc
SPACK_CXX=/usr/bin/g++
SPACK_TARGET_ARGS_CC='-march=icelake-client -mtune=icelake-client'
SPACK_TARGET_ARGS_CXX='-march=icelake-client -mtune=icelake-client'
```

The native recipe passes the same target flags through `CMAKE_C_FLAGS` and
`CMAKE_CXX_FLAGS` while using the same underlying `/usr/bin/gcc` and
`/usr/bin/g++` compilers that the Spack compiler wrapper delegates to.

## Prefix surface

The ABI-relevant installed surface is:

```text
bin/qconvex
bin/qdelaunay
bin/qhalf
bin/qhull
bin/qvoronoi
bin/rbox
include/libqhull/**
include/libqhull_r/**
include/libqhullcpp/**
lib/libqhull_r.so -> libqhull_r.so.8.0 -> libqhull_r.so.8.0.2
lib/libqhullstatic.a
lib/libqhullstatic_r.a
lib/libqhullcpp.a
lib/pkgconfig/qhull_r.pc
lib/pkgconfig/qhullcpp.pc
lib/pkgconfig/qhullstatic.pc
lib/pkgconfig/qhullstatic_r.pc
lib/cmake/Qhull/**
```

`libqhull_r.so.8.0.2` has SONAME `libqhull_r.so.8.0` and NEEDED entries
`libm.so.6` and `libc.so.6`.

## Native build recipe

`native/qhull/qhull.bzl` mirrors Spack's CMakePackage flow inside the CUDA
insula:

```text
download qhull 2020.2
validate CMAKE_PREFIX/bin/cmake
export PATH=<cmake-prefix>/bin:$PATH
export CMAKE_PREFIX_PATH=<cmake-prefix>
cmake -G "Unix Makefiles" <src>
  -DCMAKE_INSTALL_PREFIX=<prefix>
  -DCMAKE_INSTALL_RPATH_USE_LINK_PATH=ON
  -DCMAKE_INSTALL_RPATH=<prefix>/lib;<prefix>/lib64
  -DCMAKE_C_COMPILER=/usr/bin/gcc
  -DCMAKE_CXX_COMPILER=/usr/bin/g++
  -DCMAKE_C_FLAGS="-march=icelake-client -mtune=icelake-client"
  -DCMAKE_CXX_FLAGS="-march=icelake-client -mtune=icelake-client"
  -DCMAKE_BUILD_TYPE=Release
  -DCMAKE_VERBOSE_MAKEFILE=ON
  -DCMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF
  -DCMAKE_POLICY_DEFAULT_CMP0090=NEW
  -DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF
  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
  -DBUILD_SHARED_LIBS=ON
make
make install
emit prefix_path.txt
```

The mechanism verifier classifies this as `cmake` and should report:

```text
native/qhull/qhull.bzl: cmake: CMAKE_PREFIX
```

## Gates

The smoke targets are:

```text
//synthetic:use_qhull_native
//synthetic:use_qhull_prefix_native
```

The C consumer links against `@qhull_native//:lib`, calls the reentrant
`qh_new_qhull` entrypoint, and prints the Qhull version, return code, facet
count, and memory-cleanup counters.

`//synthetic:qhull_abi_parity` compares the native prefix against the hermetic
Spack reference. It covers:

- layout for headers, libraries, pkg-config files, CMake metadata, and selected
  command-line tools;
- SONAME/exported-symbol parity for `libqhull_r.so.8.0.2`;
- static archive member/symbol parity for `libqhullstatic.a`,
  `libqhullstatic_r.a`, and `libqhullcpp.a`;
- executable NEEDED parity and behavior for the installed qhull tools;
- downstream C link-and-run against `-lqhull_r`.
