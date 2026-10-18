# libffi native recipe

## Position in the hillclimb

`libffi` is the next non-toolchain C library in the installed `python` build DAG
after `berkeley-db`:

```text
0 ca-certificates-mozilla  generic
1 compiler-wrapper         generic [toolchain]
2 compiler-wrapper         generic [toolchain]
3 gcc                      autotools [toolchain]
4 gcc                      autotools [toolchain]
5 glibc                    autotools [toolchain]
6 gcc-runtime              generic [toolchain]
7 glibc                    autotools [toolchain]
8 gcc-runtime              generic [toolchain]
9 gmake                    generic [toolchain]
10 berkeley-db             autotools
11 libffi                  autotools
...
34 python                  generic
```

`spack_libffi` has been flipped from provider `spack` to provider `native`
without changing its DAG position or downstream edges.

## Spack evidence

Reference prefix:

`/vaso/cache/spack/opt/spack/linux-icelake/libffi-3.5.2-cmgacm24r7uza2trvu2l5lei75c27vmu`

The evidence comes from the Bazel-vendored Spack release, `@spack_dist`
currently pinned to upstream Spack `v1.2.2` in `MODULE.bazel`. The latest GitHub
release check for `spack/spack` returned `v1.2.2` published on
`2026-07-20T09:08:41Z`, so the Bazel pin matches the current release. No host or
standalone Spack checkout is part of this recipe.

Source provenance from the archived package recipe:

- Package repo path inside the hermetic Spack cache:
  `/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/libffi/package.py`
- Package class: `Libffi(AutotoolsPackage)`
- Homepage: `https://sourceware.org/libffi/`
- URL pattern:
  `https://github.com/libffi/libffi/releases/download/v3.4.2/libffi-3.4.2.tar.gz`
- Version `3.5.2` SHA256:
  `f3a3082a23b37c293a4fcd1053147b371f2ff91fa7ea1b2a52e335676bac82dc`
- Build dependencies: virtual `c` and `cxx`, concretized to the Spack compiler
  wrapper and GCC in the hermetic store.

Installed ABI-relevant files:

- `include/ffi.h`
- `include/ffitarget.h`
- `lib/libffi.so.8.2.0`
- `lib/libffi.so.8 -> libffi.so.8.2.0`
- `lib/libffi.so -> libffi.so.8.2.0`
- `lib/libffi.a`
- `lib/pkgconfig/libffi.pc`

The package's `headers` property searches recursively for `ffi` headers because
older libffi layouts can install them under target-specific library directories.
This hermetic prefix has the expected public headers directly under `include/`.

## Build recipe

For `3.5.2`, no package patch applies. Spack's active recipe logic is:

```python
def configure_args(self):
    args = ["--with-pic"]
    if self.spec.satisfies("@3.3:"):
        args.append("--without-gcc-arch")
    return args
```

The captured configure arguments are:

```text
--with-pic --without-gcc-arch
```

The archived build log shows the standard Autotools phases:

```text
==> libffi: Executing phase: 'autoreconf'
==> libffi: Executing phase: 'configure'
/vaso/tmp/spack-stage/.../spack-src/configure \
  --prefix=/vaso/cache/spack/opt/spack/linux-icelake/libffi-3.5.2-cmgacm24r7uza2trvu2l5lei75c27vmu \
  --with-pic \
  --without-gcc-arch
==> libffi: Executing phase: 'build'
/vaso/cache/spack/opt/spack/linux-icelake/gmake-.../bin/make V=1
==> libffi: Executing phase: 'install'
/vaso/cache/spack/opt/spack/linux-icelake/gmake-.../bin/make install
```

The configure script continues in libffi's generated build directory,
`x86_64-pc-linux-gnu`, via `--enable-builddir=x86_64-pc-linux-gnu`. The native
provider should preserve that out-of-source Autotools layout unless a simpler
invocation is proven prefix-identical.

Spack's Autotools plumbing also filters the generated `configure` script before
running it, including libtool search-path handling and
`lt_cv_apple_cc_single_mod=no`. The native provider should start by using the
release archive's generated `configure` script and the exact captured arguments,
then compare the emitted prefix against this reference.

The installed `lib/pkgconfig/libffi.pc` contract is:

```text
prefix=/vaso/cache/spack/opt/spack/linux-icelake/libffi-3.5.2-cmgacm24r7uza2trvu2l5lei75c27vmu
exec_prefix=${prefix}
libdir=${exec_prefix}/lib
toolexeclibdir=${libdir}/../lib
includedir=${prefix}/include

Name: libffi
Description: Library supporting Foreign Function Interfaces
Version: 3.5.2
Libs: -L${toolexeclibdir} -lffi
Cflags: -I${includedir}
```

## Native provider target

`native/libffi/libffi.bzl` defines `libffi_native`, a repository rule that
declares `VASO_IN_INSULA` as an environment input and refuses to build unless
the hermetic insula sets `VASO_IN_INSULA=1`. It fetches libffi `3.5.2` and emits
a prefix with the same headers, shared-library symlink chain, static archive,
and pkg-config file:

```sh
./configure \
  --prefix="$PREFIX" \
  --with-pic \
  --without-gcc-arch
make V=1 -j"${MAKE_JOBS:-$(nproc)}"
make install
find "$PREFIX" -type f -name '*.la' -delete
```

The provider flip remains a pure provider change: `spack_libffi` keeps the same
DAG position and downstream `link_deps`, while `native_overrides.json` maps
`libffi` to `@libffi_native//:lib`.

## ABI gate

`//synthetic:libffi_abi_parity` compares the native candidate prefix to the
hermetic Spack reference:

- layout: `include/ffi.h`, `include/ffitarget.h`, `libffi.so` symlink chain,
  `libffi.a`, and `lib/pkgconfig/libffi.pc`
- ABI: SONAME and exported dynamic symbols for `lib/libffi.so.8.2.0`
- link-and-run: `synthetic/use_libffi.c` calls through libffi, invoking an
  integer addition function with `ffi_prep_cif()` and `ffi_call()`, and compares
  stdout against the Spack prefix

Current verdict: migrated provider. With `libffi` enabled in
`native_overrides.json`, `SPACK_ROOT_PKG=python VASO_NATIVE=1
VASO_FORCE_FETCH_REPOS=@libffi_native ./run.sh` passes inside the CUDA insula.
The gate reports matching layout, matching SONAME (`libffi.so.8`), matching
exported symbols (42), and identical `ffi_call()` link-and-run output:

```text
libffi add: 42
```
