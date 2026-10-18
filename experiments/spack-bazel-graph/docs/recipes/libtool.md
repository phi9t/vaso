# libtool frontier recipe

`libtool@2.5.4` is the next lean `py-torch` frontier node after native
`krb5`. The reference recipe and prefix were captured only through Bazel's
vendored Spack 1.2.2 inside the CUDA 12.9.1 insula.

Focused reference command:

```bash
SPACK_ROOT_PKG='libtool@2.5.4' \
VASO_NATIVE=0 \
VASO_LOCK_OUT=/workspace/experiment/libtool_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/libtool_build_graph.json \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

The run installed:

```text
/vaso/cache/spack/opt/spack/linux-icelake/libtool-2.5.4-bryi2yijbhnfnlpx4dei5b3okmuf2olp
```

and stopped at the non-canonical-lock guard, as expected for a side reference
capture.

## Hermetic Spack recipe

Recipe path in the estate:

```text
/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/libtool/package.py
```

Concrete recipe facts:

- source: `https://ftpmirror.gnu.org/libtool/libtool-2.5.4.tar.gz`
- sha256: `da8ebb2ce4dcf46b90098daf962cffa68f4b4f62ea60f798d0ef12929ede6adf`
- build system: `AutotoolsPackage`
- build directory: `spack-build`
- configure: `../configure --prefix=<prefix>`
- build/install: `make V=1`, then `make install`
- build dep: `m4@1.4.6:`
- build/run deps: `file`, `findutils`
- post-install: add `bin/glibtool -> libtool` and
  `bin/glibtoolize -> libtoolize`

The Spack build environment sets `M4=<m4-prefix>/bin/m4`, puts `file`,
`findutils`, `gmake`, and `m4` on `PATH`, and clears ambient compile/link
search variables.

## Prefix contract

The ABI-relevant surface is:

```text
bin/libtool
bin/libtoolize
bin/glibtool
bin/glibtoolize
include/ltdl.h
include/libltdl/*.h
lib/libltdl.so
lib/libltdl.so.7
lib/libltdl.so.7.3.3
lib/libltdl.a
share/aclocal/libtool.m4
share/aclocal/ltdl.m4
share/aclocal/lt*.m4
share/info/libtool.info*
share/man/man1/libtool.1
share/man/man1/libtoolize.1
share/libtool/**
```

`lib/libltdl.so.7.3.3` has SONAME `libltdl.so.7` and exports the `lt_dl*`
dynamic loader API.

## Native provider

`native/libtool/libtool.bzl` mirrors the Spack flow:

```text
download libtool-2.5.4.tar.gz
mkdir spack-build && cd spack-build
PATH=<file>:<findutils>:<m4>:$PATH
M4=<m4-prefix>/bin/m4
../configure --prefix=<prefix>
make V=1
make install
delete *.la
ln -sf libtool bin/glibtool
ln -sf libtoolize bin/glibtoolize
```

The rule refuses to evaluate outside `VASO_IN_INSULA=1` and validates
`FILE_PREFIX`, `FINDUTILS_PREFIX`, and `M4_PREFIX` before configure. The
mechanism guard must report:

```text
native/libtool/libtool.bzl: autotools: FILE_PREFIX, FINDUTILS_PREFIX, M4_PREFIX
```

## Verification

`//synthetic:use_libtool_native` links a downstream C consumer against
`libltdl`, calls `lt_dlinit`, sets/reads the search path, and prints:

```text
libtool:2.5.4:ltdl-ok
```

`//synthetic:libtool_abi_parity` compares the native prefix against the Spack
reference for headers, `libltdl` SONAME/exported symbols, static archive member
and symbol parity, selected scripts/docs/macros, executable behavior, and the
same downstream link-and-run output.
