# UnZip frontier recipe

## Position in the hillclimb

`unzip` is the first not-yet-native py-torch frontier node after the already
migrated `nghttp2`, `readline`, and `gdbm` nodes. In the captured
`SPACK_ROOT_PKG=py-torch` graph it appears as:

```text
40  unzip  6.0  makefile  native; prefix parity green
```

The package-local verification run used:

```bash
SPACK_ROOT_PKG='unzip@6.0' \
VASO_NATIVE=1 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_unzip_native //tools:hermetic_native_deps_guard_test //synthetic:unzip_prefix_parity' \
VASO_LOCK_OUT=/workspace/experiment/unzip_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/unzip_build_graph.json \
VASO_FORCE_FETCH_REPOS='@unzip_native' \
./run.sh
```

That command seats the CUDA insula, forces the Bazel-owned native repository
fetch inside the insula, runs Bazel's vendored `@spack_dist//:spack`, applies
the `native_overrides.json` flip to `@unzip_native//:lib`, and runs the native
smoke, makefile hermetic-deps guard, and `//synthetic:unzip_prefix_parity`
inside the same insula.

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/unzip/package.py
```

The Spack 1.2.2 tool materializes the builtin package repository from
`spack/spack-packages` commit
`d4f7c711a6a42f1c4d551c8fd10fce9a11340a81` (`v2026.06.0`). The native rule
uses immutable raw URLs from that commit for the two Spack-local patches, plus
the Fedora security patch URLs and SHA256 values from the same recipe.

Source provenance from the Spack recipe:

- package class: `Unzip(MakefilePackage)`
- upstream source URL:
  `https://downloads.sourceforge.net/infozip/unzip60.tar.gz`
- source SHA256:
  `036d96991646d0449ed0aa952e4fbe21b476ce994abc276e49d30e686708bd37`
- dependencies: C compiler wrapper and `gmake` only; no non-toolchain prefix
  dependencies

The concrete `unzip@6.0` node has:

```text
build: compiler-wrapper, gcc, gmake
link: gcc-runtime, glibc
```

The hermetic Spack build log shows the Makefile flow:

```text
make -f unix/Makefile \
  'LOC=-Wno-error=implicit-function-declaration -Wno-error=implicit-int -DLARGE_FILE_SUPPORT' \
  generic
make -f unix/Makefile \
  'LOC=-Wno-error=implicit-function-declaration -Wno-error=implicit-int -DLARGE_FILE_SUPPORT' \
  prefix=<unzip-prefix> install
```

The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/unzip-6.0-hd67fyk7ek2v67eovmlzusxulivkac47
```

The ABI/behavior-relevant installed surface for this migration is:

```text
bin/funzip
bin/unzip
bin/unzipsfx
bin/zipgrep
bin/zipinfo
man/man1/funzip.1
man/man1/unzip.1
man/man1/unzipsfx.1
man/man1/zipgrep.1
man/man1/zipinfo.1
```

## Build recipe

`native/unzip/unzip.bzl` mirrors Spack's Makefile build:

```text
download unzip60.tar.gz
apply configure-cflags.patch from pinned spack/spack-packages commit
apply strip.patch from pinned spack/spack-packages commit
apply the Fedora security patches listed in the Spack recipe
make -f unix/Makefile LOC="<Spack LOC flags>" generic
make -f unix/Makefile LOC="<Spack LOC flags>" prefix=<prefix> install
```

The native rule requires `VASO_IN_INSULA=1` at repository evaluation and in the
generated build script. It has no non-toolchain dependency prefixes, and the
corresponding build-mechanism verifier reports:

```text
native/unzip/unzip.bzl: makefile: no dep prefixes
```

## Prefix parity gate

`//synthetic:unzip_prefix_parity` compares the native prefix against the
hermetic Spack reference. The gate covers:

- exact layout parity for the 10 installed executable/manpage entries;
- dynamic dependency parity for `funzip`, `unzip`, `unzipsfx`, and `zipinfo`;
- script classification parity for `zipgrep`;
- byte-identical selected manpages;
- matching `unzip -v`, `zipinfo -h`, and invalid-archive `unzip -t` behavior.

The smoke target builds a deterministic ZIP archive with Python inside the
test sandbox, extracts it with the native prefix, and verifies `zipinfo` and
`zipgrep` behavior. It prints:

```text
unzip:6.0:ok
```

Current status: native and prefix-gated. The latest run used `rootfs mode:
cuda-bundle`, reported `hermetic spack (Bazel-owned) version: 1.2.2`, flipped
`spack_unzip` to `build: native`, and passed `//synthetic:use_unzip_native`,
`//tools:hermetic_native_deps_guard_test`, and
`//synthetic:unzip_prefix_parity` inside the CUDA insula.
