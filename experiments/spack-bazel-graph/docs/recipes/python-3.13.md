# python@3.13.13 reference and native provider

## Position in the hillclimb

`python@3.13.13` is the decided CPython line for the lean `py-torch` frontier.
This document records the hermetic Spack reference prefix and the exact native
`python_313` provider that must match it before Python-bound frontier packages
are re-seated.

The focused reference graph was captured with:

```bash
SPACK_ROOT_PKG='python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib'
```

The generated side artifacts are:

```text
python_313_build_graph.json
python_313_spack_graph.lock.json
```

The hash gate matched the lean `py-torch` graph:

```text
python@3.13.13 spack_hash = 6u37x4adbcqbp247uvyjtokjsun4oewt
```

## Hermetic Spack Evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Spack state, the package repository, the source
cache, the concrete spec, configure arguments, install manifest, build log, and
reference prefix are all under `/vaso/cache/spack`; no ambient host Spack was
used.

Hermetic Spack source:

```text
Spack release: v1.2.2
builtin package repository: https://github.com/spack/spack-packages.git
builtin package repository branch: releases/v2026.06
```

Reference prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/python-3.13.13-6u37x4adbcqbp247uvyjtokjsun4oewt
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/python-3.13.13-6u37x4adbcqbp247uvyjtokjsun4oewt/.spack/repos/spack_repo/builtin/packages/python/package.py
```

Source provenance from that recipe:

- source URL:
  `https://www.python.org/ftp/python/3.13.13/Python-3.13.13.tgz`
- version `3.13.13` SHA256:
  `f9cde7b0e2ec8165d7326e2a0f59ea2686ce9d0c617dbbb3d66a7e54d31b74b9`
- Spack package class: `Python(Package)`
- Spack phases: `configure`, `build`, `install`
- concrete implementation path: CPython `./configure`, `make V=1`,
  `make install`

The focused graph contains 31 nodes and ends with:

```text
30  python  3.13.13  generic
```

The canonical ticket 07 verification graph resolves that node through the exact
native override:

```text
spack_python build = native
spack_python native_prefix = @python_313_native//:lib
spack_python version = 3.13.13
spack_python spack_hash = 6u37x4adbcqbp247uvyjtokjsun4oewt
```

The concrete dependency closure for the Python node is:

```text
bzip2@1.0.8
expat@2.8.1
gdbm@1.26
gettext@1.0
libffi@3.5.2
ncurses@6.6
openssl@3.6.1
pkgconf@2.5.1
readline@8.3
sqlite@3.53.1
util-linux-uuid@2.41
xz@5.8.3
zlib-ng@2.3.3
```

## Spack Configure Shape

The installed prefix records this concrete configure argument shape:

