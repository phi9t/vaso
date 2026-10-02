# glib-bootstrap native recipe

## Position in the hillclimb

`glib-bootstrap@2.88.1` is the first real MesonPackage captured after the
native `meson@1.11.1` tool provider in the lean `py-torch` frontier. The
focused reference graph for `SPACK_ROOT_PKG='glib-bootstrap@2.88.1
^python@3.13.13'` has 41 build-graph nodes:

```text
0..38  bootstrap/tool/library prerequisites
39     meson             python_pip
40     glib-bootstrap    meson
```

Spack still owns the DAG shape. The native flip changes only the provider for
`spack_glib_bootstrap` to `@glib_bootstrap_native//:lib`; consumers keep using
the generated `@spack_glib_bootstrap//:lib` node and the concrete Spack
dependency edges.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack`, pinned in
`MODULE.bazel` to upstream Spack `v1.2.2`, running inside the CUDA insula. Do
not use an ambient host Spack checkout for this node.

Reference prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/glib-bootstrap-2.88.1-ivdcfvm2b4ozdqgpj2z3izom3lw3g24g
```

Hermetic recipe facts:

- package class: `GlibBootstrap(MesonPackage)`
- source archive:
  `https://download.gnome.org/sources/glib/2.88/glib-2.88.1.tar.xz`
- version `2.88.1` SHA256:
  `51ab804c56f6eab3e5045c774d1290ac5e4c923d4f9a3d8e33123bee45c1840e`
- dependencies in the focused lock: `gettext`, `libffi`, `libiconv`, `pcre2`,
  `perl`, `python@3.13.13`, `python-venv`, and `zlib-ng`
- build system: Spack `meson`
- no patches were applied for this version

The concrete Spack Meson setup command was:

```text
<meson-prefix>/bin/meson setup <source> \
  -Dprefix=<glib-bootstrap-prefix> \
  -Dlibdir=<glib-bootstrap-prefix>/lib \
  -Dbuildtype=release \
  -Dstrip=false \
  -Ddefault_library=shared \
  -Dwrap_mode=nodownload \
  -Dselinux=disabled \
  -Dlibmount=disabled \
  -Dman-pages=disabled \
  -Ddtrace=disabled \
  -Dsystemtap=disabled \
  -Dsysprof=disabled \
  -Dtests=false \
  -Dnls=disabled \
  -Dlibelf=disabled \
  -Dintrospection=disabled
```

Spack then ran `ninja -v` and `ninja install`.

## Native build recipe

`native/glib_bootstrap/glib_bootstrap.bzl` preserves that Meson interface
inside the CUDA insula:

- refuses to evaluate unless `VASO_IN_INSULA=1`
- fetches the exact GLib 2.88.1 tarball by SHA256
- reads dependency prefixes from Bazel-owned native `prefix_path.txt` files
- consumes `@python_313_native`, derives `PYTHON_ABI` from that prefix, and
  validates `bin/python${PYTHON_ABI}` before configure
- validates Meson, Ninja, pkgconf, Perl, Python, python-venv, gettext, libffi,
  libiconv, pcre2, and zlib-ng before configure
- pins `PKG_CONFIG` to native pkgconf and builds `PKG_CONFIG_PATH`,
  `CPPFLAGS`, `CFLAGS`, `CXXFLAGS`, `LDFLAGS`, and `LD_LIBRARY_PATH` from
  native dependency prefixes
- exposes Meson's package under
  `MESON_PREFIX/lib/python${PYTHON_ABI}/site-packages` on `PYTHONPATH`
- invokes `"$MESON_PREFIX/bin/meson" setup` with the Spack options above
- invokes `"$NINJA_PREFIX/bin/ninja" -v` and
  `"$NINJA_PREFIX/bin/ninja" install`
- removes libtool archives and validates representative installed headers,
  pkg-config files, tools, and `lib/glib-2.0/include/glibconfig.h`

