# gobject-introspection native recipe

## Position in the hillclimb

`gobject-introspection@1.86.0` is the native Meson provider after gated
`llvm@20.1.8` in the `py-torch` frontier. LLVM remains a gated skeleton until
an explicit `build-native-llvm` authorization arrives, so this slice activates
the `gobject-introspection` provider independently once its direct ABI/prefix
gate passes.

Spack still owns the DAG shape. The native flip changes only the provider for
`spack_gobject_introspection`; consumers keep depending on the generated Spack
graph node and the same concrete dependency edges.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack`, running
inside the CUDA insula. Do not use an ambient host Spack checkout for this
node.

Hermetic recipe facts:

- package class: `GobjectIntrospection(MesonPackage, AutotoolsPackage)`
- active build system for `1.86.0`: Meson
- source archive:
  `https://download.gnome.org/sources/gobject-introspection/1.86/gobject-introspection-1.86.0.tar.xz`
- version `1.86.0` SHA256:
  `920d1a3fcedeadc32acff95c2e203b319039dd4b4a08dd1a2dfd283d19c0b9ae`
- direct dependency prefixes in the resolved `py-torch` graph:
  `bison`, `flex`, `glib-bootstrap`, `libffi`, `meson`, `ninja`, `pkgconf`,
  `py-setuptools`, and `python`
- GLib `.pc` closure prefixes required for hermetic downstream pkg-config:
  `libiconv`, `pcre2`, and `zlib-ng`
- Spack patch for this concrete Python line:
  `setuptools.patch`, which imports `setuptools` before `distutils`
- build environment: `GI_SCANNER_DISABLE_CACHE=1`
- dependent build/run environment:
  `GI_TYPELIB_PATH=<prefix>/lib/girepository-1.0` and
  `XDG_DATA_DIRS=<prefix>/share`

For `1.86.0`, the `cairo+gobject` dependency in Spack is not active; it only
applies to `@:1.78`.

## Native build recipe

`native/gobject_introspection/gobject_introspection.bzl` preserves the Meson
interface inside the CUDA insula:

- refuses to evaluate unless `VASO_IN_INSULA=1`
- fetches the exact 1.86.0 GNOME source archive by SHA256
- applies the same `setuptools.patch` carried by hermetic Spack
- reads every dependency from a Bazel-owned `prefix_path.txt`
- validates Bison, Flex, GLib-bootstrap, libffi, libiconv, Meson, Ninja, pcre2,
  pkgconf, Python, py-setuptools, and zlib-ng before configure
- derives `PYTHON_ABI` from the native Python prefix, validates the matching
  `bin/python${PYTHON_ABI}`, `include/python${PYTHON_ABI}`, Meson
  `site-packages`, and py-setuptools `site-packages`, and composes
  `PYTHONPATH` from that ABI instead of hard-coding a Python minor
- pins `PKG_CONFIG` to native pkgconf and derives `PKG_CONFIG_PATH`,
  `CPPFLAGS`, `CFLAGS`, `CXXFLAGS`, `LDFLAGS`, `LD_LIBRARY_PATH`,
  `GI_TYPELIB_PATH`, and `XDG_DATA_DIRS` from declared prefixes
- invokes `"$MESON_PREFIX/bin/meson" setup` with Spack-standard Meson options:
  `-Dprefix=`, `-Dlibdir=`, `-Dbuildtype=release`, `-Dstrip=false`,
  `-Ddefault_library=shared`, and `-Dwrap_mode=nodownload`
- disables optional cairo/doctool/test/doc surfaces that are not dependencies
  for this concrete Spack node
- invokes `"$NINJA_PREFIX/bin/ninja"` for build and install

The mechanism-specific dependency verifier should report:

```text
native/gobject_introspection/gobject_introspection.bzl: meson: BISON_PREFIX, FLEX_PREFIX, GLIB_BOOTSTRAP_PREFIX, LIBFFI_PREFIX, LIBICONV_PREFIX, MESON_PREFIX, NINJA_PREFIX, PCRE2_PREFIX, PKGCONF_PREFIX, PYTHON_PREFIX, PY_SETUPTOOLS_PREFIX, ZLIB_PREFIX
```

## Emitted prefix contract

The intended public surface is:

- tools: `bin/g-ir-scanner`, `bin/g-ir-compiler`, `bin/g-ir-generate`
- headers: `include/gobject-introspection-1.0/girepository.h`
- shared library: `lib/libgirepository-1.0.so*`
- metadata:
  `lib/pkgconfig/gobject-introspection-1.0.pc`,
  `share/gobject-introspection-1.0/Makefile.introspection`,
  `share/aclocal/introspection.m4`,
  `lib/girepository-1.0/*`

## Current gate

`native_overrides.json` flips only the exact concrete key
`gobject-introspection@1.86.0` to `@gobject_introspection_native//:lib`. The
gate runs against a canonical `gobject-introspection@1.86.0` Spack root so the
reference prefix comes from the Bazel-vendored hermetic Spack install, while
the candidate prefix comes from the Bazel-owned native Meson repository.

```bash
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
TMPDIR=$VASO_ESTATE_ROOT/agents/trae/tmp \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
SPACK_ROOT_PKG='gobject-introspection@1.86.0 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib' \
VASO_NATIVE=1 \
VASO_SPACK_TIMEOUT=600 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_FORCE_FETCH_REPOS='@gobject_introspection_native' \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_gobject_introspection_native,//synthetic:gobject_introspection_abi_parity,//tools:hermetic_native_deps_guard_test,//tools:native_dep_wiring_live_test,//tools:migration_ledger_check_live_test,//tools:migration_ledger_check_unit_test' \
./run.sh
```

Verified gate evidence:

- rootfs mode: `cuda-bundle`
- Spack source: Bazel-owned `@spack_dist//:spack`; GitHub latest release
  checked as `v1.2.2`, matching `MODULE.bazel.lock`
- hermetic Spack version: `1.2.2`
- generated lock: 41 packages, root `spack_gobject_introspection`
- generated build graph: 47 nodes, with `gobject-introspection` at focused
  topo index 46
- selected concrete prefixes:
  `/vaso/cache/spack/opt/spack/linux-icelake/gobject-introspection-1.86.0-snir7xpbdns3jlf2qpwv6wfl62m6myep`
  and
  `/vaso/cache/spack/opt/spack/linux-icelake/python-3.13.13-6u37x4adbcqbp247uvyjtokjsun4oewt`
- mechanism verifier includes the Meson dependency line above
- `//tools:hermetic_native_deps_guard_test`,
  `//tools:native_dep_wiring_live_test`,
  `//tools:migration_ledger_check_live_test`, and
  `//tools:migration_ledger_check_unit_test` passed in the same insula run
- `//synthetic:use_gobject_introspection_native` passed with output:
  `gobject-introspection:girepository-1.0`
- `//synthetic:gobject_introspection_abi_parity` passed with 33/33 layout
  paths, no missing or extra candidate paths, SONAME/exported-symbol parity for
  `lib/libgirepository-1.0.so.1.0.0` and
  `lib/gobject-introspection/giscanner/_giscanner.cpython-313-x86_64-linux-gnu.so`,
  prefix-normalized `lib/pkgconfig/gobject-introspection-1.0.pc`, exact
  `share/aclocal/introspection.m4` and
  `share/gobject-introspection-1.0/Makefile.introspection` parity,
  `g-ir-compiler --version` and `g-ir-generate --version` behavior parity,
  executable NEEDED parity, and matching downstream C link-and-run output
- full insula log:
  `$VASO_ESTATE_ROOT/agents/trae/logs/gobject-introspection-insula-20260929T093958Z.log`
