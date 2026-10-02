# cairo@1.18.4

## Position in the hillclimb

`cairo@1.18.4` is the lean `py-torch` frontier 2D graphics library after the
native font, PNG, Pixman, and LZO closure:

```text
142  cairo  1.18.4  meson
```

Spack still owns the DAG shape. The native flip changes only
`spack_cairo.build` to `native` and re-exports `@cairo_native//:lib`;
dependency edges to fontconfig, freetype, glib, libpng, lzo, pixman, pkgconf,
meson, and ninja remain Spack-derived. `bzip2` is also carried as an explicit
native prefix channel because `freetype2.pc` requires `bzip2.pc` during Cairo's
Meson/pkg-config discovery; `libxml2` is carried for the same reason through
`fontconfig.pc`.

The focused reference graph for this slice is:

```bash
SPACK_ROOT_PKG='cairo'
```

## Hermetic Spack Evidence

All recipe evidence comes from Bazel's vendored `@spack_dist//:spack` and the
Bazel-owned estate cache under `/vaso/cache/spack`. Do not use an ambient host
Spack checkout for this package.

Hermetic recipe path observed in the isolated estate:

```text
/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/cairo/package.py
```

Source provenance from that recipe:

- package class: `Cairo(AutotoolsPackage, MesonPackage)`
- selected build system: Meson only for `@1.18.0:`
- upstream source URL:
  `https://www.cairographics.org/releases/cairo-1.18.4.tar.xz`
- version `1.18.4` SHA256:
  `445ed8208a6e4823de1226a74ca319d3600e83f6369f99b14265006599c32ccb`
- concrete variants: `~X`, `~dwrite`, `+fc`, `+ft`, `~gobject`, `+pdf`,
  `+png`, `~quartz`, `+svg`, `~tee`, `+zlib`, `buildtype=release`,
  `default_library=shared`, `strip=false`

The concrete frontier node has:

```text
build: compiler-wrapper, gcc, meson, ninja, pkgconf
build+link: fontconfig, freetype, glib, libpng, lzo, pixman
link: gcc-runtime, glibc
```

The hermetic Spack reference prefix observed for this node is:

```text
/vaso/cache/spack/opt/spack/linux-icelake/cairo-1.18.4-63vksgiil5ztsvdr5xn2zconk4rme5cj
```

The ABI/prefix-relevant installed surface is:

```text
bin/cairo-trace
include/cairo/cairo.h
include/cairo/cairo-version.h
include/cairo/cairo-features.h
include/cairo/cairo-ft.h
include/cairo/cairo-pdf.h
include/cairo/cairo-ps.h
include/cairo/cairo-script.h
include/cairo/cairo-script-interpreter.h
include/cairo/cairo-svg.h
lib/libcairo.so -> libcairo.so.2 -> libcairo.so.2.11804.4
lib/libcairo-script-interpreter.so -> libcairo-script-interpreter.so.2 -> libcairo-script-interpreter.so.2.11804.4
lib/cairo/libcairo-trace.so
lib/pkgconfig/cairo*.pc
```

The reference shared-library SONAMEs are:

```text
libcairo.so.2
libcairo-script-interpreter.so.2
```

`libcairo.so` needs `libz.so.1`, `libpng16.so.16`,
`libfontconfig.so.1`, `libfreetype.so.6`, and `libpixman-1.so.0`.
`libcairo-script-interpreter.so` additionally needs `liblzo2.so.2`.

## Native Build Recipe

`native/cairo/cairo.bzl` mirrors Spack's Meson flow inside the CUDA insula:

