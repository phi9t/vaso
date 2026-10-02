# swig@4.4.1

## Position in the hillclimb

`swig@4.4.1` is the next not-yet-native Autotools frontier node after native
`qhull@2020.2`; `sqlite@3.53.1` at the intervening topo position is already
native. The full graph keeps the lean font topology:

```bash
SPACK_ROOT_PKG='py-torch cuda_arch=80,90,100 ^openblas~fortran ^font-util fonts:=encodings'
```

The focused all-Spack swig reference graph has 9 nodes. The concrete swig node
has:

```text
build: compiler-wrapper, gcc, gmake, pcre2, pkgconf, zlib-ng
link: gcc-runtime, glibc, pcre2, zlib-ng
```

`compiler-wrapper`, `gcc`, `gmake`, `gcc-runtime`, and `glibc` remain
toolchain/build-runtime infrastructure. The native provider consumes pcre2,
pkgconf, and zlib-ng through explicit Bazel-native prefix files.

## Hermetic Spack Evidence

All evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this node.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/swig-4.4.1-i2wn7calsggxstg5eolof5e3ert75pip
```

Source provenance from the archived hermetic Spack recipe:

- package class: `Swig(AutotoolsPackage, SourceforgePackage)`
- upstream source: `swig/swig-4.4.1.tar.gz`
- source SHA256:
  `40162a706c56f7592d08fd52ef5511cb7ac191f3593cf07306a0a554c6281fcf`
- no patches for `@4.4.1`

Concrete Spack build output shows:

```text
configure --prefix=<prefix>
checking whether to enable PCRE2 support... yes
checking whether to use local PCRE2... no
checking for pcre2-config... <pcre2-prefix>/bin/pcre2-config
checking whether to enable ccache-swig... yes
checking for zlib.h... yes
make V=1
make install
```

The hermetic Spack build environment records:

```text
SPACK_CC=/usr/bin/gcc
SPACK_CXX=/usr/bin/g++
SPACK_TARGET_ARGS_CC='-march=icelake-client -mtune=icelake-client'
SPACK_TARGET_ARGS_CXX='-march=icelake-client -mtune=icelake-client'
PKG_CONFIG_PATH=<pcre2>/lib/pkgconfig:<pkgconf>/lib/pkgconfig:<zlib-ng>/lib/pkgconfig
```

## Prefix Surface

The ABI/runtime-relevant installed surface is:

```text
bin/ccache-swig
bin/swig
bin/swig4.0 -> swig
share/swig/4.4.1/**
```

`bin/swig` has NEEDED entries for `libpcre2-8.so.0`, `libstdc++.so.6`,
`libgcc_s.so.1`, and `libc.so.6`. `bin/ccache-swig` has NEEDED entries for
`libz.so.1` and `libc.so.6`.

## Native Build Recipe

`native/swig/swig.bzl` mirrors Spack's Autotools flow inside the CUDA insula:

```text
download swig-4.4.1.tar.gz
validate pcre2/bin/pcre2-config
validate pkgconf/bin/pkgconf
validate zlib-ng/include/zlib.h and zlib-ng/lib/libz.so
mkdir spack-build
export PATH=<pkgconf>/bin:<pcre2>/bin:$PATH
export PKG_CONFIG=<pkgconf>/bin/pkgconf
export PKG_CONFIG_PATH=<pcre2>/lib/pkgconfig:<zlib-ng>/lib/pkgconfig
export CPPFLAGS=-I<pcre2>/include -I<zlib-ng>/include
export CFLAGS="-march=icelake-client -mtune=icelake-client"
export CXXFLAGS="-march=icelake-client -mtune=icelake-client"
export LDFLAGS=-L<pcre2>/lib -L<zlib-ng>/lib -Wl,-rpath,<pcre2>/lib:<zlib-ng>/lib
../configure --prefix=<prefix>
make V=1
make install
ln -sfn swig <prefix>/bin/swig4.0
emit prefix_path.txt
```

The mechanism verifier classifies this as `autotools` and should report:

```text
native/swig/swig.bzl: autotools: PCRE2_PREFIX, PKGCONF_PREFIX, ZLIB_PREFIX
```

## Gates

The smoke target is:

```text
//synthetic:use_swig_native
```

It verifies installed tool files, `swig -version`, `swig -swiglib`, and a
minimal Python wrapper generation.

`//synthetic:swig_prefix_parity` compares the native prefix against the hermetic
Spack reference. It covers:

- layout for `bin/ccache-swig`, `bin/swig`, `bin/swig4.0`, and the full
  `share/swig/4.4.1` runtime tree;
- executable NEEDED parity for `bin/swig` and `bin/ccache-swig`;
- selected runtime data file hashes;
- matching `swig -version`, `swig -swiglib`, and generated wrapper output;
- pcre2/zlib runtime dependency channels through explicit reference and
  candidate prefix lists.

Current status: native `swig` is gated inside the hermetic CUDA insula. The
focused native verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='swig@4.4.1' \
VASO_LOCK_OUT=/workspace/experiment/swig_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/swig_native_build_graph.json \
VASO_NATIVE=1 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_swig_native //synthetic:swig_prefix_parity //tools:hermetic_native_deps_guard_test //tools:native_build_mechanism_guard_unit_test //tools:abi_parity_unit_test' \
VASO_SPACK_TIMEOUT=600 \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside the isolated CUDA 12.9.1
insula, reported hermetic Spack version `1.2.2`, wrote
`swig_native_spack_graph.lock.json` and `swig_native_build_graph.json`, passed
`//synthetic:use_swig_native`, passed `//synthetic:swig_prefix_parity`, passed
`//tools:hermetic_native_deps_guard_test`, passed
`//tools:native_build_mechanism_guard_unit_test`, and passed
`//tools:abi_parity_unit_test`.