```text
CPPFLAGS=-I<zlib-ng>/include -I<xz>/include -I<util-linux-uuid>/include -I<sqlite>/include -I<readline>/include -I<openssl>/include -I<ncurses>/include -I<libffi>/include -I<gettext>/include -I<gdbm>/include -I<expat>/include -I<bzip2>/include -I/include -I<gcc-runtime>/include
LDFLAGS=-L<zlib-ng>/lib -L<xz>/lib -L<util-linux-uuid>/lib -L<sqlite>/lib -L<readline>/lib -L<openssl>/lib64 -L<ncurses>/lib -L<libffi>/lib -L<gettext>/lib -L<gdbm>/lib -L<expat>/lib -L<bzip2>/lib
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

Spack patches `Makefile.pre.in` before configure so `setup.py build` and
`setup.py install` run with `--no-user-cfg`, clears `PYTHONPATH` and
`PYTHONHOME` for the build, runs `make V=1`, installs with
`COMPILEALL_OPTS=-j<jobs>`, adds `python` and `python-config` symlinks when
`+pythoncmd` is enabled, and installs `bin/python3.13-gdb.py`.

## Installed Prefix Surface

The install manifest at
`<prefix>/.spack/install_manifest.json` has 3066 entries. Top-level manifest
counts:

```text
1     <prefix>
25    .spack
12    bin
270   include
2753  lib
5     share
```

The non-`.spack` filesystem surface has 2918 regular files, 10 symlinks, and
112 directories.

ABI- and consumer-relevant entries:

```text
bin/idle3 -> idle3.13
bin/idle3.13
bin/pydoc3 -> pydoc3.13
bin/pydoc3.13
bin/python -> <prefix>/bin/python3
bin/python-config -> <prefix>/bin/python3-config
bin/python3 -> python3.13
bin/python3-config -> python3.13-config
bin/python3.13
bin/python3.13-config
bin/python3.13-gdb.py
include/python3.13/**
lib/libpython3.13.so -> libpython3.13.so.1.0
lib/libpython3.13.so.1.0
lib/libpython3.so
lib/pkgconfig/python-3.13.pc
lib/pkgconfig/python-3.13-embed.pc
lib/python3.13/**
lib/python3.13/lib-dynload/*.cpython-313-x86_64-linux-gnu.so
lib/python3.13/site-packages/README.txt
share/man/man1/python3.13.1
```

`lib/libpython3.13.so.1.0` has SONAME `libpython3.13.so.1.0` and NEEDED entries
for `libm.so.6`, `libc.so.6`, and `ld-linux-x86-64.so.2`.

## Enabled Extension Modules

The prefix reports:

```text
sys.version 3.13.13
SOABI cpython-313-x86_64-linux-gnu
LDLIBRARY libpython3.13.so
LIBDIR <prefix>/lib
```

The feature modules selected by the Spack variants import successfully inside
the insula:

```text
bz2
ctypes
dbm
gzip
hashlib
lzma
readline
sqlite3
ssl
uuid
xml.etree.ElementTree
xml.parsers.expat
zlib
```

The installed `lib-dynload` directory contains 61 extension shared objects:

```text
_asyncio.cpython-313-x86_64-linux-gnu.so
_bisect.cpython-313-x86_64-linux-gnu.so
_blake2.cpython-313-x86_64-linux-gnu.so
_bz2.cpython-313-x86_64-linux-gnu.so
_codecs_cn.cpython-313-x86_64-linux-gnu.so
_codecs_hk.cpython-313-x86_64-linux-gnu.so
_codecs_iso2022.cpython-313-x86_64-linux-gnu.so
_codecs_jp.cpython-313-x86_64-linux-gnu.so
_codecs_kr.cpython-313-x86_64-linux-gnu.so
_codecs_tw.cpython-313-x86_64-linux-gnu.so
_contextvars.cpython-313-x86_64-linux-gnu.so
_csv.cpython-313-x86_64-linux-gnu.so
_ctypes.cpython-313-x86_64-linux-gnu.so
_curses.cpython-313-x86_64-linux-gnu.so
_curses_panel.cpython-313-x86_64-linux-gnu.so
_datetime.cpython-313-x86_64-linux-gnu.so
_decimal.cpython-313-x86_64-linux-gnu.so
_elementtree.cpython-313-x86_64-linux-gnu.so
_gdbm.cpython-313-x86_64-linux-gnu.so
_hashlib.cpython-313-x86_64-linux-gnu.so
_heapq.cpython-313-x86_64-linux-gnu.so
_interpchannels.cpython-313-x86_64-linux-gnu.so
_interpqueues.cpython-313-x86_64-linux-gnu.so
_interpreters.cpython-313-x86_64-linux-gnu.so
_json.cpython-313-x86_64-linux-gnu.so
_lsprof.cpython-313-x86_64-linux-gnu.so
_lzma.cpython-313-x86_64-linux-gnu.so
_md5.cpython-313-x86_64-linux-gnu.so
_multibytecodec.cpython-313-x86_64-linux-gnu.so
_multiprocessing.cpython-313-x86_64-linux-gnu.so
_opcode.cpython-313-x86_64-linux-gnu.so
_pickle.cpython-313-x86_64-linux-gnu.so
_posixshmem.cpython-313-x86_64-linux-gnu.so
_posixsubprocess.cpython-313-x86_64-linux-gnu.so
_queue.cpython-313-x86_64-linux-gnu.so
_random.cpython-313-x86_64-linux-gnu.so
_sha1.cpython-313-x86_64-linux-gnu.so
_sha2.cpython-313-x86_64-linux-gnu.so
_sha3.cpython-313-x86_64-linux-gnu.so
_socket.cpython-313-x86_64-linux-gnu.so
_sqlite3.cpython-313-x86_64-linux-gnu.so
_ssl.cpython-313-x86_64-linux-gnu.so
_statistics.cpython-313-x86_64-linux-gnu.so
_struct.cpython-313-x86_64-linux-gnu.so
_uuid.cpython-313-x86_64-linux-gnu.so
_zoneinfo.cpython-313-x86_64-linux-gnu.so
array.cpython-313-x86_64-linux-gnu.so
binascii.cpython-313-x86_64-linux-gnu.so
cmath.cpython-313-x86_64-linux-gnu.so
fcntl.cpython-313-x86_64-linux-gnu.so
grp.cpython-313-x86_64-linux-gnu.so
math.cpython-313-x86_64-linux-gnu.so
mmap.cpython-313-x86_64-linux-gnu.so
pyexpat.cpython-313-x86_64-linux-gnu.so
readline.cpython-313-x86_64-linux-gnu.so
resource.cpython-313-x86_64-linux-gnu.so
select.cpython-313-x86_64-linux-gnu.so
syslog.cpython-313-x86_64-linux-gnu.so
termios.cpython-313-x86_64-linux-gnu.so
unicodedata.cpython-313-x86_64-linux-gnu.so
zlib.cpython-313-x86_64-linux-gnu.so
```

## Reference Capture Command

The side graph and side lock were produced inside the CUDA insula with:

```bash
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib' \
VASO_SPACK_TIMEOUT=600 \
VASO_LOCK_OUT=/workspace/experiment/python_313_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/python_313_build_graph.json \
./run.sh
```

`run.sh` wrote the side lock and side graph, then refused to continue into
Bazel tests because the side lock is intentionally non-canonical:

```text
wrote /workspace/experiment/python_313_spack_graph.lock.json (26 packages, root=spack_python)
wrote /workspace/experiment/python_313_build_graph.json: 31 nodes, build systems {'autotools': 21, 'generic': 8, 'makefile': 2}
refusing to run Bazel tests against a non-canonical lock output
```

That refusal is expected for a reference-capture-only side lock. Native provider
implementation, exact override keys, and ABI/smoke targets are recorded below.

## Native Provider

The native provider is a second instance of the parametrized
`//native/python:python.bzl` repository rule:

```starlark
python_native(
    name = "python_313_native",
    version = "3.13.13",
    urls = ["https://www.python.org/ftp/python/3.13.13/Python-3.13.13.tgz"],
    sha256 = "f9cde7b0e2ec8165d7326e2a0f59ea2686ce9d0c617dbbb3d66a7e54d31b74b9",
    strip_prefix = "Python-3.13.13",
    ...
)
```

It uses the same Bazel-native dependency prefix files as the existing
`python_native` rule for bzip2, expat, gdbm, gettext, libffi, ncurses, openssl,
pkgconf, readline, sqlite, util-linux-uuid, xz, zlib-ng, and zstd. The
repository rule refuses host-root builds unless `VASO_IN_INSULA=1`, so the
configure/make/install action runs only inside the hermetic insula.

`native_overrides.json` now keys both live CPython providers exactly:

```json
"python@3.13.13": "@python_313_native//:lib",
"python@3.14.5": "@python_native//:lib"
```

The matching `provided_versions` entries are `3.13.13` and `3.14.5`, and the
ticket 07 `known_version_substitutions` entry is removed.

## Native Parity Evidence

The provider was built and verified inside the CUDA insula with Bazel 9.2.0,
Bazel-vendored Spack v1.2.2, and `VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT`:

```bash
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib' \
VASO_NATIVE=1 \
VASO_SPACK_TIMEOUT=600 \
VASO_EXTRA_TEST_TARGETS=//synthetic:python_313_abi_parity,//synthetic:use_python_313,//tools:hermetic_native_deps_guard_test \
./run.sh
```

The run completed with:

```text
//synthetic:use_python_313         PASSED, output: 3.13.13
//synthetic:python_313_abi_parity  PASSED in 12.7s
//tools:hermetic_native_deps_guard_test passed
== done (estate: $VASO_ESTATE_ROOT) ==
```

The ABI gate compared:

- complete selected layout: 336 entries, no missing or extra candidate paths
- `lib/libpython3.13.so.1.0` SONAME and 1682 exported symbols
- selected `lib/python3.13/lib-dynload/*.cpython-313-x86_64-linux-gnu.so`
  exported symbols
- prefix-normalized pkg-config files
- executable NEEDED sets for `bin/python3`, `bin/python3.13`,
  `bin/python3-config`, and `bin/python3.13-config`
- `bin/python3` version output, extension-module imports,
  `python3-config --ldflags --embed`, and embedding link-and-run
