# gettext native recipe

## Position in the hillclimb

`gettext` is topo index 33 in the `python` root graph. It follows native
`tar` and precedes the root `python` package:

```text
31 zstd    makefile
32 tar     autotools
33 gettext autotools
34 python  generic
```

The generated lock keeps Spack's DAG edges while flipping only the provider:

```json
{
  "package": "gettext",
  "version": "1.0",
  "build": "native",
  "native_prefix": "@gettext_native//:lib",
  "link_deps": [
    "spack_bzip2",
    "spack_libiconv",
    "spack_libxml2",
    "spack_ncurses",
    "spack_tar",
    "spack_xz"
  ],
  "link_libs": [
    "asprintf",
    "gettextlib",
    "gettextlib-1.0",
    "gettextpo",
    "gettextsrc",
    "gettextsrc-1.0",
    "textstyle"
  ],
  "include_dirs": ["include", "include/textstyle"]
}
```

## Spack evidence

All evidence here comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Recipe source, concrete spec, build log, configure arguments, install
manifest, and reference prefix are from the hermetic `/vaso/cache/spack` store
inside the CUDA insula. Do not use an ambient host Spack checkout for this
node.

Reference prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/gettext-1.0-oim3dsq3sapz7cqj5ygxglcbnp3ynehu
```

Hermetic recipe path:

```text
/vaso/cache/spack/opt/spack/linux-icelake/gettext-1.0-oim3dsq3sapz7cqj5ygxglcbnp3ynehu/.spack/repos/spack_repo/builtin/packages/gettext/package.py
```

Source provenance from the Spack recipe:

- package class: `Gettext(AutotoolsPackage, GNUMirrorPackage)`
- source URL: GNU mirror `gettext/gettext-1.0.tar.xz`
- version `1.0` SHA256:
  `71132a3fb71e68245b8f2ac4e9e97137d3e5c02f415636eb508ae607bc01add7`
- variants: `+bzip2 +curses +git +libxml2 -libunistring +pic +shared +tar +xz`
- dependencies: `iconv`, `ncurses`, `libxml2`, `tar`, `bzip2`, `xz`
- build system: Spack `autotools`

The Spack package patch rewrites `libtextstyle/configure` for `@0.20:+libxml2`
so the external libxml2 dependency is used:

```text
gl_cv_libxml_force_included=yes -> gl_cv_libxml_force_included=no
```

The recipe's flag handler appends `-lxml2` for `+libxml2`.

## Build recipe

The concrete Spack configure arguments are:

```text
--disable-java
--disable-csharp
--with-included-glib
--with-included-gettext
--with-included-libcroco
--without-emacs
--with-lispdir=<prefix>/share/emacs/site-lisp/gettext
--without-cvs
--disable-d
--enable-shared
--with-libiconv-prefix=<libiconv prefix>
--with-ncurses-prefix=<ncurses prefix>
--with-libxml2-prefix=<libxml2 prefix>
--with-included-libunistring
--with-pic
```

The native provider preserves that Autotools interface inside the CUDA insula:

- refuse to run unless `VASO_IN_INSULA=1`
- fetch GNU gettext 1.0 by the same SHA256
- consume `prefix_path.txt` files from native `bzip2`, `libiconv`,
  `libxml2`, `ncurses`, `tar`, and `xz`
- validate every dependency prefix and required executable/library surface
  before configure
- patch `libtextstyle/configure` to use external libxml2
- place native `tar`, `bzip2`, and `xz` tools on `PATH`, with `TAR` set to
  the native GNU tar executable
- pass libiconv, libxml2, and ncurses include/library/rpath flags through the
  Autotools `CPPFLAGS`, `LDFLAGS`, and `PKG_CONFIG_PATH` channels
- run upstream `./configure` with the Spack-derived arguments
- run `make V=1`, `make install`, and remove libtool archives

The mechanism-specific dependency verifier reports this build as:

```text
native/gettext/gettext.bzl: autotools: BZIP2_PREFIX, LIBICONV_PREFIX, LIBXML2_PREFIX, NCURSES_PREFIX, TAR_PREFIX, XZ_PREFIX
```

## Emitted prefix contract

The ABI/behavior-relevant install surface includes:

- headers: `include/gettext-po.h`, `include/autosprintf.h`,
  `include/textstyle/*.h`
- shared libraries: `lib/libasprintf.so.0.0.0`,
  `lib/libgettextlib-1.0.so`, `lib/libgettextpo.so.0.6.0`,
  `lib/libgettextsrc-1.0.so`, `lib/libtextstyle.so.0.2.7`,
  `lib/preloadable_libintl.so`
- executables: `bin/gettext`, `bin/envsubst`, `bin/msgfmt`, `bin/xgettext`,
  `bin/autopoint`, plus the other installed gettext tools
- data/macro payload: `share/aclocal/nls.m4`,
  `share/gettext/archive.dir.tar.xz`, `share/gettext/msgunfmt.tcl`

The observed executable dynamic dependency contract for the parity set is:

- `bin/gettext`: `libc.so.6`, `libiconv.so.2`
- `bin/envsubst`: `libc.so.6`, `libiconv.so.2`
- `bin/msgfmt`: `libc.so.6`, `libgettextlib-1.0.so`,
  `libgettextsrc-1.0.so`
- `bin/xgettext`: `libc.so.6`, `libgettextlib-1.0.so`,
  `libgettextsrc-1.0.so`, `libiconv.so.2`, `libtextstyle.so.0`
- `bin/autopoint`: script, no ELF NEEDED set

## Prefix, ABI, and behavior gate

`//synthetic:gettext_abi_parity` compares `@gettext_native//:prefix` against
the hermetic Spack reference prefix:

- layout: headers, shared libraries, selected executables, and selected data
- ABI: SONAME and exported dynamic symbols for every shared object in the
  reference prefix
- data: exact SHA256 for `share/aclocal/nls.m4`,
  `share/gettext/archive.dir.tar.xz`, and `share/gettext/msgunfmt.tcl`
- executable contract: matching `readelf -d` NEEDED sets for the selected
  executables
- link-and-run: downstream C consumer uses `libgettextpo` to read a PO header
  field and checks the exported `libgettextpo_version`
- behavior: identical `gettext --version`, `envsubst --version`, `msgfmt`
  compilation of a fixture `.po` file, and `xgettext` extraction from a C
  fixture

Verified native status:

```text
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 SPACK_ROOT_PKG=python VASO_NATIVE=1 VASO_SPACK_TIMEOUT=600 VASO_FORCE_FETCH_REPOS='@gettext_native' ./run.sh
rootfs mode: cuda-bundle (base root: $HOME/.vaso-estate/rootfs)
hermetic spack (Bazel-owned) version: 1.2.2
//tools:hermetic_native_deps_guard_test PASSED
native/gettext/gettext.bzl: autotools: BZIP2_PREFIX, LIBICONV_PREFIX, LIBXML2_PREFIX, NCURSES_PREFIX, TAR_PREFIX, XZ_PREFIX
//synthetic:use_gettext PASSED
//synthetic:gettext_abi_parity PASSED in 0.8s
```

The gettext gate reported `candidate_count = reference_count = 32`, exact
SHA256 matches for the selected data payloads, matching executable NEEDED
sets, matching behavior for the selected gettext tools, and matching
downstream consumer output:

```text
gettextpo:en:10000
```

Current status: native provider green inside the CUDA insula. The next
unmigrated frontier is `python`.
