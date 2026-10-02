# xtrans frontier recipe

## Position in the hillclimb

`xtrans@1.6.0` is the next migrated py-torch frontier node after
`xproto@7.0.31`. In the captured `SPACK_ROOT_PKG=py-torch` graph it appears as:

```text
46  xtrans  1.6.0  autotools  native; prefix parity green
```

The package-local graph was captured inside the CUDA insula with Bazel's
vendored Spack:

```bash
SPACK_ROOT_PKG='xtrans@1.6.0' \
VASO_NATIVE=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_LOCK_OUT=/workspace/experiment/xtrans_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/xtrans_build_graph.json \
VASO_SPACK_TIMEOUT=1200 \
./run.sh
```

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/xtrans/package.py
```

Source provenance from the Spack recipe:

- package class: `Xtrans(AutotoolsPackage, XorgPackage)`
- version: `1.6.0`
- upstream source URL:
  `https://xorg.freedesktop.org/archive/individual/lib/xtrans-1.6.0.tar.gz`
- SHA256:
  `936b74c60b19c317c3f3cb1b114575032528dbdaf428740483200ea874c2ca0a`
- patch surface: none

The concrete `xtrans@1.6.0` node depends on `pkgconf` and `util-macros` as
build-time prefixes, plus the compiler/toolchain nodes owned by the insula.

## Build recipe

Spack uses the standard X.Org Autotools phases:

```text
autoreconf
./configure --prefix=<prefix>
make V=1
make install
```

The observed hermetic Spack configure warns that optional documentation tools
are absent (`xmlto`, `fop`, `xsltproc`). The installed prefix has 10
header/source-include/pkg-config/macro/XML entries:

```text
include/X11/Xtrans/Xtrans.c
include/X11/Xtrans/Xtrans.h
include/X11/Xtrans/Xtransint.h
include/X11/Xtrans/Xtranslcl.c
include/X11/Xtrans/Xtranssock.c
include/X11/Xtrans/Xtransutil.c
include/X11/Xtrans/transport.c
share/aclocal/xtrans.m4
share/doc/xtrans/xtrans.xml
share/pkgconfig/xtrans.pc
```

`native/xtrans/xtrans.bzl` mirrors that flow with the same source tarball and
SHA256. The repository rule refuses to run unless the hermetic insula has set
`VASO_IN_INSULA=1`, reads the native `pkgconf` and `util-macros` prefixes
through mandatory Bazel `*_prefix_file` attrs, validates those prefixes in the
build script, pins `PKG_CONFIG`, and sets `PKG_CONFIG_PATH` and `ACLOCAL_PATH`
from native prefixes before configure.

This is the Autotools dependency-prefix verifier case:

```text
native/xtrans/xtrans.bzl: autotools: PKGCONF_PREFIX, UTIL_MACROS_PREFIX
```

`//tools:hermetic_native_deps_guard_test` checks that the Autotools dependency
prefixes enter through Bazel-owned files and mechanism-specific channels rather
than host discovery.

## Prefix and behavior gates

`//synthetic:xtrans_prefix_parity` compares the native prefix against the
hermetic Spack reference:

```text
/vaso/cache/spack/opt/spack/linux-icelake/xtrans-1.6.0-eo6wazbmwm7xirn3upg4nvwogtjtvacj
```

The gate covers:

- the 10-entry installed layout;
- byte-identical representative X11 transport headers/source includes;
- byte-identical `share/aclocal/xtrans.m4`;
- prefix-normalized `share/pkgconfig/xtrans.pc`;
- byte-identical `share/doc/xtrans/xtrans.xml`;
- an empty ELF ABI axis, as expected for this header/data prefix.

`//synthetic:use_xtrans_native` validates that downstream users can find the
native prefix and that it exposes the expected headers, macro, pkg-config
metadata, and XML document, printing `xtrans:1.6.0:ok`.

Current status: native and prefix-gated. The latest focused run passed inside
the CUDA insula:

```bash
VASO_NATIVE=1 \
SPACK_ROOT_PKG='xtrans@1.6.0' \
VASO_LOCK_OUT=/workspace/experiment/xtrans_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/xtrans_build_graph.json \
VASO_FORCE_FETCH_REPOS='@xtrans_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_xtrans_native //tools:hermetic_native_deps_guard_test //synthetic:xtrans_prefix_parity' \
VASO_SPACK_TIMEOUT=1200 \
./run.sh
```
