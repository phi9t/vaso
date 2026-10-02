# xproto frontier recipe

## Position in the hillclimb

`xproto@7.0.31` is the next migrated py-torch frontier node after
`libpciaccess@0.17`. In the captured `SPACK_ROOT_PKG=py-torch` graph it
appears as:

```text
45  xproto  7.0.31  autotools  native; prefix parity green
```

The package-local graph was captured inside the CUDA insula with Bazel's
vendored Spack:

```bash
SPACK_ROOT_PKG='xproto@7.0.31' \
VASO_NATIVE=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_LOCK_OUT=/workspace/experiment/xproto_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/xproto_build_graph.json \
VASO_SPACK_TIMEOUT=1200 \
./run.sh
```

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/xproto/package.py
```

Source provenance from the Spack recipe:

- package class: `Xproto(AutotoolsPackage, XorgPackage)`
- version: `7.0.31`
- upstream source URL:
  `https://xorg.freedesktop.org/archive/individual/proto/xproto-7.0.31.tar.gz`
- SHA256:
  `6d755eaae27b45c5cc75529a12855fed5de5969b367ed05003944cf901ed43c7`
- patch surface: none

The concrete `xproto@7.0.31` node depends on `pkgconf` and `util-macros` as
build-time prefixes, plus the compiler/toolchain nodes owned by the insula.

## Build recipe

Spack uses the standard X.Org Autotools phases, with a package override that
runs install serially:

```text
autoreconf
./configure --prefix=<prefix>
make V=1
make install  # serial
```

The observed hermetic Spack configure warns that optional documentation tools
are absent (`xmlto`, `fop`, `xsltproc`), while still installing the source XML
documents. The installed prefix has 28 header/pkg-config/XML entries, including:

```text
include/X11/X.h
include/X11/Xproto.h
include/X11/Xprotostr.h
include/X11/keysymdef.h
lib/pkgconfig/xproto.pc
share/doc/xproto/x11protocol.xml
```

`native/xproto/xproto.bzl` mirrors that flow with the same source tarball and
SHA256. The repository rule refuses to run unless the hermetic insula has set
`VASO_IN_INSULA=1`, reads the native `pkgconf` and `util-macros` prefixes
through mandatory Bazel `*_prefix_file` attrs, validates those prefixes in the
build script, pins `PKG_CONFIG`, sets `PKG_CONFIG_PATH` and `ACLOCAL_PATH` from
native prefixes, and preserves Spack's serial install.

This is the Autotools dependency-prefix verifier case:

```text
native/xproto/xproto.bzl: autotools: PKGCONF_PREFIX, UTIL_MACROS_PREFIX
```

`//tools:hermetic_native_deps_guard_test` checks that the Autotools dependency
prefixes enter through Bazel-owned files and mechanism-specific channels rather
than host discovery.

## Prefix and behavior gates

`//synthetic:xproto_prefix_parity` compares the native prefix against the
hermetic Spack reference:

```text
/vaso/cache/spack/opt/spack/linux-icelake/xproto-7.0.31-6wuxlzyck5bj2wzadk2laiizaasu6tkg
```

The gate covers:

- the 28-entry installed layout;
- byte-identical representative X11 protocol headers;
- prefix-normalized `lib/pkgconfig/xproto.pc`;
- byte-identical `share/doc/xproto/x11protocol.xml`;
- an empty ELF ABI axis, as expected for this header/data prefix.

`//synthetic:use_xproto_native` validates that downstream users can find the
native prefix and that it exposes the expected headers, pkg-config metadata, and
XML document, printing `xproto:7.0.31:ok`.

Current status: native and prefix-gated. The latest focused run passed inside
the CUDA insula:

```bash
VASO_NATIVE=1 \
SPACK_ROOT_PKG='xproto@7.0.31' \
VASO_LOCK_OUT=/workspace/experiment/xproto_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/xproto_build_graph.json \
VASO_FORCE_FETCH_REPOS='@xproto_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_xproto_native //tools:hermetic_native_deps_guard_test //synthetic:xproto_prefix_parity' \
VASO_SPACK_TIMEOUT=1200 \
./run.sh
```
