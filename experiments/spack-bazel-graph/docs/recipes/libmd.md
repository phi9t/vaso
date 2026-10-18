# libmd native recipe

## Position in the hillclimb

`libmd` is topo index 15 in the `python` root graph, after `bzip2` and before
`libbsd`:

```text
13 diffutils  autotools
14 bzip2      generic
15 libmd      autotools
16 libbsd     autotools
```

`spack_libmd` has been flipped from provider `spack` to provider `native`
without changing its DAG position or downstream edges:

```json
{
  "package": "libmd",
  "version": "1.1.0",
  "build": "native",
  "link_deps": [],
  "link_libs": ["md"],
  "include_dirs": ["include"],
  "native_prefix": "@libmd_native//:lib"
}
```

## Spack evidence

Reference prefix from the Bazel-vendored Spack v1.2.2 run inside the CUDA
insula:

```text
/vaso/cache/spack/opt/spack/linux-icelake/libmd-1.1.0-lkdoudvwlwldywdlt577tehescm7yn3z
```

Source provenance from the hermetic Spack package recipe:

- Package class: `Libmd(AutotoolsPackage)`
- Homepage: `https://www.hadrons.org/software/libmd/`
- Source URL used by the native rule:
  `https://archive.hadrons.org/software/libmd/libmd-1.1.0.tar.xz`
- Version `1.1.0` SHA256:
  `1bd6aa42275313af3141c7cf2e5b964e8b1fd488025caf2f971f43b00776b332`
- Build dependencies: virtual `c`
- Patch metadata: `nvhpc-aliases.patch` applies only for `%nvhpc`; it is not
  part of this concrete GCC build.

The archived Spack build log shows the standard Autotools phases:

```text
libmd: Executing phase: 'autoreconf'
libmd: Executing phase: 'configure'
./configure --prefix=/vaso/cache/spack/opt/spack/linux-icelake/libmd-1.1.0-lkdoudvwlwldywdlt577tehescm7yn3z
libmd: Executing phase: 'build'
make V=1
libmd: Executing phase: 'install'
make install
```

The installed shared object has SONAME `libmd.so.0`.

## Prefix contract

The ABI-relevant prefix contract used by the gate is:

- headers: `include/md2.h`, `include/md4.h`, `include/md5.h`,
  `include/ripemd.h`, `include/rmd160.h`, `include/sha.h`, `include/sha1.h`,
  `include/sha2.h`, `include/sha256.h`, `include/sha512.h`
- libraries: `lib/libmd.a`, `lib/libmd.so`, `lib/libmd.so.0`,
  `lib/libmd.so.0.1.0`
- pkg-config metadata: `lib/pkgconfig/libmd.pc`

The full Spack prefix also includes `share/man/man3/*.3` pages and symlinks.
The current ABI gate compares the headers/libraries/pkg-config contract because
those are the files downstream compiled consumers bind to.

## Native build

`native/libmd/libmd.bzl` defines `libmd_native`, a Bazel repository rule that
declares `VASO_IN_INSULA` as an environment input and refuses to build unless
the hermetic insula sets `VASO_IN_INSULA=1`.

The build action fetches the pinned source archive and runs:

```sh
./configure --prefix="$PREFIX"
make V=1 -j"${MAKE_JOBS:-$(nproc)}"
make install
find "$PREFIX" -type f -name '*.la' -delete || true
```

It exposes:

- `@libmd_native//:prefix` for prefix and ABI parity
- `@libmd_native//:lib`, linked with `-lmd`, as the stable provider behind
  `@spack_libmd//:lib` after the provider flip

## ABI gate

`//synthetic:libmd_abi_parity` compares `@libmd_native//:prefix` against the
hermetic Spack reference prefix with `tools/abi_parity.py`:

- layout: the ABI-relevant headers, static/shared libraries, symlink chain, and
  `lib/pkgconfig/libmd.pc`
- ABI: SONAME and exported dynamic symbols for `lib/libmd.so.0.1.0`
- link-and-run: `synthetic/use_libmd.c` calls `MD5Data()` for `hello libmd`
  and expects `16f5b9ba3226f38ce2a3b4b704c0d70c`
- data: prefix-normalized `lib/pkgconfig/libmd.pc`

Current verdict: migrated provider. With `libmd` enabled in
`native_overrides.json`, this command passes inside the CUDA insula:

```sh
SPACK_ROOT_PKG=python VASO_NATIVE=1 VASO_SPACK_TIMEOUT=600 VASO_FORCE_FETCH_REPOS=@libmd_native ./run.sh
```

The gate reports matching layout, matching SONAME (`libmd.so.0`), matching
exported symbols (89), identical `MD5Data()` link-and-run output, and matching
prefix-normalized `lib/pkgconfig/libmd.pc`.
