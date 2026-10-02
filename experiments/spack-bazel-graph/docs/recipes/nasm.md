# nasm frontier recipe

## Position in the hillclimb

`nasm` is the migrated py-torch frontier node immediately after `bison`. In the
captured `SPACK_ROOT_PKG=py-torch` graph it appears as:

```text
30  nasm  2.16.03  autotools  native; prefix parity green
```

The native verification run used the package-specific root:

```bash
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
SPACK_ROOT_PKG='nasm' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_nasm_native' \
VASO_SPACK_TIMEOUT=1800 \
VASO_LOCK_OUT=/workspace/experiment/nasm_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/nasm_build_graph.json \
VASO_FORCE_FETCH_REPOS='@nasm_native' \
./run.sh
```

That command seats the CUDA insula, forces the Bazel-owned native repository
fetch inside the insula, runs Bazel's vendored `@spack_dist//:spack`, applies
the `native_overrides.json` flip to `@nasm_native//:lib`, and runs
`//synthetic:use_nasm_native` plus `//synthetic:nasm_prefix_parity` inside the
same insula.

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/nasm/package.py
```

Source provenance from the Spack recipe:

- package class: `Nasm(AutotoolsPackage, Package)`
- homepage: `https://www.nasm.us`
- upstream source URL pattern:
  `https://www.nasm.us/pub/nasm/releasebuilds/2.14.02/nasm-2.14.02.tar.gz`
- concrete version: `2.16.03`
- concrete source URL:
  `https://www.nasm.us/pub/nasm/releasebuilds/2.16.03/nasm-2.16.03.tar.gz`
- SHA256:
  `5bc940dd8a4245686976a8f7e96ba9340a0915f2d5b88356874890e207bdb581`
- build system on Linux: Spack `autotools`
- package-specific patch surface: the recipe patch applies only
  `when="@2.13.03 %gcc@8:"`, so no patch applies to this concrete `2.16.03`
  node

The concrete `nasm@2.16.03` node has only toolchain dependencies:

```text
build: compiler-wrapper, gcc, gmake
link: gcc-runtime, glibc
```

The hermetic Spack build log shows the standard release-tarball Autotools
phases:

```text
autoreconf
<spack-stage>/spack-src/configure --prefix=<nasm-prefix>
make V=1
make install
```

The build log also notes missing optional documentation tools (`nroff`,
`asciidoc`, and `xmlto`). This is expected for the release tarball path because
the installed manpages are shipped with the source archive.

The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/nasm-2.16.03-icn6aoixxxwx6yjt5d26fdjnvxt7cocy
```

The ABI/behavior-relevant installed surface for this migration is:

```text
bin/nasm
bin/ndisasm
share/man/man1/nasm.1
share/man/man1/ndisasm.1
```

## Build recipe

`native/nasm/nasm.bzl` mirrors Spack's out-of-tree Autotools flow:

```text
mkdir -p <src>/spack-build
cd <src>/spack-build
../configure --prefix=<prefix>
make V=1
make install
find <prefix> -type f -name '*.la' -delete
```

The repository rule fetches the same upstream tarball by SHA256 and refuses to
run unless the hermetic insula has set `VASO_IN_INSULA=1`.

`nasm` has no non-toolchain dependency prefixes, so the corresponding
hermetic-deps verifier for this build mechanism is the Autotools/no-dependency
case. `//tools:hermetic_native_deps_guard_test` confirms the rule is classified
as `autotools`, the build action is insula-gated, and no dependency prefix is
discovered from the host:

```text
native/nasm/nasm.bzl: autotools: no dep prefixes
```

Autotools packages that do consume prefixes are verified by the same guard
through explicit `*_prefix_file` labels, shell-side prefix existence checks,
and `CPPFLAGS`/`LDFLAGS`/`LIBS`/`PKG_CONFIG_PATH`, `--with-*`, pinned tool
variables, or native-prefix `PATH` channels.

## Prefix and behavior gate target

`//synthetic:nasm_prefix_parity` compares the native prefix against the
hermetic Spack reference. `nasm` emits an executable/tool prefix and no public C
library ABI. The gate covers:

- exact four-file layout parity for `bin/nasm`, `bin/ndisasm`, and the two
  selected manpages;
- exact SHA256 parity for `share/man/man1/nasm.1` and
  `share/man/man1/ndisasm.1`;
- executable dynamic dependency parity for `bin/nasm` and `bin/ndisasm`;
- matching `nasm -v` stdout;
- matching `ndisasm -v` stderr;
- matching assembly return code/stdout/stderr for a deterministic x86-64
  source file.

The smoke target assembles and disassembles a tiny x86-64 program from the
native prefix and prints:

```text
nasm:2.16.03:ok
```

Current status: native and prefix/behavior-gated. The latest run used
`rootfs mode: cuda-bundle`, reported `hermetic spack (Bazel-owned) version:
1.2.2`, and passed `//synthetic:use_nasm_native`,
`//tools:hermetic_native_deps_guard_test`, and
`//synthetic:nasm_prefix_parity` inside the CUDA insula.
