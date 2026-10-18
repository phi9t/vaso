# flex frontier recipe

## Position in the hillclimb

`flex` is the migrated py-torch frontier node immediately after `findutils`.
In the focused `SPACK_ROOT_PKG='flex@2.6.3'` graph it appears as:

```text
21  flex  2.6.3  autotools
```

The focused graph keeps the build-only tool chain explicit:

```text
6   diffutils  3.12    autotools
9   m4         1.4.21  autotools
10  bison      3.8.2   autotools
20  findutils  4.10.0  autotools
21  flex       2.6.3   autotools
```

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/flex/package.py
```

Source provenance from the Spack recipe:

- package class: `Flex(AutotoolsPackage)`
- version: `2.6.3`, marked preferred by the recipe
- upstream source URL:
  `https://github.com/westes/flex/releases/download/v2.6.3/flex-2.6.3.tar.gz`
- SHA256:
  `68b2742233e747c462f781462a2a1e299dc6207401dac8f0bbb316f48565c2aa`
- variants: `~nls +lex`
- concrete build system: Autotools
- package-specific patch surface: none for concrete `2.6.3`

The concrete dependency edges for `flex@2.6.3` are:

```text
build: bison, compiler-wrapper, diffutils, findutils, gcc, gmake, m4
link:  gcc-runtime, glibc
```

The recipe's `configure_args()` maps the concrete `~nls` variant to:

```text
--disable-nls
```

The `+lex` install hook adds POSIX compatibility symlinks when the targets
exist:

```text
bin/lex -> flex
lib/libl.a -> libfl.a
lib/libl.so -> libfl.so
```

## Build recipe

`native/flex/flex.bzl` mirrors the Spack Autotools flow:

```text
mkdir -p <src>/spack-build
cd <src>/spack-build
PATH=<bison-prefix>/bin:<diffutils-prefix>/bin:<findutils-prefix>/bin:<m4-prefix>/bin:$PATH
BISON=<bison-prefix>/bin/bison
M4=<m4-prefix>/bin/m4
../configure --prefix=<prefix> --disable-nls
make V=1
make install
find <prefix> -type f -name '*.la' -delete
install lex/libl compatibility symlinks
```

The repository rule fetches the same upstream tarball by SHA256 and refuses to
run unless the hermetic insula has set `VASO_IN_INSULA=1`. Build dependencies
are not discovered from the host. `bison_prefix_file`, `diffutils_prefix_file`,
`findutils_prefix_file`, and `m4_prefix_file` are mandatory Bazel labels, read
by the repository rule, validated by the shell build, and passed through the
Autotools build-tool channel (`PATH`, `BISON`, and `M4`).

The mechanism verifier covers this as an Autotools dependency case:

```text
native/flex/flex.bzl: autotools: BISON_PREFIX, DIFFUTILS_PREFIX, FINDUTILS_PREFIX, M4_PREFIX
```

## Prefix and behavior gate target

`//synthetic:flex_prefix_parity` compares the native prefix against the
hermetic Spack reference prefix supplied by `run.sh` through
`SPACK_FLEX_PREFIX`. The gate covers:

- executable layout and dynamic dependency parity for `bin/flex`, `bin/flex++`,
  and `bin/lex`;
- compatibility library layout for `lib/libfl.a`, `lib/libfl.so`,
  `lib/libl.a`, and `lib/libl.so`;
- static archive ABI parity for `lib/libfl.a` and `lib/libl.a`;
- installed info data for `share/info/flex.info`;
- matching `flex --version` and `lex --version` behavior;
- scanner generation from a deterministic fixture.

The smoke target prints:

```text
flex:2.6.3:ok
```

Current verification command:

```bash
SPACK_ROOT_PKG='flex@2.6.3' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_GRAPH_ONLY=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_FORCE_FETCH_REPOS='@flex_native' \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_flex_native //synthetic:flex_prefix_parity //tools:hermetic_native_deps_guard_test //tools:native_build_mechanism_guard_unit_test //tools:abi_parity_unit_test //tools:lean_font_resources_test' \
VASO_LOCK_OUT=/workspace/experiment/spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/flex_native_build_graph.json \
VASO_SPACK_TIMEOUT=1200 \
./run.sh
```

The command must be launched from the host only as the insula entrypoint. All
Spack operations and Bazel tests run inside the CUDA insula and use Bazel's
vendored `@spack_dist//:spack`; host Spack is not an input.
