# diffutils native recipe

## Position in the hillclimb

`diffutils` is topo index 13 in the `python` root graph, after `libiconv` and
before `bzip2`:

```text
10 berkeley-db  autotools
11 libffi       autotools
12 libiconv     autotools
13 diffutils    autotools
14 bzip2        generic
```

It is an executable-only node in this graph. The lock has no public C headers or
link libraries for it:

```json
{
  "package": "diffutils",
  "version": "3.12",
  "build": "native",
  "link_deps": ["spack_libiconv"],
  "link_libs": [],
  "include_dirs": [],
  "native_prefix": "@diffutils_native//:lib"
}
```

## Spack evidence

Reference prefix from the Bazel-vendored Spack v1.2.2 run inside the CUDA
insula:

```text
/vaso/cache/spack/opt/spack/linux-icelake/diffutils-3.12-2gbb2j56nzjeq2ejlhopq2twu4van7ii
```

Source provenance from the hermetic Spack package recipe:

- URL: `https://ftp.gnu.org/gnu/diffutils/diffutils-3.12.tar.xz`
- SHA256: `7c8b7f9fc8609141fdea9cece85249d308624391ff61dedaf528fcb337727dfd`
- Spack package class: `AutotoolsPackage, GNUMirrorPackage`
- Build directory: `spack-build`
- Dependency edge: `depends_on("iconv")`

The hermetic build log shows the standard autotools phases:

```text
diffutils: Executing phase: 'autoreconf'
diffutils: Executing phase: 'configure'
./configure --prefix=<spack-prefix>
diffutils: Executing phase: 'build'
make V=1
diffutils: Executing phase: 'install'
make install
```

The reference prefix contract used by the gate is:

- executables: `bin/cmp`, `bin/diff`, `bin/diff3`, `bin/sdiff`
- data: `share/info/diffutils.info`
- no public C ABI in this graph

## Native build

`native/diffutils/diffutils.bzl` fetches the pinned GNU source archive and runs:

```sh
mkdir -p "$SRC/spack-build"
cd "$SRC/spack-build"
../configure --prefix="$PREFIX"
make -j"${MAKE_JOBS:-$(nproc)}" V=1
make install
rm -rf "$PREFIX/share/man" "$PREFIX/share/locale"
find "$PREFIX" -type f -name '*.la' -delete
```

The repository rule refuses to run unless `VASO_IN_INSULA=1`, so forced rebuild
evidence comes from Bazel executing inside the selected rootfs. It exposes:

- `@diffutils_native//:prefix` for prefix and behavior parity
- `@diffutils_native//:lib` as a no-op provider target, preserving the stable
  `@spack_diffutils//:lib` shape after the provider flip

## Prefix and behavior gate

`//synthetic:diffutils_prefix_parity` compares `@diffutils_native//:prefix`
against the hermetic Spack reference prefix with `tools/abi_parity.py`:

- layout: the four executables plus `share/info/diffutils.info`
- data: `share/info/diffutils.info` SHA256
- executable deps: `readelf -d` `NEEDED` entries for `diff`, `cmp`, `diff3`,
  and `sdiff`
- behavior: identical stdout/stderr/return code for `diff`, `cmp -s`, and
  `diff3`

Current verdict: migrated provider. With `diffutils` enabled in
`native_overrides.json`, this command passes inside the CUDA insula:

```sh
SPACK_ROOT_PKG=python VASO_NATIVE=1 VASO_SPACK_TIMEOUT=600 VASO_FORCE_FETCH_REPOS=@diffutils_native ./run.sh
```

The gate reports matching layout, matching `share/info/diffutils.info` SHA256,
matching executable `NEEDED` sets (`libc.so.6`), and identical behavior for the
three executable probes.
