# pixman@0.46.4

## Position in the hillclimb

`pixman@0.46.4` is a Meson pixel-compositing library node in the lean
`py-torch` frontier. In the active lean PyTorch graph it appears as:

```text
140  pixman  0.46.4  meson
```

The focused reference graph used for this migration is:

```bash
SPACK_ROOT_PKG='pixman@0.46.4'
```

In that focused closure it appears as:

```text
48  pixman  0.46.4  meson
```

The reference and native runs used Bazel's vendored `@spack_dist//:spack`
inside the isolated CUDA 12.9.1 insula. The native run flips `spack_pixman` to
`@pixman_native//:lib` without changing the Spack DAG edges.

## Hermetic Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path observed in the isolated estate:

```text
/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/pixman/package.py
```

Source provenance from the Spack recipe:

- package class: `Pixman(AutotoolsPackage, MesonPackage)`
- selected build system: `MesonPackage` for `@0.38:`, default `meson`
- version: `0.46.4`
- upstream source URL:
  `https://cairographics.org/releases/pixman-0.46.4.tar.gz`
- SHA256: `d09c44ebc3bd5bee7021c79f922fe8fb2fb57f7320f55e97ff9914d2346a591c`
- concrete variants: `+shared`, `~pic`, `buildtype=release`,
  `default_library=shared`, `strip=false`
- build dependency channel: `bison@3:`, `flex`, `meson@1.3:`, `ninja`,
  `pkgconfig`
- build/link dependency channel: `libpng`
- Spack patch: `libpng.patch`, SHA256
  `8c7ececca0d15b6f4738c0961f024fcc333ac6413480087e45b2547685072924`

Concrete Meson args from the recipe and graph are:

```text
-Dlibpng=enabled
-Dgtk=disabled
-Db_staticpic=true
-Ddefault_library=shared
```

The focused Pixman node has these dependency edges:

```text
build: bison, compiler-wrapper, flex, gcc, meson, ninja, pkgconf
build+link: libpng
link: gcc-runtime, glibc
```

The hermetic Spack reference prefix used by the ABI gate was:

```text
/vaso/cache/spack/opt/spack/linux-icelake/pixman-0.46.4-nwoyme6xsd7i33ggrimcgnmoj3oyu4cf
```

The ABI/prefix-relevant installed surface is:

```text
include/pixman-1/pixman.h
include/pixman-1/pixman-version.h
lib/libpixman-1.so -> libpixman-1.so.0 -> libpixman-1.so.0.46.4
lib/pkgconfig/pixman-1.pc
```

## Native build recipe

`native/pixman/pixman.bzl` mirrors the concrete Spack Meson flow:

```text
download pixman-0.46.4.tar.gz
apply Spack libpng.patch
validate bison, flex, libpng, meson, ninja, pkgconf, and zlib-ng prefixes
export PATH=<bison>:<flex>:<meson>:<ninja>:<pkgconf>:$PATH
derive PYTHON_ABI from <meson>/lib/pythonX.Y/site-packages
export PYTHONHOME=
export PYTHONPATH=<meson>/lib/python${PYTHON_ABI}/site-packages
export PKG_CONFIG=<pkgconf>/bin/pkgconf
export PKG_CONFIG_PATH=<libpng>/lib/pkgconfig:<zlib-ng>/lib/pkgconfig
export CPPFLAGS=-I<libpng>/include -I<zlib-ng>/include
export CFLAGS="-O3 -g0 -march=icelake-client -mtune=icelake-client ..."
export LDFLAGS="-L<libpng>/lib -L<zlib-ng>/lib -Wl,-rpath,..."
meson setup <build> <src> \
  -Dprefix=<prefix> \
  -Dlibdir=<prefix>/lib \
  -Dbuildtype=release \
  -Dstrip=false \
  -Ddefault_library=shared \
  -Dwrap_mode=nodownload \
  -Dlibpng=enabled \
  -Dgtk=disabled \
  -Db_staticpic=true
ninja -C <build> -v
ninja -C <build> install
delete .la files
```

The native provider exposes:

- `@pixman_native//:prefix` for the installed prefix filegroup;
- `@pixman_native//:prefix_path.txt` for downstream native repository rules;
- `@pixman_native//:lib` with public headers under `include/pixman-1` and
  link options for `libpixman-1`, native libpng, and native zlib-ng.

The corresponding mechanism verifier is the Meson dependency-prefix case:

```text
native/pixman/pixman.bzl: meson: BISON_PREFIX, FLEX_PREFIX, LIBPNG_PREFIX, MESON_PREFIX, NINJA_PREFIX, PKGCONF_PREFIX, ZLIB_PREFIX
```

The repository rule refuses to run unless the hermetic insula has set
`VASO_IN_INSULA=1`. It reads every dependency through a Bazel prefix file,
validates each prefix before Meson configure, pins Meson/Ninja/pkgconf through
those prefixes, and includes zlib-ng explicitly because libpng's pkg-config
closure must not discover a rootfs zlib.

## Gates

The smoke target is:

```text
//synthetic:use_pixman_native
```

It compiles a C consumer against `@pixman_native//:lib`, includes
`<pixman.h>`, creates a 1x1 ARGB image, fills it through
`pixman_image_fill_rectangles`, and prints:

```text
pixman:0.46.4:pixel=ff112233
```

`//synthetic:pixman_abi_parity` compares the native prefix against the
hermetic Spack reference prefix supplied by `run.sh` through
`SPACK_PIXMAN_PREFIX`. The gate covers:

- installed layout parity;
- SONAME and exported-symbol parity for `libpixman-1.so.0`;
- prefix-normalized `lib/pkgconfig/pixman-1.pc`;
- downstream C link-and-run parity against both the reference and native
  prefixes, with libpng and zlib-ng supplied through explicit prefix channels.

Current status: native Pixman is gated inside the hermetic CUDA insula. The
focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG=pixman \
VASO_NATIVE=1 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_pixman_native //synthetic:pixman_abi_parity' \
VASO_SPACK_INSTALL=0 \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

The run used Bazel's `@spack_dist//:spack` inside the isolated CUDA 12.9.1
insula, reported hermetic Spack version `1.2.2`, passed
`//tools:hermetic_spack_guard_test`, passed
`//tools:hermetic_native_deps_guard_test`, passed
`//tools:native_build_mechanism_guard_unit_test`, passed
`//synthetic:use_pixman_native`, and passed `//synthetic:pixman_abi_parity`.

The ABI output reported layout parity for six paths, SONAME
`libpixman-1.so.0`, 193 exported dynamic symbols on both sides,
prefix-normalized `lib/pkgconfig/pixman-1.pc` parity, and matching downstream C
output:

```text
pixman:0.46.4:pixel=ff112233
```
