# bdftopcf@1.1.2

## Position in the hillclimb

`bdftopcf@1.1.2` is the py-torch frontier node immediately after native
`libxfont@1.5.4`. In the captured `SPACK_ROOT_PKG=py-torch` graph it appears
as:

```text
69  bdftopcf  1.1.2  autotools
```

The focused reference graph used for this migration is:

```bash
SPACK_ROOT_PKG='bdftopcf@1.1.2'
```

The focused all-Spack run wrote `bdftopcf_spack_graph.lock.json` and
`bdftopcf_build_graph.json`. The native run wrote
`bdftopcf_native_spack_graph.lock.json` and
`bdftopcf_native_build_graph.json`, then flips `spack_bdftopcf` to
`@bdftopcf_native//:lib` without changing the Spack DAG edges.

## Hermetic Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path in the isolated estate:

```text
/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/bdftopcf/package.py
```

Source provenance from the Spack recipe:

- package class: `Bdftopcf(AutotoolsPackage, XorgPackage)`
- homepage: `https://gitlab.freedesktop.org/xorg/util/bdftopcf`
- concrete Linux build mechanism: Autotools
- upstream source URL:
  `https://xorg.freedesktop.org/archive/individual/util/bdftopcf-1.1.2.tar.gz`
- SHA256: `31c88b9194c34ee35c433759d141eaca177dcdead835c8832021cc013324b924`

The concrete focused node has:

```text
build: compiler-wrapper, fontsproto, gcc, gmake, libxfont, pkgconf,
       util-macros, xproto
link: gcc-runtime, glibc, libxfont
```

The native rule also consumes `bzip2`, `freetype`, `libfontenc`, `libpng`,
`xtrans`, and `zlib-ng` explicitly. Those are private pkg-config or static-link
requirements surfaced by the native `libxfont`, `freetype`, and `libpng`
closure. They must be threaded through Bazel prefix files so Autotools,
pkg-config, and the linker never discover rootfs libraries.

The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/bdftopcf-1.1.2-m5lj7eiqnwtkyyateprhsle3dc5m7knn
```

The prefix-relevant installed surface is:

```text
bin/bdftopcf
share/man/man1/bdftopcf.1
```

`bin/bdftopcf -v` prints `bdftopcf 1.1.2`. The reference executable has only
`libc.so.6` in its ELF NEEDED set; the Spack link line does not retain a
dynamic `libXfont` dependency because bdftopcf compiles the needed font code
into the executable.

## Native build recipe

`native/bdftopcf/bdftopcf.bzl` mirrors the concrete Spack Autotools flow:

```text
download bdftopcf-1.1.2.tar.gz
validate bzip2, fontsproto, freetype, libfontenc, libpng, libxfont, pkgconf,
         util-macros, xproto, xtrans, and zlib-ng prefixes from Bazel
         prefix files
export PATH=<pkgconf-prefix>/bin:$PATH
export PKG_CONFIG=<pkgconf-prefix>/bin/pkgconf
export PKG_CONFIG_PATH=<libxfont>/lib/pkgconfig:<fontsproto>/lib/pkgconfig:
       <freetype>/lib/pkgconfig:<libfontenc>/lib/pkgconfig:
       <xproto>/lib/pkgconfig:<xtrans>/share/pkgconfig:
       <bzip2>/lib/pkgconfig:<libpng>/lib/pkgconfig:
       <zlib-ng>/lib/pkgconfig:<pkgconf>/lib/pkgconfig:
       <util-macros>/share/pkgconfig
export ACLOCAL_PATH=<util-macros>/share/aclocal:<pkgconf>/share/aclocal
export CPPFLAGS=-I<libxfont>/include -I<fontsproto>/include
                -I<freetype>/include -I<freetype>/include/freetype2
                -I<libfontenc>/include -I<xproto>/include
                -I<xtrans>/include -I<bzip2>/include
                -I<libpng>/include/libpng16 -I<zlib-ng>/include
export LDFLAGS=-L<libxfont>/lib -Wl,-rpath,<libxfont>/lib
               -L<freetype>/lib -Wl,-rpath,<freetype>/lib
               -L<libfontenc>/lib -Wl,-rpath,<libfontenc>/lib
               -L<bzip2>/lib -Wl,-rpath,<bzip2>/lib
               -L<libpng>/lib -Wl,-rpath,<libpng>/lib
               -L<zlib-ng>/lib -Wl,-rpath,<zlib-ng>/lib
               -Wl,--disable-new-dtags
./configure --prefix=<prefix>
make V=1
make install
remove libtool archives
emit prefix_path.txt
```

The native provider exposes:

- `@bdftopcf_native//:prefix` for the installed prefix filegroup;
- `@bdftopcf_native//:prefix_path.txt` for downstream native repository rules;
- `@bdftopcf_native//:lib` as an empty `cc_library`, because bdftopcf is an
  executable/tool-prefix node with no public C ABI.

The corresponding mechanism verifier is the Autotools dependency-prefix case:

```text
native/bdftopcf/bdftopcf.bzl: autotools: BZIP2_PREFIX, FONTSPROTO_PREFIX, FREETYPE_PREFIX, LIBFONTENC_PREFIX, LIBPNG_PREFIX, LIBXFONT_PREFIX, PKGCONF_PREFIX, UTIL_MACROS_PREFIX, XPROTO_PREFIX, XTRANS_PREFIX, ZLIB_PREFIX
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, consumes each
dependency through a mandatory Bazel `*_prefix_file`, validates all eleven
prefixes before configure, pins `PKG_CONFIG`, and threads dependency lookup
through Autotools-specific `PKG_CONFIG_PATH`, `ACLOCAL_PATH`, `CPPFLAGS`, and
`LDFLAGS` channels.

## Gates

The smoke target is:

```text
//synthetic:use_bdftopcf_native
```

It runs the installed native executable from `@bdftopcf_native//:prefix`,
checks the manpage, verifies `bdftopcf -v`, converts a small BDF font to PCF,
and prints:

```text
bdftopcf:1.1.2:ok
```

`//synthetic:bdftopcf_prefix_parity` compares the native prefix against the
hermetic Spack reference. It covers:

- the two-path installed layout for `bin/bdftopcf` and
  `share/man/man1/bdftopcf.1`;
- byte-identical manpage SHA256
  `1530b5a7859ef17d8464ba92ea3d67e431b44bf81266932195d464edb70f1771`;
- empty shared-library ABI axis, matching the executable-only prefix;
- executable NEEDED parity for `bin/bdftopcf` (`libc.so.6`);
- matching `bdftopcf -v` output;
- matching BDF-to-PCF conversion output metadata, including output size 700
  and SHA256
  `2fb3cf7cb2744c16feacfc30c0912b651dd330b72e9d79b1e040da718f6f9b6e`.

Current status: native bdftopcf is gated inside the hermetic CUDA insula. The
focused native verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='bdftopcf@1.1.2' \
VASO_LOCK_OUT=/workspace/experiment/bdftopcf_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/bdftopcf_native_build_graph.json \
VASO_NATIVE=1 \
VASO_FORCE_FETCH_REPOS='@bdftopcf_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_bdftopcf_native //tools:hermetic_native_deps_guard_test //tools:abi_parity_unit_test' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside the isolated CUDA 12.9.1
insula, reported hermetic Spack version `1.2.2`, passed
`//tools:hermetic_spack_guard_test`, passed
`//tools:hermetic_native_deps_guard_test`, passed
`//tools:abi_parity_unit_test`, passed `//synthetic:use_bdftopcf_native`, and
passed `//synthetic:bdftopcf_prefix_parity`.
