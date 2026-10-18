# openblas frontier recipe

## Position in the hillclimb

`openblas` is the migrated py-torch frontier node immediately after `nasm`. In
the captured `SPACK_ROOT_PKG=py-torch` graph it appears as:

```text
31  openblas  0.3.33  makefile  native; ABI parity green
```

The native verification run used the package-specific root:

```bash
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
SPACK_ROOT_PKG='openblas@0.3.33 ~fortran threads=none' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_openblas_native //tools:hermetic_native_deps_guard_test //tools:abi_parity_unit_test' \
VASO_SPACK_TIMEOUT=3600 \
VASO_LOCK_OUT=/workspace/experiment/openblas_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/openblas_build_graph.json \
VASO_FORCE_FETCH_REPOS='@openblas_native' \
./run.sh
```

That command seats the CUDA insula, forces the Bazel-owned native repository
fetch inside the insula, runs Bazel's vendored `@spack_dist//:spack`, applies
the `native_overrides.json` flip to `@openblas_native//:lib`, and runs the
native smoke, Makefile hermetic-deps guard, ABI unit test, and
`//synthetic:openblas_abi_parity` inside the same insula.

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Source provenance from the Spack recipe:

- package class: `Openblas(CMakePackage, MakefilePackage)`
- selected concrete build system: `makefile`
- upstream source URL:
  `https://github.com/OpenMathLib/OpenBLAS/releases/download/v0.3.33/OpenBLAS-0.3.33.tar.gz`
- SHA256:
  `6761af1d9f5d353ab4f0b7497be2643313b36c8f31caec0144bfef198e71e6ab`
- concrete patch:
  `https://github.com/OpenMathLib/OpenBLAS/commit/88705a932831c0de1ed136b461c6c239802828b2.diff?full_index=1`
- patch SHA256:
  `723ddc1553b6d27ff89d96985f7732695935c0d4d8df766987702689bdb750ac`
- concrete variants:
  `~fortran`, `+dynamic_dispatch`, `threads=none`, `+locking`, `+shared`,
  `~static`, `~ilp64`, `symbol_suffix=none`

The concrete `openblas@0.3.33` node has only toolchain dependencies:

```text
build: compiler-wrapper, gcc, gmake
link: gcc-runtime, glibc
```

The hermetic Spack build log shows the Makefile flow:

```text
make -s CC=<spack-gcc-wrapper> MAKE_NB_JOBS=0 ARCH=x86_64 TARGET=SKYLAKEX \
  DYNAMIC_ARCH=1 NOFORTRAN=1 USE_LOCKING=1 USE_OPENMP=0 USE_THREAD=0 \
  RANLIB=ranlib
make CC=<spack-gcc-wrapper> MAKE_NB_JOBS=0 ARCH=x86_64 TARGET=SKYLAKEX \
  DYNAMIC_ARCH=1 NOFORTRAN=1 USE_LOCKING=1 USE_OPENMP=0 USE_THREAD=0 \
  RANLIB=ranlib PREFIX=<openblas-prefix> install
```

The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/openblas-0.3.33-6phoxmaa62vdfy3jzbnejrlwodjforpz
```

The ABI/behavior-relevant installed surface for this migration is:

```text
include/cblas.h
include/f77blas.h
include/lapack.h
include/lapacke*.h
include/openblas_config.h
lib/libopenblas-r0.3.33.so
lib/libopenblas.so.0
lib/libopenblas.so
lib/libopenblas-r0.3.33.a
lib/libopenblas.a
lib/pkgconfig/openblas.pc
lib/cmake/openblas/OpenBLASConfig.cmake
lib/cmake/openblas/OpenBLASConfigVersion.cmake
```

The reference shared library has SONAME `libopenblas.so.0` and needs only
`libm.so.6` and `libc.so.6`.

## Build recipe

`native/openblas/openblas.bzl` mirrors Spack's Makefile build:

```text
download OpenBLAS-0.3.33
apply 88705a932831c0de1ed136b461c6c239802828b2.diff
make -s -j$MAKE_JOBS CC=gcc MAKE_NB_JOBS=0 ARCH=x86_64 TARGET=SKYLAKEX \
  DYNAMIC_ARCH=1 NOFORTRAN=1 USE_LOCKING=1 USE_OPENMP=0 USE_THREAD=0 \
  RANLIB=ranlib
make -j$MAKE_JOBS ... PREFIX=<prefix> install
```

The repository rule fetches the same upstream tarball and patch by SHA256 and
refuses to run unless the hermetic insula has set `VASO_IN_INSULA=1`.

`openblas` has no non-toolchain dependency prefixes, so the corresponding
hermetic-deps verifier for this build mechanism is the Makefile/no-dependency
case. `//tools:hermetic_native_deps_guard_test` confirms the rule is classified
as `makefile`, the build action is insula-gated, and no dependency prefix is
discovered from the host:

```text
native/openblas/openblas.bzl: makefile: no dep prefixes
```

Makefile packages that do consume prefixes are verified by the same guard
through explicit `*_prefix_file` labels, shell-side prefix existence checks,
and make/config variables such as `CPPFLAGS`, `CFLAGS`, `LDFLAGS`, `LIBS`,
`PREFIX=`, or package-specific config lines.

## ABI gate target

`//synthetic:openblas_abi_parity` compares the native prefix against the
hermetic Spack reference. The gate covers:

- exact ABI-relevant layout parity for headers, shared libraries, static
  archive, pkg-config metadata, and CMake metadata;
- SONAME parity for `lib/libopenblas-r0.3.33.so`;
- exported dynamic symbol parity, with 17,488 symbols on both sides in the
  verified run;
- static archive member/exported-symbol parity for `libopenblas-r0.3.33.a`;
- prefix-normalized `lib/pkgconfig/openblas.pc`;
- byte-identical OpenBLAS CMake metadata;
- downstream `cblas_dgemm()` link-and-run behavior against both prefixes.

The smoke target links against the native prefix and prints:

```text
openblas:0.3.33:19.0:50.0:ok
```

Current status: native and ABI-gated. The latest run used `rootfs mode:
cuda-bundle`, reported `hermetic spack (Bazel-owned) version: 1.2.2`, and
passed `//synthetic:use_openblas_native`,
`//tools:hermetic_native_deps_guard_test`, `//tools:abi_parity_unit_test`, and
`//synthetic:openblas_abi_parity` inside the CUDA insula.
