# libfontenc frontier recipe

## Position in the hillclimb

`libfontenc@1.1.8` is the next migrated py-torch frontier node after
`xtrans@1.6.0`. The immediate topo entries between them, `xz@5.8.3` and
`zlib-ng@2.3.3`, were migrated earlier. In the captured
`SPACK_ROOT_PKG=py-torch` graph, `libfontenc` appears as:

```text
49  libfontenc  1.1.8  autotools  native; ABI parity green
```

The package-local graph was captured inside the CUDA insula with Bazel's
vendored Spack:

```bash
SPACK_ROOT_PKG='libfontenc@1.1.8' \
VASO_NATIVE=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_LOCK_OUT=/workspace/experiment/libfontenc_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/libfontenc_build_graph.json \
VASO_SPACK_TIMEOUT=1200 \
./run.sh
```

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/libfontenc/package.py
```

Source provenance from the Spack recipe:

- package class: `Libfontenc(AutotoolsPackage, XorgPackage)`
- version: `1.1.8`
- upstream source URL:
  `https://xorg.freedesktop.org/archive/individual/lib/libfontenc-1.1.8.tar.gz`
- SHA256:
  `b55039f70959a1b2f02f4ec8db071e5170528d2c9180b30575dccf7510d7fb9f`
- patch surface: none

The concrete `libfontenc@1.1.8` node depends on `zlib-api` for link and on
`xproto`, `pkgconfig`, and `util-macros` as build-time prefixes, plus the
compiler/toolchain nodes owned by the insula.

## Build recipe

Spack uses the standard X.Org Autotools phases:

```text
autoreconf
./configure --prefix=<prefix>
make V=1
make install
```

The observed hermetic Spack configure carries the dependency channels through
`PKG_CONFIG_PATH`, `FONTENC_CFLAGS` from the `xproto` prefix, and `LIBS=-lz`
from the zlib provider. Spack removes libtool `.la` files from the emitted
prefix.

The installed prefix has six public entries:

```text
include/X11/fonts/fontenc.h
lib/libfontenc.a
lib/libfontenc.so
lib/libfontenc.so.1
lib/libfontenc.so.1.0.0
lib/pkgconfig/fontenc.pc
```

`native/libfontenc/libfontenc.bzl` mirrors that flow with the same source
tarball and SHA256. The repository rule refuses to run unless the hermetic
insula has set `VASO_IN_INSULA=1`, reads the native `pkgconf`, `util-macros`,
`xproto`, and `zlib-ng` prefixes through mandatory Bazel `*_prefix_file` attrs,
validates those prefixes in the build script, pins `PKG_CONFIG`, and sets
`PKG_CONFIG_PATH`, `ACLOCAL_PATH`, `CPPFLAGS`, `LDFLAGS`, and `LIBS` from native
prefixes before configure.

This is the Autotools dependency-prefix verifier case:

```text
native/libfontenc/libfontenc.bzl: autotools: PKGCONF_PREFIX, UTIL_MACROS_PREFIX, XPROTO_PREFIX, ZLIB_PREFIX
```

`//tools:hermetic_native_deps_guard_test` checks that the Autotools dependency
prefixes enter through Bazel-owned files and mechanism-specific channels rather
than host discovery.

## Prefix and behavior gates

`//synthetic:libfontenc_abi_parity` compares the native prefix against the
hermetic Spack reference:

```text
/vaso/cache/spack/opt/spack/linux-icelake/libfontenc-1.1.8-t4jhhleohrd4m3rxwpzxkbbdcv5olwo4
```

The gate covers:

- the six-entry installed layout;
- byte-identical `include/X11/fonts/fontenc.h`;
- prefix-normalized `lib/pkgconfig/fontenc.pc`;
- SONAME parity for `lib/libfontenc.so.1.0.0` (`libfontenc.so.1`);
- exported dynamic symbol parity for 15 symbols;
- downstream link-and-run parity through deterministic XLFD encoding extraction
  and `FontEncDirectory()` behavior, with zlib available on both sides.

`//synthetic:use_libfontenc_native` validates that downstream users can compile,
link, and run against the native prefix, printing
`libfontenc:iso10646-1:encodings-dir`.

Current status: native and ABI-gated. The latest focused run passed inside the
CUDA insula:

```bash
VASO_NATIVE=1 \
SPACK_ROOT_PKG='libfontenc@1.1.8' \
VASO_LOCK_OUT=/workspace/experiment/libfontenc_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/libfontenc_build_graph.json \
VASO_FORCE_FETCH_REPOS='@libfontenc_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_libfontenc_native //tools:hermetic_native_deps_guard_test //synthetic:libfontenc_abi_parity' \
VASO_SPACK_TIMEOUT=1200 \
./run.sh
```
