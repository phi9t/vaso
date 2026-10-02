# glib native recipe

## Position in the hillclimb

`glib@2.88.1` is the full GLib frontier node after native
`gobject-introspection@1.86.0` in the PyTorch graph. It is distinct from
`glib-bootstrap`: bootstrap exists only to break the
GLib/gobject-introspection cycle, while this provider preserves Spack's full
`+introspection ~libmount tracing=none` GLib package.

Spack still owns the DAG shape. The native flip changes only the provider for
`spack_glib` to `@glib_native//:lib`; generated Spack dependency edges remain
unchanged.

## Hermetic Spack evidence

All evidence comes from Bazel's vendored `@spack_dist//:spack` running inside
the CUDA insula. Do not use an ambient host Spack checkout.

The focused reference graph was generated with:

```bash
SPACK_ROOT_PKG='glib@2.88.1+introspection~libmount tracing=none ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib'
```

Hermetic recipe facts:

- package class: `Glib(MesonPackage)`
- source archive:
  `https://download.gnome.org/sources/glib/2.88/glib-2.88.1.tar.xz`
- version `2.88.1` SHA256:
  `51ab804c56f6eab3e5045c774d1290ac5e4c923d4f9a3d8e33123bee45c1840e`
- concrete parameters:
  `buildtype=release`, `default_library=shared`, `strip=false`,
  `introspection=true`, `libmount=false`, `tracing=none`
- direct dependency prefixes:
  `elfutils`, `gettext`, `glib-bootstrap`, `gobject-introspection`, `libffi`,
  `libiconv`, `meson`, `ninja`, `pcre2`, `perl`, `pkgconf`, `python`,
  `python-venv`, `py-setuptools`, and `zlib-ng`
- selected Python node: `python@3.13.13`, served by
  `@python_313_native//:lib`
- generated reference prefix:
  `/vaso/cache/spack/opt/spack/linux-icelake/glib-2.88.1-brhbba2snxgkaassdylg52j34343hfc7`

For this version the Spack recipe applies no source patches. Spack's relevant
Meson options are:

```text
-Dintrospection=enabled
-Dlibmount=disabled
-Ddtrace=false
-Dsystemtap=false
-Dselinux=disabled
-Dgtk_doc=false
-Dlibelf=enabled
-Dsysprof=disabled
```

## Native build recipe

`native/glib/glib.bzl` reproduces that Meson build inside the insula:

- refuses to run unless `VASO_IN_INSULA=1`
- downloads the exact GLib 2.88.1 tarball by SHA256
- reads every dependency through a Bazel-owned `prefix_path.txt`
- derives `PYTHON_ABI` from the Bazel-built Python prefix and validates
  `python${PYTHON_ABI}`, `include/python${PYTHON_ABI}`, Meson's
  `mesonbuild`, and py-setuptools under that ABI's `site-packages`
- validates Meson, Ninja, pkgconf, gobject-introspection, GLib-bootstrap,
  elfutils, gettext, libffi, libiconv, pcre2, Perl, Python, python-venv,
  py-setuptools, and zlib-ng before configure
- pins `PKG_CONFIG` to native pkgconf and derives `PKG_CONFIG_PATH`,
  `CPPFLAGS`, `CFLAGS`, `CXXFLAGS`, `LDFLAGS`, and `LD_LIBRARY_PATH` from
  declared prefixes
- exposes Meson and py-setuptools on ABI-derived `PYTHONPATH` entries and seeds
  `GI_TYPELIB_PATH`/`XDG_DATA_DIRS` from `gobject-introspection` and
  `glib-bootstrap`
- invokes `"$MESON_PREFIX/bin/meson" setup` with Spack's GLib options
- invokes `"$NINJA_PREFIX/bin/ninja"` for build and install

The mechanism-specific dependency verifier should report:

```text
native/glib/glib.bzl: meson: ELFUTILS_PREFIX, GETTEXT_PREFIX, GLIB_BOOTSTRAP_PREFIX, GOBJECT_INTROSPECTION_PREFIX, LIBFFI_PREFIX, LIBICONV_PREFIX, MESON_PREFIX, NINJA_PREFIX, PCRE2_PREFIX, PERL_PREFIX, PKGCONF_PREFIX, PYTHON_PREFIX, PYTHON_VENV_PREFIX, PY_SETUPTOOLS_PREFIX, ZLIB_PREFIX
```

## Prefix and behavior gate

The smoke target is:

```text
//synthetic:use_glib_native
```

It compiles and runs a downstream C consumer against `@glib_native//:lib`,
including `<glib.h>`, `<glib-object.h>`, `<gmodule.h>`, and `<glib-unix.h>`.
The consumer checks the GLib version, string helpers, module support, and the
`GObject` type name.

The parity target is:

```text
//synthetic:glib_abi_parity
```

It compares `@glib_native//:prefix` against the hermetic Spack reference prefix
for:

- public headers and selected generated include/pkg-config metadata;
- SONAME and exported-symbol parity for the GLib shared libraries;
- downstream link-and-run parity against `gio-2.0`, `girepository-2.0`,
  `gobject-2.0`, `gmodule-2.0`, `gthread-2.0`, and `glib-2.0`;
- executable dependency parity and version behavior for
  `glib-compile-schemas` and `glib-mkenums`.

Focused verification should run through `run.sh` with the CUDA insula and the
Bazel-owned Spack distribution, for example:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
TMPDIR=$VASO_ESTATE_ROOT/agents/trae/tmp \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
SPACK_ROOT_PKG='glib@2.88.1+introspection~libmount tracing=none ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib' \
VASO_LOCK_OUT=/workspace/experiment/spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/build_graph.json \
VASO_NATIVE=1 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_FORCE_FETCH_REPOS='@glib_native' \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_glib_native,//synthetic:glib_abi_parity,//tools:hermetic_native_deps_guard_test,//tools:native_dep_wiring_live_test,//tools:migration_ledger_check_live_test,//tools:migration_ledger_check_unit_test' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

The 2026-09-29 proof ran in rootfs mode `cuda-bundle`, used Bazel-owned Spack
1.2.2, regenerated root `spack_glib`, a 43-package lock and a 49-node build
graph, and wrote
`$VASO_ESTATE_ROOT/agents/trae/logs/glib-insula-proof-20260929T100858Z.log`.
`//synthetic:use_glib_native` printed `glib:2.88:glib-native:GObject`.
`//synthetic:glib_abi_parity` passed with `"ok": true`, 338/338 layout paths,
SONAME/exported-symbol parity for the six GLib shared libraries, byte-identical
selected public headers, prefix-normalized GLib pkg-config files, matching
`glib-compile-schemas --version` and `glib-mkenums --version` output, executable
NEEDED parity, and matching downstream C link-and-run output. The same insula
run passed the hermetic native dependency guard, native dependency wiring live
guard, and migration ledger live/unit checks; the only remaining ticket-08
wiring mismatch was `pthreadpool_native.python_prefix_file`.

## ODR-sensitive provider invariant

This slice does not introduce protobuf, gRPC, Abseil, or Boost providers.
Those families remain exact-version override families so downstream consumers
see one unified concrete version family.
