# xz (autotools build-system deep dive)

This recipe captures how Spack builds and installs `xz` (the `liblzma` provider)
and how we reproduce it as a Bazel-owned native build while staying
prefix-identical and ABI-identical.

## Why this node matters

`libxml2` links against `xz` and `zlib-ng`. Migrating `xz` is therefore a
prerequisite to migrating `libxml2` without changing the Spack DAG topology.

## Spack concrete artifact

Reference prefix from the Bazel-vendored Spack v1.2.2 run inside the CUDA
insula:

- `/vaso/cache/spack/opt/spack/linux-icelake/xz-5.8.3-i3t4xxmgqchjwwfhbxjzzp3ydzkpz4ca`

Expected ABI surface:

- headers under `include/lzma/`
- shared lib `lib/liblzma.so` with SONAME `liblzma.so.5`
- versioned file `lib/liblzma.so.5.8.3`
- static `lib/liblzma.a`
- pkgconfig `lib/pkgconfig/liblzma.pc`

Notably absent in the Spack prefix:

- libtool archives `*.la` (Spack does not ship `liblzma.la` here)

## Spack build system

The concretized `build_system` variant is `autotools` (see the per-prefix
`.spack/spec.json`). For the default shared+static build, the underlying phases
are the standard autotools pipeline:

- `./configure --prefix=<prefix> --enable-shared --enable-static`
- `make`
- `make install`

Spack injects additional environment (compiler wrappers, rpaths) during the
build, but the *emitted install tree* is what our migration must match.

## Native Bazel reproduction

The native build rule lives at:

- `native/xz/xz.bzl` (`xz_native` repository_rule)

It performs:

1. `download_and_extract` of the pinned source tarball;
2. `configure && make && make install` into `@xz_native//:prefix`;
3. post-install cleanup to remove any `*.la` files so the layout matches Spack.

The consumption contract remains stable through `@spack_xz//:lib`, which becomes
an alias to `@xz_native//:lib` when `native_overrides.json` flips the provider.

## ABI parity gate

Gate target:

- `//synthetic:xz_abi_parity`

The test compares the native `@xz_native//:prefix` against the Spack reference
prefix along:

- layout diff (include/, lib*, pkgconfig)
- SONAME + exported symbols for shared libs
- link-and-run of a tiny consumer (`synthetic/use_xz.c`)

Run it inside the insula (recommended path is via `run.sh`):

- `SPACK_ROOT_PKG=xz VASO_NATIVE=1 ./run.sh`
