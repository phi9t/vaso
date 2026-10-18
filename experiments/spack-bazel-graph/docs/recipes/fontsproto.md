# fontsproto frontier recipe

## Position in the hillclimb

`fontsproto@2.1.3` is the next migrated py-torch frontier node after
`util-macros@1.20.2`. In the captured `SPACK_ROOT_PKG=py-torch` graph it
appears as:

```text
43  fontsproto  2.1.3  autotools  native; prefix parity green
```

The package-local graph was captured inside the CUDA insula with Bazel's
vendored Spack:

```bash
SPACK_ROOT_PKG='fontsproto@2.1.3' \
VASO_NATIVE=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_LOCK_OUT=/workspace/experiment/fontsproto_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/fontsproto_build_graph.json \
VASO_SPACK_TIMEOUT=1200 \
./run.sh
```

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/fontsproto/package.py
```

Source provenance from the Spack recipe:

- package class: `Fontsproto(AutotoolsPackage, XorgPackage)`
- version: `2.1.3`
- upstream source URL:
  `https://xorg.freedesktop.org/archive/individual/proto/fontsproto-2.1.3.tar.gz`
- SHA256:
  `72c44e63044b2b66f6fa112921621ecc20c71193982de4f198d9a29cda385c5e`
- patch surface: none

The concrete `fontsproto@2.1.3` node depends on `pkgconf` and `util-macros` as
build-time prefixes, plus the compiler/toolchain nodes owned by the insula.

## Build recipe

Spack uses the standard Autotools phases:

```text
./configure --prefix=<prefix>
make V=1
make install
```

The observed hermetic Spack install produces a header/pkg-config/data prefix:

```text
include/X11/fonts/FS.h
include/X11/fonts/FSproto.h
include/X11/fonts/font.h
include/X11/fonts/fontproto.h
include/X11/fonts/fontstruct.h
include/X11/fonts/fsmasks.h
lib/pkgconfig/fontsproto.pc
share/doc/fontsproto/fsproto.xml
```

`native/fontsproto/fontsproto.bzl` mirrors that flow with the same source
tarball and SHA256. The repository rule refuses to run unless the hermetic
insula has set `VASO_IN_INSULA=1`, reads the native `pkgconf` and
`util-macros` prefixes through mandatory Bazel `*_prefix_file` attrs, validates
those prefixes in the build script, pins `PKG_CONFIG`, and sets `ACLOCAL_PATH`
from the native `util-macros` prefix before configure.

This is the Autotools dependency-prefix verifier case:

```text
native/fontsproto/fontsproto.bzl: autotools: PKGCONF_PREFIX, UTIL_MACROS_PREFIX
```

`//tools:hermetic_native_deps_guard_test` checks that the Autotools dependency
prefixes enter through Bazel-owned files and mechanism-specific channels rather
than host discovery.

## Prefix and behavior gates

`//synthetic:fontsproto_prefix_parity` compares the native prefix against the
hermetic Spack reference:

```text
/vaso/cache/spack/opt/spack/linux-icelake/fontsproto-2.1.3-xudq6b5zs4jphqvhpyngxlrea7yhqh5p
```

The gate covers:

- the eight-entry installed layout;
- byte-identical X11 fonts protocol headers;
- prefix-normalized `lib/pkgconfig/fontsproto.pc`;
- byte-identical `share/doc/fontsproto/fsproto.xml`;
- an empty ELF ABI axis, as expected for this header/data prefix.

`//synthetic:use_fontsproto_native` validates that downstream users can find the
native prefix and that it exposes the expected headers, pkg-config metadata, and
XML document, printing `fontsproto:2.1.3:ok`.

Current status: native and prefix-gated. The latest focused run passed inside
the CUDA insula:

```bash
VASO_NATIVE=1 \
SPACK_ROOT_PKG='fontsproto@2.1.3' \
VASO_LOCK_OUT=/workspace/experiment/fontsproto_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/fontsproto_build_graph.json \
VASO_FORCE_FETCH_REPOS='@fontsproto_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_fontsproto_native //tools:hermetic_native_deps_guard_test //synthetic:fontsproto_prefix_parity' \
VASO_SPACK_TIMEOUT=1200 \
./run.sh
```
