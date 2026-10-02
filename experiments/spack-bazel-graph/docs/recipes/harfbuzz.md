# harfbuzz@11.5.1

## Position in the hillclimb

`harfbuzz@11.5.1` is the lean `py-torch` frontier Meson text shaping library
after native Cairo:

```text
140  pixman    0.46.4  meson  native; ABI parity green
141  cairo     1.18.4  meson  native; ABI parity green
142  harfbuzz  11.5.1  meson  native; ABI parity green
143  psimd     2020-05-17  cmake
```

Spack still owns the DAG shape. The native flip changes only
`spack_harfbuzz.build` to `native` and re-exports `@harfbuzz_native//:lib`;
dependency edges to Cairo, FreeType, GLib/GObject, gobject-introspection, ICU,
Meson, Ninja, pkgconf, and zlib-ng remain Spack-derived. The native rule also
carries explicit bzip2, fontconfig, libffi, libiconv, libpng, libxml2, lzo,
pcre2, and pixman prefix channels because those appear through the native
pkg-config dependency closure and must not be discovered from the rootfs.

The focused reference graph for this slice is:

```bash
SPACK_ROOT_PKG='harfbuzz'
```

## Hermetic Spack Evidence

All recipe evidence comes from Bazel's vendored `@spack_dist//:spack` and the
Bazel-owned estate cache under `/vaso/cache/spack`. Do not use an ambient host
Spack checkout for this package.

Hermetic Spack version observed in the focused run:

```text
1.2.2
```

The focused graph contains 75 nodes and ends with:

```text
74  harfbuzz  11.5.1  meson
```

The lean `py-torch` graph position is:

```text
142  harfbuzz  11.5.1  meson
```

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/harfbuzz-11.5.1-k7fqaluilat72h7piyfztq62euxozdjg
```

Source provenance:

- upstream source URL:
  `https://github.com/harfbuzz/harfbuzz/releases/download/11.5.1/harfbuzz-11.5.1.tar.xz`
- version `11.5.1` SHA256:
  `972a60a8d274d49e70361da6920c3a73dfb0fb4387f6c6811906a47ba634d8a1`
- selected build system: Meson
- concrete Meson variants: `buildtype=release`, `default_library=shared`,
  `strip=false`, `graphite2=false`

The concrete focused node has:

```text
build+link: cairo, freetype, glib, gobject-introspection, icu4c, meson, zlib-ng
build: compiler-wrapper, gcc, ninja, pkgconf
link: gcc-runtime, glibc
```

The ABI/prefix-relevant installed surface is:

```text
bin/hb-info
bin/hb-shape
bin/hb-subset
bin/hb-view
include/harfbuzz/*.h
lib/libharfbuzz.so -> libharfbuzz.so.0 -> libharfbuzz.so.0.61151.0
lib/libharfbuzz-subset.so -> libharfbuzz-subset.so.0 -> libharfbuzz-subset.so.0.61151.0
lib/libharfbuzz-icu.so -> libharfbuzz-icu.so.0 -> libharfbuzz-icu.so.0.61151.0
lib/libharfbuzz-cairo.so -> libharfbuzz-cairo.so.0 -> libharfbuzz-cairo.so.0.61151.0
lib/libharfbuzz-gobject.so -> libharfbuzz-gobject.so.0 -> libharfbuzz-gobject.so.0.61151.0
lib/pkgconfig/harfbuzz*.pc
```

## Native Build Recipe

`native/harfbuzz/harfbuzz.bzl` mirrors Spack's Meson flow inside the CUDA
insula:

