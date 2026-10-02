# readline native recipe

## Position in the hillclimb

`readline` is topo index 21 in the `python` root graph, after `less` and before
`gdbm`:

```text
19 ncurses autotools
20 less    autotools
21 readline autotools
22 gdbm    autotools
```

It is the next C library node after the executable-only `less` frontier. The
lock exposes both readline libraries and keeps the `ncurses` dependency edge:

```json
{
  "package": "readline",
  "version": "8.3",
  "build": "spack",
  "link_deps": ["spack_ncurses"],
  "link_libs": ["history", "readline"],
  "include_dirs": ["include", "include/readline"]
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
/vaso/cache/spack/opt/spack/linux-icelake/readline-8.3-zdaxcrsa3tppubwi3walkywzhpha7sgp
```

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/readline/package.py
```

Concrete spec:

- `readline@8.3 build_system=autotools`
- dependency edge: `depends_on("ncurses")`
- patches:
  - `readline83-001`, SHA256 `21f0a03106dbe697337cd25c70eb0edbaa2bdb6d595b45f83285cdd35bac84de`
  - `readline83-002`, SHA256 `e27364396ba9f6debf7cbaaf1a669e2b2854241ae07f7eca74ca8a8ba0c97472`
  - `readline83-003`, SHA256 `72dee13601ce38f6746eb15239999a7c56f8e1ff5eb1ec8153a1f213e4acdb29`

Source provenance from the Spack recipe:

- GNU mirror path: `readline/readline-8.0.tar.gz`
- concrete source URL: `https://ftp.gnu.org/gnu/readline/readline-8.3.tar.gz`
- SHA256: `fe5383204467828cd495ee8d1d3c037a7eba1389c22bc6a041f627976f9061cc`

## Build environment

Spack's installed build environment records these package-relevant inputs:

```text
CC=/vaso/cache/spack/opt/spack/linux-icelake/compiler-wrapper-1.1.0-xg3dfnk6ehbrwwvkxzr2dlk6enfhrv56/libexec/spack/gcc/gcc
PKG_CONFIG_PATH=/vaso/cache/spack/opt/spack/linux-icelake/ncurses-6.6-43optwvfncob7gw7kojtnqblmudi4nzv/lib/pkgconfig
SPACK_STORE_INCLUDE_DIRS=/vaso/cache/spack/opt/spack/linux-icelake/ncurses-6.6-43optwvfncob7gw7kojtnqblmudi4nzv/include
SPACK_STORE_LINK_DIRS=<gcc-runtime>/lib:<ncurses>/lib
SPACK_STORE_RPATH_DIRS=<readline>/lib:<readline>/lib64:<gcc-runtime>/lib:<ncurses>/lib
SPACK_DISABLE_NEW_DTAGS=--disable-new-dtags
SPACK_TARGET_ARGS_CC='-march=icelake-client -mtune=icelake-client'
```

Native `readline` must run inside the insula with `VASO_IN_INSULA=1`, must
consume the Bazel-selected `ncurses` provider prefix, and must not discover
tools, recipes, or package state from host Spack.

## Spack build phases

The hermetic build log shows:

```text
==> Applied patch https://ftpmirror.gnu.org/readline/readline-8.3-patches/readline83-001
==> Applied patch https://ftpmirror.gnu.org/readline/readline-8.3-patches/readline83-002
==> Applied patch https://ftpmirror.gnu.org/readline/readline-8.3-patches/readline83-003
==> readline: Executing phase: 'autoreconf'
==> readline: Executing phase: 'configure'
configure --prefix=<spack-prefix> bash_cv_wcwidth_broken=no
==> readline: Executing phase: 'build'
make 'SHLIB_LIBS=-L<ncurses>/lib -lncursesw -ltinfow'
gcc -shared ... -Wl,-soname,libhistory.so.8 ... -o libhistory.so.8.3 ... -lncursesw -ltinfow
gcc -shared ... -Wl,-soname,libreadline.so.8 ... -o libreadline.so.8.3 ... -lncursesw -ltinfow
==> readline: Executing phase: 'install'
make install
```

The `bash_cv_wcwidth_broken=no` configure argument comes from Spack's
`configure_args()` override. Spack's `build()` override passes
`SHLIB_LIBS=<ncurses:wide ld_flags>`, which resolves to:

```text
-L<ncurses>/lib -lncursesw -ltinfow
```

## Emitted prefix contract

The installed reference manifest has 79 entries. The ABI-relevant payload is:

- headers under `include/readline/`
- shared libraries: `lib/libreadline.so.8.3`, `lib/libhistory.so.8.3` plus
  their symlink chains
- static libraries: `lib/libreadline.a`, `lib/libhistory.a`
- pkg-config files: `lib/pkgconfig/readline.pc`, `lib/pkgconfig/history.pc`
- documentation/examples under `share/info/`, `share/man/man3/`,
  `share/doc/readline/`, and `share/readline/`

Shared library dynamic contract:

- `libhistory.so.8.3`: SONAME `libhistory.so.8`, NEEDED `libc.so.6`
- `libreadline.so.8.3`: SONAME `libreadline.so.8`, NEEDED `libtinfow.so.6`,
  `libc.so.6`

## Prefix and ABI gate

`//synthetic:readline_abi_parity` should compare `@readline_native//:prefix`
against the hermetic Spack reference prefix with `tools/abi_parity.py`:

- layout: headers, libraries, pkg-config files, plus selected info/man files
- data: prefix-normalized pkg-config files and exact info/man SHA256s
- ABI: SONAME and exported dynamic symbols for `libreadline` and `libhistory`
- link-and-run: a downstream C consumer that uses both history and readline
  symbols, linked against the reference prefix and the native prefix

Current status: recipe captured; native provider must pass the gate inside the
CUDA insula before the ledger status advances to `native`.
