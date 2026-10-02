# magma@2.6.1

## Position in the native PyTorch closure

MAGMA is required for the full-feature native PyTorch v2.14 build because
`USE_MAGMA=1` is part of the selected CUDA linear algebra profile. Probe 5
found no ambient MAGMA provider in the CUDA 12.9 rootfs, so ticket 19 requires
a declared native provider.

This capture uses PyTorch's official Linux CUDA package input:

```text
magma-cuda129-2.6.1-1
```

The upstream PyTorch v2.14.0 MAGMA packaging files are the source authority:

```text
.ci/magma/Makefile
.ci/magma/build_magma.sh
.ci/magma/package_files/
```

## Hermetic Spack evidence

All Spack facts here come from Bazel's vendored hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic Spack rejects the PyTorch official MAGMA pin against the selected CUDA
rootfs:

```text
SPACK_ROOT_PKG='magma@2.6.1 cuda_arch=100 ^cuda@12.9.1 ^openblas'
magma: '^cuda@12.6:' conflicts with '@:2.8.0'
```

The unconstrained hermetic Spack solve for the same CUDA line selects
`magma@2.10.0`, not `magma@2.6.1`. Therefore this provider is intentionally an
exact-version PyTorch-official source provider (`magma@2.6.1`) and not a
package-name override for future `magma` graph nodes.

## Source provenance

PyTorch v2.14.0 uses:

```text
MAGMA_VERSION=2.6.1
source URL: http://icl.utk.edu/projectsfiles/magma/downloads/magma-2.6.1.tar.gz
source SHA256: 6cd83808c6e8bc7a44028e05112b3ab4e579bcc73202ed14733f66661127e213
CUDA 12.9 package target: magma-cuda129
DESIRED_CUDA=12.9
```

The official PyTorch patch set is SHA256-pinned in `MODULE.bazel`:

```text
CMake.patch
cmakelists.patch
thread_queue.patch
cuda13.patch
getrf_shfl.patch
getrf_nbparam.patch
magma-2.6.1.sha256
```

For the first native torch build, this provider builds the B200 target
requested by the selected torch profile:

```text
-gencode arch=compute_100,code=sm_100
```

## Native build recipe

`native/magma/magma.bzl` mirrors the PyTorch package flow inside the insula:

```text
download magma-2.6.1.tar.gz by SHA256
download the PyTorch v2.14.0 MAGMA patch set by SHA256
read CMAKE_PREFIX from @cmake_native//:prefix_path.txt
read NINJA_PREFIX from @ninja_native//:prefix_path.txt
read CUDA_PREFIX from @cuda_native//:prefix_path.txt
read OPENBLAS_PREFIX from @openblas_native//:prefix_path.txt
validate VASO_IN_INSULA=1
validate CMAKE_PREFIX/bin/cmake, NINJA_PREFIX/bin/ninja-build,
         CUDA_PREFIX/bin/nvcc, and OPENBLAS_PREFIX include/lib payloads
apply the PyTorch MAGMA patches in the official order
configure CMake with USE_FORTRAN=OFF, GPU_TARGET=All,
         CUDA_TOOLKIT_ROOT_DIR=<cuda prefix>,
         LAPACK_LIBRARIES=<native openblas>,
         BLAS_LIBRARIES=<native openblas>,
         CUDA_ARCH_LIST=<selected arch list>
build and install into prefix
copy recipe/license metadata into prefix/info
emit prefix_path.txt and source_build.json
```

The emitted prefix contract is the PyTorch MAGMA package surface:

```text
include/magma.h
include/magma_v2.h
include/magma_types.h
lib/libmagma.a
info/recipe/*
info/licenses/COPYRIGHT
```

The corresponding mechanism verifier is the CMake dependency-prefix case:

```text
native/magma/magma.bzl: cmake: CMAKE_PREFIX, CUDA_PREFIX, NINJA_PREFIX, OPENBLAS_PREFIX
```

## Gates

There is no CUDA 12.9 Spack ABI reference for `magma@2.6.1`, so this slice uses
a behavior/prefix gate instead of a Spack ABI-parity gate:

```text
//synthetic:use_magma_native
```

That gate validates the prefix layout and manifest, then links and runs a
downstream C++ consumer against `libmagma.a`, native CUDA (`cudart`, `cublas`,
`cusparse`), and native OpenBLAS.
