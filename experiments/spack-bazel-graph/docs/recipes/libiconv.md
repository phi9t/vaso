# libiconv (autotools build-system deep dive)

This recipe captures how Spack builds GNU `libiconv` and how we reproduced it
as a Bazel-owned native build while keeping prefix/ABI parity.

## Spack concrete artifact

Reference prefix from the Bazel-vendored Spack v1.2.2 run inside the CUDA
insula:

- `/vaso/cache/spack/opt/spack/linux-icelake/libiconv-1.18-l4qa7mobxvahg6t6xwrzttegi7lkrf6x`

ABI surface:

- headers in `include/`: `iconv.h`, `libcharset.h`, `localcharset.h`
- shared libs in `lib/`:
  - `libiconv.so` with SONAME `libiconv.so.2` (versioned `libiconv.so.2.7.0`)
  - `libcharset.so` with SONAME `libcharset.so.1` (versioned `libcharset.so.1.0.0`)
- static libs in `lib/`: `libiconv.a`, `libcharset.a`
- no `*.la` in the Spack prefix

## Spack build system

The concretized `build_system` is `autotools`. The underlying phases are the
standard autotools pipeline:

- `./configure --prefix=<prefix> --enable-shared --enable-static`
- `make`
- `make install`

## Native Bazel reproduction

Native rule:

- `native/libiconv/libiconv.bzl` (`libiconv_native`)

It runs the same autotools pipeline into an in-repo `prefix/` and deletes any
`*.la` files after install to match the Spack prefix contract.

The consumption contract stays stable via `@spack_libiconv//:lib`, which becomes
an alias to `@libiconv_native//:lib` under `native_overrides.json`.

## ABI parity gate

Gate target:

- `//synthetic:libiconv_abi_parity`

The gate compares the candidate `@libiconv_native//:prefix` against the Spack
prefix for layout + SONAME/symbols + a link-and-run consumer
(`synthetic/use_libiconv.c`) that exercises `iconv_open/iconv/iconv_close`.
