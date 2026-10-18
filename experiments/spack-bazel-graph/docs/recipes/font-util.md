# font-util@1.4.1

## Position in the hillclimb

`font-util@1.4.1` is the py-torch frontier X.Org Autotools package immediately
after native `mkfontdir@1.0.7`.

The focused reference graph used for this migration is deliberately lean:

```bash
SPACK_ROOT_PKG='font-util@1.4.1 fonts:=encodings'
```

The `:=` replacement is important. `fonts=encodings` adds `encodings` to
Spack's default font-resource set, while `fonts:=encodings` replaces that set
and keeps only `encodings`. The PyTorch frontier probe uses the same topology
choice:

```bash
SPACK_ROOT_PKG='py-torch cuda_arch=80,90,100 ^openblas~fortran ^font-util fonts:=encodings'
```

That probe produced `py_torch_lean_fonts_build_graph.json` with 171 nodes, and
the concrete `font-util` node has:

```text
parameters.fonts = ["encodings"]
```

`run.sh` enforces this lean path for direct `font-util` roots and for
`py-torch` roots. Set `VASO_ALLOW_BROAD_FONT_RESOURCES=1` only when
deliberately reproducing a broad-font probe; `VASO_LEAN_FONT_RESOURCES=0`
alone does not widen the PyTorch graph.

The lean frontier still contains X.Org font build tools and libraries needed by
the selected recipe path (`fontsproto`, `libfontenc`, `libXfont`,
`mkfontscale`, `mkfontdir`). It does not include broad bitmap/type1 font payload
packages such as `font-adobe-100dpi`, `font-misc-misc`, or
`font-bh-type1`; `tools/build_graph.py --require-lean-font-resources` rejects
those package nodes in addition to checking `font-util`'s `fonts` parameter.

The focused all-Spack run wrote `font_util_lean_spack_graph.lock.json` and
`font_util_lean_build_graph.json`. The native run wrote
`font_util_lean_native_spack_graph.lock.json` and
`font_util_lean_native_build_graph.json`, then flips `spack_font_util` to
`@font_util_native//:lib` without changing the Spack DAG edges.

## Hermetic Spack Evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

The focused lean reference prefix installed by hermetic Spack is:

```text
/vaso/cache/spack/opt/spack/linux-icelake/font-util-1.4.1-chruc5s77yhx7e55zyxllkmxa75qj5h4
```

The lean prefix contains only these top-level font data directories:

```text
share/fonts/X11/encodings
share/fonts/X11/util
```

`share/fonts/X11/util` is installed by `font-util` itself. The only external
font-resource payload is `encodings-1.0.4`.

Source provenance from the Spack recipe:

- package class: `FontUtil(AutotoolsPackage, XorgPackage)`
- concrete Linux build mechanism: Autotools
- upstream source URL:
  `https://www.x.org/archive/individual/font/font-util-1.4.1.tar.gz`
- SHA256: `f029ae80cdd75d89bee7f7af61c21e07982adfb9f72344a158b99f91f77ef5ed`
- selected resource: `encodings-1.0.4`
- selected resource SHA256:
  `55861d9cf456bd717a3d30a3193402c02174ed3c0dcee828798165fe307ee324`

The concrete focused node has:

```text
build: autoconf, automake, bdftopcf, compiler-wrapper, gcc, gmake,
       mkfontdir, mkfontscale, pkgconf, util-macros
link: gcc-runtime, glibc
```

The prefix-relevant installed surface is executable and data-only:

```text
bin/bdftruncate
bin/ucs2any
lib/pkgconfig/fontutil.pc
share/aclocal/fontutil.m4
share/fonts/X11/encodings/**
share/fonts/X11/util/**
share/man/man1/bdftruncate.1
share/man/man1/ucs2any.1
```

## Native Build Recipe

`native/font_util/font_util.bzl` mirrors Spack's two-phase Autotools flow:

