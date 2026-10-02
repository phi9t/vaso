# fontconfig frontier recipe

## Position in the hillclimb

`fontconfig@2.15.0` is the lean `py-torch` frontier font discovery/configuration
library after native `python@3.13.13` and before `icu4c@76.1`:

```text
84  python      3.13.13  generic    native; ABI parity green
85  fontconfig  2.15.0   autotools
86  icu4c       76.1     autotools  next unresolved frontier
```

This is not a broad font-payload capture. The PyTorch frontier remains
constrained by:

```bash
SPACK_ROOT_PKG='py-torch cuda_arch=80,90,100 ^openblas~fortran ^font-util fonts:=encodings'
```

`fontconfig` consumes the already-native lean `font-util` prefix and points its
default font directory at `font-util/share/fonts`; it does not add X.Org bitmap
or Type1 font resource packages.

The focused reference graph for `SPACK_ROOT_PKG='fontconfig@2.15.0'` ends with:

```text
51  fontconfig  2.15.0  autotools
```

Spack still owns the DAG shape. The native flip changes only
`spack_fontconfig.build` to `native` and re-exports
`@fontconfig_native//:lib`; link edges to `font-util`, `freetype`, `libxml2`,
and `util-linux-uuid` remain Spack-derived.

## Spack evidence

All recipe evidence comes from Bazel's vendored `@spack_dist//:spack` running
inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/fontconfig-2.15.0-4xdraptsmlonhqm7mntwmlvgtzjsbnq3
```

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/fontconfig/package.py
```

Source provenance from that recipe:

- package class: `Fontconfig(AutotoolsPackage)`
- upstream source URL used by the native rule:
  `https://www.freedesktop.org/software/fontconfig/release/fontconfig-2.15.0.tar.gz`
- version `2.15.0` SHA256:
  `f5f359d6332861bd497570848fcb42520964a9e83d5e3abe397b6b6db9bcaaf4`

The concrete focused node has:

```text
build: compiler-wrapper, font-util, freetype, gcc, gmake, gperf, libxml2,
       pkgconf, python, util-linux-uuid
link: font-util, freetype, gcc-runtime, glibc, libxml2, util-linux-uuid
```

The hermetic Spack configure contract is:

```text
rm -f src/fcobjshash.h
./configure --prefix=<prefix> --enable-libxml2 --disable-docs \
  --with-default-fonts=<font-util-prefix>/share/fonts
make
make install
edit etc/fonts/fonts.conf to add <dir>/usr/share/fonts</dir>
```

## Native build recipe

`native/fontconfig/fontconfig.bzl` mirrors that Autotools flow inside the CUDA
insula:

```text
download fontconfig-2.15.0.tar.gz
validate BZIP2_PREFIX, FONT_UTIL_PREFIX, FREETYPE_PREFIX, GPERF_PREFIX,
         LIBICONV_PREFIX, LIBPNG_PREFIX, LIBXML2_PREFIX, PKGCONF_PREFIX,
         PYTHON_PREFIX, UTIL_LINUX_UUID_PREFIX, XZ_PREFIX, ZLIB_PREFIX
export PATH=<gperf>:<pkgconf>:<python>:$PATH
export PYTHON=<python-prefix>/bin/python3
export PKG_CONFIG=<pkgconf-prefix>/bin/pkgconf
export PKG_CONFIG_PATH=<native dependency pkgconfig dirs>
export CPPFLAGS=<native dependency include dirs>
export LDFLAGS=<native dependency lib/rpath dirs>
rm -f src/fcobjshash.h
./configure --prefix=<prefix> --enable-libxml2 --disable-docs \
  --with-default-fonts=<font-util-prefix>/share/fonts
make V=1
make install
patch fonts.conf with Spack's /usr/share/fonts entry
remove libtool archives
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, consumes every
dependency through a mandatory Bazel `*_prefix_file`, validates each prefix
before configure, and routes dependency discovery through Autotools-specific
`PATH`, `PKG_CONFIG_PATH`, `CPPFLAGS`, and `LDFLAGS` channels.

For the Python 3.13 re-seat, `fontconfig_native.python_prefix_file` is wired to
`@python_313_native//:prefix_path.txt`. This is a build-only Python edge in the
Spack graph; no `fontconfig` output path embeds Python ABI state. The exported
C link interface uses exact SONAME link options (`-l:libfontconfig.so.1`,
`-l:libfreetype.so.6`, `-l:libxml2.so.2`, `-l:libuuid.so.1`) so downstream
consumers bind the native Spack-compatible libraries instead of any rootfs
library with the same generic `-l` name.

The corresponding mechanism verifier reports:

```text
native/fontconfig/fontconfig.bzl: autotools: BZIP2_PREFIX, FONT_UTIL_PREFIX, FREETYPE_PREFIX, GPERF_PREFIX, LIBICONV_PREFIX, LIBPNG_PREFIX, LIBXML2_PREFIX, PKGCONF_PREFIX, PYTHON_PREFIX, UTIL_LINUX_UUID_PREFIX, XZ_PREFIX, ZLIB_PREFIX
```

## Gates

The smoke target is:

```text
//synthetic:use_fontconfig_native
```

It links a C consumer against `@fontconfig_native//:lib`, calls `FcInit` and
`FcGetVersion`, and prints:

```text
fontconfig:2.15.0
```

`//synthetic:fontconfig_abi_parity` compares the native prefix against the
hermetic Spack reference. It covers:

- shared-library layout and SONAME/exported-symbol parity;
- prefix-normalized `lib/pkgconfig/fontconfig.pc`;
- prefix-normalized `etc/fonts/fonts.conf`, including the package cache path
  and the lean `font-util/share/fonts` dependency path;
- exact `etc/fonts/conf.d/README` and `share/xml/fontconfig/fonts.dtd` hashes;
- executable NEEDED parity for `fc-cache`, `fc-match`, `fc-list`, and
  `fc-query`;
- matching `fc-cache -V` and `fc-match --version` behavior;
- downstream `FcInit`/`FcGetVersion` link-and-run behavior against both the
  Spack reference and native candidate prefixes.

Current status: native `fontconfig` is gated inside the hermetic CUDA insula.
The focused verification command was:

```bash
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='fontconfig@2.15.0 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib' \
VASO_NATIVE=1 \
VASO_SPACK_TIMEOUT=600 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_fontconfig_native,//synthetic:fontconfig_abi_parity,//tools:hermetic_native_deps_guard_test,//tools:native_dep_wiring_live_test,//tools:migration_ledger_check_live_test,//tools:migration_ledger_check_unit_test' \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside rootfs mode `cuda-bundle`,
reported hermetic Spack version `1.2.2`, passed
`//tools:hermetic_spack_guard_test`, passed
`//tools:hermetic_native_deps_guard_test`, passed
`//tools:native_dep_wiring_live_test` with 13 remaining allowlisted ticket-08
mismatches, passed `//tools:migration_ledger_check_live_test`, passed
`//tools:migration_ledger_check_unit_test`, passed
`//synthetic:use_fontconfig_native` with output `fontconfig:2.15.0`, and passed
`//synthetic:fontconfig_abi_parity`.

The regenerated focused graph has 52 nodes and ends with:

```text
50  python      3.13.13  generic    /vaso/cache/spack/opt/spack/linux-icelake/python-3.13.13-6u37x4adbcqbp247uvyjtokjsun4oewt
51  fontconfig  2.15.0   autotools  /vaso/cache/spack/opt/spack/linux-icelake/fontconfig-2.15.0-4xdraptsmlonhqm7mntwmlvgtzjsbnq3
```
