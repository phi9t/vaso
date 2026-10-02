# pthreadpool native recipe

## Position in the hillclimb

`pthreadpool@2023-08-29` is the next CMakePackage source build after native
`psimd` in the lean `py-torch` frontier:

```text
143  psimd       2020-05-17  cmake  native
144  pthreadpool 2023-08-29  cmake
145  py-llvmlite 0.47.0      python_pip
```

The focused reference graph for `SPACK_ROOT_PKG='pthreadpool'` has 38
build-graph nodes and ends in `psimd`, then `pthreadpool`. Spack still owns the
DAG shape; the native flip changes only `spack_pthreadpool.build` to `native`
and re-exports `@pthreadpool_native//:lib`.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout. The
vendored distribution observed by the self-check is upstream Spack `v1.2.2`.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/pthreadpool-2023-08-29-dpncjqjcwu6fbpyzsmq2wfy2gdbw2yzg
```

Hermetic recipe path observed from the Bazel-owned Spack package repository:

```text
/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/pthreadpool/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `Pthreadpool(CMakePackage)`
- homepage and git remote: `https://github.com/Maratyszcza/pthreadpool`
- version: `2023-08-29`
- source commit:
  `4fe0e1e183925bf8cfa6aae24237e724a96479b8`
- source archive used by the native rule:
  `https://github.com/Maratyszcza/pthreadpool/archive/4fe0e1e183925bf8cfa6aae24237e724a96479b8.tar.gz`
- source SHA256:
  `9e8fb6a8ce03616b84f773d7b50ebe0ea2fe3af5b7df6c6f96fa6b3f049a3303`
- CMake generator: `Ninja`
- build dependencies: C, CXX, CMake `@3.5:`, Python, and Ninja via generator
- Spack resource: FXdiv at `deps/fxdiv`; the native rule pins the observed
  source-cache commit archive
  `b408327ac2a15ec3e43352421954f5b1967701d1`
- GoogleTest and Google Benchmark resources are declared by the Spack recipe
  but are not needed by the concrete build because tests and benchmarks are
  disabled

The concrete Spack configure command used hermetic CMake, Ninja, and Python
prefixes:

```text
cmake -G Ninja \
  -DCMAKE_INSTALL_PREFIX:STRING=<pthreadpool-prefix> \
  -DCMAKE_INSTALL_RPATH_USE_LINK_PATH:BOOL=ON \
  -DCMAKE_INSTALL_RPATH:STRING=<pthreadpool-prefix>/lib;<pthreadpool-prefix>/lib64 \
  -DCMAKE_PREFIX_PATH:STRING=<cmake-prefix>;<compiler-wrapper-prefix>;<gcc-runtime-prefix>;<ninja-prefix>;<python-prefix> \
  -DCMAKE_BUILD_TYPE:STRING=Release \
  -DCMAKE_INTERPROCEDURAL_OPTIMIZATION:BOOL=OFF \
  -DCMAKE_POLICY_DEFAULT_CMP0090:STRING=NEW \
  -DCMAKE_FIND_USE_PACKAGE_REGISTRY:BOOL=OFF \
  -DCMAKE_EXPORT_COMPILE_COMMANDS:BOOL=ON \
  -DPYTHON_EXECUTABLE:PATH=<python-prefix>/bin/python3.13 \
  -DPython_EXECUTABLE:PATH=<python-prefix>/bin/python3.13 \
  -DPython3_EXECUTABLE:PATH=<python-prefix>/bin/python3.13 \
  -DFXDIV_SOURCE_DIR:STRING=<source>/deps/fxdiv \
  -DGOOGLETEST_SOURCE_DIR:STRING=<source>/deps/googletest \
  -DGOOGLEBENCHMARK_SOURCE_DIR:STRING=<source>/deps/googlebenchmark \
  -DPTHREADPOOL_BUILD_TESTS:BOOL=OFF \
  -DPTHREADPOOL_BUILD_BENCHMARKS:BOOL=OFF \
  -DPTHREADPOOL_LIBRARY_TYPE:STRING=static \
  -DPTHREADPOOL_ALLOW_DEPRECATED_API:BOOL=ON \
  -DCMAKE_POSITION_INDEPENDENT_CODE:BOOL=ON
```

The installed stable prefix surface is:

- `include/pthreadpool.h`
- `include/fxdiv.h`
- `lib/libpthreadpool.a`

There are no public shared libraries, executables, pkg-config files, or CMake
package files in this concrete prefix.

