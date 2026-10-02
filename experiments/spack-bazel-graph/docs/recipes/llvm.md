# LLVM native build-action skeleton

## Position in the hillclimb

`llvm@20.1.8` was captured as the next not-yet-native node after `fxdiv` in an
older lean `py-torch` graph:

```text
132  cpuinfo  2025-11-14  cmake  native
133  fp16     2020-05-14  cmake  native
134  fxdiv    2020-04-17  cmake  native
135  llvm     20.1.8      cmake  spack
```

This checkpoint does not flip `llvm` in `native_overrides.json`. After the
one-LLVM decision, this native rule is **not** a provider for `py-torch`,
`py-triton`, `py-jaxlib`, `py-jax`, or any host-clang role. It remains only as a
legacy dry-run skeleton for the pruned numba/py-llvmlite path, whose upstream
llvmlite line still asks for LLVM 20. The triumvirate uses the rootfs external
llvm-project `35901313800ea6e6cbeb9226e51c7c4b29bfc40e` under
`/usr/lib/llvm-23`, and `//tools:one_llvm_graph_guard_live_test` rejects any
captured triumvirate graph that depends on another LLVM.

LLVM is a large compiler/runtime stack, so the skeleton remains gated and
records the Spack-derived CMake interface only; no full LLVM 20 build is
attempted by default.

## Hermetic Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running in the insula and from the generated lean PyTorch build graph. Do not
use an ambient host Spack checkout. The vendored distribution is upstream Spack
`v1.2.2`.

The lean graph records the concrete node as:

```text
llvm@20.1.8
build_system=cmake
status=spack
spack_hash=3umuotpu7swr7uqj5kfwudcid2tgzdue
```

The Spack recipe class is:

```text
class Llvm(CMakePackage, CudaPackage, LlvmDetection, CompilerPackage)
```

Source provenance from the hermetic Spack recipe:

- source URL:
  `https://github.com/llvm/llvm-project/archive/refs/tags/llvmorg-20.1.8.tar.gz`
- source SHA256:
  `a6cbad9b2243b17e87795817cfff2107d113543a12486586f8a055a2bb044963`
- source strip prefix:
  `llvm-project-llvmorg-20.1.8`
- CMake source root:
  `llvm`
- generator:
  `Ninja`

The lean graph variants for this node are:

```text
+clang
~flang
+lld
+lldb
~mlir
+offload
+libomptarget
libomptarget_debug=false
compiler-rt=runtime
libcxx=runtime
libunwind=runtime
openmp=runtime
+polly
~python
+lua
~gold
~split_dwarf
+llvm_dylib
~link_llvm_dylib
~z3
~zstd
~utils
targets=aarch64,amdgpu,nvptx,x86
version_suffix=none
shlib_symbol_version=none
```

The package dependency boundary is:

```text
build: cmake, compiler-wrapper, gcc, ninja, perl-data-dumper, pkgconf, python
link/run: gcc-runtime, glibc, hwloc, libedit, libffi, libxml2, lua, ncurses,
          swig, xz, zlib-ng
```

`compiler-wrapper`, `gcc`, `gcc-runtime`, and `glibc` remain toolchain/runtime
infrastructure. The native build-action skeleton consumes explicit Bazel-native
prefix files for the package-level dependencies: CMake, Ninja, Python, pkgconf,
perl-data-dumper, hwloc, libedit, libffi, libxml2, Lua, ncurses, SWIG, xz, and
zlib-ng.

## Spack CMake contract

The native skeleton preserves the Spack recipe's `cmake_args()` shape for this
concrete variant surface:

