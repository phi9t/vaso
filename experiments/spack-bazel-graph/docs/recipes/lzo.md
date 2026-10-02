# lzo frontier recipe

## Position in the hillclimb

`lzo` is the migrated py-torch frontier node after `libyaml`. In the captured
`SPACK_ROOT_PKG=py-torch` graph it appears as:

```text
27  lzo  2.10  autotools  native; ABI parity green
```

The native verification run used the package-specific root:

```bash
SPACK_ROOT_PKG='lzo' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_lzo_native' \
VASO_SPACK_TIMEOUT=1800 \
VASO_LOCK_OUT=/workspace/experiment/lzo_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/lzo_build_graph.json \
VASO_FORCE_FETCH_REPOS='@lzo_native' \
./run.sh
```

That command seats the CUDA insula, runs Bazel's vendored
`@spack_dist//:spack`, applies the `native_overrides.json` flip to
`@lzo_native//:lib`, and runs `//synthetic:use_lzo_native` plus
`//synthetic:lzo_abi_parity` inside the insula.

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/lzo/package.py
```

Source provenance from the Spack recipe:

- package class: `Lzo(AutotoolsPackage)`
- version: `2.10`
- upstream source URL:
  `https://www.oberhumer.com/opensource/lzo/download/lzo-2.10.tar.gz`
- SHA256:
  `c0f892943208266f9b6543b3ae308fab6284c5c90e627931446fb49b4221a072`
- variant: `libs`, default `shared,static`
- configure args:
  `--disable-dependency-tracking --enable-shared --enable-static`
- concrete dependency edges: toolchain only
- package-specific patch surface: none for concrete `2.10`

The hermetic Spack build log shows the standard Autotools phases:

```text
autoreconf
<spack-stage>/spack-src/configure --prefix=<lzo-prefix> \
  --disable-dependency-tracking --enable-shared --enable-static
make V=1
make install
find <lzo-prefix> -name '*.la'
```

The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/lzo-2.10-2r7dy6hr7azy57pznmyl5vds62g52dch
```

The prefix contains the public C ABI surface:

```text
include/lzo/lzo1.h
include/lzo/lzo1a.h
include/lzo/lzo1b.h
include/lzo/lzo1c.h
include/lzo/lzo1f.h
include/lzo/lzo1x.h
include/lzo/lzo1y.h
include/lzo/lzo1z.h
include/lzo/lzo2a.h
include/lzo/lzo_asm.h
include/lzo/lzoconf.h
include/lzo/lzodefs.h
include/lzo/lzoutil.h
lib/liblzo2.a
lib/liblzo2.so -> liblzo2.so.2.0.0
lib/liblzo2.so.2 -> liblzo2.so.2.0.0
lib/liblzo2.so.2.0.0
lib/pkgconfig/lzo2.pc
```

## Build recipe

`native/lzo/lzo.bzl` mirrors Spack's in-source Autotools flow:

```text
./configure --prefix=<prefix> \
  --disable-dependency-tracking --enable-shared --enable-static
make V=1
make install
find <prefix> -type f -name '*.la' -delete
```

The repository rule fetches the same upstream tarball by SHA256 and refuses to
run unless the hermetic insula has set `VASO_IN_INSULA=1`. `lzo` has no
non-toolchain dependency prefixes, so its corresponding mechanism verifier is
the Autotools/no-dependency case in `//tools:hermetic_native_deps_guard_test`;
the live guard reports:

```text
native/lzo/lzo.bzl: autotools: no dep prefixes
```

## Prefix and ABI gate target

`//synthetic:lzo_abi_parity` compares the native prefix against the hermetic
Spack reference. The gate covers:

- 18 ABI-relevant layout entries under `include/` and `lib/`;
- SONAME parity: `liblzo2.so.2`;
- exported dynamic symbol parity: 116 symbols;
- prefix-normalized pkg-config parity for `lib/pkgconfig/lzo2.pc`;
- a downstream C link-and-run consumer that compresses and safely decompresses
  a literal payload with `lzo1x_1_compress` and `lzo1x_decompress_safe`.

The smoke target prints:

```text
lzo:2.10:26:ok
```

Current status: native and ABI-gated. The latest run passed
`//synthetic:use_lzo_native`, `//tools:hermetic_native_deps_guard_test`, and
`//synthetic:lzo_abi_parity` inside the CUDA insula.
