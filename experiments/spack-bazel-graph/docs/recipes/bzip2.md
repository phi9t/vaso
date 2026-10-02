# bzip2 native recipe

## Position in the hillclimb

`bzip2` is topo index 14 in the `python` root graph, after `diffutils` and
before `libmd`:

```text
10 berkeley-db  autotools
11 libffi       autotools
12 libiconv     autotools
13 diffutils    autotools
14 bzip2        generic
15 libmd        autotools
```

`spack_bzip2` has been flipped from provider `spack` to provider `native`
without changing its DAG position or downstream edges:

```json
{
  "package": "bzip2",
  "version": "1.0.8",
  "build": "native",
  "link_deps": [],
  "link_libs": ["bz2"],
  "include_dirs": ["include"],
  "native_prefix": "@bzip2_native//:lib"
}
```

## Spack evidence

Reference prefix from the Bazel-vendored Spack v1.2.2 run inside the CUDA
insula:

```text
/vaso/cache/spack/opt/spack/linux-icelake/bzip2-1.0.8-3vizt2b2psdlsmzwwsswdpdpiaeqgset
```

Source provenance from the hermetic Spack package recipe:

- Package class: `Bzip2(Package, SourcewarePackage)`
- Sourceware mirror path: `bzip2/bzip2-1.0.8.tar.gz`
- Source URL used by the native rule:
  `https://sourceware.org/pub/bzip2/bzip2-1.0.8.tar.gz`
- Version `1.0.8` SHA256:
  `ab5a03176ee106d3f0fa90e381da478ddae405918153cca248e682cd0c4a2269`
- Variants in this Linux concrete build: `+shared`, `~pic`, `~debug`
- Build dependencies: `diffutils`, `gmake`, and virtual `c`

The recipe's `patch()` phase replaces hardcoded `CC=gcc` in both upstream
Makefiles with Spack's compiler wrapper. The native rule applies the same patch
shape, but selects the compiler from the Bazel-owned insula environment.

The archived Spack build log shows a generic install-only phase:

```text
==> Ran patch() for bzip2
==> bzip2: Executing phase: 'install'
make -f Makefile-libbz2_so
make
make install PREFIX=/vaso/cache/spack/opt/spack/linux-icelake/bzip2-1.0.8-3vizt2b2psdlsmzwwsswdpdpiaeqgset
```

The shared build line sets the ABI soname:

```text
gcc -shared -Wl,-soname -Wl,libbz2.so.1.0 -o libbz2.so.1.0.8 ...
```

After upstream install, Spack installs `bzip2-shared` over `bin/bzip2`, installs
`lib/libbz2.so.1.0.8`, adds the `libbz2.so`, `libbz2.so.1`, and
`libbz2.so.1.0` symlinks, rewrites `bin/bunzip2` and `bin/bzcat` as symlinks
to `bzip2`, and writes `lib/pkgconfig/bzip2.pc`.

## Prefix contract

The ABI-relevant prefix contract used by the gate is:

- header: `include/bzlib.h`
- libraries: `lib/libbz2.a`, `lib/libbz2.so.1.0.8`, and the
  `libbz2.so` / `libbz2.so.1` / `libbz2.so.1.0` symlink chain
- pkg-config metadata: `lib/pkgconfig/bzip2.pc`
- executables: `bin/bzip2`, `bin/bunzip2`, `bin/bzcat`

The full Spack prefix also includes helper scripts (`bzgrep`, `bzmore`,
`bzdiff`, and symlink aliases) plus `man/man1/*.1`; those are emitted by the
native install because they come from upstream `make install`, but the current
ABI gate intentionally checks only the files above.

## Native build

`native/bzip2/bzip2.bzl` defines `bzip2_native`, a Bazel repository rule that
declares `VASO_IN_INSULA` as an environment input and refuses to build unless
the hermetic insula sets `VASO_IN_INSULA=1`.

The build action fetches the pinned source archive and runs:

```sh
for mf in Makefile Makefile-libbz2_so; do
  sed -i "s/^CC=gcc$/CC=${CC:-gcc}/" "$mf"
done

make -f Makefile-libbz2_so -j"${MAKE_JOBS:-$(nproc)}"
make -j"${MAKE_JOBS:-$(nproc)}"
make install PREFIX="$PREFIX"

install -m 0755 bzip2-shared "$PREFIX/bin/bzip2"
install -m 0644 libbz2.so.1.0.8 "$PREFIX/lib/libbz2.so.1.0.8"
ln -sfn libbz2.so.1.0.8 "$PREFIX/lib/libbz2.so"
ln -sfn libbz2.so.1.0.8 "$PREFIX/lib/libbz2.so.1"
ln -sfn libbz2.so.1.0.8 "$PREFIX/lib/libbz2.so.1.0"
ln -s bzip2 "$PREFIX/bin/bunzip2"
ln -s bzip2 "$PREFIX/bin/bzcat"
```

It then writes the same `bzip2.pc` fields as Spack. Because pkg-config files
embed install roots, `tools/abi_parity.py` reports both raw hashes and a
prefix-normalized hash for `.pc` files; this preserves the contract while
allowing Bazel runfiles and the Spack store to live at different absolute
paths.

## ABI gate

`//synthetic:bzip2_abi_parity` compares `@bzip2_native//:prefix` against the
hermetic Spack reference prefix:

- layout: header, static/shared libraries, symlink chain, pkg-config file, and
  selected executables
- ABI: SONAME and exported dynamic symbols for `lib/libbz2.so.1.0.8`
- link-and-run: `synthetic/use_bzip2.c` compresses and decompresses
  `hello bzip2`
- executable deps: `readelf -d` `NEEDED` entries for `bzip2`, `bunzip2`, and
  `bzcat`
- behavior: `bin/bzip2 -c` produces identical compressed stdout bytes, compared
  by SHA256 because the output is binary

Current verdict: migrated provider. With `bzip2` enabled in
`native_overrides.json`, this command passes inside the CUDA insula:

```sh
SPACK_ROOT_PKG=python VASO_NATIVE=1 VASO_SPACK_TIMEOUT=600 VASO_FORCE_FETCH_REPOS=@bzip2_native ./run.sh
```

The gate reports matching layout, matching SONAME (`libbz2.so.1.0`), matching
exported symbols (35), identical `hello bzip2` link-and-run output, matching
executable `NEEDED` sets (`libbz2.so.1.0`, `libc.so.6`), identical compressed
stdout SHA256, and matching prefix-normalized `lib/pkgconfig/bzip2.pc`.
