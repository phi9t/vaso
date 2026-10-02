# coreutils frontier recipe

## Position in the hillclimb

`coreutils@9.10` is the migrated py-torch frontier node after already-native
`openssl@3.6.1`. In the captured `SPACK_ROOT_PKG=py-torch` graph, the nearby
entries are:

```text
55  libxcrypt  4.5.2   autotools  native; ABI parity green
56  openssl    3.6.1   generic    native; ABI parity green
57  coreutils  9.10    autotools  native; prefix parity green
58  cuda       12.9.1  generic    next frontier/rootfs SDK boundary
```

The package-local reference graph was captured and verified inside the CUDA
insula with Bazel's vendored Spack:

```bash
VASO_NATIVE=1 \
SPACK_ROOT_PKG='coreutils@9.10' \
VASO_LOCK_OUT=/workspace/experiment/coreutils_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/coreutils_build_graph.json \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//tools:hermetic_native_deps_guard_test //synthetic:use_coreutils_native //synthetic:coreutils_prefix_parity' \
VASO_SPACK_TIMEOUT=1200 \
VASO_FORCE_FETCH_REPOS='@coreutils_native' \
./run.sh
```

That run completed with `rootfs mode: cuda-bundle`, wrote a 14-package
`coreutils_spack_graph.lock.json`, and wrote a 25-node
`coreutils_build_graph.json` with `autotools=15` and `generic=10`.

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/coreutils/package.py
```

Source provenance from the Spack recipe:

- package class: `Coreutils(AutotoolsPackage, GNUMirrorPackage)`
- version: `9.10`
- upstream source URL:
  `https://ftpmirror.gnu.org/coreutils/coreutils-9.10.tar.xz`
- SHA256:
  `16535a9adf0b10037364e2d612aad3d9f4eca3a344949ced74d12faf4bd51d25`
- concrete variant: `gprefix=false`
- package dependency relevant to this native build:
  `openssl@3:` as a link dependency
- configure arguments: `--disable-nls`
- package-specific patches do not apply to `9.10`

The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/coreutils-9.10-qk3fh3wtnv3ufpruslhz6mchzm2ovb27
```

The generated lock keeps Coreutils' Spack DAG edge while flipping only the
provider:

```json
{
  "package": "coreutils",
  "version": "9.10",
  "build": "native",
  "native_prefix": "@coreutils_native//:lib",
  "link_deps": ["spack_openssl"],
  "link_libs": [],
  "include_dirs": []
}
```

## Build recipe

`native/coreutils/coreutils.bzl` mirrors Spack's Autotools flow:

```text
mkdir -p <src>/spack-build
cd <src>/spack-build
CPPFLAGS=-I<openssl-prefix>/include
LDFLAGS=-L<openssl-prefix>/lib64 -Wl,-rpath,<openssl-prefix>/lib64
LIBS=-lcrypto
../configure --prefix=<prefix> --disable-nls --with-openssl=yes
make V=1
make install
find <prefix> -type f -name '*.la' -delete
```

The repository rule fetches the same upstream tarball by SHA256 and refuses to
run unless the hermetic insula has set `VASO_IN_INSULA=1`. Its OpenSSL
dependency is not discovered from the host: `openssl_prefix_file` is a
mandatory Bazel label, read by the repository rule, validated by the shell
build, and passed through the Autotools compiler/linker channel.

This is the Autotools dependency-prefix verifier case:

```text
native/coreutils/coreutils.bzl: autotools: OPENSSL_PREFIX
```

`//tools:hermetic_native_deps_guard_test` checks that the OpenSSL prefix enters
through a Bazel-owned file and a mechanism-specific Autotools channel rather
than host discovery.

## Prefix and behavior gate target

`//synthetic:coreutils_prefix_parity` compares the native prefix against the
hermetic Spack reference. The gate covers:

- layout parity for selected executable and documentation paths:
  `bin/sha256sum`, `bin/sort`, `bin/realpath`, `bin/env`, `bin/printf`,
  `share/info/coreutils.info`, and representative manpages;
- executable NEEDED parity for the selected binaries, including
  `bin/sha256sum` linking against `libcrypto.so.3` through the OpenSSL DAG
  edge;
- prefix-normalized executable behavior for `sha256sum`, `sort`, `realpath`,
  and `env`/`printenv`.

The smoke target prints:

```text
coreutils:9.10:64:expected.txt:ok
```

Current status: native and prefix/behavior-gated. The latest focused run passed
inside the CUDA insula with `rootfs mode: cuda-bundle`, used Bazel-owned Spack
`1.2.2`, flipped `spack_coreutils` to `@coreutils_native//:lib`, and passed:

```text
//synthetic:spack_selfcheck
//tools:hermetic_spack_guard_test
//tools:spack_lock_test
//tools:build_graph_unit_test
//tools:native_build_mechanism_guard_unit_test
//tools:hermetic_native_deps_guard_test
//tools:spack_recipe_provenance_unit_test
//tools:pytorch_recipe_provenance_test
//native/pytorch:plan_test
//synthetic:use_coreutils_native
//synthetic:coreutils_prefix_parity
```
