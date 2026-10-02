# file/libmagic frontier recipe

## Position in the hillclimb

`file@5.46` is a migrated Autotools node in the PyTorch frontier. In the
captured lean `SPACK_ROOT_PKG=py-torch` graph it appears as:

```text
74  file  5.46  autotools
```

The focused reference graph for `SPACK_ROOT_PKG='file@5.46'` ends with:

```text
11  file  5.46  autotools
```

`spack_file` has been flipped from provider `spack` to provider `native`
without changing its DAG position or downstream edges:

```json
{
  "package": "file",
  "version": "5.46",
  "build": "native",
  "link_deps": ["spack_bzip2", "spack_xz", "spack_zlib_ng", "spack_zstd"],
  "link_libs": ["magic"],
  "include_dirs": ["include"],
  "native_prefix": "@file_native//:lib"
}
```

## Spack evidence

All recipe evidence comes from Bazel's vendored `@spack_dist//:spack` running
inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/file-5.46-uq5pp2k2hgrmlt6kkctf5qhb3netz5lf
```

Source provenance from the hermetic Spack package recipe:

- package class: `File(AutotoolsPackage)`
- upstream source URL used by the native rule:
  `https://astron.com/pub/file/file-5.46.tar.gz`
- version `5.46` SHA256:
  `c9cc77c7c560c543135edc555af609d5619dbef011997e988ce40a3d75d86088`
- build system: Autotools
- concrete variant: `+static`
- build/link dependencies: `bzip2`, `xz`, `zlib-ng`, and `zstd`

The focused graph records those dependencies as build+link edges for each
compression library:

```text
build,link: bzip2, xz, zlib-ng, zstd
build:      compiler-wrapper, gcc, gmake
link:       gcc-runtime, glibc
```

## Native build

`native/file/file.bzl` defines `file_native`, a Bazel repository rule that
declares `VASO_IN_INSULA` as an environment input and refuses to build unless
the hermetic insula sets `VASO_IN_INSULA=1`.

The rule consumes only Bazel-native dependency prefixes:

```text
BZIP2_PREFIX <- @bzip2_native//:prefix_path.txt
XZ_PREFIX    <- @xz_native//:prefix_path.txt
ZLIB_PREFIX  <- @zlib_ng_native//:prefix_path.txt
ZSTD_PREFIX  <- @zstd_native//:prefix_path.txt
```

The build action fetches the pinned source archive, creates `spack-build`, and
runs the Autotools flow:

```sh
export CPPFLAGS="-I${ZSTD_PREFIX}/include -I${XZ_PREFIX}/include -I${BZIP2_PREFIX}/include -I${ZLIB_PREFIX}/include"
export CFLAGS="-O3 -g0 -march=icelake-client -mtune=icelake-client"
export CXXFLAGS="-O3 -g0 -march=icelake-client -mtune=icelake-client"
export LDFLAGS="-L${ZSTD_PREFIX}/lib -L${XZ_PREFIX}/lib -L${BZIP2_PREFIX}/lib -L${ZLIB_PREFIX}/lib -Wl,-rpath,<native-and-dep-libs> -Wl,--disable-new-dtags"

../configure \
  --prefix="$PREFIX" \
  --disable-dependency-tracking \
  --enable-fsect-man5 \
  --enable-zlib \
  --enable-bzlib \
  --enable-xzlib \
  --enable-zstdlib \
  --disable-lzlib \
  --enable-static

make V=1
make install
find "$PREFIX" -type f -name '*.la' -delete
```

The mechanism verifier records the dependency contract as:

```text
native/file/file.bzl: autotools: BZIP2_PREFIX, XZ_PREFIX, ZLIB_PREFIX, ZSTD_PREFIX
```

That is the Autotools dependency channel for this package: dependency include
and library paths enter through `CPPFLAGS` and `LDFLAGS`, with no ambient host
library discovery.

## Prefix and ABI gate

The ABI-relevant prefix contract used by the gate is:

- header: `include/magic.h`
- libraries: `lib/libmagic.a`, `lib/libmagic.so`,
  `lib/libmagic.so.1`, `lib/libmagic.so.1.0.0`
- pkg-config metadata: `lib/pkgconfig/libmagic.pc`
- data: `share/misc/magic.mgc`
- executable: `bin/file`

`//synthetic:use_file_native` locates only Bazel runfiles prefixes for
`@file_native`, `@bzip2_native`, `@xz_native`, `@zlib_ng_native`, and
`@zstd_native`, then checks:

```text
file --version contains file-5.46
file /tmp/file-native-smoke.txt detects ASCII text
```

`//synthetic:file_abi_parity` compares `@file_native//:prefix` against the
hermetic Spack reference prefix:

- layout for the header, libmagic libraries and symlinks, pkg-config file,
  magic database, and `bin/file`;
- SONAME and exported dynamic symbols for `lib/libmagic.so.1.0.0`;
- downstream link-and-run via `synthetic/use_file.c`, which calls
  `magic_open`, `magic_load`, `magic_buffer`, and `magic_version`;
- executable dependency parity for `bin/file`;
- matching behavior for `bin/file --version` and text-file detection.

Current verdict: migrated provider. The focused command below passed inside the
CUDA insula:

```sh
SPACK_ROOT_PKG='file@5.46' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_FORCE_FETCH_REPOS='@file_native' \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_file_native //synthetic:file_abi_parity //tools:hermetic_native_deps_guard_test //tools:native_build_mechanism_guard_unit_test //tools:abi_parity_unit_test' \
VASO_SPACK_TIMEOUT=900 \
./run.sh
```

The gate covers both build-mechanism hermeticity and runtime compatibility:
the package refuses host-root execution, all compression-library dependencies
come from Bazel prefix files, and the linked consumer exercises `libmagic`
through the same Spack-generated facade used by downstream nodes.