```text
download font-util-1.4.1.tar.gz
download encodings-1.0.4.tar.gz
validate AUTOCONF_PREFIX, AUTOMAKE_PREFIX, BDFTOPCF_PREFIX,
         MKFONTDIR_PREFIX, MKFONTSCALE_PREFIX, PKGCONF_PREFIX,
         UTIL_MACROS_PREFIX
export PATH=<autoconf>:<automake>:<bdftopcf>:<mkfontdir>:<mkfontscale>:<pkgconf>:$PATH
export PKG_CONFIG=<pkgconf-prefix>/bin/pkgconf
export PKG_CONFIG_PATH=<prefix>/lib/pkgconfig:<pkgconf>/lib/pkgconfig:<util-macros>/share/pkgconfig
export ACLOCAL_PATH=<util-macros>/share/aclocal:<prefix>/share/aclocal:<pkgconf>/share/aclocal
./configure --prefix=<prefix>
make V=1
make install
for encodings:
  autoreconf -ifv --include=<util-macros>/share/aclocal --include=<prefix>/share/aclocal
  ./configure --prefix=<prefix>
  make V=1
  make install
remove libtool archives
emit prefix_path.txt
```

The native provider exposes:

- `@font_util_native//:prefix` for the installed prefix filegroup;
- `@font_util_native//:prefix_path.txt` for downstream native repository
  rules;
- `@font_util_native//:lib` as an empty `cc_library`, because `font-util` is a
  tool/data prefix with no public C ABI.

The corresponding mechanism verifier is the Autotools dependency-prefix case:

```text
native/font_util/font_util.bzl: autotools: AUTOCONF_PREFIX, AUTOMAKE_PREFIX, BDFTOPCF_PREFIX, MKFONTDIR_PREFIX, MKFONTSCALE_PREFIX, PKGCONF_PREFIX, UTIL_MACROS_PREFIX
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, consumes each
dependency through a mandatory Bazel `*_prefix_file`, validates all seven
prefixes before configure, pins `PKG_CONFIG`, and threads dependency lookup
through Autotools-specific `PATH`, `PKG_CONFIG_PATH`, and `ACLOCAL_PATH`
channels.

## Gates

The smoke target is:

```text
//synthetic:use_font_util_native
```

It runs the installed native tools from `@font_util_native//:prefix`, checks
`fontutil.pc`, `fontutil.m4`, manpages, the selected `encodings` data, and
asserts the top-level `share/fonts/X11` directory set is exactly:

```text
encodings util
```

The smoke target prints:

```text
font-util:1.4.1:fonts=encodings:ok
```

`//synthetic:font_util_prefix_parity` compares the native prefix against the
hermetic Spack lean reference. It covers:

- selected executable/data layout;
- prefix-normalized `fontutil.pc`;
- exact selected encoding-map hashes;
- prefix-normalized generated `ucs2any.1` manpage;
- executable NEEDED parity for `bdftruncate` and `ucs2any`;
- matching `bdftruncate 0x3200` behavior;
- matching `ucs2any` usage output and return code `0`.

Current status: native `font-util` is gated inside the hermetic CUDA insula. The
focused native verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='font-util@1.4.1 fonts:=encodings' \
VASO_LOCK_OUT=/workspace/experiment/font_util_lean_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/font_util_lean_native_build_graph.json \
VASO_NATIVE=1 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_font_util_native //synthetic:font_util_prefix_parity //tools:hermetic_native_deps_guard_test //tools:native_build_mechanism_guard_unit_test //tools:abi_parity_unit_test' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside the isolated CUDA 12.9.1
insula, reported hermetic Spack version `1.2.2`, passed
`//tools:hermetic_spack_guard_test`, passed
`//tools:hermetic_native_deps_guard_test`, passed
`//tools:native_build_mechanism_guard_unit_test`, passed
`//tools:abi_parity_unit_test`, passed `//synthetic:use_font_util_native`, and
passed `//synthetic:font_util_prefix_parity` with `SPACK_FONT_UTIL_PREFIX`
injected by `run.sh`.
