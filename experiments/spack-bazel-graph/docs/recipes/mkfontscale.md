# mkfontscale@1.2.3

## Position in the hillclimb

`mkfontscale@1.2.3` is the py-torch frontier node immediately after native
`lua@5.3.6`. In the captured `SPACK_ROOT_PKG=py-torch` graph it appears as:

```text
71  mkfontscale  1.2.3  autotools
```

The focused reference graph used for this migration is:

```bash
SPACK_ROOT_PKG='mkfontscale@1.2.3'
```

The focused all-Spack run wrote `mkfontscale_spack_graph.lock.json` and
`mkfontscale_build_graph.json`. The native run wrote
`mkfontscale_native_spack_graph.lock.json` and
`mkfontscale_native_build_graph.json`, then flips `spack_mkfontscale` to
`@mkfontscale_native//:lib` without changing the Spack DAG edges.

## Hermetic Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path in the isolated estate:

```text
/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/mkfontscale/package.py
```

Source provenance from the Spack recipe:

- package class: `Mkfontscale(AutotoolsPackage, XorgPackage)`
- homepage: `https://gitlab.freedesktop.org/xorg/app/mkfontscale`
- concrete Linux build mechanism: Autotools
- upstream source URL:
  `https://xorg.freedesktop.org/archive/individual/app/mkfontscale-1.2.3.tar.gz`
- SHA256: `3a026b468874eb672a1d0a57dbd3ddeda4f0df09886caf97d30097b70c2df3f8`

The concrete focused node has:

```text
build: compiler-wrapper, freetype, gcc, gmake, libfontenc, pkgconf,
       util-macros, xproto
link: freetype, gcc-runtime, glibc, libfontenc
```

The native rule also consumes `bzip2`, `libpng`, and `zlib-ng` explicitly.
Those are private pkg-config or static-link requirements surfaced by the
native `freetype` and `libpng` closure. They must be threaded through Bazel
prefix files so Autotools, pkg-config, and the linker never discover rootfs
libraries.

The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/mkfontscale-1.2.3-vdkisvpyv7x5ttn7ibsv75c2ejhmouil
```

The prefix-relevant installed surface is:

```text
bin/mkfontscale
bin/mkfontdir
share/man/man1/mkfontscale.1
share/man/man1/mkfontdir.1
```

`bin/mkfontscale -v` prints `mkfontscale 1.2.3`. The reference
`bin/mkfontdir` is a shell wrapper that re-enters the same prefix's
`bin/mkfontscale`:

```bash
PATH="/vaso/cache/spack/opt/spack/linux-icelake/mkfontscale-1.2.3-vdkisvpyv7x5ttn7ibsv75c2ejhmouil/bin:$PATH"; exec mkfontscale -b -s -l "$@"
```

The reference `bin/mkfontscale` executable has this ELF NEEDED set:

```text
libc.so.6
libfontenc.so.1
libfreetype.so.6
libz.so.1
```

## Native build recipe

`native/mkfontscale/mkfontscale.bzl` mirrors the concrete Spack Autotools flow:

```text
download mkfontscale-1.2.3.tar.gz
validate BZIP2_PREFIX, FREETYPE_PREFIX, LIBFONTENC_PREFIX, LIBPNG_PREFIX,
         PKGCONF_PREFIX, UTIL_MACROS_PREFIX, XPROTO_PREFIX, and ZLIB_PREFIX
export PATH=<pkgconf-prefix>/bin:$PATH
export PKG_CONFIG=<pkgconf-prefix>/bin/pkgconf
export PKG_CONFIG_PATH=<libfontenc>/lib/pkgconfig:<freetype>/lib/pkgconfig:
       <xproto>/lib/pkgconfig:<bzip2>/lib/pkgconfig:
       <libpng>/lib/pkgconfig:<zlib-ng>/lib/pkgconfig:
       <pkgconf>/lib/pkgconfig:<util-macros>/share/pkgconfig
