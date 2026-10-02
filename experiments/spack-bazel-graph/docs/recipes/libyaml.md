# libyaml frontier recipe

## Position in the hillclimb

`libyaml` is the migrated py-torch frontier node after `libidn2`. In the
captured `SPACK_ROOT_PKG=py-torch` graph it appears as:

```text
26  libyaml  0.2.5  autotools  native; ABI parity green
```

The native verification run used the package-specific root:

```bash
SPACK_ROOT_PKG='libyaml' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_libyaml_native' \
VASO_SPACK_TIMEOUT=1800 \
VASO_LOCK_OUT=/workspace/experiment/libyaml_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/libyaml_build_graph.json \
./run.sh
```

That command seats the CUDA insula, runs Bazel's vendored
`@spack_dist//:spack`, applies the `native_overrides.json` flip to
`@libyaml_native//:lib`, and runs `//synthetic:use_libyaml_native` plus
`//synthetic:libyaml_abi_parity` inside the insula.

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/libyaml/package.py
```

Source provenance from the Spack recipe:

- package class: `Libyaml(AutotoolsPackage)`
- version: `0.2.5`
- upstream source URL:
  `https://pyyaml.org/download/libyaml/yaml-0.2.5.tar.gz`
- SHA256:
  `c642ae9b75fee120b2d96c712538bd2cf283228d2337df2cf2988e3c02678ef4`
- concrete dependency edges: toolchain only
- package-specific patch surface: none for concrete `0.2.5`

The hermetic Spack build log shows the standard Autotools phases:

```text
autoreconf
<spack-src>/configure --prefix=<libyaml-prefix>
make V=1
make install
find <libyaml-prefix> -name '*.la'
```

The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/libyaml-0.2.5-u5wr3urg73gjnyysrxmcd4t6dmkyifhn
```

The prefix contains the public C ABI surface:

```text
include/yaml.h
lib/libyaml.a
lib/libyaml.so -> libyaml-0.so.2.0.9
lib/libyaml-0.so.2 -> libyaml-0.so.2.0.9
lib/libyaml-0.so.2.0.9
lib/pkgconfig/yaml-0.1.pc
```

## Build recipe

`native/libyaml/libyaml.bzl` mirrors Spack's in-source Autotools flow:

```text
./configure --prefix=<prefix>
make V=1
make install
find <prefix> -type f -name '*.la' -delete
```

The repository rule fetches the same upstream tarball by SHA256 and refuses to
run unless the hermetic insula has set `VASO_IN_INSULA=1`. `libyaml` has no
non-toolchain dependency prefixes, so its corresponding mechanism verifier is
the Autotools/no-dependency case in `//tools:hermetic_native_deps_guard_test`;
the live guard reports:

```text
native/libyaml/libyaml.bzl: autotools: no dep prefixes
```

## Prefix and ABI gate target

`//synthetic:libyaml_abi_parity` compares the native prefix against the
hermetic Spack reference. The gate covers:

- 6 ABI-relevant layout entries under `include/` and `lib/`;
- SONAME parity: `libyaml-0.so.2`;
- exported dynamic symbol parity: 58 symbols;
- a downstream C link-and-run consumer that parses `answer: 42` with
  `yaml_parser_load` and verifies a mapping document root.

The smoke target prints:

```text
libyaml:0.2.5:mapping
```

Current status: native and ABI-gated. The latest run passed
`//synthetic:use_libyaml_native`, `//tools:hermetic_native_deps_guard_test`, and
`//synthetic:libyaml_abi_parity` inside the CUDA insula.
