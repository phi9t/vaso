# pkgconf native recipe

## Position in the hillclimb

`pkgconf` is topo index 18 in the `python` root graph, after `expat` and
before `ncurses`:

```text
16 libbsd  autotools
17 expat   autotools
18 pkgconf autotools
19 ncurses autotools
```

Spack marks `pkgconf` as `build_system=autotools`. It is primarily a
build-time tool (`pkgconf` / `pkg-config`) but also installs `libpkgconf`, so
the migration needs both a runnable tool prefix and a normal ABI gate for the
library.

## Spack evidence

Reference prefix:

`/vaso/cache/spack/opt/spack/linux-icelake/pkgconf-2.5.1-kudvhugwerlucanwcfcryeofe7xc6w5j`

Source provenance from the Bazel-vendored Spack v1.2.2 package recipe and
reference prefix inside the CUDA insula:

- URL: `https://distfiles.ariadne.space/pkgconf/pkgconf-2.5.1.tar.xz`
- SHA256: `cd05c9589b9f86ecf044c10a2269822bc9eb001eced2582cfffd658b0a50c243`
- Spack package class: `AutotoolsPackage`
- Version: `2.5.1`

The installed package recipe has no package-specific `configure_args`. The
Spack build log shows the standard autotools phases:

```text
pkgconf: Executing phase: 'autoreconf'
pkgconf: Executing phase: 'configure'
./configure --prefix=<spack-prefix>
pkgconf: Executing phase: 'build'
make V=1
pkgconf: Executing phase: 'install'
make install
```

Spack then runs the package hook:

```python
@run_after("install")
def link_pkg_config(self):
    symlink("pkgconf", f"{self.prefix.bin}/pkg-config")
    symlink("pkgconf.1", f"{self.prefix.share.man.man1}/pkg-config.1")
```

Spack's archived post-install metadata records removal of
`lib/libpkgconf.la`; the native rule deletes `.la` files after install to match
the emitted prefix.

## Emitted prefix contract

ABI-relevant files:

- `include/pkgconf/libpkgconf/*.h`
- `lib/libpkgconf.so.7.0.0`
- `lib/libpkgconf.so.7 -> libpkgconf.so.7.0.0`
- `lib/libpkgconf.so -> libpkgconf.so.7.0.0`
- `lib/libpkgconf.a`
- `lib/pkgconfig/libpkgconf.pc`

Tooling files:

- `bin/pkgconf`
- `bin/pkg-config -> pkgconf`
- `bin/bomtool`
- `share/aclocal/pkg.m4`
- `share/man/man1/pkg-config.1 -> pkgconf.1`

## Native build

`native/pkgconf/pkgconf.bzl` declares `VASO_IN_INSULA` as a repository
environment input and refuses to build unless the hermetic insula sets
`VASO_IN_INSULA=1`. It fetches the pinned release source archive and uses the
archive's generated `configure` script:

```sh
./configure --prefix="$PREFIX"
make -j"${MAKE_JOBS:-$(nproc)}" V=1
make install
ln -sf pkgconf "$PREFIX/bin/pkg-config"
ln -sf pkgconf.1 "$PREFIX/share/man/man1/pkg-config.1"
find "$PREFIX" -type f -name '*.la' -delete
```

Ruling: the native rule does not run Spack's `autoreconf` phase for this
release tarball because the hermetic CUDA insula does not provide
`autoreconf`, and the archive already contains the generated `configure`
script. If a future source archive omits `configure`, the rule fails loudly
instead of falling back to a host autotools installation.

It exposes `@pkgconf_native//:lib` as a `cc_library` over
`prefix/include/pkgconf` and `prefix/lib/libpkgconf.so`.

## ABI gate

`//synthetic:pkgconf_abi_parity` compares the native prefix to the Spack
reference using `tools/abi_parity.py`:

- layout: headers, `libpkgconf.so*`, `libpkgconf.a`, and `libpkgconf.pc`
- ABI: SONAME `libpkgconf.so.7` and exported `pkgconf_*` symbols
- link-and-run: `synthetic/use_pkgconf.c` initializes a `pkgconf_client_t`,
  prints `LIBPKGCONF_VERSION_STR`, and deinitializes the client.

Current verdict: migrated provider. With `pkgconf` enabled in
`native_overrides.json`, this command passes inside the CUDA insula:

```sh
SPACK_ROOT_PKG=python VASO_NATIVE=1 VASO_SPACK_TIMEOUT=600 ./run.sh
```

The gate reports matching layout, matching SONAME (`libpkgconf.so.7`),
matching exported symbols (112), and identical `LIBPKGCONF_VERSION_STR`
link-and-run output.
