# elfutils@0.194

## Position in the hillclimb

`elfutils@0.194` is the next non-toolchain Autotools frontier after native
`file@5.46` in the current focused PyTorch migration path. The focused
reference graph is:

```bash
SPACK_ROOT_PKG='elfutils@0.194'
```

Spack still owns the DAG shape. The native flip changes only
`spack_elfutils.build` to `native` and re-exports `@elfutils_native//:lib`;
link edges to `bzip2`, `gettext`, `libiconv`, `pkgconf`, `xz`, `zlib-ng`, and
`zstd` stay Spack-derived.

## Hermetic Spack evidence

All recipe evidence comes from Bazel's vendored `@spack_dist//:spack` running
inside the CUDA insula. Do not use an ambient host Spack checkout.

The focused all-Spack reference run wrote:

```text
elfutils_spack_graph.lock.json
elfutils_build_graph.json
```

The reference prefix was:

```text
/vaso/cache/spack/opt/spack/linux-icelake/elfutils-0.194-4cu5shywdmdbqmz76oiwrym7f2paluc2
```

The hermetic Spack recipe is copied into that reference prefix at:

```text
.spack/repos/spack_repo/builtin/packages/elfutils/package.py
```

Concrete variants and parameters:

```text
build_system: autotools
debuginfod: false
exeprefix: true
nls: true
```

Hermetic Spack configure arguments:

```text
--with-bzlib=<bzip2-prefix>
--with-lzma=<xz-prefix>
--with-zlib=<zlib-ng-prefix>
--program-prefix='eu-'
--with-zstd=<zstd-prefix>
--with-libiconv-prefix=<libiconv-prefix>
--without-libintl-prefix
--disable-debuginfod
--disable-libdebuginfod
--disable-debuginfod-ima-verification
```

The recipe's `patch()` removes `-Werror` from generated `*/Makefile.in` files,
and its post-install hook copies `libelf/elf.h` into `prefix/include/elf.h`.

The ABI-relevant prefix surface is:

```text
bin/eu-addr2line
bin/eu-ar
bin/eu-elfclassify
bin/eu-elfcmp
bin/eu-elfcompress
bin/eu-elflint
bin/eu-findtextrel
bin/eu-make-debug-archive
bin/eu-nm
bin/eu-objdump
bin/eu-ranlib
bin/eu-readelf
bin/eu-size
bin/eu-srcfiles
bin/eu-stack
bin/eu-strings
bin/eu-strip
bin/eu-unstrip
include/dwarf.h
include/elf.h
include/gelf.h
include/libelf.h
include/nlist.h
include/elfutils/libasm.h
include/elfutils/libdw.h
include/elfutils/libdwelf.h
include/elfutils/libdwfl.h
include/elfutils/libdwfl_stacktrace.h
include/elfutils/version.h
lib/libasm-0.194.so
lib/libasm.so -> libasm.so.1
lib/libasm.so.1 -> libasm-0.194.so
lib/libasm.a
lib/libdw-0.194.so
lib/libdw.so -> libdw.so.1
lib/libdw.so.1 -> libdw-0.194.so
lib/libdw.a
lib/libelf-0.194.so
lib/libelf.so -> libelf.so.1
lib/libelf.so.1 -> libelf-0.194.so
lib/libelf.a
lib/pkgconfig/libdw.pc
lib/pkgconfig/libelf.pc
```

## Native build recipe

`native/elfutils/elfutils.bzl` reproduces the Spack Autotools build inside the
insula:

```text
download elfutils-0.194.tar.bz2
validate BZIP2_PREFIX, GETTEXT_PREFIX, LIBICONV_PREFIX, M4_PREFIX,
         PKGCONF_PREFIX, XZ_PREFIX, ZLIB_PREFIX, ZSTD_PREFIX
remove -Werror from */Makefile.in
export PATH=<gettext>/bin:<m4>/bin:<pkgconf>/bin:$PATH
export M4=<m4>/bin/m4
export PKG_CONFIG=<pkgconf>/bin/pkgconf
export PKG_CONFIG_PATH=<zlib-ng>:<xz>:<zstd>:<libiconv>:<gettext> pkgconfig dirs
export CPPFLAGS=-I<zstd>/include -I<xz>/include -I<bzip2>/include
                -I<zlib-ng>/include -I<libiconv>/include
export CFLAGS/CXXFLAGS=-O3 -g0 -march=icelake-client -mtune=icelake-client
export LDFLAGS=-L<zstd>/lib -L<xz>/lib -L<bzip2>/lib -L<zlib-ng>/lib
               -L<libiconv>/lib plus rpath
../configure <Spack flags>
make V=1
make install
copy libelf/elf.h to prefix/include/elf.h if upstream install omitted it
remove .la files
emit prefix_path.txt
```

The mechanism verifier should record the dependency contract as:

```text
native/elfutils/elfutils.bzl: autotools: BZIP2_PREFIX, GETTEXT_PREFIX, LIBICONV_PREFIX, M4_PREFIX, PKGCONF_PREFIX, XZ_PREFIX, ZLIB_PREFIX, ZSTD_PREFIX
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`; every dependency is
read from a Bazel `*_prefix_file`, then passed through Autotools-specific
channels (`PATH`, `M4`, `PKG_CONFIG`, `PKG_CONFIG_PATH`, `CPPFLAGS`, and
`LDFLAGS`).

## Gates

The consumer target is:

```text
//synthetic:use_elfutils
```

It links through the Spack-generated `@spack_elfutils//:lib` facade and calls
`elf_version(EV_CURRENT)`, so the test is unchanged when the provider flips
from Spack to native.

The ABI gate is:

```text
//synthetic:elfutils_abi_parity
```

It compares the native prefix against the hermetic Spack reference for:

- public headers and ABI-relevant library/pkg-config layout;
- SONAME and exported dynamic symbols for `libelf`, `libdw`, and `libasm`;
- prefix-normalized `libelf.pc` and `libdw.pc`;
- downstream link-and-run against `libelf`;
- executable dependency parity and version behavior for `eu-readelf`, `eu-nm`,
  and `eu-elfclassify`.

Focused verification command:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='elfutils@0.194' \
VASO_LOCK_OUT=/workspace/experiment/spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/elfutils_native_build_graph.json \
VASO_NATIVE=1 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_FORCE_FETCH_REPOS='@elfutils_native' \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_elfutils //synthetic:elfutils_abi_parity //tools:hermetic_native_deps_guard_test //tools:native_build_mechanism_guard_unit_test //tools:abi_parity_unit_test' \
VASO_SPACK_TIMEOUT=900 \
./run.sh
```

The focused command passed in `rootfs mode: cuda-bundle`: it rebuilt/fetched
`@elfutils_native` under the Bazel output base, wrote the canonical
`spack_graph.lock.json`, linked and ran `//synthetic:use_elfutils` with
`elfutils:libelf:1`, and passed `//synthetic:elfutils_abi_parity`. The ABI gate
reported matching layout counts (`candidate_count=30`, `reference_count=30`),
no missing/extra ABI paths, matching selected executable checks, and overall
`"ok": true`.
