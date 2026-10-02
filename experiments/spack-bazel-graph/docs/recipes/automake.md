# automake frontier recipe

## Position in the hillclimb

`automake@1.18.1` is the migrated py-torch frontier node after
`autoconf@2.72`. In the captured `SPACK_ROOT_PKG=py-torch` graph, the nearby
entries are:

```text
52  perl      5.42.0  generic    native in ledger
53  autoconf  2.72    autotools  native; prefix parity green
54  automake  1.18.1  autotools  native; prefix parity green
55  libxcrypt 4.5.2   autotools  next unresolved frontier
```

The package-local reference graph was captured inside the CUDA insula with
Bazel's vendored Spack:

```bash
VASO_NATIVE=1 \
SPACK_ROOT_PKG='automake@1.18.1' \
VASO_LOCK_OUT=/workspace/experiment/automake_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/automake_build_graph.json \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_automake_native //tools:hermetic_native_deps_guard_test //tools:native_build_mechanism_guard_unit_test //tools:abi_parity_unit_test' \
VASO_SPACK_TIMEOUT=1200 \
./run.sh
```

That run completed with `rootfs mode: cuda-bundle`, wrote a 15-package
`automake_spack_graph.lock.json`, and wrote a 26-node
`automake_build_graph.json` with `autotools=18` and `generic=8`.

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/automake/package.py
```

Source provenance from the Spack recipe:

- package class: `Automake(AutotoolsPackage, GNUMirrorPackage)`
- version: `1.18.1`
- upstream source URL: `https://ftp.gnu.org/gnu/automake/automake-1.18.1.tar.gz`
- SHA256:
  `63e585246d0fc8772dffdee0724f2f988146d1a3f1c756a3dc5cfbefa3c01915`
- declared recipe dependencies: `autoconf@2.65:` as a build dependency and
  `perl+threads` as a build/run dependency
- concrete lock dependency edge: `spack_perl`; runtime behavior still requires
  Autoconf tools such as `autom4te` on `PATH`
- build directory: `spack-build`
- package-specific patch surface for `@1.16.3:`: `bin/aclocal.in` and
  `bin/automake.in` are rewritten from `#!@PERL@` to `#!/usr/bin/env perl`

The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/automake-1.18.1-6esaenz247f3oafaznjrg6h7y2udx2bo
```

The stable public prefix surface for this migration is executable, macro, Perl
module, and documentation data:

```text
bin/aclocal
bin/aclocal-1.18
bin/automake
bin/automake-1.18
share/aclocal-1.18/amversion.m4
share/aclocal-1.18/init.m4
share/aclocal-1.18/internal/ac-config-macro-dirs.m4
share/aclocal/README
share/automake-1.18/Automake/Config.pm
share/automake-1.18/Automake/General.pm
share/automake-1.18/am/header.am
share/automake-1.18/install-sh
share/automake-1.18/missing
share/doc/automake/amhello-1.0.tar.gz
share/info/automake.info
share/info/automake.info-1
share/info/automake.info-2
share/info/automake-history.info
share/man/man1/aclocal.1
share/man/man1/aclocal-1.18.1
share/man/man1/automake.1
share/man/man1/automake-1.18.1
```

## Build recipe

`native/automake/automake.bzl` mirrors Spack's out-of-tree Autotools flow:

```text
patch bin/aclocal.in and bin/automake.in to #!/usr/bin/env perl
mkdir -p <src>/spack-build
cd <src>/spack-build
PATH=<autoconf-prefix>/bin:<perl-prefix>/bin:$PATH
PERL=<perl-prefix>/bin/perl
../configure --prefix=<prefix>
make V=1
make install
find <prefix> -type f -name '*.la' -delete
```

The repository rule fetches the same upstream tarball by SHA256 and refuses to
run unless the hermetic insula has set `VASO_IN_INSULA=1`. Its dependencies are
not discovered from the host: `autoconf_prefix_file` and `perl_prefix_file`
are mandatory Bazel labels, read by the repository rule, validated by the shell
build, and passed through Autotools build-tool channels.

This is the Autotools dependency-prefix verifier case:

```text
native/automake/automake.bzl: autotools: AUTOCONF_PREFIX, PERL_PREFIX
```

`//tools:hermetic_native_deps_guard_test` checks that the Autoconf and Perl
prefixes enter through Bazel-owned files and mechanism-specific Autotools
channels rather than host discovery.

## Prefix and behavior gate target

`//synthetic:automake_prefix_parity` compares the native prefix against the
hermetic Spack reference. `automake` emits no public C library ABI, so the gate
covers:

- layout parity for 22 installed executable, macro, Perl module, info, and
  manpage paths;
- executable dynamic dependency parity for `bin/aclocal`, `bin/aclocal-1.18`,
  `bin/automake`, and `bin/automake-1.18`;
- prefix-normalized installed script/module data, including explicit
  Autoconf and Perl dependency-prefix aliases;
- byte-identical representative macro/module/doc payloads;
- matching `automake --version` and `aclocal --version` output;
- matching `aclocal` behavior in a temporary project;
- matching `automake --add-missing --foreign` behavior after a setup
  `aclocal` invocation in the same temporary project.

The smoke target prints:

```text
automake:1.18.1:ok
```

Current status: native and prefix/behavior-gated. The latest focused run passed
inside the CUDA insula with `rootfs mode: cuda-bundle`, used Bazel-owned Spack
`1.2.2`, flipped `spack_automake` to `@automake_native//:lib`, and passed:

```text
//synthetic:spack_selfcheck
//tools:hermetic_spack_guard_test
//tools:hermetic_native_deps_guard_test
//synthetic:use_automake_native
//tools:native_build_mechanism_guard_unit_test
//tools:abi_parity_unit_test
//synthetic:automake_prefix_parity
```
