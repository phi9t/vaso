# libxfont@1.5.4

## Position in the hillclimb

`libxfont@1.5.4` is the py-torch frontier node immediately after native
`freetype@2.14.2`. In the captured `SPACK_ROOT_PKG=py-torch` graph it appears
as:

```text
68  libxfont  1.5.4  autotools
```

The focused reference graph used for this migration is:

```bash
SPACK_ROOT_PKG='libxfont@1.5.4'
```

The focused all-Spack run wrote `libxfont_spack_graph.lock.json` and
`libxfont_build_graph.json`. The native run wrote
`libxfont_native_spack_graph.lock.json` and
`libxfont_native_build_graph.json`, then flips `spack_libxfont` to
`@libxfont_native//:lib` without changing the Spack DAG edges.

## Hermetic Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path in the isolated estate:

```text
/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/libxfont/package.py
```

Source provenance from the Spack recipe:

- package class: `Libxfont(AutotoolsPackage, XorgPackage)`
- homepage: `https://gitlab.freedesktop.org/xorg/lib/libXfont`
- concrete Linux build mechanism: Autotools
- upstream source URL:
  `https://xorg.freedesktop.org/archive/individual/lib/libXfont-1.5.4.tar.gz`
- SHA256: `59be6eab53f7b0feb6b7933c11d67d076ae2c0fd8921229c703fc7a4e9a80d6e`

The concrete focused node has:

```text
build: compiler-wrapper, fontsproto, freetype, gcc, gmake, libfontenc,
       pkgconf, util-macros, xproto, xtrans
link: fontsproto, freetype, gcc-runtime, glibc, libfontenc, xproto, xtrans
```

The native rule also consumes `bzip2`, `libpng`, and `zlib-ng` explicitly.
Those are not direct libXfont edges in the focused Spack graph; they are
private pkg-config/link requirements surfaced by the native freetype/libpng
closure and by libXfont's `xfont.pc` `Libs.private: -lz -lm`. They must be
threaded through Bazel prefix files so pkg-config, the linker, and static
metadata never discover rootfs libraries.

The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/libxfont-1.5.4-tzvpqqqqdjjekiza6us7vtd55d4po64b
```

The ABI/prefix-relevant installed surface is:

```text
include/X11/fonts/bdfint.h
include/X11/fonts/bitmap.h
include/X11/fonts/bufio.h
include/X11/fonts/fntfil.h
include/X11/fonts/fntfilio.h
include/X11/fonts/fntfilst.h
include/X11/fonts/fontconf.h
include/X11/fonts/fontencc.h
include/X11/fonts/fontmisc.h
include/X11/fonts/fontshow.h
include/X11/fonts/fontutil.h
include/X11/fonts/fontxlfd.h
include/X11/fonts/ft.h
include/X11/fonts/ftfuncs.h
include/X11/fonts/pcf.h
lib/libXfont.a
lib/libXfont.so
lib/libXfont.so.1
lib/libXfont.so.1.4.1
lib/pkgconfig/xfont.pc
```

The reference shared object has SONAME `libXfont.so.1` and NEEDED entries for
`libfreetype.so.6`, `libz.so.1`, `libm.so.6`, `libfontenc.so.1`, and
`libc.so.6`. The parity gate's exported-symbol filter reports 210 exported
dynamic symbols.

## Native build recipe

`native/libxfont/libxfont.bzl` mirrors the concrete Spack Autotools flow:

```text
download libXfont-1.5.4.tar.gz
validate bzip2, fontsproto, freetype, libfontenc, libpng, pkgconf,
         util-macros, xproto, xtrans, and zlib-ng prefixes from Bazel
         prefix files
export PATH=<pkgconf-prefix>/bin:$PATH
export PKG_CONFIG=<pkgconf-prefix>/bin/pkgconf
export PKG_CONFIG_PATH=<bzip2>/lib/pkgconfig:<fontsproto>/lib/pkgconfig:
       <freetype>/lib/pkgconfig:<libfontenc>/lib/pkgconfig:
       <libpng>/lib/pkgconfig:<pkgconf>/lib/pkgconfig:
       <xproto>/lib/pkgconfig:<xtrans>/share/pkgconfig:
       <zlib-ng>/lib/pkgconfig:<util-macros>/share/pkgconfig
