# findutils frontier recipe

## Position in the hillclimb

`findutils` is the migrated py-torch frontier node immediately after
`elfutils`. In the captured lean `SPACK_ROOT_PKG=py-torch` graph it appears as:

```text
78  findutils  4.10.0  autotools
```

The focused reference graph for `SPACK_ROOT_PKG='findutils@4.10.0'` ends with:

```text
15  tar       autotools
16  gettext   autotools
17  findutils autotools
```

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path in the Spack-installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/findutils-4.10.0-di23jlm22xyenz7iqn4cwtuoy5yrsh46/.spack/repos/spack_repo/builtin/packages/findutils/package.py
```

Source provenance from that recipe:

- package class: `Findutils(AutotoolsPackage, GNUMirrorPackage)`
- version: `4.10.0`
- upstream source URL: `https://ftpmirror.gnu.org/findutils/findutils-4.10.0.tar.xz`
- SHA256:
  `1387e0b67ff247d2abde998f90dfbf70c1491391a59ddfecb8ae698789f0a4f5`
- build directory: `spack-build`
- concrete patch: `nonnull.patch`
- patch SHA256:
  `440b9543365b4692a2e6e0b5674809659846658d34d1dfc542c4397c8d668b92`

The concrete `findutils@4.10.0` node has only toolchain/link runtime deps plus
one build-tool dependency:

```text
build: compiler-wrapper, gcc, gettext, gmake
link: gcc-runtime, glibc
```

The hermetic Spack build log shows:

```text
Applied patch .../findutils/nonnull.patch
findutils: Executing phase: 'autoreconf'
findutils: Executing phase: 'configure'
<spack-stage>/spack-src/configure --prefix=<findutils-prefix>
<gmake-prefix>/bin/make V=1
make install
```

The reference prefix surface is executable-only:

```text
bin/find
bin/locate
bin/updatedb
bin/xargs
libexec/frcode
share/info/find-maint.info
share/info/find.info
share/man/man1/find.1
share/man/man1/locate.1
share/man/man1/updatedb.1
share/man/man1/xargs.1
share/man/man5/locatedb.5
share/locale/*/LC_MESSAGES/findutils.mo
var/
```

## Build recipe

`native/findutils/findutils.bzl` mirrors the Spack Autotools flow:

```text
apply nonnull.patch equivalent
../configure --prefix=<prefix>
make V=1
make install
```

The repository rule fetches the same upstream tarball by SHA256, creates the
out-of-tree `spack-build` directory, and refuses to run unless the hermetic
insula has set `VASO_IN_INSULA=1`. `gettext` is consumed only through
`@gettext_native//:prefix_path.txt`; the build script verifies
`GETTEXT_PREFIX/bin/msgfmt`, prepends the Bazel-built gettext `bin` directory,
and pins `MSGFMT`.

The mechanism verifier covers this as the Autotools/build-tool dependency case:
the rule is insula-gated, has a mandatory `gettext_prefix_file` label, reads it
with `repository_ctx.read`, passes `GETTEXT_PREFIX` through
`repository_ctx.execute`, and exposes the dependency through a mechanism-native
tool variable and `PATH` channel.

## Prefix and behavior gate target

`//synthetic:findutils_prefix_parity` compares the native prefix against the
hermetic Spack reference:

```text
/vaso/cache/spack/opt/spack/linux-icelake/findutils-4.10.0-di23jlm22xyenz7iqn4cwtuoy5yrsh46
```

The gate covers:

- executable layout and dynamic dependency parity for `find`, `locate`,
  `updatedb`, `xargs`, and `libexec/frcode`;
- installed info and manpage files;
- `find --version` and `locate --version`;
- `find` over a small directory tree;
- `xargs` argument grouping behavior.

Verified status: native and parity-gated with the focused command below:

```bash
SPACK_ROOT_PKG='findutils@4.10.0' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_FORCE_FETCH_REPOS='@findutils_native' \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_findutils_native //synthetic:findutils_prefix_parity //tools:hermetic_native_deps_guard_test //tools:native_build_mechanism_guard_unit_test //tools:abi_parity_unit_test' \
VASO_SPACK_TIMEOUT=900 \
./run.sh
```

Observed evidence from the hermetic CUDA insula run:

- `rootfs mode: cuda-bundle`
- `//synthetic:use_findutils_native` passed and printed `findutils:ok`
- `//synthetic:findutils_prefix_parity` passed with `ok: true`
- parity compared against the hermetic Spack reference prefix above, not an
  ambient host Spack prefix
- `//tools:hermetic_native_deps_guard_test` included
  `native/findutils/findutils.bzl: autotools: GETTEXT_PREFIX`
