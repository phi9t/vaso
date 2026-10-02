# ncurses native recipe

## Position in the hillclimb

`ncurses` is topo index 19 in the `python` root graph, after `pkgconf` and
before `xz`:

```text
17 expat   autotools
18 pkgconf autotools
19 ncurses autotools
20 xz      autotools
```

Current status: recipe captured only. `ncurses` remains a Spack provider in
`spack_graph.lock.json`; the native provider must not be flipped on until the
red parity tests below exist and pass inside the CUDA insula.

## Spack evidence

All evidence in this document comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned to GitHub's latest Spack release at the time of
capture:

- release: `v1.2.2`
- asset: `https://github.com/spack/spack/releases/download/v1.2.2/spack-1.2.2.tar.gz`
- SHA256: `ed39d08bc295571cdec23a4566cbd8aa7ef4ebd582013d43874471a2b1257bf5`

Do not use an ambient host Spack checkout for this node. Recipe source,
concretized spec, build environment, and reference prefix all come from the
Bazel-vendored Spack state under `/vaso/cache/spack`.

Reference prefix:

`/vaso/cache/spack/opt/spack/linux-icelake/ncurses-6.6-43optwvfncob7gw7kojtnqblmudi4nzv`

Hermetic recipe path:

`/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/ncurses/package.py`

Concrete spec:

- `ncurses@6.6~symlinks+termlib abi=none`
- `build_system=autotools`
- build dependency: `pkgconf@2.5.1`
- patch hash: `7a351bc4953a4ab70dabdbea31c8db0c03d40ce505335f3b6687180dde24c535`
- patch file: `rxvt_unicode_6_4.patch`

Source provenance from the Spack recipe:

- GNU mirror path: `ncurses/ncurses-6.1.tar.gz`
- concrete version: `6.6`
- SHA256: `355b4cbbed880b0381a04c46617b7656e362585d52e9cf84a67e2009b749ff11`

The native source URL should therefore be the GNU mirror release archive for
`ncurses-6.6.tar.gz`; verify the exact URL during implementation and keep the
SHA above as the acceptance hash.

## Build environment

Spack's installed build environment records these package-relevant inputs:

```text
unset TERMINFO
SPACK_CFLAGS=-fPIC
SPACK_CXXFLAGS=-fPIC
PKG_CONFIG_PATH=/vaso/cache/spack/opt/spack/linux-icelake/pkgconf-2.5.1-kudvhugwerlucanwcfcryeofe7xc6w5j/lib/pkgconfig
```

Native `ncurses` must run inside the insula with `VASO_IN_INSULA=1`, must use
the Bazel-owned native `pkgconf` provider or the corresponding hermetic Spack
prefix for build metadata, and must not discover tools or package recipes from
host Spack state.

## Spack build phases

The Spack recipe is not a single build-directory autotools package. It runs
`../configure` twice from sibling build directories and installs both outputs
into the same prefix.

Common configure options:

```text
--disable-stripping
--with-shared
--with-cxx-shared
--enable-overwrite
--without-ada
--enable-pc-files
--disable-overwrite
--with-pkg-config-libdir=<stage.source_path>/lib/pkgconfig
--with-termlib
--enable-termcap
--enable-getcap
--enable-tcap-names
--with-versioned-syms
```

The `abi=none` variant means there is no `--with-abi-version` option. The
`~symlinks` variant means there is no `--enable-symlinks` option.

Non-wide build:

```text
mkdir -p build_ncurses
cd build_ncurses
../configure --prefix=<prefix> <common opts> --disable-widec --without-manpages --without-tests
make
make install
```

Wide build:

```text
mkdir -p build_ncursesw
cd build_ncursesw
../configure --prefix=<prefix> <common opts> --enable-widec --without-manpages --without-tests
make
make install
```

Post-install actions:

- symlink every `include/ncursesw/*.h` into top-level `include/`
- install the staged `lib/pkgconfig` directory into `<prefix>/lib/pkgconfig`
- symlink `lib/libcurses.so` to `lib/libncurses.so` if `libcurses.so` is absent

## Emitted prefix contract

The installed reference manifest has 3,117 entries:

- 12 executable tools under `bin/`
- 54 headers under `include/`, `include/ncurses/`, and `include/ncursesw/`
- 61 library files under `lib/`
- 12 pkg-config files under `lib/pkgconfig/`
- 2,943 terminfo entries under `share/terminfo/`

Important tools:

```text
bin/captoinfo
bin/clear
bin/infocmp
bin/infotocap
bin/ncurses6-config
bin/ncursesw6-config
bin/reset
bin/tabs
bin/tic
bin/toe
bin/tput
bin/tset
```

Important headers:

```text
include/curses.h
include/form.h
include/menu.h
include/ncurses.h
include/panel.h
include/term.h
include/termcap.h
include/unctrl.h
include/ncurses/*.h
include/ncursesw/*.h
```

Top-level headers are expected to be symlinks into `include/ncursesw/` so that
`#include <ncurses.h>` uses the wide build by default.

Libraries to preserve:

```text
lib/libcurses.so
lib/libform.so.6.6
lib/libformw.so.6.6
lib/libmenu.so.6.6
lib/libmenuw.so.6.6
lib/libncurses.so.6.6
lib/libncursesw.so.6.6
lib/libpanel.so.6.6
lib/libpanelw.so.6.6
lib/libtinfo.so.6.6
lib/libtinfow.so.6.6
lib/libncurses++.so.6.6
lib/libncurses++w.so.6.6
```

The prefix also contains static libraries and debug static variants such as
`libncurses_g.a`, `libform_g.a`, `libpanelw_g.a`, and
`libncurses++w_g.a`. The native prefix must match those layout decisions unless
the ABI gate records a deliberate, reviewed exception before the provider flip.

Pkg-config files to preserve:

```text
form.pc
formw.pc
menu.pc
menuw.pc
ncurses++.pc
ncurses++w.pc
ncurses.pc
ncursesw.pc
panel.pc
panelw.pc
tinfo.pc
tinfow.pc
```

The generated pkg-config metadata records `version=6.6.20251230` and absolute
`-L<reference-prefix>/lib` paths in the Spack prefix. The parity gate should
compare pkg-config files after normalizing the reference and candidate prefix
paths, as the existing native package gates do.

The `rxvt_unicode_6_4.patch` patch should be visible in the emitted terminfo
tree. At minimum, the parity gate should check these entries:

```text
share/terminfo/r/rxvt-unicode
share/terminfo/r/rxvt-unicode-256color
```

## Red tests before native implementation

Before implementing `native/ncurses`, add tests that fail while `ncurses`
still has no native provider:

- ABI/layout parity against the hermetic Spack prefix for all headers,
  libraries, pkg-config files, and the terminfo subset above.
- SONAME and exported-symbol comparison for `libncurses`, `libncursesw`,
  `libtinfo`, `libtinfow`, `libform`, `libformw`, `libmenu`, `libmenuw`,
  `libpanel`, and `libpanelw`.
- A C link-and-run consumer for `initscr`/`endwin` or a lower-level terminfo
  call that works in the sealed insula without requiring an interactive TTY.
- Tool behavior checks for `ncursesw6-config --version`,
  `ncursesw6-config --libs`, and `infocmp rxvt-unicode`.

Only after those tests are red for the missing native provider should the
native repository rule be added. The rule must declare `VASO_IN_INSULA` in its
repository environment inputs and fail loudly when the insula guard is absent.
