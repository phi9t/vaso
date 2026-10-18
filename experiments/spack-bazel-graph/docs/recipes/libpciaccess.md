# libpciaccess frontier recipe

## Position in the hillclimb

`libpciaccess@0.17` is the next migrated py-torch frontier node after
`fontsproto@2.1.3`. In the captured `SPACK_ROOT_PKG=py-torch` graph it appears
as:

```text
44  libpciaccess  0.17  autotools  native; ABI parity green
```

The package-local graph was captured inside the CUDA insula with Bazel's
vendored Spack:

```bash
SPACK_ROOT_PKG='libpciaccess@0.17' \
VASO_NATIVE=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_LOCK_OUT=/workspace/experiment/libpciaccess_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/libpciaccess_build_graph.json \
VASO_SPACK_TIMEOUT=1200 \
./run.sh
```

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/libpciaccess/package.py
```

Source provenance from the Spack recipe:

- package class: `Libpciaccess(AutotoolsPackage, XorgPackage)`
- version: `0.17`
- upstream source URL:
  `https://xorg.freedesktop.org/archive/individual/lib/libpciaccess-0.17.tar.gz`
- SHA256:
  `bf6985a77d2ecb00e2c79da3edfb26b909178ffca3f2e9d14ed0620259ab733b`
- patch surface: `nvhpc.patch` only when `%nvhpc`; not applied for the current
  GCC concrete spec
- configure args: none for this GCC/Linux concrete spec

The concrete `libpciaccess@0.17` node depends on `pkgconf` and `util-macros` as
build-time prefixes, plus the compiler/toolchain nodes owned by the insula.

## Build recipe

Spack uses the standard Autotools phases:

```text
autoreconf
./configure --prefix=<prefix>
make V=1
make install
```

The observed hermetic Spack install produces:

```text
include/pciaccess.h
lib/libpciaccess.a
lib/libpciaccess.so
lib/libpciaccess.so.0
lib/libpciaccess.so.0.11.1
lib/pkgconfig/pciaccess.pc
```

`native/libpciaccess/libpciaccess.bzl` mirrors the configured build with the
same source tarball and SHA256. The repository rule refuses to run unless the
hermetic insula has set `VASO_IN_INSULA=1`, reads the native `pkgconf` and
`util-macros` prefixes through mandatory Bazel `*_prefix_file` attrs, validates
those prefixes in the build script, pins `PKG_CONFIG`, and sets `ACLOCAL_PATH`
from the native `util-macros` prefix before configure.

This is the Autotools dependency-prefix verifier case:

```text
native/libpciaccess/libpciaccess.bzl: autotools: PKGCONF_PREFIX, UTIL_MACROS_PREFIX
```

`//tools:hermetic_native_deps_guard_test` checks that the Autotools dependency
prefixes enter through Bazel-owned files and mechanism-specific channels rather
than host discovery.

## Prefix and behavior gates

`//synthetic:libpciaccess_abi_parity` compares the native prefix against the
hermetic Spack reference:

```text
/vaso/cache/spack/opt/spack/linux-icelake/libpciaccess-0.17-e6mggyjmjm4irx6j2djubhoj5j2pshuj
```

The gate covers:

- the six-entry installed layout;
- byte-identical `include/pciaccess.h`;
- prefix-normalized `lib/pkgconfig/pciaccess.pc`;
- SONAME parity for `lib/libpciaccess.so.0.11.1`
  (`libpciaccess.so.0`);
- exported dynamic symbol parity for 58 symbols;
- downstream link-and-run parity through deterministic null-iterator API
  behavior.

`//synthetic:use_libpciaccess_native` validates that a downstream consumer can
compile, link, and run against the native prefix, printing
`libpciaccess:0.17:null-iterator-ok`.

Current status: native and ABI-gated. The latest focused run passed inside the
CUDA insula:

```bash
VASO_NATIVE=1 \
SPACK_ROOT_PKG='libpciaccess@0.17' \
VASO_LOCK_OUT=/workspace/experiment/libpciaccess_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/libpciaccess_build_graph.json \
VASO_FORCE_FETCH_REPOS='@libpciaccess_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_libpciaccess_native //tools:hermetic_native_deps_guard_test //synthetic:libpciaccess_abi_parity' \
VASO_SPACK_TIMEOUT=1200 \
./run.sh
```
