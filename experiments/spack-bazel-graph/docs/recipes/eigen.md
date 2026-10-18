# eigen@5.0.1

## Position in the hillclimb

`eigen` is the py-torch frontier node immediately after native cuSPARSELt. In
the captured `SPACK_ROOT_PKG=py-torch` graph it appears as:

```text
63  eigen  5.0.1  cmake
```

The focused reference graph used for this migration is:

```bash
SPACK_ROOT_PKG='eigen@5.0.1'
```

The reference run used Bazel's vendored `@spack_dist//:spack` inside the
isolated CUDA 12.9.1 insula and wrote `eigen_spack_graph.lock.json` plus
`eigen_build_graph.json`. The reference lock keeps `spack_eigen` as
`build: "spack"`; the native run flips the provider to
`@eigen_native//:lib` without adding link edges.

## Hermetic Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path in the isolated estate:

```text
/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/eigen/package.py
```

Source provenance from the Spack recipe:

- package class: `Eigen(CMakePackage, ROCmPackage)`
- Spack build-system bucket: `cmake`
- upstream source URL:
  `https://gitlab.com/libeigen/eigen/-/archive/5.0.1/eigen-5.0.1.tar.gz`
- SHA256: `e9c326dc8c05cd1e044c71f30f1b2e34a6161a3b6ecf445d56b53ff1669e3dec`
- concrete variants: `~blas ~lapack ~ipo ~nightly ~rocm build_type=Release`
- dependency channel: `cmake` as the build tool

The concrete focused `eigen@5.0.1` node has:

```text
build: compiler-wrapper, cmake, gcc, gmake
link: gcc-runtime, glibc
```

The hermetic Spack build log shows this CMake vector:

```text
cmake -G 'Unix Makefiles' \
  -DCMAKE_BUILD_TYPE:STRING=Release \
  -DCMAKE_VERBOSE_MAKEFILE:BOOL=ON \
  -DCMAKE_INTERPROCEDURAL_OPTIMIZATION:BOOL=OFF \
  -DCMAKE_POLICY_DEFAULT_CMP0090:STRING=NEW \
  -DCMAKE_FIND_USE_PACKAGE_REGISTRY:BOOL=OFF \
  -DCMAKE_EXPORT_COMPILE_COMMANDS:BOOL=ON \
  -DEIGEN_BUILD_TESTING:BOOL=OFF \
  -DEIGEN_LEAVE_TEST_IN_ALL_TARGET:BOOL=OFF \
  -DEIGEN_BUILD_BLAS:BOOL=OFF \
  -DEIGEN_BUILD_LAPACK:BOOL=OFF
```

The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/eigen-5.0.1-fn7tmt4is4q2qfrsruiwm3h3ro3dvcpy
```

The ABI/prefix-relevant installed surface for this migration is a header-only
CMake package prefix:

```text
include/eigen3/
include/eigen3/Eigen/Core
include/eigen3/Eigen/Dense
include/eigen3/Eigen/Version
include/eigen3/signature_of_eigen3_matrix_library
share/pkgconfig/eigen3.pc
share/eigen3/cmake/Eigen3Config.cmake
share/eigen3/cmake/Eigen3ConfigVersion.cmake
share/eigen3/cmake/Eigen3Targets.cmake
```

The public version header intentionally keeps the Eigen3 world version while
using semantic package version components:

```text
EIGEN_WORLD_VERSION 3
EIGEN_MAJOR_VERSION 5
EIGEN_MINOR_VERSION 0
EIGEN_PATCH_VERSION 1
EIGEN_VERSION_STRING "5.0.1-dev"
```

## Native build recipe

`native/eigen/eigen.bzl` mirrors the concrete Spack CMake flow:

```text
download eigen-5.0.1.tar.gz
validate CMAKE_PREFIX/bin/cmake from @cmake_native//:prefix_path.txt
export PATH=<cmake-prefix>/bin:$PATH
export CMAKE_PREFIX_PATH=<cmake-prefix>
cmake -G "Unix Makefiles" with the Spack-observed flags
make
make install
emit prefix_path.txt
```

Eigen is header-only for this graph. The native provider exposes:

- `@eigen_native//:prefix` for the installed prefix filegroup;
- `@eigen_native//:prefix_path.txt` for downstream native repository rules;
- `@eigen_native//:lib` as a header-only `cc_library` with
  `prefix/include` and `prefix/include/eigen3` include roots.

The corresponding mechanism verifier is the CMake dependency-prefix case:

```text
native/eigen/eigen.bzl: cmake: CMAKE_PREFIX
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, consumes
`@cmake_native//:prefix_path.txt`, validates `CMAKE_PREFIX/bin/cmake`, and
threads CMake discovery through `PATH` and `CMAKE_PREFIX_PATH`.

## Gates

The smoke target is:

```text
//synthetic:use_eigen_native
```

It compiles a small C++17 consumer against `@eigen_native//:lib`, includes
`<Eigen/Dense>`, `<Eigen/Geometry>`, and `<Eigen/Version>`, asserts the public
Eigen header version contract, and prints:

```text
eigen:5.0.1-dev:1.5   1   4:1
```

`//synthetic:eigen_prefix_parity` compares the native prefix against the
hermetic Spack reference. Because Eigen is header-only here, this is a selected
prefix/header behavior gate rather than a shared-library ABI gate. It covers:

- 604 installed layout paths;
- byte-identical representative public headers and CMake metadata;
- prefix-normalized `share/pkgconfig/eigen3.pc`;
- empty shared-library ABI axis;
- downstream C++ link-and-run output for both native and reference prefixes.

Current status: native Eigen is gated inside the hermetic CUDA insula. The
focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='eigen@5.0.1' \
VASO_LOCK_OUT=/workspace/experiment/eigen_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/eigen_native_build_graph.json \
VASO_NATIVE=1 \
VASO_FORCE_FETCH_REPOS='@eigen_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_eigen_native //tools:hermetic_native_deps_guard_test' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

The run used Bazel's `@spack_dist//:spack` inside the isolated CUDA 12.9.1
insula, reported hermetic Spack version `1.2.2`, passed
`//tools:hermetic_spack_guard_test`, passed
`//tools:hermetic_native_deps_guard_test`, passed
`//synthetic:use_eigen_native`, and passed `//synthetic:eigen_prefix_parity`.

The parity JSON reported `ok: true`: 604 layout paths, byte-identical selected
headers and CMake metadata, prefix-normalized `eigen3.pc`, empty
shared-library ABI axis, and matching downstream C++ output
`eigen:5.0.1-dev:1.5   1   4:1`.
