# cpuinfo native recipe

## Position in the hillclimb

`cpuinfo@2025-11-14` is the next CMakePackage source build after native
`ninja` in the lean `py-torch` frontier:

```text
130  re2c     4.4         autotools  native
131  ninja    1.13.2      generic    native
132  cpuinfo  2025-11-14  cmake
```

The focused reference graph for `SPACK_ROOT_PKG='cpuinfo'` has 37 build-graph
nodes and ends in `python`, `re2c`, `ninja`, then `cpuinfo`. Spack still owns
the DAG shape; the native flip changes only `spack_cpuinfo.build` to `native`
and re-exports `@cpuinfo_native//:lib`.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.
The vendored distribution is upstream Spack `v1.2.2`.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/cpuinfo-2025-11-14-wvxynvmrd2yrrjbygw7vinws5fhiahbb
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/cpuinfo-2025-11-14-wvxynvmrd2yrrjbygw7vinws5fhiahbb/.spack/repos/spack_repo/builtin/packages/cpuinfo/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `Cpuinfo(CMakePackage)`
- homepage and git remote: `https://github.com/pytorch/cpuinfo`
- version: `2025-11-14`
- source commit:
  `f858c30bcb16f8effd5ff46996f0514539e17abc`
- source archive used by the native rule:
  `https://github.com/pytorch/cpuinfo/archive/f858c30bcb16f8effd5ff46996f0514539e17abc.tar.gz`
- source SHA256:
  `30c390d5ac7c59ae1c0a8a1aefb9bb70cb4d482bd15e8de706f5c8bf60357fa6`
- CMake generator: `Ninja`
- build dependencies: C, CXX, CMake, and Ninja
- concrete CMake args:
  `CPUINFO_BUILD_UNIT_TESTS=OFF`,
  `CPUINFO_BUILD_MOCK_TESTS=OFF`,
  `CPUINFO_BUILD_BENCHMARKS=OFF`,
  `CPUINFO_LIBRARY_TYPE=shared`,
  `CPUINFO_LOG_LEVEL=error`,
  `CMAKE_SKIP_RPATH=ON`

The concrete Spack configure command used the hermetic CMake and Ninja prefixes:

```text
cmake -G Ninja \
  -DCMAKE_INSTALL_PREFIX:STRING=<cpuinfo-prefix> \
  -DCMAKE_INSTALL_RPATH_USE_LINK_PATH:BOOL=ON \
  -DCMAKE_INSTALL_RPATH:STRING=<cpuinfo-prefix>/lib;<cpuinfo-prefix>/lib64 \
  -DCMAKE_PREFIX_PATH:STRING=<cmake-prefix>;<compiler-wrapper-prefix>;<gcc-runtime-prefix>;<ninja-prefix> \
  -DCMAKE_BUILD_TYPE:STRING=Release \
  -DCMAKE_INTERPROCEDURAL_OPTIMIZATION:BOOL=OFF \
  -DCMAKE_POLICY_DEFAULT_CMP0090:STRING=NEW \
  -DCMAKE_FIND_USE_PACKAGE_REGISTRY:BOOL=OFF \
  -DCMAKE_EXPORT_COMPILE_COMMANDS:BOOL=ON \
  -DCPUINFO_BUILD_UNIT_TESTS:BOOL=OFF \
  -DCPUINFO_BUILD_MOCK_TESTS:BOOL=OFF \
  -DCPUINFO_BUILD_BENCHMARKS:BOOL=OFF \
  -DCPUINFO_LIBRARY_TYPE:STRING=shared \
  -DCPUINFO_LOG_LEVEL:STRING=error \
  -DCMAKE_SKIP_RPATH:BOOL=ON
```

The installed stable prefix surface is:

- executables: `bin/cache-info`, `bin/cpu-info`, `bin/cpuid-dump`, `bin/isa-info`
- public header: `include/cpuinfo.h`
- public shared library: `lib/libcpuinfo.so`
- pkg-config metadata: `lib/pkgconfig/libcpuinfo.pc`
- CMake metadata:
  `share/cpuinfo/cpuinfo-config.cmake`,
  `share/cpuinfo/cpuinfo-targets-release.cmake`,
  `share/cpuinfo/cpuinfo-targets.cmake`

`lib/libcpuinfo.so` has SONAME `libcpuinfo.so` and NEEDED `libc.so.6`.

## Native build recipe

`native/cpuinfo/cpuinfo.bzl` mirrors the CMakePackage install:

```text
download and extract the exact cpuinfo commit archive by SHA256
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

The mechanism verifier is expected to report this build channel:

```text
native/cpuinfo/cpuinfo.bzl: cmake: CMAKE_PREFIX, NINJA_PREFIX
```

`cmake` is the mechanism-specific guard for this native provider. It requires
explicit `*_prefix_file` attrs, shell-side prefix checks, and dependency
injection through CMake/PATH channels rather than ambient host discovery.

## Prefix and behavior gate

The smoke target is:

```text
//synthetic:use_cpuinfo_native
```

It depends on the generated `@spack_cpuinfo//:lib` provider surface, calls
`cpuinfo_initialize()`, and checks that the runtime reports at least one
processor and one package.

The parity target is:

```text
//synthetic:cpuinfo_abi_parity
```

It compares `@cpuinfo_native//:prefix` against the hermetic Spack reference
prefix and covers:

- `libcpuinfo.so` SONAME, NEEDED set, and exported dynamic symbols;
- downstream link-and-run behavior for `use_cpuinfo.c`;
- pkg-config and CMake metadata parity after prefix normalization;
- executable layout and dynamic dependency parity for the four installed tools;
- runtime behavior for `cpu-info`, `isa-info`, and `cache-info` under the
  parity helper's prefix-scoped `LD_LIBRARY_PATH`.

`cpuid-dump` is intentionally not stdout-compared: it reports APIC values for
the CPU that runs that individual process, so candidate and reference runs can
differ when scheduled on different cores even though both binaries execute
successfully.

## ODR-sensitive provider invariant

This slice does not add protobuf, gRPC, Abseil, or Boost providers. Those
families must stay on one unified compatible concrete version family across
all companion packages before any native flip. The generator rejects
unqualified overrides and rejects mixed concrete family versions, including
protobuf/Python protobuf and gRPC/gRPC C++ pairings.
