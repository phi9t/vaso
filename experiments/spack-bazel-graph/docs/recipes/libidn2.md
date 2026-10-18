# libidn2 frontier recipe

## Position in the hillclimb

`libidn2` is the migrated py-torch frontier node after `libunistring`. In the
captured `SPACK_ROOT_PKG=py-torch` graph it appears as:

```text
25  libidn2  2.3.8  autotools  native; ABI parity green
```

The native verification run used the package-specific root:

```bash
SPACK_ROOT_PKG='libidn2' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_libidn2_native' \
VASO_SPACK_TIMEOUT=1800 \
VASO_LOCK_OUT=/workspace/experiment/libidn2_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/libidn2_build_graph.json \
./run.sh
```

That command seats the CUDA insula, runs Bazel's vendored
`@spack_dist//:spack`, applies the `native_overrides.json` flip to
`@libidn2_native//:lib`, and runs `//synthetic:use_libidn2_native` plus
`//synthetic:libidn2_abi_parity` inside the insula.

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/libidn2/package.py
```

Source provenance from the Spack recipe:

- package class: `Libidn2(AutotoolsPackage, GNUMirrorPackage)`
- version: `2.3.8`
- upstream source URL:
  `https://ftpmirror.gnu.org/libidn/libidn2-2.3.8.tar.gz`
- SHA256:
  `f557911bf6171621e1f72ff35f5b1825bb35b52ed45325dcdee931e5d3c0787a`
- concrete dependency edge: `depends_on("libunistring")`
- build directory: `spack-build`
- package-specific patch surface: none for concrete `2.3.8`

The concrete `libidn2@2.3.8` node has toolchain build/link edges plus a
build+link edge on `libunistring`. The hermetic Spack build environment also
exposes `libiconv` through the `libunistring` closure, so the native rule
threads both dependency prefixes explicitly rather than relying on ambient
compiler-wrapper discovery.

The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/libidn2-2.3.8-zsdydj3muf5vptmk6mz6iwhigj6tdiy7
```

The prefix contains the public CLI/header/library surface:

```text
bin/idn2
include/idn2.h
lib/libidn2.a
lib/libidn2.so -> libidn2.so.0
lib/libidn2.so.0 -> libidn2.so.0.4.0
lib/libidn2.so.0.4.0
lib/pkgconfig/libidn2.pc
share/info/libidn2.info
```

## Build recipe

`native/libidn2/libidn2.bzl` mirrors Spack's Autotools flow with an out-of-tree
`spack-build` directory and explicit dependency-prefix threading:

```text
CPPFLAGS=-I<libunistring>/include -I<libiconv>/include
LDFLAGS=-L<libunistring>/lib -L<libiconv>/lib \
  -Wl,-rpath,<libunistring>/lib -Wl,-rpath,<libiconv>/lib \
  -Wl,--disable-new-dtags
LIBS=-lunistring -liconv
../configure --prefix=<prefix>
make V=1
make install
find <prefix> -type f -name '*.la' -delete
```

The repository rule fetches the same upstream tarball by SHA256, refuses to run
unless the hermetic insula has set `VASO_IN_INSULA=1`, reads
`LIBUNISTRING_PREFIX` and `LIBICONV_PREFIX` only from mandatory Bazel
`*_prefix_file` labels, and validates both prefixes before configure.

The corresponding mechanism verifier is the Autotools dependency-prefix case in
`//tools:hermetic_native_deps_guard_test`; the live guard reports:

```text
native/libidn2/libidn2.bzl: autotools: LIBICONV_PREFIX, LIBUNISTRING_PREFIX
```

## Prefix and ABI gate target

`//synthetic:libidn2_abi_parity` compares the native prefix against the hermetic
Spack reference. The gate covers:

- 6 ABI-relevant layout entries under `include/` and `lib/`;
- SONAME parity: `libidn2.so.0`;
- exported dynamic symbol parity: 23 symbols;
- a downstream C link-and-run consumer using `idn2_lookup_u8`, linked against
  `libidn2`, `libunistring`, and `libiconv`.

The smoke target prints:

```text
libidn2:2.3.8:xn--bcher-kva.example
```

Current status: native and ABI-gated. The latest run passed
`//synthetic:use_libidn2_native`, `//tools:hermetic_native_deps_guard_test`, and
`//synthetic:libidn2_abi_parity` inside the CUDA insula.