The mechanism-specific dependency verifier reports this build as `meson` and
requires the Meson/Ninja/pkgconf prefix channel:

```text
native/glib_bootstrap/glib_bootstrap.bzl: meson: GETTEXT_PREFIX, LIBFFI_PREFIX, LIBICONV_PREFIX, MESON_PREFIX, NINJA_PREFIX, PCRE2_PREFIX, PERL_PREFIX, PKGCONF_PREFIX, PYTHON_PREFIX, PYTHON_VENV_PREFIX, ZLIB_PREFIX
```

## Emitted prefix contract

The public ABI/link surface from the generated lock is:

- include dirs: `include`, `include/gio-unix-2.0`, `include/glib-2.0`
- generated include dir required by real consumers:
  `lib/glib-2.0/include`
- link libraries: `gio-2.0`, `girepository-2.0`, `glib-2.0`,
  `gmodule-2.0`, `gobject-2.0`, and `gthread-2.0`
- public shared library sonames:
  `libgio-2.0.so.0`, `libgirepository-2.0.so.0`, `libglib-2.0.so.0`,
  `libgmodule-2.0.so.0`, `libgobject-2.0.so.0`,
  `libgthread-2.0.so.0`
- stable metadata/tools: `lib/pkgconfig/glib-2.0.pc`,
  `lib/pkgconfig/gobject-2.0.pc`, `lib/pkgconfig/gio-2.0.pc`,
  `lib/pkgconfig/gthread-2.0.pc`, `bin/glib-compile-schemas`,
  `bin/glib-genmarshal`, `bin/glib-mkenums`,
  `share/aclocal/glib-2.0.m4`, `share/aclocal/glib-gettext.m4`,
  `share/aclocal/gsettings.m4`

## Prefix, ABI, and behavior gate

The smoke target is:

```text
//synthetic:use_glib_bootstrap_native
```

It compiles and runs a downstream C consumer against
`@glib_bootstrap_native//:lib`, including `<glib.h>`, `<glib-object.h>`,
`<gmodule.h>`, and `<glib-unix.h>`. The consumer checks the GLib version,
basic string helpers, module support, and the `GObject` type name.

The parity target is:

```text
//synthetic:glib_bootstrap_abi_parity
```

It compares `@glib_bootstrap_native//:prefix` against the hermetic Spack
reference prefix and covers:

- layout for headers, shared libraries, selected pkg-config files, and selected
  installed tools
- SONAME and exported-symbol parity for the six public shared libraries
- downstream link-and-run parity with native/reference dependency prefixes
  supplied explicitly for gettext, libffi, libiconv, pcre2, and zlib-ng
- executable behavior for `glib-compile-schemas --version` and
  `glib-mkenums --version`

The 2026-09-29 ticket-08 re-seat proof ran wholly inside the CUDA insula with
Bazel-owned Spack `1.2.2`, rootfs mode `cuda-bundle`, and estate
`$VASO_ESTATE_ROOT`. It regenerated
`root=spack_glib_bootstrap`, a 35-package lock, and a 41-node build graph.
`//synthetic:glib_bootstrap_abi_parity` passed with `"ok": true`, 338/338
layout parity, six-library SONAME/exported-symbol parity, matching
`glib-compile-schemas --version` and `glib-mkenums --version`, and matching
downstream C link-and-run output
`glib-bootstrap:2.88:glib-bootstrap:GObject`. The same run passed
`//synthetic:use_glib_bootstrap_native`,
`//tools:hermetic_native_deps_guard_test`,
`//tools:native_dep_wiring_live_test`,
`//tools:migration_ledger_check_live_test`, and
`//tools:migration_ledger_check_unit_test`; the wiring guard reported three
remaining ticket-08 allowlisted mismatches.

## ODR-sensitive provider invariant

This slice does not introduce protobuf, gRPC, Abseil, or Boost providers. Those
families remain exact-version override families because downstream consumers
must see one unified concrete version family to avoid ODR violations.
