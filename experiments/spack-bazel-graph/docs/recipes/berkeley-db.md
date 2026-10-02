# berkeley-db native recipe

## Position in the hillclimb

`berkeley-db` is the next non-toolchain C library in the installed `python`
build DAG after the data-only `ca-certificates-mozilla` node:

```text
0 ca-certificates-mozilla  generic
1 compiler-wrapper         generic [toolchain]
2 gcc                      autotools [toolchain]
3 glibc                    autotools [toolchain]
4 gcc-runtime              generic [toolchain]
5 gmake                    generic [toolchain]
6 berkeley-db              autotools
7 libffi                   autotools
...
30 python                  generic
```

Spack marks it as `build_system=autotools`, but its recipe is not the simple
`./configure` layout: configure lives under `dist/` and the build happens under
`build_unix/`.

## Spack evidence

Reference prefix:

`/vaso/cache/spack/opt/spack/linux-icelake/berkeley-db-18.1.40-rnm6sck3xyczysjvsmavdd4u723xw7mq`

Source provenance from the archived package recipe:

- URL: `https://download.oracle.com/berkeley-db/db-18.1.40.tar.gz`
- SHA256: `0cecb2ef0c67b166de93732769abdeba0555086d51de1090df325e18ee8da9c8`
- Spack package class: `AutotoolsPackage`
- `configure_directory = "dist"`
- `build_directory = "build_unix"`

Installed ABI-relevant files:

- `include/db.h`, `include/db_185.h`, `include/db_cxx.h`
- `include/dbstl_*.h`
- `lib/libdb-18.1.so`, `lib/libdb-18.so`, `lib/libdb.so`
- `lib/libdb_cxx-18.1.so`, `lib/libdb_cxx-18.so`, `lib/libdb_cxx.so`
- `lib/libdb_stl-18.1.so`, `lib/libdb_stl-18.so`, `lib/libdb_stl.so`

Spack configure args from `.spack/spack-configure-args.txt`:

```text
--disable-static
--enable-dbm
--enable-compat185
--with-repmgr-ssl=no
--enable-cxx
--enable-stl
```

Spack applies two patches before configure:

- `drop-docs.patch`: limit the docs install to `index.html` for `~docs`.
- `tls.patch`: include `<stdlib.h>` in the C++ TLS configure probe.

For `18.1.40`, the package `patch()` also removes missing documentation targets
`bdb-sql` and `gsg_db_server` from `dist/Makefile.in`.

## Native build

`native/berkeley_db/berkeley_db.bzl` fetches the pinned source archive, applies
the same patch effects, then runs:

```sh
mkdir -p build_unix
cd build_unix
../dist/configure \
  --prefix="$PREFIX" \
  --disable-static \
  --enable-dbm \
  --enable-compat185 \
  --with-repmgr-ssl=no \
  --enable-cxx \
  --enable-stl
make -j"${MAKE_JOBS:-$(nproc)}"
make install
find "$PREFIX" -type f -name '*.la' -delete
```

It exposes `@berkeley_db_native//:lib` as a `cc_library` over the installed
headers and `libdb`, `libdb_cxx`, `libdb_stl` shared objects. The native repo
declares `VASO_IN_INSULA` as a repository environment input and refuses to build
unless the hermetic insula sets it. `run.sh` can force a rebuild with
`VASO_FORCE_FETCH_REPOS=@berkeley_db_native`, which keeps the repository-rule
build tied to the selected rootfs and prevents stale host-built output from
being reused as migration evidence.

Historical note: older evidence from outside the hermetic insula is superseded.
The current migration evidence uses only the Bazel-vendored Spack v1.2.2
reference under `/vaso/cache/spack` inside the CUDA insula.

## ABI gate

`//synthetic:berkeley_db_abi_parity` compares the native prefix to the Spack
reference using `tools/abi_parity.py`:

- layout: headers and shared-library symlink chains
- ABI: SONAME and exported dynamic symbols for `libdb`, `libdb_cxx`, and
  `libdb_stl`
- link-and-run: `synthetic/use_berkeley_db.c` calls `db_version()` and checks
  that the Spack and native prefixes print identical version output

Current verdict: migrated provider. With `berkeley-db` enabled in
`native_overrides.json`, `SPACK_ROOT_PKG=python VASO_NATIVE=1
VASO_FORCE_FETCH_REPOS=@berkeley_db_native ./run.sh` passes inside the CUDA
insula. The gate reports matching layout, matching SONAMEs, matching exported
symbols (`libdb`: 2038, `libdb_cxx`: 2705, `libdb_stl`: 2849), and identical
`db_version()` link-and-run output.