```text
download harfbuzz-11.5.1.tar.xz
validate bzip2, cairo, fontconfig, freetype, glib, gobject-introspection,
         icu4c, libffi, libiconv, libpng, libxml2, lzo, meson, ninja, pcre2,
         pixman, pkgconf, and zlib-ng prefixes
export PATH=<meson>:<ninja>:<pkgconf>:<gobject-introspection>:$PATH
derive PYTHON_ABI from <meson>/lib/pythonX.Y/site-packages
export PYTHONHOME=
export PYTHONPATH=<meson>/lib/python${PYTHON_ABI}/site-packages
export PKG_CONFIG=<pkgconf-prefix>/bin/pkgconf
export PKG_CONFIG_PATH=<native dependency pkgconfig dirs>
export CPPFLAGS=<native dependency include dirs>
export CFLAGS="-O3 -g0 -march=icelake-client -mtune=icelake-client ..."
export CXXFLAGS="-O3 -g0 -march=icelake-client -mtune=icelake-client ..."
export LDFLAGS="-L<dep>/lib ... -Wl,-rpath-link,<dep>/lib ... -Wl,-rpath,<dep>/lib ... -Wl,-rpath,<prefix>/lib -Wl,--disable-new-dtags"
meson setup <build> <src> \
  -Dprefix=<prefix> \
  -Dlibdir=<prefix>/lib \
  -Dbuildtype=release \
  -Dstrip=false \
  -Ddefault_library=shared \
  -Dwrap_mode=nodownload \
  -Ddocs=disabled \
  -Dfreetype=enabled \
  -Dgraphite2=disabled \
  -Dcoretext=disabled \
  -Dintrospection=disabled
ninja -C <build> -v
ninja -C <build> install
delete .la files
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, consumes every
dependency through a mandatory Bazel `*_prefix_file`, validates each prefix
before configure, pins Meson/Ninja/pkgconf/gobject-introspection through those
prefixes, and routes dependency discovery through pkg-config, compiler flags,
linker flags, and `LD_LIBRARY_PATH`. The explicit `-Wl,-rpath-link` entries
keep link-time resolution of indirect native DSOs inside the hermetic prefix
closure.

The corresponding mechanism verifier is the Meson dependency-prefix case:

```text
native/harfbuzz/harfbuzz.bzl: meson: BZIP2_PREFIX, CAIRO_PREFIX, FONTCONFIG_PREFIX, FREETYPE_PREFIX, GLIB_PREFIX, GOBJECT_INTROSPECTION_PREFIX, ICU4C_PREFIX, LIBFFI_PREFIX, LIBICONV_PREFIX, LIBPNG_PREFIX, LIBXML2_PREFIX, LZO_PREFIX, MESON_PREFIX, NINJA_PREFIX, PCRE2_PREFIX, PIXMAN_PREFIX, PKGCONF_PREFIX, ZLIB_PREFIX
```

## Gates

The smoke target is:

```text
//synthetic:use_harfbuzz_native
```

It links a C consumer against the generated `@spack_harfbuzz//:lib` provider,
creates an
`hb_buffer_t`, checks version `11.5.1`, shapes a short string, and prints:

```text
harfbuzz:11.5.1:len=4:dir=4:first=118
```

`//synthetic:harfbuzz_abi_parity` compares the native prefix against the
hermetic Spack reference prefix supplied by `run.sh` through
`SPACK_HARFBUZZ_PREFIX`. The gate covers:

- installed layout parity;
- SONAME and exported-symbol parity for `libharfbuzz.so.0`,
  `libharfbuzz-subset.so.0`, `libharfbuzz-icu.so.0`,
  `libharfbuzz-cairo.so.0`, and `libharfbuzz-gobject.so.0`;
- explicit ELF dependency parity for the subset, ICU, Cairo, and GObject
  shared-library symlink paths;
- executable NEEDED parity for `hb-info`, `hb-shape`, `hb-subset`, and
  `hb-view`;
- prefix-normalized pkg-config metadata for `harfbuzz.pc`,
  `harfbuzz-subset.pc`, `harfbuzz-icu.pc`, `harfbuzz-cairo.pc`, and
  `harfbuzz-gobject.pc`;
- downstream C link-and-run parity against both reference and native prefixes.

Current status: native HarfBuzz is verified in the focused hermetic CUDA
12.9.1 insula run. `//synthetic:harfbuzz_abi_parity` passed against the
hermetic Spack reference prefix for 65 installed layout paths,
SONAME/exported-symbol parity across five shared libraries, prefix-normalized
pkg-config metadata, executable dependency parity, and matching downstream C
link-and-run output:

```text
harfbuzz:11.5.1:len=4:dir=4:first=118
```

The same verification run force-refetched `@icu4c_native` and confirmed the
ICU `pkgdata.inc` symlink preservation fix with `//synthetic:icu4c_abi_parity`
before rerunning HarfBuzz.
