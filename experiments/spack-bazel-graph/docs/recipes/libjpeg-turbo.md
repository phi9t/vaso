# libjpeg-turbo@3.1.3

## Position in the hillclimb

`libjpeg-turbo` is the py-torch frontier node immediately after native
`libevent`. In the captured `SPACK_ROOT_PKG=py-torch` graph it appears as:

```text
65  libjpeg-turbo  3.1.3  cmake
```

The focused reference graph used for this migration is:

```bash
SPACK_ROOT_PKG='libjpeg-turbo@3.1.3'
```

The reference and native runs used Bazel's vendored `@spack_dist//:spack`
inside the isolated CUDA 12.9.1 insula. The native run flips
`spack_libjpeg_turbo` to `@libjpeg_turbo_native//:lib` without changing the
Spack DAG edges.

## Hermetic Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic archived recipe path in the isolated estate:

```text
/vaso/cache/spack/opt/spack/linux-icelake/libjpeg-turbo-3.1.3-7nukymgatqdmbvtznk7dcmmavi6zwk77/.spack/repos/spack_repo/builtin/packages/libjpeg_turbo/package.py
```

Source provenance from the Spack recipe:

- package class: `LibjpegTurbo(CMakePackage)`
- Spack build-system bucket: `cmake`
- upstream source URL:
  `https://github.com/libjpeg-turbo/libjpeg-turbo/archive/3.1.3.tar.gz`
- SHA256: `3a13a5ba767dc8264bc40b185e41368a80d5d5f945944d1dbaa4b2fb0099f4e5`
- concrete variants: `libs=shared,static`, `~jpeg8`, `+pic`,
  `~partial_decoder`, `build_type=Release`, `generator=make`, `~ipo`
- build dependency channel: `nasm`
- concrete CMake args:
  `-DENABLE_SHARED:BOOL=ON -DENABLE_STATIC:BOOL=ON -DWITH_JPEG8:BOOL=OFF -DCMAKE_POSITION_INDEPENDENT_CODE:BOOL=ON`

The focused `libjpeg-turbo@3.1.3` node has:

```text
build: cmake, compiler-wrapper, gcc, gmake, nasm
link: gcc-runtime, glibc
```

The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/libjpeg-turbo-3.1.3-7nukymgatqdmbvtznk7dcmmavi6zwk77
```

The ABI/prefix-relevant installed surface is:

```text
bin/cjpeg
bin/djpeg
bin/jpegtran
bin/rdjpgcom
bin/tjbench
bin/wrjpgcom
include/jconfig.h
include/jerror.h
include/jmorecfg.h
include/jpeglib.h
include/turbojpeg.h
lib/libjpeg.so -> libjpeg.so.62 -> libjpeg.so.62.4.0
lib/libturbojpeg.so -> libturbojpeg.so.0 -> libturbojpeg.so.0.4.0
lib/libjpeg.a
lib/libturbojpeg.a
lib/pkgconfig/libjpeg.pc
lib/pkgconfig/libturbojpeg.pc
lib/cmake/libjpeg-turbo/
```

## Native build recipe

`native/libjpeg_turbo/libjpeg_turbo.bzl` mirrors the concrete Spack CMake
flow:

```text
download libjpeg-turbo-3.1.3.tar.gz
validate native CMake and NASM prefixes from Bazel prefix files
export PATH=<cmake>/bin:<nasm>/bin:$PATH
export CMAKE_PREFIX_PATH=<cmake>;<nasm>
export CMAKE_PROGRAM_PATH=<cmake>/bin;<nasm>/bin
cmake -G "Unix Makefiles" \
  -DCMAKE_INSTALL_PREFIX=<prefix> \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF \
  -DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF \
  -DCMAKE_ASM_NASM_COMPILER=<nasm>/bin/nasm \
  -DENABLE_SHARED=ON \
  -DENABLE_STATIC=ON \
  -DWITH_JPEG8=OFF \
  -DCMAKE_POSITION_INDEPENDENT_CODE=ON
make
make install
emit prefix_path.txt
```

The native provider exposes:

- `@libjpeg_turbo_native//:prefix` for the installed prefix filegroup;
- `@libjpeg_turbo_native//:prefix_path.txt` for downstream native repository
  rules;
- `@libjpeg_turbo_native//:lib` with the same public link surface as Spack:
  `jpeg` and `turbojpeg`.

The corresponding mechanism verifier is the CMake dependency-prefix case:

```text
native/libjpeg_turbo/libjpeg_turbo.bzl: cmake: CMAKE_PREFIX, NASM_PREFIX
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, consumes
`@cmake_native//:prefix_path.txt` and `@nasm_native//:prefix_path.txt`, validates
`CMAKE_PREFIX/bin/cmake` and `NASM_PREFIX/bin/nasm`, and threads dependency
lookup through `CMAKE_PREFIX_PATH`, `CMAKE_PROGRAM_PATH`, and
`-DCMAKE_ASM_NASM_COMPILER`.

## Gates

The smoke target is:

```text
//synthetic:use_libjpeg_turbo_native
```

It compiles a C consumer against `@libjpeg_turbo_native//:lib`, includes
`<jpeglib.h>` and `<turbojpeg.h>`, creates a TurboJPEG handle, creates a
libjpeg compressor, and prints:

```text
libjpeg-turbo:62:samp=7:cs=5
```

`//synthetic:libjpeg_turbo_abi_parity` compares the native prefix against the
hermetic Spack reference. It covers:

- 25 installed layout paths;
- SONAME and exported-symbol parity for `libjpeg.so.62` and
  `libturbojpeg.so.0`;
- static archive member and symbol parity for `libjpeg.a` and
  `libturbojpeg.a`;
- prefix-normalized pkg-config metadata;
- byte-equivalent selected CMake package metadata;
- executable presence and NEEDED parity for the six installed tools;
- matching `cjpeg -version`, `djpeg -version`, and `jpegtran -version`;
- downstream C link-and-run output for both native and reference prefixes.

Current status: native libjpeg-turbo is gated inside the hermetic CUDA insula.
The focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='libjpeg-turbo@3.1.3' \
VASO_LOCK_OUT=/workspace/experiment/libjpeg_turbo_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/libjpeg_turbo_native_build_graph.json \
VASO_NATIVE=1 \
VASO_FORCE_FETCH_REPOS='@libjpeg_turbo_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_libjpeg_turbo_native' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

The run used Bazel's `@spack_dist//:spack` inside the isolated CUDA 12.9.1
insula, reported hermetic Spack version `1.2.2`, passed
`//tools:hermetic_spack_guard_test`, passed
`//tools:hermetic_native_deps_guard_test`, passed
`//tools:native_build_mechanism_guard_unit_test`, passed
`//synthetic:use_libjpeg_turbo_native`, and passed
`//synthetic:libjpeg_turbo_abi_parity`.

The parity JSON reported `ok: true`: 25 layout paths, SONAME/exported-symbol
parity for `libjpeg.so.62.4.0` and `libturbojpeg.so.0.4.0`, 191 exported
`libjpeg` symbols, 80 exported `libturbojpeg` symbols, static archive parity,
metadata parity for pkg-config and CMake package files, executable parity for
the six installed tools, and downstream C output matched
`libjpeg-turbo:62:samp=7:cs=5`.
