# util-macros frontier recipe

## Position in the hillclimb

`util-macros@1.20.2` is the next migrated py-torch frontier node after
`unzip@6.0`. In the captured `SPACK_ROOT_PKG=py-torch` graph it appears as:

```text
42  util-macros  1.20.2  autotools  native; prefix parity green
```

`util-linux-uuid@2.41` at topo index 41 was already native, so this slice
advances the next not-yet-native py-torch node. The package-local graph was
captured inside the CUDA insula with Bazel's vendored Spack:

```bash
SPACK_ROOT_PKG='util-macros@1.20.2' \
VASO_NATIVE=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_LOCK_OUT=/workspace/experiment/util_macros_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/util_macros_build_graph.json \
VASO_SPACK_TIMEOUT=1200 \
./run.sh
```

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/util_macros/package.py
```

Source provenance from the Spack recipe:

- package class: `UtilMacros(AutotoolsPackage, XorgPackage)`
- version: `1.20.2`
- upstream source URL:
  `https://xorg.freedesktop.org/archive/individual/util/util-macros-1.20.2.tar.xz`
- SHA256:
  `9ac269eba24f672d7d7b3574e4be5f333d13f04a7712303b1821b2a51ac82e8e`
- patch surface: none
- dependent build environment: appends `share/aclocal` to `ACLOCAL_PATH`

The concrete `util-macros@1.20.2` node has only the `gmake` build dependency.
There are no non-toolchain link/include prefixes.

## Build recipe

Spack uses the standard Autotools phases:

```text
./configure --prefix=<prefix>
make V=1
make install
```

The observed hermetic Spack install produces a data-only prefix:

```text
share/aclocal/xorg-macros.m4
share/pkgconfig/xorg-macros.pc
share/util-macros/INSTALL
```

`native/util_macros/util_macros.bzl` mirrors that flow with the same source
tarball and SHA256. The repository rule refuses to run unless the hermetic
insula has set `VASO_IN_INSULA=1`, then builds into its Bazel-owned `prefix/`
directory and removes any libtool archive files if a future archive emits them.

`util-macros` has no non-toolchain dependency prefixes, so the corresponding
hermetic-deps verifier for this build mechanism is the Autotools/no-dependency
case. `//tools:hermetic_native_deps_guard_test` confirms the rule is classified
as:

```text
native/util_macros/util_macros.bzl: autotools: no dep prefixes
```

This satisfies the mechanism-specific hermetic dependency check for this
package: the build action is insula-gated and does not discover dependency
prefixes from the host. Autotools packages that consume prefixes are checked by
the same guard through mandatory `*_prefix_file` labels, shell-side prefix
existence checks, and explicit compiler/linker/pkg-config/configure channels.

## Prefix and behavior gate target

`//synthetic:util_macros_prefix_parity` compares the native prefix against the
hermetic Spack reference:

```text
/vaso/cache/spack/opt/spack/linux-icelake/util-macros-1.20.2-xjy7uxzigehilbwvq4i3xhsuph4x3ad7
```

The gate covers:

- the three-file macro/pkg-config/install-doc layout;
- byte-identical `share/aclocal/xorg-macros.m4`;
- prefix-normalized `share/pkgconfig/xorg-macros.pc`;
- byte-identical `share/util-macros/INSTALL`;
- an empty ELF ABI axis, as expected for a data-only prefix.

`//synthetic:use_util_macros_native` also validates that downstream users can
find the native prefix and that it exposes the expected macro/pkg-config files,
printing `util-macros:1.20.2:ok`.

Current status: native and parity-gated. The latest focused run passed inside
the CUDA insula:

```bash
VASO_NATIVE=1 \
SPACK_ROOT_PKG='util-macros@1.20.2' \
VASO_LOCK_OUT=/workspace/experiment/util_macros_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/util_macros_build_graph.json \
VASO_FORCE_FETCH_REPOS='@util_macros_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_util_macros_native //tools:hermetic_native_deps_guard_test //synthetic:util_macros_prefix_parity' \
VASO_SPACK_TIMEOUT=1200 \
./run.sh
```
