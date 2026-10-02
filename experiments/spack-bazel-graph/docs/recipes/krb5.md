# krb5 frontier recipe

## Position in the hillclimb

`krb5@1.22.2` is the migrated PyTorch frontier node immediately after native
`flex`. In the lean PyTorch graph it appears as:

```text
80  krb5  1.22.2  autotools
```

The PyTorch root spec stays on the lean font topology:

```bash
SPACK_ROOT_PKG='py-torch cuda_arch=80,90,100 ^openblas~fortran ^font-util fonts:=encodings'
```

This `krb5` migration does not add any font resources. The only font payload in
the frontier remains native `font-util@1.4.1` with the replacement
`fonts:=encodings` resource set.

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/krb5/package.py
```

Source provenance from the Spack recipe:

- package class: `Krb5(AutotoolsPackage)`
- version: `1.22.2`
- upstream source URL:
  `https://kerberos.org/dist/krb5/1.22/krb5-1.22.2.tar.gz`
- SHA256:
  `3243ffbc8ea4d4ac22ddc7dd2a1dc54c57874c40648b60ff97009763554eaf13`
- concrete build system: Autotools
- configure directory: `src`
- build directory: `src`
- concrete variant: `+shared`

The concrete PyTorch-frontier dependency edges for `krb5@1.22.2` are:

```text
build: bison, compiler-wrapper, diffutils, findutils, gcc, gmake, perl, pkgconf
build/link: gettext, libedit, openssl
link: gcc-runtime, glibc
```

The hermetic Spack recipe maps this concrete variant to:

```text
--without-system-verto
--without-keyutils
--disable-static
CFLAGS=-fcommon
```

The package patch rewrites `src/configure` to include `<unistd.h>` before the
constructor test that otherwise fails with newer compilers.

## Build recipe

`native/krb5/krb5.bzl` mirrors the Spack Autotools flow:

```text
download krb5-1.22.2.tar.gz
patch src/configure constructor probe
cd <src>/src
PATH=<bison>:<diffutils>:<findutils>:<gettext>:<perl>:<pkgconf>:$PATH
BISON=<bison>/bin/bison
YACC="<bison>/bin/bison -y"
PERL=<perl>/bin/perl
PKG_CONFIG=<pkgconf>/bin/pkgconf
PKG_CONFIG_PATH=<libedit>/lib/pkgconfig:<openssl>/lib*/pkgconfig:<gettext>/lib/pkgconfig:<ncurses>/lib/pkgconfig
CPPFLAGS="-I<gettext>/include -I<libedit>/include -I<ncurses>/include -I<ncurses>/include/ncursesw -I<openssl>/include"
LDFLAGS="-L<gettext>/lib -L<libedit>/lib -L<ncurses>/lib -L<openssl>/lib* plus rpaths"
./configure --prefix=<prefix> --without-system-verto --without-keyutils --disable-static CFLAGS=-fcommon
make V=1
make install
delete .la and .a files
```

The native rule does not export a global `LIBS` override. MIT krb5 bakes global
`LIBS` into `bin/krb5-config`; Spack's generated script only contains
`LIBS='-lresolv '`, so dependency libraries are provided through the normal
probe/linker search channels instead.

The repository rule refuses to run unless the hermetic insula has set
`VASO_IN_INSULA=1`. Build and link dependencies are not discovered from the
host. These mandatory Bazel prefix files are read by the repository rule and
validated by the shell build before configure:

```text
BISON_PREFIX
DIFFUTILS_PREFIX
FINDUTILS_PREFIX
GETTEXT_PREFIX
LIBEDIT_PREFIX
NCURSES_PREFIX
OPENSSL_PREFIX
PERL_PREFIX
PKGCONF_PREFIX
```

The mechanism verifier covers this as an Autotools dependency case:

```text
native/krb5/krb5.bzl: autotools: BISON_PREFIX, DIFFUTILS_PREFIX, FINDUTILS_PREFIX, GETTEXT_PREFIX, LIBEDIT_PREFIX, NCURSES_PREFIX, OPENSSL_PREFIX, PERL_PREFIX, PKGCONF_PREFIX
```

## ABI and behavior gate

`//synthetic:krb5_abi_parity` compares the native prefix against the hermetic
Spack reference prefix supplied by `run.sh` through `SPACK_KRB5_PREFIX`. The
gate covers:

- installed layout parity for 98 files;
- SONAME and exported-symbol parity for all shared libraries and plugins;
- prefix-normalized `bin/krb5-config`;
- prefix-normalized pkg-config files: `krb5.pc`, `krb5-gssapi.pc`,
  `mit-krb5.pc`, and `mit-krb5-gssapi.pc`;
- executable dynamic dependency parity for `bin/krb5-config` and `bin/klist`;
- matching `bin/krb5-config --version` behavior;
- downstream C link-and-run behavior against `krb5_init_context` and
  `gss_indicate_mechs`.

The smoke target prints:

```text
krb5:1.22.2:gssapi-ok
```

Current focused verification command:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='krb5@1.22.2' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_krb5_native //synthetic:krb5_abi_parity //tools:hermetic_native_deps_guard_test //tools:native_build_mechanism_guard_unit_test //tools:abi_parity_unit_test' \
VASO_LOCK_OUT=/workspace/experiment/spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/krb5_native_build_graph.json \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

That command must be launched from the host only as the insula entrypoint. All
Spack operations and Bazel tests run inside the CUDA insula and use Bazel's
vendored `@spack_dist//:spack`; host Spack is not an input.
