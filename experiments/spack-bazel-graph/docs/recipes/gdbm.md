# gdbm native recipe

## Position in the hillclimb

`gdbm` is topo index 22 in the `python` root graph, immediately after
`readline`:

```text
20 less      autotools
21 readline  autotools
22 gdbm      autotools
23 util-linux-uuid autotools
```

The generated lock keeps the Spack DAG edge to `readline` and exposes both
GDBM libraries:

```json
{
  "package": "gdbm",
  "version": "1.26",
  "build": "spack",
  "link_deps": ["spack_readline"],
  "link_libs": ["gdbm", "gdbm_compat"],
  "include_dirs": ["include"]
}
```

## Spack evidence

All evidence here comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
The reference prefix, package recipe, concrete spec, build environment, and
build log are from the hermetic `/vaso/cache/spack` store inside the insula.
Do not use an ambient host Spack checkout for this node.

Reference prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/gdbm-1.26-dzgwtrcajqs5aegjkwl2mgkpyushhfr7
```

Hermetic recipe path:

```text
/vaso/cache/spack/opt/spack/linux-icelake/gdbm-1.26-dzgwtrcajqs5aegjkwl2mgkpyushhfr7/.spack/repos/spack_repo/builtin/packages/gdbm/package.py
```

Source provenance from the Spack recipe:

- package class: `Gdbm(AutotoolsPackage, GNUMirrorPackage)`
- GNU mirror path pattern: `gdbm/gdbm-1.13.tar.gz`
- concrete source URL: `https://ftp.gnu.org/gnu/gdbm/gdbm-1.26.tar.gz`
- SHA256: `6a24504a14de4a744103dcb936be976df6fbe88ccff26065e54c1c47946f4a5e`
- dependency edge: `depends_on("readline")`

No patch applies to `gdbm@1.26` on Linux. The Darwin nanosleep patch is gated
to `@1.25 platform=darwin`, and the bundled `gdbm.patch` is only for older
`@:1.18` compilers.

## Build recipe

The active Spack recipe logic is:

```python
def configure_args(self):
    return ["--enable-libgdbm-compat", "CPPFLAGS=-D_GNU_SOURCE"]
```

The installed configure argument capture is exactly:

```text
--enable-libgdbm-compat CPPFLAGS=-D_GNU_SOURCE
```

Package-relevant environment from the hermetic build:

```text
PKG_CONFIG_PATH=<readline>/lib/pkgconfig:<ncurses>/lib/pkgconfig
SPACK_STORE_INCLUDE_DIRS=<ncurses>/include:<readline>/include
SPACK_STORE_LINK_DIRS=<gcc-runtime>/lib:<ncurses>/lib:<readline>/lib
SPACK_STORE_RPATH_DIRS=<gdbm>/lib:<gdbm>/lib64:<gcc-runtime>/lib:<ncurses>/lib:<readline>/lib
SPACK_DISABLE_NEW_DTAGS=--disable-new-dtags
SPACK_TARGET_ARGS_CC='-march=icelake-client -mtune=icelake-client'
```

The native provider must run inside the insula with `VASO_IN_INSULA=1`, consume
the Bazel-selected `readline` provider prefix, and keep `ncurses` available as
readline's link dependency.

## Spack build phases

The hermetic build log shows the standard Autotools phases:

```text
==> gdbm: Executing phase: 'autoreconf'
==> gdbm: Executing phase: 'configure'
configure --prefix=<spack-prefix> --enable-libgdbm-compat CPPFLAGS=-D_GNU_SOURCE
checking for readline in -lreadline... yes
checking for readline/readline.h... yes
==> gdbm: Executing phase: 'build'
make V=1
libtool --mode=link ... -version-info 6:0:0 -o libgdbm.la ... -Wl,-soname -Wl,libgdbm.so.6
libtool --mode=link ... -o gdbmtool ... ../src/libgdbm.la -lreadline
libtool --mode=link ... -version-info 4:0:0 -o libgdbm_compat.la ... ../src/libgdbm.la
==> gdbm: Executing phase: 'install'
make install
```

Spack's Autotools plumbing filters generated libtool/configure path handling
before configure. The native provider starts from the release archive's
generated configure script, runs the same configure arguments, builds with
`make V=1`, installs, and removes libtool `.la` files so the prefix matches the
reference payload.

## Emitted prefix contract

ABI-relevant installed files:

- headers: `include/gdbm.h`, `include/dbm.h`, `include/ndbm.h`
- shared libraries:
  - `lib/libgdbm.so.6.0.0`, SONAME `libgdbm.so.6`
  - `lib/libgdbm_compat.so.4.0.0`, SONAME `libgdbm_compat.so.4`
- static libraries: `lib/libgdbm.a`, `lib/libgdbm_compat.a`
- executables: `bin/gdbmtool`, `bin/gdbm_dump`, `bin/gdbm_load`
- data files: `share/info/gdbm.info`, `share/man/man1/gdbmtool.1`,
  `share/man/man1/gdbm_dump.1`, `share/man/man1/gdbm_load.1`,
  `share/man/man3/gdbm.3`

Shared library dynamic contract:

- `libgdbm.so.6.0.0`: NEEDED `libc.so.6`, `ld-linux-x86-64.so.2`; 91 exported
  dynamic symbols
- `libgdbm_compat.so.4.0.0`: NEEDED `libgdbm.so.6`, `libc.so.6`; 20 exported
  dynamic symbols

Executable dynamic contract:

- `gdbmtool`: NEEDED `libgdbm.so.6`, `libreadline.so.8`, `libc.so.6`
- `gdbm_dump`: NEEDED `libgdbm.so.6`, `libc.so.6`
- `gdbm_load`: NEEDED `libgdbm.so.6`, `libc.so.6`

## Prefix and ABI gate

`//synthetic:gdbm_abi_parity` compares `@gdbm_native//:prefix` against the
hermetic Spack reference prefix:

- layout: headers, shared/static libraries, selected documentation, and
  executables
- ABI: SONAME and exported dynamic symbols for `libgdbm` and
  `libgdbm_compat`
- executable contract: NEEDED sets for `gdbmtool`, `gdbm_dump`, and
  `gdbm_load`
- link-and-run: a downstream C consumer stores and fetches a value through
  `libgdbm`, checks `gdbm_count`, and prints the version tuple and fetched
  value

Current status: recipe captured; native provider must pass this gate inside the
CUDA insula before the ledger status advances to `native`.
