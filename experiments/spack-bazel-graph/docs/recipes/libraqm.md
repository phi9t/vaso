# libraqm@0.10.5

## Position in the hillclimb

`libraqm@0.10.5` is the lean `py-torch` frontier Meson text layout library
after the native FreeType, FriBidi, HarfBuzz, Meson, Ninja, and pkgconf
providers are available.

```text
142  harfbuzz  11.5.1  meson  native; ABI parity green
162  libraqm   0.10.5  meson
```

The focused reference graph for this slice is:

```bash
SPACK_ROOT_PKG='libraqm@0.10.5'
```

Spack still owns the DAG shape. The native flip changes only
`spack_libraqm.build` to `native` and re-exports `@libraqm_native//:lib`;
the dependency edges to FreeType, FriBidi, and HarfBuzz remain Spack-derived.

## Hermetic Spack Evidence

All recipe evidence comes from Bazel's vendored `@spack_dist//:spack` and the
Bazel-owned estate cache under `/vaso/cache/spack`. Do not use an ambient host
Spack checkout for this package.

Hermetic Spack version observed in the focused run:

```text
1.2.2
```

The focused graph contains 80 nodes and ends with:

```text
79  libraqm  0.10.5  meson
```

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/libraqm-0.10.5-nwcn75wovuoreqage36w3h4jhpplf7t6
```

Source provenance:

- upstream source URL:
  `https://github.com/HOST-Oman/libraqm/releases/download/v0.10.5/raqm-0.10.5.tar.xz`
- version `0.10.5` SHA256:
  `563053e724892a7b037913110ea2daef50ad575d4fa9f7c368ae1e4515f5e856`
- selected build system: Meson
- concrete variant: `bidi_algo=fribidi`

The concrete focused node has:

```text
link: freetype, fribidi, harfbuzz
build: compiler-wrapper, gcc, meson, ninja, pkgconf
link runtime: gcc-runtime, glibc
```

The ABI/prefix-relevant installed surface is:

```text
include/raqm.h
include/raqm-version.h
lib/libraqm.so -> libraqm.so.0 -> libraqm.so.0.10.5
lib/pkgconfig/raqm.pc
```

`libraqm.so.0.10.5` has SONAME `libraqm.so.0` and NEEDED entries for
`libfreetype.so.6`, `libharfbuzz.so.0`, `libfribidi.so.0`, and `libc.so.6`.

## Native Build Recipe

`native/libraqm/libraqm.bzl` mirrors Spack's Meson flow inside the CUDA insula:

```text
download raqm-0.10.5.tar.xz
validate bzip2, freetype, fribidi, glib, harfbuzz, libpng, meson, ninja,
pcre2, pkgconf, and zlib-ng prefixes
export PATH=<meson>:<ninja>:<pkgconf>:$PATH
derive PYTHON_ABI from <meson>/lib/pythonX.Y/site-packages
export PYTHONHOME=
export PYTHONPATH=<meson>/lib/python${PYTHON_ABI}/site-packages
export PKG_CONFIG=<pkgconf-prefix>/bin/pkgconf
export PKG_CONFIG_PATH=<native dependency pkgconfig dirs, including
FreeType's bzip2/libpng/zlib-ng closure and HarfBuzz's glib/pcre2 closure>
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
  -Dwrap_mode=nodownload
ninja -C <build> -v
ninja -C <build> install
delete .la files if any appear
```

The repository rule refuses to run unless the hermetic insula has set
`VASO_IN_INSULA=1`. Dependency discovery is constrained through mandatory Bazel
prefix files and pinned pkg-config:

```text
BZIP2_PREFIX
FREETYPE_PREFIX
FRIBIDI_PREFIX
GLIB_PREFIX
HARFBUZZ_PREFIX
LIBPNG_PREFIX
MESON_PREFIX
NINJA_PREFIX
PCRE2_PREFIX
PKGCONF_PREFIX
ZLIB_PREFIX
```

The mechanism verifier must report:

```text
native/libraqm/libraqm.bzl: meson: BZIP2_PREFIX, FREETYPE_PREFIX, FRIBIDI_PREFIX, GLIB_PREFIX, HARFBUZZ_PREFIX, LIBPNG_PREFIX, MESON_PREFIX, NINJA_PREFIX, PCRE2_PREFIX, PKGCONF_PREFIX, ZLIB_PREFIX
```

## ABI and behavior gate

The package gate is:

```text
//synthetic:use_libraqm_native
//synthetic:libraqm_abi_parity
```

`use_libraqm_native` compiles against `@spack_libraqm//:lib`, so the consumer is
unchanged by the provider flip. `libraqm_abi_parity` compares layout, SONAME,
exported dynamic symbols, prefix-normalized `raqm.pc`, and a downstream C
link-and-run probe against the hermetic Spack reference prefix while injecting
the matching FreeType, FriBidi, HarfBuzz, and pkg-config closure prefixes for
both sides.
