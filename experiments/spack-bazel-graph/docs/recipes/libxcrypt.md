# libxcrypt frontier recipe

## Position in the hillclimb

`libxcrypt@4.5.2` is the migrated py-torch frontier node after
`automake@1.18.1`. In the captured `SPACK_ROOT_PKG=py-torch` graph, the nearby
entries are:

```text
54  automake  1.18.1  autotools  native; prefix parity green
55  libxcrypt 4.5.2   autotools  native; ABI parity green
56  openssl   3.6.1   generic    native in ledger
57  coreutils 9.10    autotools  next unresolved frontier
```

The package-local reference graph was captured inside the CUDA insula with
Bazel's vendored Spack:

```bash
VASO_NATIVE=1 \
SPACK_ROOT_PKG='libxcrypt@4.5.2' \
VASO_LOCK_OUT=/workspace/experiment/libxcrypt_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/libxcrypt_build_graph.json \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_libxcrypt_native //tools:hermetic_native_deps_guard_test //synthetic:libxcrypt_abi_parity' \
VASO_SPACK_TIMEOUT=1200 \
./run.sh
```

That run completed with `rootfs mode: cuda-bundle`, wrote a 12-package
`libxcrypt_spack_graph.lock.json`, and wrote a 23-node
`libxcrypt_build_graph.json` with `autotools=15` and `generic=8`.

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/libxcrypt/package.py
```

Source provenance from the Spack recipe:

- package class: `Libxcrypt(AutotoolsPackage)`
- version: `4.5.2`
- upstream source URL:
  `https://github.com/besser82/libxcrypt/releases/download/v4.5.2/libxcrypt-4.5.2.tar.xz`
- SHA256:
  `71513a31c01a428bccd5367a32fd95f115d6dac50fb5b60c779d5c7942aec071`
- concrete variant: `~obsolete_api`
- declared recipe dependency: `perl@5.14:+open` as a build dependency
- package-specific patches do not apply to `4.5.2`
- configure arguments:
  `ac_cv_path_python3_passlib=not found`, `--disable-werror`,
  `--disable-obsolete-api`

The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/libxcrypt-4.5.2-yhue7ak6oqvcmubvzmyvijhdk7orqteb
```

The stable public prefix surface for this migration is:

```text
include/crypt.h
lib/libcrypt.a
lib/libcrypt.so
lib/libcrypt.so.2
lib/libcrypt.so.2.0.0
lib/pkgconfig/libcrypt.pc
lib/pkgconfig/libxcrypt.pc
```

## Build recipe

`native/libxcrypt/libxcrypt.bzl` mirrors Spack's Autotools flow:

```text
cd <src>
PATH=<perl-prefix>/bin:$PATH
PERL=<perl-prefix>/bin/perl
./configure --prefix=<prefix> \
  ac_cv_path_python3_passlib="not found" \
  --disable-werror \
  --disable-obsolete-api
make V=1
make install
find <prefix> -type f -name '*.la' -delete
```

The repository rule fetches the same upstream tarball by SHA256 and refuses to
run unless the hermetic insula has set `VASO_IN_INSULA=1`. Its build-only Perl
dependency is not discovered from the host: `perl_prefix_file` is a mandatory
Bazel label, read by the repository rule, validated by the shell build, and
passed through the Autotools build-tool channel.

This is the Autotools dependency-prefix verifier case:

```text
native/libxcrypt/libxcrypt.bzl: autotools: PERL_PREFIX
```

`//tools:hermetic_native_deps_guard_test` checks that the Perl prefix enters
through a Bazel-owned file and a mechanism-specific Autotools channel rather
than host discovery.

## Prefix and behavior gate target

`//synthetic:libxcrypt_abi_parity` compares the native prefix against the
hermetic Spack reference. The gate covers:

- layout parity for the seven installed header, library, symlink, and
  pkg-config paths;
- SONAME parity for `lib/libcrypt.so.2.0.0` (`libcrypt.so.2`);
- exported dynamic symbol parity for `libcrypt.so.2.0.0` (9 symbols);
- static archive member and symbol parity for `lib/libcrypt.a` (36 members and
  116 symbols);
- prefix-normalized `lib/pkgconfig/libcrypt.pc` and
  `lib/pkgconfig/libxcrypt.pc`;
- downstream `crypt()` link-and-run behavior with matching output
  `libxcrypt:4.5.2:116`.

The smoke target prints:

```text
libxcrypt:4.5.2:116
```

Current status: native and ABI/behavior-gated. The latest focused run passed
inside the CUDA insula with `rootfs mode: cuda-bundle`, used Bazel-owned Spack
`1.2.2`, flipped `spack_libxcrypt` to `@libxcrypt_native//:lib`, and passed:

```text
//synthetic:spack_selfcheck
//tools:hermetic_spack_guard_test
//tools:hermetic_native_deps_guard_test
//synthetic:use_libxcrypt_native
//synthetic:libxcrypt_abi_parity
```
