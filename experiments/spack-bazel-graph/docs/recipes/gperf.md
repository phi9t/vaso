# gperf frontier recipe

## Position in the hillclimb

`gperf` is the next migrated py-torch frontier node after Boost. In the
captured `SPACK_ROOT_PKG=py-torch` graph it appears as:

```text
14  gperf  3.3  autotools  native; prefix parity green
```

The graph was captured inside the CUDA insula with Bazel's vendored Spack. The
native verification run used the package-specific root:

```bash
SPACK_ROOT_PKG='gperf@3.3' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SPACK_TIMEOUT=1800 \
VASO_LOCK_OUT=/workspace/experiment/gperf_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/gperf_build_graph.json \
./run.sh
```

That command installs the reference prefix through `@spack_dist//:spack`,
applies the `native_overrides.json` flip to `@gperf_native//:lib`, and runs
`//synthetic:gperf_prefix_parity` inside the same insula.

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/gperf/package.py
```

Source provenance from the Spack recipe:

- package class: `Gperf(AutotoolsPackage, GNUMirrorPackage)`
- version: `3.3`
- upstream source URL: `https://ftpmirror.gnu.org/gperf/gperf-3.3.tar.gz`
- SHA256:
  `fd87e0aba7e43ae054837afd6cd4db03a3f2693deb3619085e6ed9d8d9604ad8`
- patch surface: `register.patch` applies only to `@:3.1`, so it is not
  applied for the concrete `3.3` node
- package-specific configure args for this GCC/CUDA-insula build: none

The concrete `gperf@3.3` node has only toolchain dependencies:

```text
build: compiler-wrapper, gcc
link: gcc-runtime, glibc
```

Its reference prefix is executable/doc-only:

```text
bin/gperf
share/doc/gperf.html
share/info/gperf.info
share/man/man1/gperf.1
```

## Build recipe

Spack uses the standard Autotools phases for this package:

```text
../configure --prefix=<prefix>
make
make install
```

`native/gperf/gperf.bzl` mirrors that flow with the same source tarball and
SHA256. The repository rule refuses to run unless the hermetic insula has set
`VASO_IN_INSULA=1`, then builds into its Bazel-owned `prefix/` directory and
removes any libtool archive files if a future archive emits them.

`gperf` has no non-toolchain dependency prefixes, so the corresponding
hermetic-deps verifier for this build mechanism is the Autotools/no-dependency
case: `//tools:hermetic_native_deps_guard_test` confirms the rule is classified
as `autotools`, the build action is insula-gated, and no dependency prefix is
discovered from the host. Autotools packages that do consume prefixes are
verified by the same guard through explicit `*_prefix_file` labels, shell-side
prefix existence checks, and `CPPFLAGS`/`LDFLAGS`/`LIBS`/`PKG_CONFIG_PATH` or
`--with-*` configure channels.

## Prefix and behavior gate target

`//synthetic:gperf_prefix_parity` compares the native prefix against the
hermetic Spack reference:

```text
/vaso/cache/spack/opt/spack/linux-icelake/gperf-3.3-l26jrf5imbae3hynvykmqzitwamrhaxg
```

The gate covers:

- the four-file executable/doc layout;
- `bin/gperf` dynamic dependency parity;
- `gperf --version` behavior;
- deterministic perfect-hash C code generation for a small keyword table.

Current status: native and parity-gated. The latest run passed
`//synthetic:gperf_prefix_parity` inside the CUDA insula.