```text
download cairo-1.18.4.tar.xz
validate bzip2, fontconfig, freetype, libpng, libxml2, lzo, meson, ninja,
         pixman, pkgconf, and zlib-ng prefixes
export PATH=<meson>:<ninja>:<pkgconf>:$PATH
derive PYTHON_ABI from <meson>/lib/pythonX.Y/site-packages
export PYTHONHOME=
export PYTHONPATH=<meson>/lib/python${PYTHON_ABI}/site-packages
export PKG_CONFIG=<pkgconf-prefix>/bin/pkgconf
export PKG_CONFIG_PATH=<native dependency pkgconfig dirs>
export CPPFLAGS=<native dependency include dirs>
export CFLAGS="-O3 -g0 -march=icelake-client -mtune=icelake-client ..."
export LDFLAGS="-L<dep>/lib ... -Wl,-rpath-link,<dep>/lib ... -Wl,-rpath,<dep>/lib ... -Wl,--disable-new-dtags"
meson setup <build> <src> \
  -Dprefix=<prefix> \
  -Dlibdir=<prefix>/lib \
  -Dbuildtype=release \
  -Dstrip=false \
  -Ddefault_library=shared \
  -Dwrap_mode=nodownload \
  -Ddwrite=disabled \
  -Dfontconfig=enabled \
  -Dfreetype=enabled \
  -Dpng=enabled \
  -Dquartz=disabled \
  -Dtee=disabled \
  -Dxcb=disabled \
  -Dxlib=disabled \
  -Dxlib-xcb=disabled \
  -Dzlib=enabled \
  -Dglib=disabled \
  -Dspectre=disabled \
  -Dsymbol-lookup=disabled
ninja -C <build> -v
ninja -C <build> install
delete .la files
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, consumes every
dependency through a mandatory Bazel `*_prefix_file`, validates each prefix
before configure, pins Meson/Ninja/pkgconf through those prefixes, and routes
dependency discovery through Meson's pkg-config and compiler/linker channels.
The linker channel includes `-Wl,-rpath-link` for explicit dependency prefixes
so link-time resolution of indirect native DSOs such as fontconfig -> libxml2
stays inside the hermetic prefix closure.

The corresponding mechanism verifier is the Meson dependency-prefix case:

```text
native/cairo/cairo.bzl: meson: BZIP2_PREFIX, FONTCONFIG_PREFIX, FREETYPE_PREFIX, LIBPNG_PREFIX, LIBXML2_PREFIX, LZO_PREFIX, MESON_PREFIX, NINJA_PREFIX, PIXMAN_PREFIX, PKGCONF_PREFIX, ZLIB_PREFIX
```

## Gates

The smoke target is:

```text
//synthetic:use_cairo_native
```

It links a C consumer against the generated `@spack_cairo//:lib` provider,
creates an ARGB image
surface, paints a pixel through Cairo, and prints:

```text
cairo:1.18.4:pixel=<nonzero>
```

`//synthetic:cairo_abi_parity` compares the native prefix against the hermetic
Spack reference prefix supplied by `run.sh` through `SPACK_CAIRO_PREFIX`. The
gate covers:

- installed layout parity;
- SONAME and exported-symbol parity for `libcairo.so.2` and
  `libcairo-script-interpreter.so.2`;
- ELF parity for `lib/cairo/libcairo-trace.so`;
- executable NEEDED parity for `bin/cairo-trace`;
- prefix-normalized pkg-config metadata for `cairo.pc`, `cairo-fc.pc`,
  `cairo-ft.pc`, `cairo-pdf.pc`, `cairo-png.pc`, `cairo-ps.pc`,
  `cairo-script.pc`, `cairo-script-interpreter.pc`, and `cairo-svg.pc`;
- downstream C link-and-run parity against both reference and native prefixes,
  with bzip2, fontconfig, freetype, libpng, libxml2, lzo, pixman, and zlib-ng
  supplied through explicit prefix channels.

Current status: native Cairo is verified in the focused hermetic CUDA 12.9.1
insula run. `//synthetic:use_cairo_native` passed with:

```text
cairo:1.18.4:pixel=ff4080bf
```

`//synthetic:cairo_abi_parity` passed against the hermetic Spack reference
prefix for 27 installed layout paths, SONAME/exported-symbol parity for
`libcairo.so.2`, `libcairo-script-interpreter.so.2`, and
`lib/cairo/libcairo-trace.so`, prefix-normalized pkg-config metadata,
`bin/cairo-trace` executable parity, and matching downstream C link-and-run
output. A forced insula refetch of `@fontconfig_native` and `@cairo_native`
cleared stale provider drift; the rebuilt `@fontconfig_native` now has
`NEEDED libxml2.so.2` and an RPATH to `@libxml2_native`.

## ODR-sensitive Provider Invariant

This slice does not add protobuf, gRPC, Abseil, or Boost providers. Those
families remain governed by `docs/pytorch-odr-dependencies.md` and exact native
override keys.
