# libunistring frontier recipe

## Position in the hillclimb

`libunistring` is the migrated py-torch frontier node after `libsigsegv`. In
the captured `SPACK_ROOT_PKG=py-torch` graph it appears as:

```text
24  libunistring  1.4.2  autotools  native; ABI parity green
```

The native verification run used the package-specific root:

```bash
SPACK_ROOT_PKG='libunistring@1.4.2' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_libunistring_native' \
VASO_SPACK_TIMEOUT=1800 \
VASO_LOCK_OUT=/workspace/experiment/libunistring_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/libunistring_build_graph.json \
./run.sh
```

That command seats the CUDA insula, runs Bazel's vendored
`@spack_dist//:spack`, applies the `native_overrides.json` flip to
`@libunistring_native//:lib`, and runs `//synthetic:use_libunistring_native`
plus `//synthetic:libunistring_abi_parity` inside the insula.

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/libunistring/package.py
```

Source provenance from the Spack recipe:

- package class: `Libunistring(AutotoolsPackage, GNUMirrorPackage)`
- version: `1.4.2`
- upstream source URL:
  `https://ftpmirror.gnu.org/libunistring/libunistring-1.4.2.tar.xz`
- SHA256:
  `5b46e74377ed7409c5b75e7a96f95377b095623b689d8522620927964a41499c`
- concrete dependency edge: `depends_on("iconv")`
- package-specific patch surface: none for concrete `1.4.2`

The concrete `libunistring@1.4.2` node has toolchain build/link edges plus a
build+link edge on `libiconv`. The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/libunistring-1.4.2-omh4phdbb66asopo24cwzb462n5ghlmn
```

## Build recipe

`native/libunistring/libunistring.bzl` mirrors the Spack Autotools flow while
threading the Bazel-built libiconv prefix through the mechanism-specific
dependency channel:

```text
CPPFLAGS=-I<libiconv>/include
LDFLAGS=-L<libiconv>/lib -Wl,-rpath,<libiconv>/lib -Wl,--disable-new-dtags
LIBS=-liconv
./configure --prefix=<prefix> --enable-shared --enable-static \
  --with-libiconv-prefix=<libiconv>
make V=1
make install
```

The repository rule fetches the same upstream tarball by SHA256, refuses to run
unless the hermetic insula has set `VASO_IN_INSULA=1`, reads
`LIBICONV_PREFIX` only from the mandatory `libiconv_prefix_file` Bazel label,
and validates that prefix before configure. It removes libtool `.la` files
after install, matching the Spack-emitted prefix.

The corresponding mechanism verifier is the Autotools dependency-prefix case in
`//tools:hermetic_native_deps_guard_test`; the live guard reports:

```text
native/libunistring/libunistring.bzl: autotools: LIBICONV_PREFIX
```

## Prefix and ABI gate target

`//synthetic:libunistring_abi_parity` compares the native prefix against the
hermetic Spack reference. The gate covers:

- 24 ABI-relevant layout entries under `include/` and `lib/`;
- SONAME parity: `libunistring.so.5`;
- exported dynamic symbol parity: 749 symbols;
- a downstream C link-and-run consumer using `u8_strlen` and `u8_check`, linked
  against `libunistring` plus `libiconv`.

The parity harness must pass `--include-dir include` for this package. The
default harness also adds nested include directories, and
`include/unistring/stdint.h` intentionally wraps `<stdint.h>`; adding
`include/unistring` to the compiler search path causes recursive header
shadowing for both the Spack and native prefixes.

Current status: native and ABI-gated. The latest run passed
`//synthetic:use_libunistring_native` and `//synthetic:libunistring_abi_parity`
inside the CUDA insula.
