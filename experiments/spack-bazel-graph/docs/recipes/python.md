# python native recipe

## Position in the hillclimb

`python` is topo index 34 and the root of the current installed Python graph.
It follows native `gettext`; migrating it completes the `SPACK_ROOT_PKG=python`
frontier:

```text
32 tar     autotools
33 gettext autotools
34 python  generic
```

The generated lock keeps Spack's DAG edges while flipping only the provider:

```json
{
  "package": "python",
  "version": "3.14.5",
  "build": "native",
  "native_prefix": "@python_native//:lib",
  "link_deps": [
    "spack_bzip2",
    "spack_expat",
    "spack_gdbm",
    "spack_gettext",
    "spack_libffi",
    "spack_ncurses",
    "spack_openssl",
    "spack_readline",
    "spack_sqlite",
    "spack_util_linux_uuid",
    "spack_xz",
    "spack_zlib_ng",
    "spack_zstd"
  ],
  "link_libs": ["python3", "python3.14"],
  "include_dirs": ["include", "include/python3.14"]
}
```

## Spack evidence

All evidence here comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Recipe source, concrete spec, configure arguments, install manifest, build log,
and reference prefix are from the hermetic `/vaso/cache/spack` store inside the
CUDA insula. Do not use an ambient host Spack checkout for this node.

Reference prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/python-3.14.5-solfiatysur6w3u4vivttg4slpszvhu7
```

Hermetic recipe path:

```text
/vaso/cache/spack/opt/spack/linux-icelake/python-3.14.5-solfiatysur6w3u4vivttg4slpszvhu7/.spack/repos/spack_repo/builtin/packages/python/package.py
```

Source provenance:

- source URL: `https://www.python.org/ftp/python/3.14.5/Python-3.14.5.tgz`
- source SHA256:
  `9c22bfe9939a6c5418fc74b289a5f1cc41859ae82ac6b163016b5844bd0a86bc`
- build system in Spack: `generic`
- concrete implementation path: CPython `./configure`, `make`, `make install`
- install manifest size: 3185 entries in the hermetic Spack prefix

The link dependency set is the concrete Spack DAG: `bzip2`, `expat`, `gdbm`,
`gettext`, `libffi`, `ncurses`, `openssl`, `readline`, `sqlite`,
`util-linux-uuid`, `xz`, `zlib-ng`, and `zstd`.

## Build recipe

The concrete Spack configure shape is:

```text
CPPFLAGS=<dependency include flags>
LDFLAGS=<dependency library flags>
--without-pydebug
--enable-shared
--without-static-libpython
--disable-test-modules
--without-ensurepip
--with-openssl=<openssl prefix>
--with-dbmliborder=gdbm
--with-system-expat
py_cv_module__tkinter=n/a
CFLAGS=-fPIC
```

The native provider preserves that CPython configure/make interface inside the
CUDA insula:

- refuse to run unless `VASO_IN_INSULA=1`
- fetch CPython 3.14.5 by the same SHA256
- consume `prefix_path.txt` files from every native dependency prefix
- validate every dependency prefix before configure
- pin `PKG_CONFIG` to the Bazel-built `@pkgconf_native` executable
- pass all dependency include and library paths through `CPPFLAGS`,
  `LDFLAGS`, `PKG_CONFIG_PATH`, and `LD_LIBRARY_PATH`
- pass `--with-openssl`, `--with-dbmliborder=gdbm`, and
  `--with-system-expat` exactly as package-specific configure inputs
- clear `PYTHONPATH` and `PYTHONHOME` during the build
- run `make V=1`, `make install`, add Spack-compatible `python` and
  `python-config` symlinks, and delete libtool archives if any appear

Although Spack labels the recipe `generic`, the native build mechanism verifier
classifies the Bazel-native rule by the actual build action. For this package it
reports an Autotools-style dependency channel:

```text
native/python/python.bzl: autotools: BZIP2_PREFIX, EXPAT_PREFIX, GDBM_PREFIX, GETTEXT_PREFIX, LIBFFI_PREFIX, NCURSES_PREFIX, OPENSSL_PREFIX, PKGCONF_PREFIX, READLINE_PREFIX, SQLITE_PREFIX, UTIL_LINUX_UUID_PREFIX, XZ_PREFIX, ZLIB_PREFIX, ZSTD_PREFIX
```

This is the corresponding hermetic-deps verifier for the CPython
configure/make build mechanism. The same guard also verifies the CMake,
Makefile, generic, and Python-wheel native rule classes in this experiment.

## Emitted prefix contract

The ABI/behavior-relevant install surface includes:

- executables: `bin/python3`, `bin/python3.14`, `bin/python3-config`,
  `bin/python3.14-config`
- headers: `include/python3.14/**`
- shared libraries: `lib/libpython3.14.so.1.0`, `lib/libpython3.so`, and the
  enabled extension modules under `lib/python3.14/lib-dynload`
- pkg-config files: `lib/pkgconfig/python-3.14.pc`,
  `lib/pkgconfig/python-3.14-embed.pc`, `lib/pkgconfig/python3.pc`,
  `lib/pkgconfig/python3-embed.pc`
- runtime library tree: the selected `lib/python3.14/**` standard-library
  payload emitted by Spack with tests disabled and ensurepip disabled

The parity gate includes import coverage for the enabled native extension
dependencies:

```python
import ssl, sqlite3, bz2, lzma, zlib, ctypes, uuid
import dbm.gnu, readline, pyexpat, compression.zstd
```

## Prefix, ABI, and behavior gate

`//synthetic:python_abi_parity` compares `@python_native//:prefix` against the
hermetic Spack reference prefix:

- layout: selected runtime/header/executable/pkg-config surface
- ABI: SONAME and exported dynamic symbols for `libpython` and every selected
  shared extension module
- data: prefix-normalized pkg-config files
- executable contract: matching `readelf -d` NEEDED sets for the selected
  installed executables
- link-and-run: downstream C embedding consumer initializes CPython and prints
  the major/minor version
- behavior: identical `python3` version output, identical enabled-extension
  import smoke output, and identical `python3-config --ldflags --embed` output

Verified native status:

```text
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 SPACK_ROOT_PKG=python VASO_NATIVE=1 VASO_SPACK_TIMEOUT=600 VASO_FORCE_FETCH_REPOS='@libxml2_native @gdbm_native @gettext_native @less_native @readline_native @sqlite_native @python_native' ./run.sh
rootfs mode: cuda-bundle (base root: $HOME/.vaso-estate/rootfs)
hermetic spack (Bazel-owned) version: 1.2.2
//tools:hermetic_spack_guard_test PASSED
//tools:hermetic_native_deps_guard_test PASSED
//synthetic:use_python PASSED
//synthetic:python_abi_parity PASSED in 1.2s
```

The Python gate reported `candidate_count = reference_count = 355`, matching
`libpython3.14.so.1.0` SONAME and 1832 exported symbols, matching enabled
extension-module symbol surfaces, prefix-normalized pkg-config parity, matching
selected executable NEEDED sets, matching downstream embedding output, and
matching runtime smoke output:

```text
python:3.14
3.14.5
OpenSSL 3.53.1 1.3.1.zlib-ng
```

Current status: native provider green inside the CUDA insula. The installed
`SPACK_ROOT_PKG=python` graph has no remaining unmigrated frontier node.
