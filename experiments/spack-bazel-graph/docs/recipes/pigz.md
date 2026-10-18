# pigz native recipe

## Position in the hillclimb

`pigz` is topo index 29 in the `python` root graph. It follows native
`openssl` and depends on the already-migrated `zlib-ng` provider:

```text
28 openssl  generic
29 pigz     makefile
30 sqlite   autotools
```

The generated lock keeps the Spack DAG edge while flipping only the provider:

```json
{
  "package": "pigz",
  "version": "2.8",
  "build": "native",
  "native_prefix": "@pigz_native//:lib",
  "link_deps": ["spack_zlib_ng"],
  "link_libs": [],
  "include_dirs": []
}
```

`pigz` is an executable-only node for this graph. It exports no public C headers
or libraries, but downstream graph topology still records the `zlib-ng`
dependency edge owned by Spack.

## Spack evidence

All evidence here comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Recipe source, concrete spec, build log, and reference prefix are from the
hermetic `/vaso/cache/spack` store inside the CUDA insula. Do not use an ambient
host Spack checkout for this node.

Reference prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/pigz-2.8-kk3m7a6cdxorbycfy34mjpqsg63bwran
```

Hermetic recipe path:

```text
/vaso/cache/spack/opt/spack/linux-icelake/pigz-2.8-kk3m7a6cdxorbycfy34mjpqsg63bwran/.spack/repos/spack_repo/builtin/packages/pigz/package.py
```

Source provenance from the Spack recipe:

- package class: `Pigz(MakefilePackage)`
- homepage: `https://zlib.net/pigz/`
- upstream source URL pattern:
  `https://github.com/madler/pigz/archive/v2.3.4.tar.gz`
- version `2.8` SHA256:
  `2f7f6a6986996d21cb8658535fff95f1c7107ddce22b5324f4b41890e2904706`
- package dependencies: generated build-time `c` and `depends_on("zlib-api")`
- build system: Spack `makefile`
- no patches for this concrete package

## Build recipe

Spack's `Pigz.build()` intentionally forces the compiler name and flags:

```python
make("CC=cc", "CFLAGS=-O3 -Wall")
```

The hermetic build log shows the effective build:

```text
make CC=cc 'CFLAGS=-O3 -Wall'
cc -O3 -Wall   -c -o pigz.o pigz.c
cc -O3 -Wall   -c -o yarn.o yarn.c
cc -O3 -Wall   -c -o try.o try.c
cc -O3 -Wall -c zopfli/src/zopfli/deflate.c
...
cc  -o pigz pigz.o yarn.o try.o deflate.o blocksplitter.o tree.o lz77.o cache.o hash.o util.o squeeze.o katajainen.o symbols.o -lm -lpthread -lz
ln -f pigz unpigz
```

Spack installs only two payload files:

```text
bin/pigz
man/man1/pigz.1
```

The native provider follows the same Makefile path inside the CUDA insula:

- refuse to run unless `VASO_IN_INSULA=1`
- fetch pigz 2.8 by the same SHA256
- consume `@zlib_ng_native//:prefix_path.txt`
- run upstream `make` with `CC="${CC:-cc}"` and Spack's `-O3 -Wall` flags
- thread the native zlib-ng prefix through include, library, and rpath flags
- install `pigz` and `pigz.1` into a Spack-shaped prefix

## Emitted prefix contract

The ABI/behavior-relevant install surface is:

- executable: `bin/pigz`
- manpage: `man/man1/pigz.1`
- dynamic links for `bin/pigz`: `libc.so.6`, `libm.so.6`, `libz.so.1`

There are no public headers, shared libraries, static libraries, or pkg-config
files for this node.

## Prefix and behavior gate

`//synthetic:pigz_prefix_parity` compares `@pigz_native//:prefix` against the
hermetic Spack reference prefix:

- layout: exact two-file payload
- data: exact SHA256 for `man/man1/pigz.1`
- executable contract: matching `readelf -d` NEEDED set for `bin/pigz`
- behavior: identical `pigz -V` stdout/stderr/return code
- behavior: identical deterministic gzip bytes for `pigz -n -c {plain}`

Verified native status:

```text
SPACK_ROOT_PKG=python VASO_NATIVE=1 VASO_SPACK_TIMEOUT=600 VASO_FORCE_FETCH_REPOS=@pigz_native ./run.sh
rootfs mode: cuda-bundle (base root: $HOME/.vaso-estate/rootfs)
//synthetic:use_pigz PASSED
//synthetic:pigz_prefix_parity PASSED in 0.1s
```

The gate reported `candidate_count = reference_count = 2`, matching manpage
SHA256 `05f32e54bafb4bd946c6cc26c24979f8a49a3b8e70445c8f72693025457b63a5`,
matching executable NEEDED sets, `pigz -V` output `pigz 2.8`, and matching
deterministic compressed output SHA256
`a36e619964e4b5411dc2cdb2cabb8ae1c5c987a9dc2142e735474ceb921ab0eb`.

Current status: native provider green inside the CUDA insula. The next frontier
is `sqlite`.
