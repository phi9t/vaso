# less native recipe

## Position in the hillclimb

`less` is topo index 20 in the `python` root graph, after `ncurses` and before
`zlib-ng`:

```text
18 pkgconf autotools
19 ncurses autotools
20 less    autotools
21 zlib-ng autotools
```

It is an executable-only node in this graph. The lock has no public C headers
or link libraries, but the topology keeps the `ncurses` dependency edge:

```json
{
  "package": "less",
  "version": "692",
  "build": "spack",
  "link_deps": ["spack_ncurses"],
  "link_libs": [],
  "include_dirs": []
}
```

## Spack evidence

All evidence in this document comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned to GitHub's latest Spack release at the time of
capture:

- release: `v1.2.2`
- asset: `https://github.com/spack/spack/releases/download/v1.2.2/spack-1.2.2.tar.gz`
- SHA256: `ed39d08bc295571cdec23a4566cbd8aa7ef4ebd582013d43874471a2b1257bf5`

Do not use an ambient host Spack checkout for this node. Recipe source,
concretized spec, build environment, build log, and reference prefix all come
from the Bazel-vendored Spack state under `/vaso/cache/spack`.

Reference prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/less-692-ic6s3wutd633smdnxqwpd2khga2yqyrz
```

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/less/package.py
```

Concrete spec:

- `less@692 build_system=autotools`
- dependency edge: `depends_on("ncurses")`
- no package-specific variants
- no patches

Source provenance from the Spack recipe:

- URL: `https://www.greenwoodsoftware.com/less/less-692.tar.gz`
- SHA256: `61300f603798ecf1d7786570789f0ff3f5a1acf075a6fb9f756837d166e37d14`

## Build environment

Spack's installed build environment records these package-relevant inputs:

```text
CC=/vaso/cache/spack/opt/spack/linux-icelake/compiler-wrapper-1.1.0-xg3dfnk6ehbrwwvkxzr2dlk6enfhrv56/libexec/spack/gcc/gcc
PATH=<compiler-wrapper>:<gmake>/bin:<ncurses>/bin:/usr/bin:/bin:/vaso/tools/bin:/usr/local/cuda/bin:/usr/bin:/bin
PKG_CONFIG_PATH=/vaso/cache/spack/opt/spack/linux-icelake/ncurses-6.6-43optwvfncob7gw7kojtnqblmudi4nzv/lib/pkgconfig
SPACK_STORE_INCLUDE_DIRS=/vaso/cache/spack/opt/spack/linux-icelake/ncurses-6.6-43optwvfncob7gw7kojtnqblmudi4nzv/include
SPACK_STORE_LINK_DIRS=<gcc-runtime>/lib:<ncurses>/lib
SPACK_STORE_RPATH_DIRS=<less>/lib:<less>/lib64:<gcc-runtime>/lib:<ncurses>/lib
```

Native `less` must run inside the insula with `VASO_IN_INSULA=1`, must consume
the Bazel-selected `ncurses` provider prefix, and must not discover tools,
recipes, or package state from host Spack.

## Spack build phases

The hermetic build log shows the standard AutotoolsPackage path:

```text
==> No patches needed for less
==> less: Executing phase: 'autoreconf'
==> less: Executing phase: 'configure'
configure --prefix=<spack-prefix>
checking for tgoto in -ltinfo... yes
checking for tgoto in -ltinfow... yes
checking for initscr in -lncursesw... yes
checking for working terminal libraries... using -ltinfow
==> less: Executing phase: 'build'
make V=1
gcc -o lessecho ... -ltinfow
gcc -o lesskey ... -ltinfow
gcc -o less ... -ltinfow
==> less: Executing phase: 'install'
make install
```

## Emitted prefix contract

The installed reference manifest has 28 entries. Ignoring Spack metadata, the
ABI/behavior-relevant payload is:

- executables: `bin/less`, `bin/lesskey`, `bin/lessecho`
- manpages: `share/man/man1/less.1`, `share/man/man1/lesskey.1`,
  `share/man/man1/lessecho.1`
- no public C ABI in this graph

Executable dynamic dependency contract:

- `bin/less`: `libtinfow.so.6`, `libc.so.6`
- `bin/lesskey`: `libc.so.6`
- `bin/lessecho`: `libc.so.6`

## Native build

`native/less/less.bzl` fetches the pinned upstream source archive and runs the
same configure/build/install sequence inside the insula:

```sh
mkdir -p "$SRC/spack-build"
cd "$SRC/spack-build"
export CPPFLAGS="-I${NCURSES_PREFIX}/include ${CPPFLAGS:-}"
export LDFLAGS="-L${NCURSES_PREFIX}/lib -Wl,-rpath,${NCURSES_PREFIX}/lib ${LDFLAGS:-}"
export PKG_CONFIG_PATH="${NCURSES_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
../configure --prefix="$PREFIX"
make V=1 -j"${MAKE_JOBS:-$(nproc)}"
make install
find "$PREFIX" -type f -name '*.la' -delete
```

The repository rule refuses to run unless `VASO_IN_INSULA=1`. It consumes
`@ncurses_native//:prefix_path.txt` when the frontier has already migrated
`ncurses`, preserving Spack's DAG edge while avoiding hardcoded hermetic-cache
paths in the native rule.

## Prefix and behavior gate

`//synthetic:less_prefix_parity` compares `@less_native//:prefix` against the
hermetic Spack reference prefix with `tools/abi_parity.py`:

- layout: the three executables plus the three manpages
- data: exact SHA256 for the three manpages
- executable deps: `readelf -d` `NEEDED` entries for `less`, `lesskey`, and
  `lessecho`
- behavior: identical stdout/stderr/return code for `less --version`,
  `lesskey -V`, and `lessecho alpha "two words"`

Current status: migrated provider. With `less` enabled in
`native_overrides.json`, the full Python-root run passes inside the CUDA insula:

```sh
SPACK_ROOT_PKG=python VASO_NATIVE=1 VASO_SPACK_TIMEOUT=600 VASO_FORCE_FETCH_REPOS=@less_native ./run.sh
```
