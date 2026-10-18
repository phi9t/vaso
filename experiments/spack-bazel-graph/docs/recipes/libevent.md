# libevent@2.1.12

## Position in the hillclimb

`libevent` is the py-torch frontier node immediately after native Eigen. In
the captured `SPACK_ROOT_PKG=py-torch` graph it appears as:

```text
64  libevent  2.1.12  autotools
```

The focused reference graph used for this migration is:

```bash
SPACK_ROOT_PKG='libevent@2.1.12 +openssl'
```

The reference run used Bazel's vendored `@spack_dist//:spack` inside the
isolated CUDA 12.9.1 insula and wrote `libevent_spack_graph.lock.json` plus
`libevent_build_graph.json`. The native run flips `spack_libevent` to
`@libevent_native//:lib` without changing the Spack DAG edges.

## Hermetic Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path in the isolated estate:

```text
/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/libevent/package.py
```

Source provenance from the Spack recipe:

- package class: `Libevent(AutotoolsPackage)`
- Spack build-system bucket: `autotools`
- upstream source URL:
  `https://github.com/libevent/libevent/releases/download/release-2.1.12-stable/libevent-2.1.12-stable.tar.gz`
- SHA256: `92e6de1be9ec176428fd2367677e61ceffc2ee1cb119035037a27d346b0403bb`
- concrete variant: `+openssl`
- dependency channel: `openssl` as a build/link dependency
- recipe configure arg: `--enable-openssl`

The concrete focused `libevent@2.1.12 +openssl` node has:

```text
build: compiler-wrapper, gcc, gmake, openssl
link: gcc-runtime, glibc, openssl
```

The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/libevent-2.1.12-v7wrpflqryaorib4xlxqp6gaed4gh3rk
```

The ABI/prefix-relevant installed surface is:

```text
bin/event_rpcgen.py
include/event2/
lib/libevent-2.1.so.7.0.1
lib/libevent_core-2.1.so.7.0.1
lib/libevent_extra-2.1.so.7.0.1
lib/libevent_openssl-2.1.so.7.0.1
lib/libevent_pthreads-2.1.so.7.0.1
lib/libevent*.a
lib/pkgconfig/libevent*.pc
```

## Native build recipe

`native/libevent/libevent.bzl` mirrors the concrete Spack Autotools flow:

```text
download libevent-2.1.12-stable.tar.gz
validate OpenSSL, pkgconf, and zlib-ng prefixes from Bazel prefix files
export PKG_CONFIG=<pkgconf-prefix>/bin/pkgconf
export PKG_CONFIG_PATH=<openssl-libdir>/pkgconfig:<zlib-prefix>/lib/pkgconfig
export CPPFLAGS=-I<openssl>/include -I<zlib-ng>/include
export LDFLAGS=-L<openssl-libdir> -Wl,-rpath,<openssl-libdir> \
               -L<zlib-libdir> -Wl,-rpath,<zlib-libdir> \
               -Wl,--disable-new-dtags
./configure --prefix=<prefix> --enable-openssl
make V=1
make install
remove libtool archives
normalize libevent_openssl.pc whitespace to match the Spack Autotools prefix
emit prefix_path.txt
```

`zlib-ng` is an explicit native dependency even though Spack does not show it
as a direct libevent edge in the focused lock: the hermetic Spack configure log
finds `zlib.h` and links `-lz`, and the PyTorch frontier has already migrated
`zlib-ng`. The native build therefore routes zlib discovery through
`@zlib_ng_native//:prefix_path.txt` instead of allowing Autotools to discover a
rootfs zlib.

The native provider exposes:

- `@libevent_native//:prefix` for the installed prefix filegroup;
- `@libevent_native//:prefix_path.txt` for downstream native repository rules;
- `@libevent_native//:lib` with the same public link surface as Spack:
  `event`, `event_core`, `event_extra`, `event_openssl`, and
  `event_pthreads`, plus native OpenSSL/zlib link directories.

The corresponding mechanism verifier is the Autotools dependency-prefix case:

```text
native/libevent/libevent.bzl: autotools: OPENSSL_PREFIX, PKGCONF_PREFIX, ZLIB_PREFIX
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, consumes
`@openssl_native//:prefix_path.txt`, `@pkgconf_native//:prefix_path.txt`, and
`@zlib_ng_native//:prefix_path.txt`, validates all three prefixes before
configure, pins `PKG_CONFIG`, and threads dependency lookup through
`PKG_CONFIG_PATH`, `CPPFLAGS`, and `LDFLAGS`.

## Gates

The smoke target is:

```text
//synthetic:use_libevent_native
```

It compiles a C consumer against `@libevent_native//:lib`, includes
`<event2/event.h>` and `<event2/thread.h>`, initializes pthread support, builds
and frees an event base, and prints:

```text
libevent:2.1.12-stable:pthreads
```

`//synthetic:libevent_abi_parity` compares the native prefix against the
hermetic Spack reference. It covers:

- 57 installed layout paths;
- SONAME and exported-symbol parity for all five shared libraries;
- static archive member and symbol parity for all five `.a` files;
- prefix-normalized pkg-config metadata for all five `.pc` files;
- executable presence and NEEDED parity for `bin/event_rpcgen.py`;
- downstream C link-and-run output for both native and reference prefixes.

Current status: native libevent is gated inside the hermetic CUDA insula. The
focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='libevent@2.1.12 +openssl' \
VASO_LOCK_OUT=/workspace/experiment/libevent_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/libevent_native_build_graph.json \
VASO_NATIVE=1 \
VASO_FORCE_FETCH_REPOS='@libevent_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_libevent_native //tools:hermetic_native_deps_guard_test //tools:native_build_mechanism_guard_unit_test //tools:abi_parity_unit_test' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

The run used Bazel's `@spack_dist//:spack` inside the isolated CUDA 12.9.1
insula, reported hermetic Spack version `1.2.2`, passed
`//tools:hermetic_spack_guard_test`, passed
`//tools:hermetic_native_deps_guard_test`, passed
`//tools:native_build_mechanism_guard_unit_test`, passed
`//tools:abi_parity_unit_test`, passed `//synthetic:use_libevent_native`, and
passed `//synthetic:libevent_abi_parity`.

The parity JSON reported `ok: true`: 57 layout paths, all five shared-library
SONAME/exported-symbol sets matched, all five static archives had matching
member and symbol sets, all five pkg-config files matched after prefix
normalization, `bin/event_rpcgen.py` was present on both sides, and downstream
C output matched `libevent:2.1.12-stable:pthreads`.
