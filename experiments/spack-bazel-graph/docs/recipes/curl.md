# curl frontier recipe

## Position in the hillclimb

`curl` is the py-torch frontier node immediately after the CUDA/cuDNN boundary.
In the captured `SPACK_ROOT_PKG=py-torch` graph it appears as:

```text
60  curl  8.20.0  autotools
```

The focused reference graph used for this migration is:

```bash
SPACK_ROOT_PKG='curl@8.20.0 +nghttp2 tls=openssl ^openssl@3.6.1 ^nghttp2@1.67.1 ^zlib-ng@2.3.3'
```

That run seats the CUDA 12.9.1 insula, runs Bazel's vendored
`@spack_dist//:spack`, and writes `curl_spack_graph.lock.json` plus
`curl_build_graph.json`. The reference lock keeps `spack_curl` as
`build: "spack"` and records link deps on `spack_nghttp2`, `spack_openssl`,
and `spack_zlib_ng`.

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic archived recipe path from the installed reference prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/curl-8.20.0-rhs5xjezzx2zcevml6qu2qtujxm4pnvm/.spack/repos/spack_repo/builtin/packages/curl/package.py
```

Source provenance from the Spack recipe:

- package class: `Curl(NMakePackage, AutotoolsPackage, CMakePackage)`
- upstream source URL: `https://curl.se/download/curl-8.20.0.tar.bz2`
- SHA256: `4be48e69cf467246cb97d369b85d78a08528f2b37cffef2418ee16e6a4eb596e`
- concrete build system for this Linux graph: Autotools
- concrete variants: `~gssapi ~ldap ~libidn2 ~librtmp ~libssh ~libssh2 +nghttp2 libs=shared,static tls=openssl`
- dependency channels: `nghttp2`, `openssl`, `pkgconf`, and `zlib-api`

The concrete focused `curl@8.20.0` node has:

```text
build: compiler-wrapper, gcc, gmake, nghttp2, openssl, pkgconf, zlib-ng
link: gcc-runtime, glibc, nghttp2, openssl, zlib-ng
```

The hermetic Spack build log shows this Autotools configure vector:

```text
./configure --prefix=<curl-prefix> \
  --with-zlib=<zlib-prefix> \
  --without-brotli \
  --without-libgsasl \
  --without-libpsl \
  --without-zstd \
  --disable-docs \
  --disable-manual \
  --enable-shared \
  --enable-static \
  --without-ca-bundle \
  --without-ca-path \
  --with-ca-fallback \
  --without-gssapi \
  --without-gnutls \
  --without-mbedtls \
  --with-openssl=<openssl-prefix> \
  --without-secure-transport \
  --without-sspi \
  --without-libidn2 \
  --without-librtmp \
  --with-nghttp2=<nghttp2-prefix> \
  --without-libssh2 \
  --without-libssh \
  --disable-ldap
```

The upstream configure script warns that `--without-secure-transport`,
`--without-sspi`, and `--without-librtmp` are unrecognized on this platform,
but these flags are part of the hermetic Spack argument vector and the build
succeeds with them.

Relevant configure findings from the hermetic reference:

```text
checking for zlib.h... yes
configure: found both libz and libz.h header
configure: PKG_CONFIG_LIBDIR will be set to "<openssl-prefix>/lib64/pkgconfig"
checking for pkg-config... <pkgconf-prefix>/bin/pkg-config
checking for openssl options with pkg-config... found
checking for SSL_connect in -lssl... yes
checking for openssl/ssl.h... yes
configure: built with one SSL backend
checking for libnghttp2 options with pkg-config... found
checking for nghttp2_session_get_stream_local_window_size in -lnghttp2... yes
checking for nghttp2/nghttp2.h... yes
SSL:              enabled (OpenSSL)
Build libcurl:    Shared=yes, Static=yes
HTTP2:            enabled (nghttp2)
Features:         alt-svc AsynchDNS HSTS HTTP2 HTTPS-proxy IPv6 Largefile libz SSL threadsafe TLS-SRP UnixSockets
```

