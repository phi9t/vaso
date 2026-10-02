# nghttp2 frontier recipe

## Position in the hillclimb

`nghttp2` is the migrated py-torch frontier node immediately after `libedit`.
In the captured `SPACK_ROOT_PKG=py-torch` graph it appears as:

```text
37  nghttp2  1.67.1  autotools  native; ABI parity green
```

The package-local verification run used:

```bash
SPACK_ROOT_PKG='nghttp2@1.67.1' \
VASO_NATIVE=1 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_nghttp2_native //tools:hermetic_native_deps_guard_test //synthetic:nghttp2_abi_parity' \
VASO_LOCK_OUT=/workspace/experiment/nghttp2_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/nghttp2_build_graph.json \
VASO_FORCE_FETCH_REPOS='@nghttp2_native' \
./run.sh
```

That command seats the CUDA insula, forces the Bazel-owned native repository
fetch inside the insula, runs Bazel's vendored `@spack_dist//:spack`, applies
the `native_overrides.json` flip to `@nghttp2_native//:lib`, and runs the
native smoke, Autotools hermetic-deps guard, and `//synthetic:nghttp2_abi_parity`
inside the same insula.

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/nghttp2/package.py
```

Source provenance from the Spack recipe:

- package class: `Nghttp2(AutotoolsPackage)`
- upstream source URL:
  `https://github.com/nghttp2/nghttp2/releases/download/v1.67.1/nghttp2-1.67.1.tar.gz`
- SHA256:
  `da8d640f55036b1f5c9cd950083248ec956256959dc74584e12c43550d6ec0ef`
- dependencies: `pkgconfig` and `diffutils` as build dependencies

The concrete `nghttp2@1.67.1` node has:

```text
build: compiler-wrapper, diffutils, gcc, gmake, pkgconf
link: gcc-runtime, glibc
```

The hermetic Spack build log shows the Autotools flow:

```text
configure --prefix=<nghttp2-prefix> \
  --enable-lib-only \
  --with-libxml2=no \
  --with-jansson=no \
  --with-zlib=no \
  --with-libevent-openssl=no \
  --with-libcares=no \
  --with-openssl=no \
  --with-libev=no \
  --with-cunit=no \
  --with-jemalloc=no \
  --with-systemd=no \
  --with-mruby=no \
  --with-neverbleed=no \
  --with-boost=no \
  --with-wolfssl=no
make V=1
make install
```

The upstream configure script warns that `--with-cunit` and `--with-boost` are
unrecognized, but these flags are still part of the Spack recipe's observed
argument vector and the hermetic Spack build succeeds with them.

The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/nghttp2-1.67.1-k7c67knpxzjjblqhbtxq4s5h2cetusr3
```

The ABI/behavior-relevant installed surface for this migration is:

```text
include/nghttp2/nghttp2.h
include/nghttp2/nghttp2ver.h
lib/libnghttp2.so.14.29.1
lib/libnghttp2.so.14
lib/libnghttp2.so
lib/libnghttp2.a
lib/pkgconfig/libnghttp2.pc
share/doc/nghttp2/README.rst
share/man/man1/h2load.1
share/man/man1/nghttp.1
share/man/man1/nghttpd.1
share/man/man1/nghttpx.1
```

The reference shared library has SONAME `libnghttp2.so.14`, 181 exported
dynamic symbols, and a static archive with 26 members and 421 globally defined
symbols.

## Build recipe

`native/nghttp2/nghttp2.bzl` mirrors Spack's Autotools build:

```text
download nghttp2-1.67.1.tar.gz
export PATH="<diffutils-prefix>/bin:<pkgconf-prefix>/bin:$PATH"
export PKG_CONFIG="<pkgconf-prefix>/bin/pkgconf"
export PKG_CONFIG_PATH=
export LDFLAGS="-Wl,-rpath,<prefix>/lib -Wl,--disable-new-dtags"
configure --prefix=<prefix> --enable-lib-only \
  --with-libxml2=no --with-jansson=no --with-zlib=no \
  --with-libevent-openssl=no --with-libcares=no --with-openssl=no \
  --with-libev=no --with-cunit=no --with-jemalloc=no --with-systemd=no \
  --with-mruby=no --with-neverbleed=no --with-boost=no --with-wolfssl=no
make V=1 -j$MAKE_JOBS
make install
find <prefix> -type f -name '*.la' -delete
```

The native rule consumes `@diffutils_native//:prefix_path.txt` and
`@pkgconf_native//:prefix_path.txt`, validates both tool prefixes before
configure, pins `PKG_CONFIG`, and places only those Bazel-native tool bins at
the front of `PATH`. The corresponding mechanism verifier is the Autotools
dependency-prefix case:

```text
native/nghttp2/nghttp2.bzl: autotools: DIFFUTILS_PREFIX, PKGCONF_PREFIX
```

## ABI gate target

`//synthetic:nghttp2_abi_parity` compares the native prefix against the
hermetic Spack reference. The gate covers:

- exact ABI-relevant layout parity for 12 entries;
- SONAME parity for `libnghttp2.so.14`;
- exported dynamic symbol parity: 181 symbols from `libnghttp2.so.14.29.1`;
- static archive member/exported-symbol parity: 26 members and 421 symbols;
- prefix-normalized `lib/pkgconfig/libnghttp2.pc`;
- byte-identical `README.rst` and selected manpages;
- downstream `nghttp2_version()` and header-name validation link-and-run
  behavior against reference and native prefixes.

The smoke target links against the native prefix and prints:

```text
nghttp2:1.67.1:h2
```

Current status: native and ABI-gated. The latest run used `rootfs mode:
cuda-bundle`, reported `hermetic spack (Bazel-owned) version: 1.2.2`, flipped
`spack_nghttp2` to `build: native`, and passed
`//synthetic:use_nghttp2_native`, `//tools:hermetic_native_deps_guard_test`,
and `//synthetic:nghttp2_abi_parity` inside the CUDA insula.