## Native build recipe

`native/pthreadpool/pthreadpool.bzl` mirrors the CMakePackage install:

```text
download and extract the exact pthreadpool commit archive by SHA256
download and extract the pinned FXdiv archive into src/deps/fxdiv
create empty src/deps/googletest and src/deps/googlebenchmark directories
read @cmake_native//:prefix_path.txt
read @ninja_native//:prefix_path.txt
read @python_313_native//:prefix_path.txt
validate CMAKE_PREFIX/bin/cmake
validate NINJA_PREFIX/bin/ninja-build
derive PYTHON_ABI from PYTHON_PREFIX/bin/python3
validate PYTHON_PREFIX/bin/python${PYTHON_ABI}
set PATH=<cmake-prefix>/bin:<ninja-prefix>/bin:<python-prefix>/bin:$PATH
set CMAKE_PREFIX_PATH=<cmake-prefix>;<ninja-prefix>;<python-prefix>
run "$CMAKE_PREFIX/bin/cmake" -G Ninja with Spack's concrete CMake args
run "$NINJA_PREFIX/bin/ninja-build" -v
run "$NINJA_PREFIX/bin/ninja-build" install
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so configure, build,
install, and dependency discovery run only inside the sealed CUDA rootfs. It
never searches for host Spack; all Spack facts come from the Bazel-vendored
Spack run, and CMake, Ninja, and Python are supplied by Bazel-native prefixes.

The mechanism verifier reports this build channel:

```text
native/pthreadpool/pthreadpool.bzl: cmake: CMAKE_PREFIX, NINJA_PREFIX, PYTHON_PREFIX
```

`cmake` is the mechanism-specific guard for this native provider. It requires
explicit `*_prefix_file` attrs, shell-side prefix checks, and dependency
injection through CMake/PATH channels rather than ambient host discovery.

## Prefix and behavior gate

The smoke target is:

```text
//synthetic:use_pthreadpool_native
```

It depends on the generated `@spack_pthreadpool//:lib` provider surface,
creates a two-thread pool, runs an eight-item one-dimensional parallel loop,
and prints:

```text
pthreadpool:2:148
```

The prefix/ABI parity target is:

```text
//synthetic:pthreadpool_prefix_parity
```

It compares the native prefix against the hermetic Spack reference prefix for
the three installed layout paths, validates the static archive surface, and
links/runs the same downstream consumer with `-lpthreadpool` and `-pthread`.

The green insula run reported:

- `SPACK_ROOT_PKG='pthreadpool@2023-08-29 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib'`
  regenerated a 32-package lock rooted at `spack_pthreadpool` and a 38-node
  build graph in `cuda-bundle` rootfs mode using Bazel-owned Spack `v1.2.2`
- layout parity: 3 reference paths, 3 candidate paths, no missing or extra paths
- byte-identical headers:
  `include/pthreadpool.h` SHA256
  `afae65ccebb810dde2a3370875df42f89ef2e43502a3430208d7b6e980f4b74c`
  and `include/fxdiv.h` SHA256
  `7d290b7f60f171d3e240817db3dd14019618b7fef4c5ef135a65350c0dcc01ce`
- static archive ABI parity for `lib/libpthreadpool.a`: identical member set
  and 68 matching globally defined symbols
- link-and-run parity: both reference and native prefixes print
  `pthreadpool:2:148`
- Python freeze proof: both the hermetic Spack prefix and native candidate
  prefix are `ALLOWED` with no Python-ABI-bound artifacts
- focused guard proof: after removing the stale ratchet lines,
  `//tools:hermetic_native_deps_guard_test`,
  `//tools:native_dep_wiring_live_test`,
  `//tools:python_abi_literal_guard_unit_test`, and
  `//tools:native_build_mechanism_guard_unit_test` passed on the fixed host
  output base, then Bazel was shut down
- logs:
  `$VASO_ESTATE_ROOT/agents/trae/logs/pthreadpool-red-guards-after-ratchet-20260929T103634Z.log`,
  `$VASO_ESTATE_ROOT/agents/trae/logs/pthreadpool-green-guards-20260929T103815Z.log`,
  `$VASO_ESTATE_ROOT/agents/trae/logs/pthreadpool-insula-proof-focused-20260929T104221Z.log`,
  and
  `$VASO_ESTATE_ROOT/agents/trae/logs/pthreadpool-freeze-proof-20260929T104509Z.log`