The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/curl-8.20.0-rhs5xjezzx2zcevml6qu2qtujxm4pnvm
```

The ABI/behavior-relevant installed surface for this migration is:

```text
bin/curl
bin/wcurl
bin/curl-config
include/curl/*.h
lib/libcurl.so.4.8.0
lib/libcurl.so.4
lib/libcurl.so
lib/libcurl.a
lib/pkgconfig/libcurl.pc
share/aclocal/libcurl.m4
```

Spack installs and then removes the libtool archive; the native provider does
the same `find <prefix> -type f -name '*.la' -delete` cleanup.

## Build recipe

`native/curl/curl.bzl` mirrors the Spack Autotools build:

```text
download curl-8.20.0.tar.bz2
validate NGHTTP2_PREFIX, OPENSSL_PREFIX, PKGCONF_PREFIX, and ZLIB_PREFIX
export PATH="<pkgconf-prefix>/bin:$PATH"
export PKG_CONFIG="<pkgconf-prefix>/bin/pkgconf"
export PKG_CONFIG_PATH="<nghttp2-prefix>/lib/pkgconfig:<openssl-libdir>/pkgconfig:<zlib-prefix>/lib/pkgconfig"
export CC="${CC:-gcc}"
enable_symbol_hiding=no configure with the Spack argument vector above
export LD_RUN_PATH="<curl-prefix>/lib:<zlib-libdir>:<openssl-libdir>:<nghttp2-libdir>"
make V=1
make install
remove *.la
```

The native rule consumes `@nghttp2_native//:prefix_path.txt`,
`@openssl_native//:prefix_path.txt`, `@pkgconf_native//:prefix_path.txt`, and
`@zlib_ng_native//:prefix_path.txt`, validates each prefix before configure,
pins `PKG_CONFIG`, and threads all dependency lookup through Autotools
channels rather than host discovery.

The native rule intentionally avoids extra explicit `CPPFLAGS`, `LDFLAGS`, and
`LIBS` exports. Adding those produced `libcurl.pc` and `curl-config` metadata
that no longer matched Spack's emitted prefix. The dependency channels are
instead the Autotools `--with-*` arguments plus pinned `pkgconf` and
`PKG_CONFIG_PATH`.

The hermetic Spack build asked configure for symbol hiding, but the compiler
wrapper probe concluded that hidden visibility would not be used. Native gcc
would otherwise enable it and shrink the exported `libcurl.so.4` ABI from the
reference surface, so the native build sets the Autoconf cache variable
`enable_symbol_hiding=no` before `./configure`. This preserves the effective
Spack ABI without adding a visible `--disable-symbol-hiding` argument to
`bin/curl-config --configure`.

`LD_RUN_PATH` is set before `make` so the installed native libcurl records the
native zlib-ng, OpenSSL, and nghttp2 library directories rather than resolving
against rootfs system copies at runtime.

The corresponding mechanism verifier is the Autotools dependency-prefix case:

```text
native/curl/curl.bzl: autotools: NGHTTP2_PREFIX, OPENSSL_PREFIX, PKGCONF_PREFIX, ZLIB_PREFIX
```

## ABI gate target

`//synthetic:curl_abi_parity` compares the native prefix against the hermetic
Spack reference. The gate covers:

- ABI-relevant layout parity for curl headers, `libcurl` shared/static
  libraries, pkg-config metadata, `libcurl.m4`, and installed executables;
- SONAME and exported-symbol parity for `libcurl.so.4`;
- static archive parity for `lib/libcurl.a`;
- prefix-normalized `lib/pkgconfig/libcurl.pc`, `share/aclocal/libcurl.m4`, and
  `bin/curl-config`;
- executable NEEDED parity and behavior for `bin/curl`, `bin/wcurl`, and
  `bin/curl-config`;
- downstream link-and-run behavior against `curl`, `nghttp2`, `ssl`, `crypto`,
  and `z`.

The smoke target links against the native prefix and validates that
`curl_version_info()` reports version `8.20.0` with SSL and HTTP2 features.

Current status: native Autotools provider is ABI-gated inside the hermetic CUDA
insula. The focused verification command was:

```bash
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='curl@8.20.0 +nghttp2 tls=openssl ^openssl@3.6.1 ^nghttp2@1.67.1 ^zlib-ng@2.3.3' \
VASO_NATIVE=1 \
VASO_LOCK_OUT=/workspace/experiment/curl_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/curl_native_build_graph.json \
VASO_FORCE_FETCH_REPOS='@curl_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_curl_native //tools:hermetic_native_deps_guard_test' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

The run used Bazel's `@spack_dist//:spack` inside the insula, reported
hermetic Spack version `1.2.2`, passed `//tools:hermetic_spack_guard_test`,
passed `//tools:hermetic_native_deps_guard_test`, passed
`//synthetic:use_curl_native`, and passed `//synthetic:curl_abi_parity`.
The ABI parity summary was `ok: true`: 21 layout paths, shared/static
`libcurl` symbol parity with 1080 exported dynamic symbols, prefix-normalized
`libcurl.pc`, `libcurl.m4`, and `curl-config`, executable dependency checks,
matching `curl --version` and `curl-config --feature`, and downstream output
`curl:8.20.0:ssl:http2`.
