# libxml2 (autotools consumer deep dive)

This recipe captures how Spack builds `libxml2` and how we reproduced it as a
Bazel-owned native build while keeping ABI/prefix parity and (critically) while
keeping dependency edges unchanged.

## Why this node matters

`libxml2` is an early “real consumer” node:

- it links against `zlib-ng` (compression) and `libiconv` (encoding),
- the concretized DAG also includes `xz` (liblzma) on this host,
- it produces both a shared library and tools (`xmllint`, `xmlcatalog`).

That combination makes it a good proof that migration works beyond leaf libs.

Reference prefix (Spack install):

- `/vaso/cache/spack/opt/spack/linux-icelake/libxml2-2.13.9-lp67nrne6ntrdzr6y3jrkwzdyxpiuhqh`

## Spack build system

The concretized `build_system` variant is `autotools` (see `.spack/spec.json`).
The underlying build is the usual autotools pipeline:

- `./configure --prefix=<prefix> ...`
- `make`
- `make install`

Two Spack-emitted details we had to match for parity:

1. **Headers are under a nested include dir.** Spack installs headers under
   `include/libxml2/`, and consumers typically include them as
   `<libxml/parser.h>`, so consumers need `-I<prefix>/include/libxml2`.
2. **Static library is not shipped.** The Spack prefix contains `libxml2.so*`
   but not `libxml2.a` (so our native install must not ship it either).

## Native Bazel reproduction

Native rule:

- `native/libxml2/libxml2.bzl` (`libxml2_native`)

It drives `configure/make/install` and stitches in Spack-owned dependencies via
environment (prefix paths), so the topology remains Spack-defined.

Key reproduction points:

- `--with-iconv=<libiconv prefix>` and explicit `CPPFLAGS/LDFLAGS/LIBS` to make
  the link step resolve `libiconv.so.2` correctly.
- `--with-zlib=<zlib-ng prefix>` and `PKG_CONFIG_PATH` pointing at zlib-ng’s
  pkgconfig so configure can find zlib.
- `--with-lzma=<xz prefix>` plus explicit include/lib/rpath flags for the
  hermetic Spack-selected `xz` provider.
- `--without-python` and `--without-http` to keep optional integrations aligned
  with the current hermetic Spack recipe.
- `--disable-static` (plus a post-install cleanup of any `*.a`/`*.la`) to match
  the Spack prefix layout.

## ABI parity gate

Gate target:

- `//synthetic:libxml2_abi_parity`

The gate compares the native candidate prefix `@libxml2_native//:prefix` vs the
Spack prefix for:

- layout (include/lib*, pkgconfig)
- SONAME + exported symbols for `libxml2.so.2`
- link-and-run of `synthetic/use_libxml2.c` (prints the parsed root element)

The hermetic path runs inside the insula and snapshots the lock with Bazel's
`@spack_dist//:spack` wrapper:

- `SPACK_ROOT_PKG=python VASO_NATIVE=1 ./run.sh`
