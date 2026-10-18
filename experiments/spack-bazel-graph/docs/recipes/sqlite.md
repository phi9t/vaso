# sqlite native recipe

## Position in the hillclimb

`sqlite` is topo index 30 in the `python` root graph. It follows native
`pigz` and precedes `zstd`, which had already been migrated earlier:

```text
29 pigz    makefile
30 sqlite  autotools
31 zstd    makefile
32 tar     autotools
```

The generated lock keeps Spack's DAG edges while flipping only the provider:

```json
{
  "package": "sqlite",
  "version": "3.53.1",
  "build": "native",
  "native_prefix": "@sqlite_native//:lib",
  "link_deps": ["spack_readline", "spack_zlib_ng"],
  "link_libs": ["sqlite3"],
  "include_dirs": ["include"]
}
```

## Spack evidence

All evidence here comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Recipe source, concrete spec, build log, build environment, and reference
prefix are from the hermetic `/vaso/cache/spack` store inside the CUDA insula.
Do not use an ambient host Spack checkout for this node.

Reference prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/sqlite-3.53.1-x6qzwjuox25frwpm2z6binp6s6nhpmnz
```

Hermetic recipe path:

```text
/vaso/cache/spack/opt/spack/linux-icelake/sqlite-3.53.1-x6qzwjuox25frwpm2z6binp6s6nhpmnz/.spack/repos/spack_repo/builtin/packages/sqlite/package.py
```

Concrete spec:

- `sqlite@3.53.1 +column_metadata +fts +rtree build_system=autotools`
- dependencies: `readline`, `zlib-api`
- build dependencies: compiler wrapper, `gmake`, `pkgconf`
- source URL: `https://www.sqlite.org/2026/sqlite-autoconf-3530100.tar.gz`
- source SHA256:
  `83e6b2020a034e9a7ad4a72feea59e1ad52f162e09cbd26735a3ffb98359fc4f`

## Build recipe

The hermetic Spack build log shows the exact configure shape:

```text
configure --prefix=<spack-prefix> --enable-fts4 --enable-fts5 --enable-rtree CPPFLAGS=-DSQLITE_ENABLE_COLUMN_METADATA=1
```

Key configure outcomes from the same log:

```text
Checking for zlib.h...ok
Line-editing support for the sqlite3 shell: none
Library feature flags: -DSQLITE_ENABLE_COLUMN_METADATA=1 -DSQLITE_ENABLE_FTS4 -DSQLITE_ENABLE_FTS5 -DSQLITE_ENABLE_MATH_FUNCTIONS -DSQLITE_ENABLE_PERCENTILE -DSQLITE_ENABLE_RTREE -DSQLITE_HAVE_ZLIB=1 -DSQLITE_THREADSAFE=1
```

The Spack DAG includes `readline`, but the concrete build does not link the
`sqlite3` shell against readline. The native provider therefore validates the
Bazel-selected `@readline_native//:prefix_path.txt` input as a DAG edge, but it
does not add readline include or link flags to configure. Adding those flags
would change the emitted executable dependency set.

The native provider follows the Autotools path inside the CUDA insula:

- refuse to run unless `VASO_IN_INSULA=1`
- fetch sqlite 3.53.1 by the same SHA256
- consume `@readline_native//:prefix_path.txt` and
  `@zlib_ng_native//:prefix_path.txt`
- validate both dependency prefixes in the shell before configure
- run upstream `./configure --enable-fts4 --enable-fts5 --enable-rtree`
- pass `CPPFLAGS=-DSQLITE_ENABLE_COLUMN_METADATA=1 -I<zlib>/include`
- pass zlib library and rpath flags through the Autotools link channel
- run `make V=1`, `make install`, and remove libtool archives

## Emitted prefix contract

The ABI/behavior-relevant install surface is:

- executable: `bin/sqlite3`
- headers: `include/sqlite3.h`, `include/sqlite3ext.h`
- shared library: `lib/libsqlite3.so.3.53.1` plus symlink chain
- static library: `lib/libsqlite3.a`
- pkg-config file: `lib/pkgconfig/sqlite3.pc`
- manpage: `share/man/man1/sqlite3.1`

The observed dynamic dependency contract is:

- `lib/libsqlite3.so.3.53.1`: `libm.so.6`, `libc.so.6`
- `bin/sqlite3`: `libm.so.6`, `libz.so.1`, `libc.so.6`

## Prefix, ABI, and behavior gate

`//synthetic:sqlite_abi_parity` compares `@sqlite_native//:prefix` against the
hermetic Spack reference prefix:

- layout: headers, shared/static libraries, pkg-config, `bin/sqlite3`, manpage
- data: prefix-normalized `lib/pkgconfig/sqlite3.pc`
- data: exact SHA256 for `share/man/man1/sqlite3.1`
- ABI: exported dynamic symbols for `libsqlite3.so.3.53.1`
- executable contract: matching `readelf -d` NEEDED set for `bin/sqlite3`
- link-and-run: downstream C consumer exercises in-memory DB use plus
  `ENABLE_COLUMN_METADATA`, FTS5, and RTREE compile options
- behavior: identical `sqlite3 :memory: 'select sqlite_version();'`
- behavior: identical shell execution of an FTS5 virtual table statement

Verified native status:

```text
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 SPACK_ROOT_PKG=python VASO_NATIVE=1 VASO_SPACK_TIMEOUT=600 VASO_FORCE_FETCH_REPOS='@sqlite_native' ./run.sh
rootfs mode: cuda-bundle (base root: $HOME/.vaso-estate/rootfs)
hermetic spack (Bazel-owned) version: 1.2.2
//tools:hermetic_native_deps_guard_test PASSED
native/sqlite/sqlite.bzl: autotools: READLINE_PREFIX, ZLIB_PREFIX
//synthetic:use_sqlite PASSED
//synthetic:sqlite_abi_parity PASSED in 0.8s
```

The sqlite gate reported `candidate_count = reference_count = 9`, 285 exported
dynamic symbols on both sides, matching pkg-config content after prefix
normalization, manpage SHA256
`1edbafb8886b64e5e72ca559d306f5ee63356cbf1059f72e908b66d1e231c46e`,
matching `bin/sqlite3` NEEDED sets, and matching consumer output:

```text
sqlite=3.53.1 column_metadata=1 fts5=1 rtree=1 ok=1
```

Current status: native provider green inside the CUDA insula. The next
unmigrated frontier is `tar`.
