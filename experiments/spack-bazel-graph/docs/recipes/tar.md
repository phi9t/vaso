# tar native recipe

## Position in the hillclimb

`tar` is topo index 32 in the `python` root graph. It follows native `sqlite`;
`zstd` at topo index 31 was already native before this slice:

```text
30 sqlite  autotools
31 zstd    makefile
32 tar     autotools
33 gettext autotools
```

The generated lock keeps Spack's DAG edges while flipping only the provider:

```json
{
  "package": "tar",
  "version": "1.35",
  "build": "native",
  "native_prefix": "@tar_native//:lib",
  "link_deps": [
    "spack_bzip2",
    "spack_libiconv",
    "spack_pigz",
    "spack_xz",
    "spack_zstd"
  ],
  "link_libs": [],
  "include_dirs": []
}
```

`tar` is an executable-only node for this graph. It exports no public C headers
or libraries, but downstream topology still records the Spack-owned compressor
and libiconv edges.

## Spack evidence

All evidence here comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Recipe source, concrete spec, build log, build environment, and reference
prefix are from the hermetic `/vaso/cache/spack` store inside the CUDA insula.
Do not use an ambient host Spack checkout for this node.

Reference prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/tar-1.35-teuiqnrb2ypy4b4yr3mj4gwo2cfuhhhk
```

Hermetic recipe path:

```text
$HOME/.vaso-estate/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/tar/package.py
```

Source provenance from the Spack recipe:

- package class: `Tar(AutotoolsPackage, GNUMirrorPackage)`
- source URL: GNU mirror `tar/tar-1.35.tar.xz`
- version `1.35` SHA256:
  `14d55e32063ea9526e057fbf35fcabd53378e769787eff7919c3755b02d2b57e`
- variant: `zip` defaulting to `pigz`
- link dependency: `iconv`
- runtime compressor dependencies: `pigz`, `zstd+programs`, `xz`, `bzip2`
- build system: Spack `autotools`
- no patches for this concrete package

## Build recipe

The Spack recipe configures tar 1.35 with the absolute compressor tools from
its concrete dependency prefixes:

```text
--disable-nls
--with-xz=<xz prefix>/bin/xz
--with-lzma=<xz prefix>/bin/lzma
--with-bzip2=<bzip2 prefix>/bin/bzip2
--with-zstd=<zstd prefix>/bin/zstd
--with-gzip=<pigz prefix>/bin/pigz
--with-libiconv-prefix=<libiconv prefix>
```

The recipe's flag handler also appends `-liconv` for `tar@1.35 ^libiconv`.
The native provider preserves that Autotools channel inside the CUDA insula:

- refuse to run unless `VASO_IN_INSULA=1`
- fetch GNU tar 1.35 by the same SHA256
- consume `prefix_path.txt` files from native `bzip2`, `libiconv`, `pigz`,
  `xz`, and `zstd`
- validate the executable/library surface for each dependency before configure
- run upstream `./configure` with the Spack compressor and libiconv arguments
- pass libiconv include, library, rpath, and `-liconv` flags through Autotools
- run `make V=1`, `make install`, and remove libtool archives

The mechanism-specific dependency verifier reports this build as:

```text
native/tar/tar.bzl: autotools: BZIP2_PREFIX, LIBICONV_PREFIX, PIGZ_PREFIX, XZ_PREFIX, ZSTD_PREFIX
```

## Emitted prefix contract

The ABI/behavior-relevant install surface is:

- executable: `bin/tar`
- executable: `libexec/rmt`
- info docs: `share/info/tar.info`, `share/info/tar.info-1`,
  `share/info/tar.info-2`, `share/info/tar.info-3`
- manpages: `share/man/man1/tar.1`, `share/man/man8/rmt.8`

There are no public headers, shared libraries, static libraries, or pkg-config
files for this node.

The observed executable dynamic dependency contract is:

- `bin/tar`: `libc.so.6`, `libiconv.so.2`
- `libexec/rmt`: `libc.so.6`

## Prefix and behavior gate

`//synthetic:tar_prefix_parity` compares `@tar_native//:prefix` against the
hermetic Spack reference prefix:

- layout: exact executable, info, and manpage payload
- data: exact SHA256 for the info and manpage files
- executable contract: matching `readelf -d` NEEDED sets for `bin/tar` and
  `libexec/rmt`
- behavior: identical `tar --version` stdout/stderr/return code
- behavior: identical return code/output behavior while creating an archive
  from temporary fixture files

`//synthetic:use_tar` exercises the native prefix directly by creating,
listing, and extracting an archive, then creating gzip, bzip2, xz, and zstd
archives through the compressor paths embedded by configure.

Verified native status:

```text
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 SPACK_ROOT_PKG=python VASO_NATIVE=1 VASO_SPACK_TIMEOUT=600 VASO_FORCE_FETCH_REPOS='@tar_native' ./run.sh
rootfs mode: cuda-bundle (base root: $HOME/.vaso-estate/rootfs)
hermetic spack (Bazel-owned) version: 1.2.2
//tools:hermetic_native_deps_guard_test PASSED
native/tar/tar.bzl: autotools: BZIP2_PREFIX, LIBICONV_PREFIX, PIGZ_PREFIX, XZ_PREFIX, ZSTD_PREFIX
//synthetic:use_tar PASSED
//synthetic:tar_prefix_parity PASSED in 0.1s
```

The tar gate reported `candidate_count = reference_count = 8`, exact SHA256
matches for all info/manpage payload files, matching executable NEEDED sets
for `bin/tar` and `libexec/rmt`, matching `tar --version` output SHA256
`b112e5578260c103cafae7150e33b8fe1b5d0720a158d88be5ea8f6d9d26f478`, and
matching archive-creation return code/stdout/stderr behavior.

Current status: native provider green inside the CUDA insula. The next
unmigrated frontier is `gettext`.
