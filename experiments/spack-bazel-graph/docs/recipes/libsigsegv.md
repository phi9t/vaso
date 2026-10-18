# libsigsegv frontier recipe

## Position in the hillclimb

`libsigsegv` is the migrated py-torch frontier node after gzip. In the captured
`SPACK_ROOT_PKG=py-torch` graph it appears as:

```text
23  libsigsegv  2.15  autotools  native; ABI parity green
```

The native verification run used the package-specific root:

```bash
SPACK_ROOT_PKG='libsigsegv@2.15' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_libsigsegv_native' \
VASO_SPACK_TIMEOUT=1800 \
VASO_LOCK_OUT=/workspace/experiment/libsigsegv_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/libsigsegv_build_graph.json \
./run.sh
```

That command seats the CUDA insula, runs Bazel's vendored
`@spack_dist//:spack`, applies the `native_overrides.json` flip to
`@libsigsegv_native//:lib`, and runs `//synthetic:use_libsigsegv_native` plus
`//synthetic:libsigsegv_abi_parity` inside the insula.

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/libsigsegv/package.py
```

Source provenance from the Spack recipe:

- package class: `Libsigsegv(AutotoolsPackage, GNUMirrorPackage)`
- version: `2.15`
- upstream source URL:
  `https://ftpmirror.gnu.org/libsigsegv/libsigsegv-2.15.tar.gz`
- SHA256:
  `036855660225cb3817a190fc00e6764ce7836051bacb48d35e26444b8c1729d9`
- configure args: `--enable-shared`
- patch surface: `new_config_guess.patch` applies only to `@2.10`, so it is
  not applied for the concrete `2.15` node

The concrete `libsigsegv@2.15` node has only toolchain dependencies:

```text
build: compiler-wrapper, gcc, gmake
link: gcc-runtime, glibc
```

The hermetic Spack build log shows Spack filtering the release `configure`
script for libtool portability, then running:

```text
<spack-stage>/spack-src/configure --prefix=<libsigsegv-prefix> --enable-shared
```

followed by `make V=1` and `make install`.

## Build recipe

`native/libsigsegv/libsigsegv.bzl` mirrors the Spack Autotools flow:

```text
./configure --prefix=<prefix> --enable-shared
make V=1
make install
```

The repository rule fetches the same upstream tarball by SHA256, builds inside
the extracted source tree, and refuses to run unless the hermetic insula has set
`VASO_IN_INSULA=1`. It removes libtool `.la` files after install, matching the
Spack-emitted prefix.

`libsigsegv` has no non-toolchain dependency prefixes, so the corresponding
mechanism verifier is the Autotools/no-dependency case in
`//tools:hermetic_native_deps_guard_test`: the rule is classified as Autotools
and insula-gated, with no host Spack or host dependency discovery.

## Prefix and ABI gate target

`//synthetic:libsigsegv_abi_parity` compares the native prefix against the
hermetic Spack reference:

```text
/vaso/cache/spack/opt/spack/linux-icelake/libsigsegv-2.15-r4a3kvxxanxl6ca2iyz3hlrhjjklzvaz
```

The gate covers:

- `include/sigsegv.h`;
- `lib/libsigsegv.a` and the `libsigsegv.so -> libsigsegv.so.2 ->
  libsigsegv.so.2.0.8` chain;
- SONAME parity: `libsigsegv.so.2`;
- exported dynamic symbol parity: 12 symbols;
- a downstream C link-and-run consumer that installs/deinstalls a handler and
  checks `libsigsegv_version == LIBSIGSEGV_VERSION`.

Current status: native and ABI-gated. The latest run passed
`//synthetic:use_libsigsegv_native` and `//synthetic:libsigsegv_abi_parity`
inside the CUDA insula.
