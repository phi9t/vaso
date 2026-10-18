# bison frontier recipe

## Position in the hillclimb

`bison` is the migrated py-torch frontier node immediately after `m4`. In the
captured `SPACK_ROOT_PKG=py-torch` graph it appears as:

```text
29  bison  3.8.2  autotools  native; prefix parity green
```

The native verification run used the package-specific root:

```bash
SPACK_ROOT_PKG='bison' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_bison_native' \
VASO_SPACK_TIMEOUT=1800 \
VASO_LOCK_OUT=/workspace/experiment/bison_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/bison_build_graph.json \
VASO_FORCE_FETCH_REPOS='@bison_native' \
./run.sh
```

That command seats the CUDA insula, runs Bazel's vendored
`@spack_dist//:spack`, applies the `native_overrides.json` flip to
`@bison_native//:lib`, and runs `//synthetic:use_bison_native` plus
`//synthetic:bison_prefix_parity` inside the insula.

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/bison/package.py
```

Source provenance from the Spack recipe:

- package class: `Bison(AutotoolsPackage, GNUMirrorPackage)`
- version: `3.8.2`
- upstream source URL: `https://ftpmirror.gnu.org/bison/bison-3.8.2.tar.gz`
- SHA256:
  `06c9e13bdf7eb24d4ceb6b59205a4f67c2c7e7213119644430fe82fbd14a0abb`
- variant: `color`, default `False`
- provides: `yacc`
- build directory: `spack-build`
- concrete dependency edges: `m4@1.4.6:` as a build/run tool and `diffutils`
  as a build tool, plus toolchain nodes
- package-specific patch surface: none for concrete `3.8.2`

The hermetic Spack build log shows the standard Autotools phases:

```text
autoreconf
<spack-stage>/spack-src/configure --prefix=<bison-prefix>
make V=1
make install
```

The Spack build environment pins the parser macro processor explicitly:

```text
M4=<m4-prefix>/bin/m4
PATH=<diffutils-prefix>/bin:<gmake-prefix>/bin:<m4-prefix>/bin:...
```

The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/bison-3.8.2-4qaumm37uog2t7mfl4zmq6x2cwtk3cwx
```

The ABI/behavior-relevant installed surface for this migration is:

```text
bin/bison
bin/yacc
lib/liby.a
share/aclocal/bison-i18n.m4
share/bison/skeletons/yacc.c
share/bison/m4sugar/m4sugar.m4
share/info/bison.info
share/man/man1/bison.1
share/man/man1/yacc.1
```

## Build recipe

`native/bison/bison.bzl` mirrors Spack's out-of-tree Autotools flow:

```text
mkdir -p <src>/spack-build
cd <src>/spack-build
PATH=<diffutils-prefix>/bin:<m4-prefix>/bin:$PATH
M4=<m4-prefix>/bin/m4
../configure --prefix=<prefix>
make V=1
make install
find <prefix> -type f -name '*.la' -delete
```

The repository rule fetches the same upstream tarball by SHA256 and refuses to
run unless the hermetic insula has set `VASO_IN_INSULA=1`. Its dependencies are
not discovered from the host: `diffutils_prefix_file` and `m4_prefix_file` are
mandatory Bazel labels, read by the repository rule, validated by the shell
build, and passed through the Autotools build-tool channel (`PATH` and
`M4=<m4-prefix>/bin/m4`). The corresponding mechanism verifier is
`//tools:hermetic_native_deps_guard_test`, which reports:

```text
native/bison/bison.bzl: autotools: DIFFUTILS_PREFIX, M4_PREFIX
```

## Prefix and behavior gate target

`//synthetic:bison_prefix_parity` compares the native prefix against the
hermetic Spack reference. `bison` emits an executable/tool prefix and static
`liby.a`; there is no shared-library SONAME surface. The gate covers:

- nine-entry layout parity for `bin/bison`, `bin/yacc`, `lib/liby.a`, and
  selected aclocal/skeleton/info/man data;
- static archive ABI parity for `lib/liby.a`: the raw archive SHA256 differs,
  but the member set (`main.o`, `yyerror.o`) and globally defined symbol set
  match exactly;
- exact SHA256 parity for `share/aclocal/bison-i18n.m4`,
  `share/bison/skeletons/yacc.c`, `share/bison/m4sugar/m4sugar.m4`,
  `share/info/bison.info`, `share/man/man1/bison.1`, and
  `share/man/man1/yacc.1`;
- executable dynamic dependency parity for `bin/bison` and `bin/yacc`;
- matching `bison --version` output;
- matching parser-generation return code/stdout/stderr for a deterministic
  grammar.

The smoke target prints:

```text
bison:3.8.2:ok
```

Current status: native and prefix/behavior-gated. The latest run passed
`//synthetic:use_bison_native`, `//tools:hermetic_native_deps_guard_test`, and
`//synthetic:bison_prefix_parity` inside the CUDA insula.
