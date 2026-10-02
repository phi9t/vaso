# libedit frontier recipe

## Position in the hillclimb

`libedit` is the migrated py-torch frontier node immediately after `pcre2`. In
the captured `SPACK_ROOT_PKG=py-torch` graph it appears as:

```text
36  libedit  3.1-20251016  autotools  native; ABI parity green
```

The package-local verification run used:

```bash
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
SPACK_ROOT_PKG='libedit@3.1-20251016' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_libedit_native //tools:hermetic_native_deps_guard_test //tools:native_build_mechanism_guard_unit_test //tools:abi_parity_unit_test' \
VASO_SPACK_TIMEOUT=1800 \
VASO_LOCK_OUT=/workspace/experiment/libedit_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/libedit_build_graph.json \
VASO_FORCE_FETCH_REPOS='@libedit_native' \
./run.sh
```

That command seats the CUDA insula, forces the Bazel-owned native repository
fetch inside the insula, runs Bazel's vendored `@spack_dist//:spack`, applies
the `native_overrides.json` flip to `@libedit_native//:lib`, and runs the
native smoke, Autotools hermetic-deps guard, ABI metadata unit test, and
`//synthetic:libedit_abi_parity` inside the same insula.

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/libedit/package.py
```

Source provenance from the Spack recipe:

- package class: `Libedit(AutotoolsPackage)`
- upstream source URL pattern:
  `http://thrysoee.dk/editline/libedit-20251016-3.1.tar.gz`
- SHA256:
  `21362b00653bbfc1c71f71a7578da66b5b5203559d43134d2dd7719e313ce041`
- dependencies: `pkgconfig` as a build dependency; `ncurses` as build/link

The concrete `libedit@3.1-20251016` node has:

```text
build: compiler-wrapper, gcc, gmake, pkgconf
link: gcc-runtime, glibc, ncurses
```

The hermetic Spack build log shows the Autotools flow:

```text
autoreconf
configure --prefix=<libedit-prefix> \
  ac_cv_lib_curses_tgetent=no \
  ac_cv_lib_termcap_tgetent=no \
  ac_cv_lib_ncurses_tgetent=no
make V=1
make install
```

Spack chooses these configure cache values so `configure` resolves `tgetent`
from the `ncurses +termlib` provider's `libtinfo`, not from curses, termcap, or
the non-termlib ncurses probe:

```text
checking for tgetent in -lncurses... (cached) no
checking for tgetent in -lcurses... (cached) no
checking for tgetent in -ltermcap... (cached) no
checking for tgetent in -ltinfo... yes
```

The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/libedit-3.1-20251016-rs5cjbifsr54ej2s6voieufpfjyw4dck
```

The ABI/behavior-relevant installed surface for this migration is:

```text
include/histedit.h
include/editline/readline.h
lib/libedit.so.0.0.76
lib/libedit.so.0
lib/libedit.so
lib/libedit.a
lib/pkgconfig/libedit.pc
share/man/man3/editline.3
share/man/man5/editrc.5
share/man/man7/editline.7
```

The reference shared library has SONAME `libedit.so.0` and NEEDED entries
`libtinfo.so.6` and `libc.so.6`.

## Build recipe

`native/libedit/libedit.bzl` mirrors Spack's Autotools build:

```text
download libedit-20251016-3.1.tar.gz
export PKG_CONFIG=<pkgconf-prefix>/bin/pkgconf
export PKG_CONFIG_PATH=<ncurses-prefix>/lib/pkgconfig
export CPPFLAGS="-I<ncurses-prefix>/include -I<ncurses-prefix>/include/ncursesw"
export LDFLAGS="-L<ncurses-prefix>/lib -Wl,-rpath,<ncurses-prefix>/lib -Wl,--disable-new-dtags"
configure --prefix=<prefix> \
  ac_cv_lib_curses_tgetent=no \
  ac_cv_lib_termcap_tgetent=no \
  ac_cv_lib_ncurses_tgetent=no
make V=1 -j$MAKE_JOBS
make install
find <prefix> -type f -name '*.la' -delete
```

The native rule consumes `@ncurses_native//:prefix_path.txt` and
`@pkgconf_native//:prefix_path.txt`, validates both prefixes before configure,
and threads them through Autotools' explicit environment channels. The
corresponding mechanism verifier is the Autotools dependency-prefix case:
`//tools:hermetic_native_deps_guard_test` reports:

```text
native/libedit/libedit.bzl: autotools: NCURSES_PREFIX, PKGCONF_PREFIX
```

## ABI gate target

`//synthetic:libedit_abi_parity` compares the native prefix against the
hermetic Spack reference. The gate covers:

- exact ABI-relevant layout parity for 10 entries;
- SONAME parity for `libedit.so.0`;
- exported dynamic symbol parity: 226 symbols from `libedit.so.0.0.76`;
- static archive member/exported-symbol parity: 29 members and 458 symbols;
- prefix-normalized `lib/pkgconfig/libedit.pc`;
- byte-identical selected manpages: `editline.3`, `editrc.5`, and
  `editline.7`;
- downstream `history_init()`/`history()` link-and-run behavior against
  reference and native prefixes, with matching native/reference ncurses termlib
  link prefixes.

The smoke target links against the native prefix and prints:

```text
libedit:2:beta
```

Current status: native and ABI-gated. The latest run used `rootfs mode:
cuda-bundle`, reported `hermetic spack (Bazel-owned) version: 1.2.2`, and
passed `//synthetic:use_libedit_native`,
`//tools:hermetic_native_deps_guard_test`,
`//tools:native_build_mechanism_guard_unit_test`,
`//tools:abi_parity_unit_test`, and `//synthetic:libedit_abi_parity` inside the
CUDA insula.
