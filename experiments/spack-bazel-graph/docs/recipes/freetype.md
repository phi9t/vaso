# freetype@2.14.2

## Position in the hillclimb

`freetype@2.14.2` is the py-torch frontier node immediately after native
`libpng@1.6.58`. In the captured `SPACK_ROOT_PKG=py-torch` graph it appears as:

```text
67  freetype  2.14.2  autotools
```

The focused reference graph used for this migration is:

```bash
SPACK_ROOT_PKG='freetype@2.14.2'
```

The reference run used Bazel's vendored `@spack_dist//:spack` inside the
isolated CUDA 12.9.1 insula and wrote `freetype_spack_graph.lock.json` plus
`freetype_build_graph.json`. The native run flips `spack_freetype` to
`@freetype_native//:lib` without changing the Spack DAG edges.

## Hermetic Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path in the isolated estate:

```text
/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/freetype/package.py
```

Source provenance from the Spack recipe:

- package class: `Freetype(AutotoolsPackage, CMakePackage)`
- concrete Linux build mechanism used here: Autotools
- upstream source URL:
  `https://download.savannah.gnu.org/releases/freetype/freetype-2.14.2.tar.gz`
- SHA256: `752c2671f85c54a84b7f0dd2b5cd26b6b741117033886ffbc5ac89a68464b848`
- Autotools build directory: `builds/unix`

Although the recipe also has a CMake builder, the hermetic Spack recipe defaults
to Autotools for this concrete node. The recipe notes that CMake does not
install `freetype-config`, and `freetype-config` is part of the observed Spack
prefix surface, so the native provider uses the Autotools path.

The concrete focused node has:

```text
build: bzip2, compiler-wrapper, gcc, gmake, libpng, pkgconf
link: bzip2, gcc-runtime, glibc, libpng
```

The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/freetype-2.14.2-qptrkml4byigw5zwuwvbk2e3hl446urh
```

The ABI/prefix-relevant installed surface is:

```text
bin/freetype-config
include/freetype2/ft2build.h
include/freetype2/freetype/
lib/libfreetype.so -> libfreetype.so.6.20.5
lib/libfreetype.so.6 -> libfreetype.so.6.20.5
lib/libfreetype.so.6.20.5
lib/libfreetype.a
lib/pkgconfig/freetype2.pc
share/aclocal/freetype2.m4
```

The reference shared object has SONAME `libfreetype.so.6`, 220 exported dynamic
symbols, and NEEDED entries for `libbz2.so.1.0`, `libpng16.so.16`, and
`libc.so.6`.

## Native build recipe

`native/freetype/freetype.bzl` mirrors the concrete Spack Autotools flow:

```text
download freetype-2.14.2.tar.gz
validate bzip2, libpng, pkgconf, and zlib-ng prefixes from Bazel prefix files
export PATH=<pkgconf-prefix>/bin:$PATH
export PKG_CONFIG=<pkgconf-prefix>/bin/pkgconf
export PKG_CONFIG_PATH=<bzip2>/lib/pkgconfig:<libpng>/lib/pkgconfig:<zlib-ng>/lib/pkgconfig:<pkgconf>/lib/pkgconfig
export CPPFLAGS=-I<bzip2>/include -I<libpng>/include/libpng16 -I<zlib-ng>/include
export LDFLAGS=-L<bzip2>/lib -Wl,-rpath,<bzip2>/lib \
               -L<libpng>/lib -Wl,-rpath,<libpng>/lib \
               -L<zlib-ng>/lib -Wl,-rpath,<zlib-ng>/lib \
               -Wl,--disable-new-dtags
./configure --prefix=<prefix> \
  --with-brotli=no \
  --with-bzip2=yes \
  --with-harfbuzz=no \
  --with-png=yes \
  --with-zlib=no \
  --enable-freetype-config \
  --enable-shared \
  --with-pic
make V=1
make install
remove libtool archives
emit prefix_path.txt
```

`zlib-ng` is an explicit native dependency even though the focused Spack graph
shows `libpng` as the direct freetype edge. The hermetic libpng pkg-config file
carries `Requires.private: zlib`; the native freetype build therefore routes
that static metadata through `@zlib_ng_native//:prefix_path.txt` instead of
allowing `pkgconf` or Autotools to discover a rootfs zlib.

The native provider exposes:

- `@freetype_native//:prefix` for the installed prefix filegroup;
- `@freetype_native//:prefix_path.txt` for downstream native repository rules;
- `@freetype_native//:lib` with the same public link surface as Spack:
  `freetype` plus the native bzip2, libpng, zlib-ng, and math libraries.

The corresponding mechanism verifier is the Autotools dependency-prefix case:

```text
native/freetype/freetype.bzl: autotools: BZIP2_PREFIX, LIBPNG_PREFIX, PKGCONF_PREFIX, ZLIB_PREFIX
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, consumes
`@bzip2_native//:prefix_path.txt`, `@libpng_native//:prefix_path.txt`,
`@pkgconf_native//:prefix_path.txt`, and `@zlib_ng_native//:prefix_path.txt`,
validates all four prefixes before configure, pins `PKG_CONFIG`, and threads
dependency lookup through Autotools-specific `PKG_CONFIG_PATH`, `CPPFLAGS`, and
`LDFLAGS` channels.

## Gates

The smoke target is:

```text
//synthetic:use_freetype_native
```

It compiles a C consumer against `@freetype_native//:lib`, initializes a
FreeType library with `FT_Init_FreeType`, queries `FT_Library_Version`, and
prints:

```text
freetype:2.14.2
```

`//synthetic:freetype_abi_parity` compares the native prefix against the
hermetic Spack reference. It covers:

- installed layout under `include/freetype2`, `lib`, `lib/pkgconfig`, and
  `share/aclocal`;
- SONAME and exported-symbol parity for `libfreetype.so.6.20.5`;
- static archive member and symbol parity for `lib/libfreetype.a`;
- prefix-normalized `lib/pkgconfig/freetype2.pc`;
- prefix-normalized `share/aclocal/freetype2.m4`;
- executable presence and behavior for `bin/freetype-config --version`,
  `--libs`, and `--static --libs`;
- downstream C link-and-run output for both native and reference prefixes,
  with bzip2, libpng, and zlib-ng link prefixes supplied explicitly.

Current status: native freetype is gated inside the hermetic CUDA insula. The
focused native verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='freetype@2.14.2' \
VASO_LOCK_OUT=/workspace/experiment/freetype_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/freetype_native_build_graph.json \
VASO_NATIVE=1 \
VASO_FORCE_FETCH_REPOS='@freetype_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_freetype_native' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

The run used Bazel's `@spack_dist//:spack` inside the isolated CUDA 12.9.1
insula, reported hermetic Spack version `1.2.2`, passed
`//tools:hermetic_spack_guard_test`, passed
`//tools:hermetic_native_deps_guard_test`, passed
`//tools:native_build_mechanism_guard_unit_test`, passed
`//synthetic:use_freetype_native`, and passed
`//synthetic:freetype_abi_parity`.