export ACLOCAL_PATH=<util-macros>/share/aclocal:<pkgconf>/share/aclocal
export CPPFLAGS=-I<bzip2>/include -I<fontsproto>/include
                -I<freetype>/include -I<freetype>/include/freetype2
                -I<libfontenc>/include -I<libpng>/include/libpng16
                -I<xproto>/include -I<xtrans>/include -I<zlib-ng>/include
export LDFLAGS=-L<bzip2>/lib -Wl,-rpath,<bzip2>/lib
               -L<freetype>/lib -Wl,-rpath,<freetype>/lib
               -L<libfontenc>/lib -Wl,-rpath,<libfontenc>/lib
               -L<libpng>/lib -Wl,-rpath,<libpng>/lib
               -L<zlib-ng>/lib -Wl,-rpath,<zlib-ng>/lib
               -Wl,-rpath,<prefix>/lib -Wl,--disable-new-dtags
export LIBS="-lz -lm"
./configure --prefix=<prefix>
make V=1
make install
remove libtool archives
emit prefix_path.txt
```

The native provider exposes:

- `@libxfont_native//:prefix` for the installed prefix filegroup;
- `@libxfont_native//:prefix_path.txt` for downstream native repository rules;
- `@libxfont_native//:lib` with the same public link surface as Spack:
  `Xfont`, freetype, zlib-ng, math, libfontenc, libpng, and bzip2.

The corresponding mechanism verifier is the Autotools dependency-prefix case:

```text
native/libxfont/libxfont.bzl: autotools: BZIP2_PREFIX, FONTSPROTO_PREFIX, FREETYPE_PREFIX, LIBFONTENC_PREFIX, LIBPNG_PREFIX, PKGCONF_PREFIX, UTIL_MACROS_PREFIX, XPROTO_PREFIX, XTRANS_PREFIX, ZLIB_PREFIX
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, consumes each
dependency through a mandatory Bazel `*_prefix_file`, validates all ten
prefixes before configure, pins `PKG_CONFIG`, and threads dependency lookup
through Autotools-specific `PKG_CONFIG_PATH`, `ACLOCAL_PATH`, `CPPFLAGS`,
`LDFLAGS`, and `LIBS` channels.

## Gates

The smoke target is:

```text
//synthetic:use_libxfont_native
```

It compiles a C consumer against `@libxfont_native//:lib`, uses the libXfont
atom table entry points, and prints:

```text
libxfont:1:libxfont-native-smoke
```

`//synthetic:libxfont_abi_parity` compares the native prefix against the
hermetic Spack reference. It covers:

- the 20-path installed layout under `include/X11/fonts`, `lib`, and
  `lib/pkgconfig`;
- SONAME parity for `lib/libXfont.so.1.4.1` (`libXfont.so.1`);
- exported-symbol parity for 210 dynamic symbols;
- static archive member and symbol parity for `lib/libXfont.a`;
- prefix-normalized `lib/pkgconfig/xfont.pc`;
- downstream C link-and-run output for both native and reference prefixes,
  with bzip2, freetype, libfontenc, libpng, and zlib-ng link prefixes supplied
  explicitly.

Current status: native libXfont is gated inside the hermetic CUDA insula. The
focused native verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='libxfont@1.5.4' \
VASO_LOCK_OUT=/workspace/experiment/libxfont_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/libxfont_native_build_graph.json \
VASO_NATIVE=1 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_libxfont_native' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside the isolated CUDA 12.9.1
insula, reported hermetic Spack version `1.2.2`, passed
`//tools:hermetic_spack_guard_test`, passed
`//tools:hermetic_native_deps_guard_test`, passed
`//tools:native_build_mechanism_guard_unit_test`, passed
`//synthetic:use_libxfont_native`, and passed
`//synthetic:libxfont_abi_parity`.