export ACLOCAL_PATH=<util-macros>/share/aclocal:<pkgconf>/share/aclocal
export CPPFLAGS=-I<libfontenc>/include -I<freetype>/include
                -I<freetype>/include/freetype2 -I<xproto>/include
                -I<bzip2>/include -I<libpng>/include/libpng16
                -I<zlib-ng>/include
export LDFLAGS=-L<libfontenc>/lib -Wl,-rpath,<libfontenc>/lib
               -L<freetype>/lib -Wl,-rpath,<freetype>/lib
               -L<bzip2>/lib -Wl,-rpath,<bzip2>/lib
               -L<libpng>/lib -Wl,-rpath,<libpng>/lib
               -L<zlib-ng>/lib -Wl,-rpath,<zlib-ng>/lib
               -Wl,--disable-new-dtags
export LIBS=-lz
./configure --prefix=<prefix>
make V=1
make install
remove libtool archives
emit prefix_path.txt
```

The native provider exposes:

- `@mkfontscale_native//:prefix` for the installed prefix filegroup;
- `@mkfontscale_native//:prefix_path.txt` for downstream native repository
  rules;
- `@mkfontscale_native//:lib` as an empty `cc_library`, because mkfontscale is
  an executable/tool-prefix node with no public C ABI.

The corresponding mechanism verifier is the Autotools dependency-prefix case:

```text
native/mkfontscale/mkfontscale.bzl: autotools: BZIP2_PREFIX, FREETYPE_PREFIX, LIBFONTENC_PREFIX, LIBPNG_PREFIX, PKGCONF_PREFIX, UTIL_MACROS_PREFIX, XPROTO_PREFIX, ZLIB_PREFIX
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, consumes each
dependency through a mandatory Bazel `*_prefix_file`, validates all eight
prefixes before configure, pins `PKG_CONFIG`, and threads dependency lookup
through Autotools-specific `PKG_CONFIG_PATH`, `ACLOCAL_PATH`, `CPPFLAGS`,
`LDFLAGS`, and `LIBS` channels.

## Gates

The smoke target is:

```text
//synthetic:use_mkfontscale_native
```

It runs the installed native executables from `@mkfontscale_native//:prefix`,
checks both manpages, verifies `mkfontscale -v`, creates an empty
`fonts.scale`, creates an empty `fonts.dir` through the wrapper, and prints:

```text
mkfontscale:1.2.3:ok
```

`//synthetic:mkfontscale_prefix_parity` compares the native prefix against the
hermetic Spack reference. It covers:

- the four-path installed layout for `bin/mkfontscale`, `bin/mkfontdir`,
  `share/man/man1/mkfontscale.1`, and `share/man/man1/mkfontdir.1`;
- prefix-normalized `bin/mkfontdir` wrapper content;
- exact manpage hashes;
- empty shared-library ABI axis, matching the executable-only prefix;
- executable NEEDED parity for `bin/mkfontscale`;
- matching `mkfontscale -v` output;
- matching empty-directory `fonts.scale` and wrapper-produced `fonts.dir`
  output, with SHA256
  `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa`.

Current status: native mkfontscale is gated inside the hermetic CUDA insula.
The focused native verification command used a temporary one-package override
file containing `{"native":{"mkfontscale":"@mkfontscale_native//:lib"}}` so the
run exercised this new provider without sweeping unrelated native parity gates:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='mkfontscale@1.2.3' \
VASO_LOCK_OUT=/workspace/experiment/mkfontscale_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/mkfontscale_native_build_graph.json \
VASO_NATIVE=1 \
VASO_NATIVE_OVERRIDES=.tmp_mkfontscale_native_overrides.json \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_mkfontscale_native //tools:hermetic_native_deps_guard_test //tools:abi_parity_unit_test' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside the isolated CUDA 12.9.1
insula, reported hermetic Spack version `1.2.2`, passed
`//tools:hermetic_spack_guard_test`, passed
`//tools:hermetic_native_deps_guard_test`, passed
`//tools:abi_parity_unit_test`, passed `//synthetic:use_mkfontscale_native`,
and passed `//synthetic:mkfontscale_prefix_parity`.