```text
LLVM_REQUIRES_RTTI=ON
LLVM_ENABLE_RTTI=ON
LLVM_ENABLE_EH=ON
LLVM_ENABLE_LIBXML2=OFF
CLANG_DEFAULT_OPENMP_RUNTIME=libomp
LIBOMP_USE_HWLOC=ON
LIBOMP_HWLOC_INSTALL_DIR=<hwloc-prefix>
LLVM_ENABLE_ZSTD=OFF
CUDA_TOOLKIT_ROOT_DIR=IGNORE
CUDA_SDK_ROOT_DIR=IGNORE
CUDA_NVCC_EXECUTABLE=IGNORE
LIBOMPTARGET_DEP_CUDA_DRIVER_LIBRARIES=IGNORE
LIBOMPTARGET_ENABLE_DEBUG=OFF
LIBOMPTARGET_BUILD_AMDGPU_PLUGIN=OFF
python_executable=<python-prefix>/bin/python3.14
LLDB_ENABLE_LIBEDIT=ON
LLDB_ENABLE_CURSES=ON
LLDB_ENABLE_LIBXML2=ON
LLDB_ENABLE_LUA=ON
LLDB_ENABLE_LZMA=ON
LLDB_CURSES_LIBS=-lncursesw
LLDB_ENABLE_PYTHON=OFF
OPENMP_ENABLE_LIBOMPTARGET=ON
LLVM_BUILD_LLVM_DYLIB=ON
LLVM_LINK_LLVM_DYLIB=OFF
LLVM_USE_SPLIT_DWARF=OFF
LIBCXX_ENABLE_STATIC_ABI_LIBRARY=ON
CMAKE_FIND_PACKAGE_PREFER_CONFIG=ON
CMAKE_FIND_USE_PACKAGE_ROOT_PATH=OFF
CMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=OFF
CMAKE_FIND_USE_PACKAGE_REGISTRY=OFF
CMAKE_FIND_USE_SYSTEM_PATH=OFF
LLVM_TARGETS_TO_BUILD=AArch64;AMDGPU;NVPTX;X86
LIBOMP_TSAN_SUPPORT=OFF
LLVM_ENABLE_PROJECTS=lldb;clang;clang-tools-extra;lld;polly
LLVM_ENABLE_RUNTIMES=openmp;offload;compiler-rt;libcxx;libcxxabi;libunwind
RUNTIMES_CMAKE_ARGS=-DCMAKE_INSTALL_RPATH_USE_LINK_PATH=ON
LIBCXXABI_USE_LLVM_UNWINDER=ON
```

The skeleton also threads Spack's target flags through the compiler cache:

```text
CMAKE_C_FLAGS=-march=icelake-client -mtune=icelake-client
CMAKE_CXX_FLAGS=-march=icelake-client -mtune=icelake-client
```

## Native skeleton

`native/llvm/llvm.bzl` is intentionally gated:

- it refuses repository evaluation outside the hermetic insula;
- it reads every dependency through mandatory Bazel `*_prefix_file` labels;
- it writes `build_plan.json` through `native/llvm/plan.py`;
- the plan records the concrete CMake cache, pinned CMake/Ninja tools,
  `PATH` prefix order, `CMAKE_PREFIX_PATH`, `PKG_CONFIG`, `PKG_CONFIG_PATH`,
  and Spack target flags used by the eventual build action;
- without `VASO_NATIVE_LLVM_TOKEN=build-native-llvm`, it emits only the plan and
  reviewable `build.sh`, and does not download or build LLVM;
- with the token, it still refuses the full build in this checkpoint until the
  prefix/ABI gate has been reviewed and wired.

`//native/llvm:plan_test` checks that every CMake define in the planner is also
present in the repository rule's build script, so the dry-run contract and the
future execute path cannot drift silently.

The mechanism verifier should report:

```text
native/llvm/llvm.bzl: cmake: CMAKE_PREFIX, HWLOC_PREFIX, LIBEDIT_PREFIX, LIBFFI_PREFIX, LIBXML2_PREFIX, LUA_PREFIX, NCURSES_PREFIX, NINJA_PREFIX, PERL_DATA_DUMPER_PREFIX, PKGCONF_PREFIX, PYTHON_PREFIX, SWIG_PREFIX, XZ_PREFIX, ZLIB_NG_PREFIX
```

## Planned ABI gate

The LLVM 20 native flip is not on the triumvirate path. If the pruned
numba/py-llvmlite path is revived and still needs this provider, the slice
needs:

- a full native build in the CUDA insula with the token;
- layout parity for `bin/`, `include/`, `lib/`, `lib/cmake/`, and
  `lib/pkgconfig/`;
- SONAME/exported-symbol parity for the shared LLVM, Clang, LLDB, OpenMP, and
  runtime libraries emitted by this concrete prefix;
- executable dependency parity and behavior probes for `llvm-config`, `clang`,
  `clang++`, `ld.lld`, and `lldb`;
- downstream C and C++ link-and-run tests using `llvm-config` output;
- an ABI policy for the compiler/runtime role before registering LLVM as a
  Spack external for downstream consumers.

## ODR-sensitive provider invariant

This slice does not add protobuf, gRPC, Abseil, or Boost providers. Those
families must stay on one unified compatible concrete version family across all
companion packages before any native flip. The generator rejects unqualified
overrides and rejects mixed concrete family versions, including
protobuf/Python protobuf and gRPC/gRPC C++ pairings.
