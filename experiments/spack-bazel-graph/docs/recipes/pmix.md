# PMIx frontier recipe

## Position in the hillclimb

`pmix@6.1.0` is the next uncaptured non-toolchain node after the native Git
frontier in the captured lean PyTorch graph:

```text
86  pmix  6.1.0  autotools
```

The focused reference graph for `SPACK_ROOT_PKG='pmix@6.1.0'` ends with:

```text
34  pmix  6.1.0  autotools
```

Spack still owns the DAG shape. The native flip changes only `spack_pmix.build`
to `native` and re-exports `@pmix_native//:lib`; link edges to `hwloc`,
`libevent`, and `zlib-ng` remain Spack-derived.

## Spack evidence

All recipe evidence comes from Bazel's vendored `@spack_dist//:spack` running
inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/pmix-6.1.0-s3ea37tyylwvzyc2drxuvow76z4btk57
```

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/pmix/package.py
```

Source provenance from that recipe:

- package class: `Pmix(AutotoolsPackage)`
- upstream source URL used by the native rule:
  `https://github.com/openpmix/openpmix/releases/download/v6.1.0/pmix-6.1.0.tar.bz2`
- version `6.1.0` SHA256:
  `bb9021c8e100a376f5070ecca727f83a29b5f652dfe381793b88daa79a3b98a2`
- concrete variants: `~munge`, `~python`
- build dependencies: `pkgconfig`, `libtool`, `gmake`
- build/link dependencies: `hwloc`, `libevent`, `zlib-ng`

The Spack-generated configure argument file records:

```text
--enable-shared
--enable-static
--with-zlib=<zlib-ng-prefix>
--with-libevent=<libevent-prefix>
--with-hwloc=<hwloc-prefix>
--disable-python-bindings
--without-munge
```

The hermetic Spack build log confirms this is the release-tarball Autotools
path. `autoreconf()` is effectively a no-op because `configure` exists; Spack
then runs `configure`, `make V=1`, and `make install`.

## Native build

`native/pmix/pmix.bzl` defines `pmix_native`, a Bazel repository rule that
declares `VASO_IN_INSULA` as an environment input and refuses to build unless
the hermetic insula sets `VASO_IN_INSULA=1`.

The rule consumes only Bazel-native dependency prefixes:

```text
HWLOC_PREFIX       <- @hwloc_native//:prefix_path.txt
LIBEVENT_PREFIX    <- @libevent_native//:prefix_path.txt
LIBICONV_PREFIX    <- @libiconv_native//:prefix_path.txt
LIBPCIACCESS_PREFIX <- @libpciaccess_native//:prefix_path.txt
LIBTOOL_PREFIX     <- @libtool_native//:prefix_path.txt
LIBXML2_PREFIX     <- @libxml2_native//:prefix_path.txt
PKGCONF_PREFIX     <- @pkgconf_native//:prefix_path.txt
XZ_PREFIX          <- @xz_native//:prefix_path.txt
ZLIB_PREFIX        <- @zlib_ng_native//:prefix_path.txt
```

The build action runs:

```sh
export PATH="${LIBTOOL_PREFIX}/bin:${PKGCONF_PREFIX}/bin:$PATH"
export ACLOCAL_PATH="${LIBTOOL_PREFIX}/share/aclocal:${PKGCONF_PREFIX}/share/aclocal"
export PKG_CONFIG="${PKGCONF_PREFIX}/bin/pkgconf"
export PKG_CONFIG_PATH="${HWLOC_PREFIX}/lib/pkgconfig:${LIBEVENT_PREFIX}/lib/pkgconfig:${ZLIB_PREFIX}/lib/pkgconfig:${LIBPCIACCESS_PREFIX}/lib/pkgconfig:${LIBXML2_PREFIX}/lib/pkgconfig:${XZ_PREFIX}/lib/pkgconfig:${PKGCONF_PREFIX}/lib/pkgconfig"
export CPPFLAGS="-I${HWLOC_PREFIX}/include -I${LIBEVENT_PREFIX}/include -I${ZLIB_PREFIX}/include"
export CFLAGS="-O3 -g0 -march=icelake-client -mtune=icelake-client"
export CXXFLAGS="-O3 -g0 -march=icelake-client -mtune=icelake-client"
export LDFLAGS="-L${HWLOC_PREFIX}/lib -L${LIBEVENT_PREFIX}/lib -L${ZLIB_PREFIX}/lib plus rpath"

./configure \
  --prefix="$PREFIX" \
  --enable-shared \
  --enable-static \
  --with-zlib="$ZLIB_PREFIX" \
  --with-libevent="$LIBEVENT_PREFIX" \
  --with-hwloc="$HWLOC_PREFIX" \
  --disable-python-bindings \
  --without-munge

make V=1
make install
find "$PREFIX" -type f -name '*.la' -delete
```

After install, the rule rewrites only the `pmix.pc` `Libs.private`, `Cflags`,
and `Requires.private` lines from the declared Bazel-native prefixes. This
matches the hermetic Spack static dependency metadata: PMIx advertises direct
`zlib-ng`, `libevent`, and `hwloc` paths, plus hwloc's private
`libpciaccess`, `libxml2`, and `libiconv` closure. The rewrite is deliberately
scoped to installed metadata; the library and executable binaries still come
from the same PMIx `configure && make && make install` path.

The mechanism verifier records the dependency contract as:

```text
native/pmix/pmix.bzl: autotools: HWLOC_PREFIX, LIBEVENT_PREFIX, LIBICONV_PREFIX, LIBPCIACCESS_PREFIX, LIBTOOL_PREFIX, LIBXML2_PREFIX, PKGCONF_PREFIX, XZ_PREFIX, ZLIB_PREFIX
```

## Prefix and ABI gate

The ABI-relevant prefix contract used by the gate is:

- headers under `include/` and `include/pmix/`
- libraries: `lib/libpmix.a`, `lib/libpmix.so`,
  `lib/libpmix.so.2`, `lib/libpmix.so.2.23.0`
- pkg-config metadata: `lib/pkgconfig/pmix.pc`
- executable: `bin/pmix_info`

`//synthetic:use_pmix` links through the Spack-generated `@spack_pmix//:lib`
facade and calls `PMIx_Get_version()`, so the consumer remains unchanged when
the provider flips. `//synthetic:use_pmix_native` links directly against
`@pmix_native//:lib` as a native-prefix smoke test.

`//synthetic:pmix_abi_parity` compares `@pmix_native//:prefix` against the
hermetic Spack reference prefix for layout, SONAME/exported symbols,
prefix-normalized `pmix.pc`, downstream link-and-run, executable dependency
parity, and `pmix_info --version` behavior.

The focused native verifier for `SPACK_ROOT_PKG='pmix@6.1.0'` was run through
`run.sh` inside the CUDA insula with Bazel's hermetic Spack. It passed:

- `//synthetic:use_pmix`
- `//synthetic:use_pmix_native`
- `//synthetic:pmix_abi_parity`
- `//tools:hermetic_native_deps_guard_test`
- `//tools:native_build_mechanism_guard_unit_test`
- `//tools:abi_parity_unit_test`

The ABI verdict matched layout count `128`, SONAME `libpmix.so.2`, exported
symbol count `1311`, prefix-normalized `pmix.pc` SHA256
`077196056fd9a9bf0e5cd9bcf2c92a9bf10a09230b1bd18b1786ae723e8b11e0`,
downstream `PMIx_Get_version()` output, `bin/pmix_info` NEEDED entries, and
`pmix_info --version` output.
