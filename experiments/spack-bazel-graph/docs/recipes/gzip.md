# gzip frontier recipe

## Position in the hillclimb

`gzip` is the migrated py-torch frontier node immediately after gperf. In the
captured `SPACK_ROOT_PKG=py-torch` graph it appears as:

```text
15  gzip  1.14  autotools  native; prefix parity green
```

The native verification run used the package-specific root:

```bash
SPACK_ROOT_PKG='gzip@1.14' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SPACK_TIMEOUT=1800 \
VASO_LOCK_OUT=/workspace/experiment/gzip_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/gzip_build_graph.json \
./run.sh
```

That command seats the CUDA insula, runs Bazel's vendored
`@spack_dist//:spack`, applies the `native_overrides.json` flip to
`@gzip_native//:lib`, and runs `//synthetic:gzip_prefix_parity` inside the
insula.

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/gzip/package.py
```

Source provenance from the Spack recipe:

- package class: `Gzip(AutotoolsPackage, GNUMirrorPackage)`
- version: `1.14`
- upstream source URL: `https://ftpmirror.gnu.org/gzip/gzip-1.14.tar.gz`
- SHA256:
  `613d6ea44f1248d7370c7ccdeee0dd0017a09e6c39de894b3c6f03f981191c6b`
- build directory: `spack-build`, because the recipe notes that in-source
  builds can create recursive symlinks
- package-specific configure args for this GCC/CUDA-insula build: none

The concrete `gzip@1.14` node has only toolchain dependencies:

```text
build: compiler-wrapper, gcc, gmake
link: gcc-runtime, glibc
```

The hermetic Spack build log shows the effective configure phase:

```text
<spack-stage>/spack-src/configure --prefix=<gzip-prefix>
```

followed by `make V=1` and `make install`.

## Build recipe

`native/gzip/gzip.bzl` mirrors the Spack Autotools flow:

```text
../configure --prefix=<prefix>
make V=1
make install
```

The repository rule fetches the same upstream tarball by SHA256, creates the
out-of-tree `spack-build` directory, and refuses to run unless the hermetic
insula has set `VASO_IN_INSULA=1`. `gzip` has no non-toolchain dependency
prefixes, so the corresponding mechanism verifier is the Autotools/no-dependency
case in `//tools:hermetic_native_deps_guard_test`: the rule is classified as
Autotools and insula-gated, with no host Spack or host dependency discovery.

## Prefix and behavior gate target

`//synthetic:gzip_prefix_parity` compares the native prefix against the
hermetic Spack reference:

```text
/vaso/cache/spack/opt/spack/linux-icelake/gzip-1.14-2l3gqtpkkjscnkpxmzndm5b6scuj2kzt
```

The gate covers the complete executable/doc prefix surface:

- executables/scripts:
  `gzip`, `gunzip`, `gzexe`, `uncompress`, `zcat`, `zcmp`, `zdiff`, `zegrep`,
  `zfgrep`, `zforce`, `zgrep`, `zmore`, `znew`;
- data files:
  `share/info/gzip.info` plus all installed `share/man/man1/*.1` files;
- `bin/gzip` dynamic dependency parity;
- `gzip --version` behavior;
- deterministic `gzip -n -c` compression output for a small text input.

Current status: native and parity-gated. The latest run passed
`//synthetic:gzip_prefix_parity` inside the CUDA insula.
