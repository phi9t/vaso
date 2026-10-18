# expat native recipe

## Position in the hillclimb

`expat` is topo index 17 in the `python` root graph, after `libbsd` and before
`pkgconf`:

```text
15 libmd   autotools
16 libbsd  autotools
17 expat   autotools
18 pkgconf autotools
```

`spack_expat` has been flipped from provider `spack` to provider `native`
without changing its DAG position or downstream edges:

```json
{
  "package": "expat",
  "version": "2.8.1",
  "build": "native",
  "link_deps": ["spack_libbsd"],
  "link_libs": ["expat"],
  "include_dirs": ["include"],
  "native_prefix": "@expat_native//:lib"
}
```

The unchanged `spack_expat -> spack_libbsd` edge is preserved even though
upstream expat does not link against libbsd for this concrete build. The native
rule still receives the Bazel-built libbsd prefix so provider migration does
not alter the Spack-owned graph topology.

## Spack evidence

Reference prefix from the Bazel-vendored Spack v1.2.2 run inside the CUDA
insula:

```text
/vaso/cache/spack/opt/spack/linux-icelake/expat-2.8.1-yaozvb2su5jxfnbkxdfogzz35byc42wz
```

Source provenance from the hermetic Spack package recipe:

- Package class: `Expat(AutotoolsPackage, CMakePackage)`
- Default build system for this concrete Spack package: `autotools`
- Homepage: `https://libexpat.github.io/`
- Source URL used by the native rule:
  `https://github.com/libexpat/libexpat/releases/download/R_2_8_1/expat-2.8.1.tar.bz2`
- Version `2.8.1` SHA256:
  `f5833dd2e1cd7739ec9182804a1a29c4f0cc7c2f26b633d3a2188b7766a88ecb`
- Variant edge: `+libbsd`, represented as the unchanged DAG dependency on
  `spack_libbsd`

The concrete Spack build uses the Autotools flow:

```text
./configure --prefix=/vaso/cache/spack/opt/spack/linux-icelake/expat-2.8.1-yaozvb2su5jxfnbkxdfogzz35byc42wz --without-docbook --enable-static --with-libbsd
make V=1
make install
```

For expat 2.8.1, configure prints this warning:

```text
configure: WARNING: unrecognized options: --with-libbsd
```

The native rule deliberately keeps `--with-libbsd` because the goal is to
replay the hermetic Spack recipe path, including harmless configure options.

## Prefix contract

The ABI-relevant prefix contract used by the gate is:

- headers: `include/expat.h`, `include/expat_config.h`,
  `include/expat_external.h`
- libraries: `lib/libexpat.a`, `lib/libexpat.so`, `lib/libexpat.so.1`,
  `lib/libexpat.so.1.12.1`
- pkg-config metadata: `lib/pkgconfig/expat.pc`
- CMake metadata:
  `lib/cmake/expat-2.8.1/expat-config.cmake`,
  `lib/cmake/expat-2.8.1/expat-config-version.cmake`,
  `lib/cmake/expat-2.8.1/expat-noconfig.cmake`,
  `lib/cmake/expat-2.8.1/expat.cmake`
- executable: `bin/xmlwf`

`lib/libexpat.so.1.12.1` has SONAME `libexpat.so.1`, exports 72 dynamic
symbols in this concrete build, and needs only `libc.so.6`. `bin/xmlwf` needs
`libexpat.so.1` and `libc.so.6`.

## Native build

`native/expat/expat.bzl` defines `expat_native`, a Bazel repository rule that
declares `VASO_IN_INSULA` as an environment input and refuses to build unless
the hermetic insula sets `VASO_IN_INSULA=1`.

The build action fetches the pinned source archive, reads the Bazel-built
`libbsd` prefix from `@libbsd_native//:prefix_path.txt`, and runs:

```sh
export CPPFLAGS="-I${LIBBSD_PREFIX}/include ${CPPFLAGS:-}"
export LDFLAGS="-L${LIBBSD_PREFIX}/lib -Wl,-rpath,${LIBBSD_PREFIX}/lib ${LDFLAGS:-}"
./configure --prefix="$PREFIX" --without-docbook --enable-static --with-libbsd
make V=1 -j"${MAKE_JOBS:-$(nproc)}"
make install
find "$PREFIX" -type f -name '*.la' -delete || true
```

It exposes:

- `@expat_native//:prefix` for prefix and ABI parity
- `@expat_native//:lib`, linked with `-lexpat`, as the stable provider behind
  `@spack_expat//:lib` after the provider flip

## ABI gate

`//synthetic:expat_abi_parity` compares `@expat_native//:prefix` against the
hermetic Spack reference prefix with `tools/abi_parity.py`:

- layout: headers, static/shared libraries, pkg-config/CMake metadata, and
  `bin/xmlwf`
- ABI: SONAME and exported dynamic symbols for `lib/libexpat.so.1.12.1`
- data: prefix-normalized `expat.pc` plus byte-identical CMake metadata
- executables: `bin/xmlwf` NEEDED set
- link-and-run: `synthetic/use_expat.c` parses `<root><child/></root>` and
  expects `expat_2.8.1`
- exec-and-run: reference and candidate `xmlwf` both accept the same XML input

Current verdict: migrated provider. With `expat` enabled in
`native_overrides.json`, this command passes inside the CUDA insula:

```sh
SPACK_ROOT_PKG=python VASO_NATIVE=1 VASO_SPACK_TIMEOUT=600 ./run.sh
```

The gate reports matching layout, matching SONAME (`libexpat.so.1`), matching
exported symbols (72), identical link-and-run output, matching executable
NEEDED sets, matching `xmlwf` behavior, matching prefix-normalized
`expat.pc`, and byte-identical CMake metadata files.
