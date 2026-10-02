# binutils@2.46.1

## Position in the hillclimb

`binutils@2.46.1` is the next native provider after the SWIG frontier. The
focused reference graph is:

```bash
SPACK_ROOT_PKG='binutils@2.46.1'
```

Spack still owns the DAG shape. The native flip changes only
`spack_binutils.build` to `native` and re-exports `@binutils_native//:lib`;
link edges to `zlib-ng` and `zstd` stay Spack-derived.

## Hermetic Spack evidence

All recipe evidence comes from Bazel's vendored `@spack_dist//:spack` running
inside the CUDA insula. Do not use an ambient host Spack checkout.

The focused all-Spack reference run wrote:

```text
binutils_spack_graph.lock.json
binutils_build_graph.json
```

The reference prefix was:

```text
/vaso/cache/spack/opt/spack/linux-icelake/binutils-2.46.1-lrvswwgdx7wy25nkbqwtarvwey7xgzwr
```

The hermetic Spack recipe is:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/binutils/package.py
```

Concrete variants and parameters:

```text
build_system: autotools
libs: shared,static
plugins: true
compress_debug_sections: zlib
gas: false
ld: false
gprofng: false
headers: false
libiberty: false
lto: false
nls: false
pgo: false
debuginfod: false
interwork: false
```

Hermetic Spack configure shape:

```text
CFLAGS=-O3 -g0
CXXFLAGS=-O3 -g0
--disable-dependency-tracking
--disable-werror
--enable-64-bit-bfd
--enable-deterministic-archives
--enable-multilib
--enable-pic
--enable-targets=x86_64-linux-gnu
--with-sysroot=/
--with-system-zlib
--disable-gas
--disable-gprofng
--disable-install-libiberty
--disable-interwork
--disable-ld
--enable-shared
--enable-static
--disable-lto
--disable-nls
--enable-plugins
--without-debuginfod
--disable-pgo-build
--enable-compressed-debug-sections=all
--enable-default-compressed-debug-sections-algorithm=zlib
```

## Native build recipe

`native/binutils/binutils.bzl` reproduces the Spack Autotools build inside the
insula:

```text
download binutils-2.46.1.tar.bz2
validate DIFFUTILS_PREFIX, PKGCONF_PREFIX, ZLIB_PREFIX, ZSTD_PREFIX
export PATH=<diffutils>/bin:<pkgconf>/bin:$PATH
export PKG_CONFIG=<pkgconf>/bin/pkgconf
export PKG_CONFIG_PATH=<zlib-ng>/lib/pkgconfig:<zstd>/lib/pkgconfig
export CPPFLAGS=-I<zlib-ng>/include -I<zstd>/include
export CFLAGS/CXXFLAGS=-O3 -g0 -march=icelake-client -mtune=icelake-client
export LDFLAGS=-L<zlib-ng>/lib -L<zstd>/lib plus rpath
../configure <Spack flags>
make V=1
make install
remove .la files, manpages, and locale payload
emit prefix_path.txt
```

The mechanism verifier records the dependency contract as:

```text
native/binutils/binutils.bzl: autotools: DIFFUTILS_PREFIX, PKGCONF_PREFIX, ZLIB_PREFIX, ZSTD_PREFIX
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`; every dependency is
read from a Bazel `*_prefix_file`, then passed through the Autotools-specific
channels (`PATH`, `PKG_CONFIG`, `PKG_CONFIG_PATH`, `CPPFLAGS`, `LDFLAGS`).

## Gates

The smoke target is:

```text
//synthetic:use_binutils_native
```

It runs `ar`, `nm`, `objdump`, `readelf`, `strings`, and `strip`, checks the
installed headers and shared-library layout, then creates and lists a small
archive. It prints:

```text
binutils:2.46.1:ok
```

The ABI gate is:

```text
//synthetic:binutils_abi_parity
```

It compares the native prefix against the hermetic Spack reference for:

- selected headers plus the ABI-relevant library layout, including symlinks;
- SONAME and exported dynamic symbols for installed shared libraries;
- downstream link-and-run against `libbfd`, `libopcodes`, `libctf`,
  `libctf-nobfd`, and `libsframe`;
- executable dependency parity for `ar`, `nm`, `objdump`, and `readelf`;
- matching `ar --version` and `readelf --version` behavior.

Focused verification command:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='binutils@2.46.1' \
VASO_LOCK_OUT=/workspace/experiment/binutils_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/binutils_native_build_graph.json \
VASO_NATIVE=1 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_binutils_native //synthetic:binutils_abi_parity //tools:hermetic_native_deps_guard_test //tools:native_build_mechanism_guard_unit_test //tools:abi_parity_unit_test' \
VASO_SPACK_TIMEOUT=1200 \
./run.sh
```
