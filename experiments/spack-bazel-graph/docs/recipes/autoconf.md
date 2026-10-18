# autoconf frontier recipe

## Position in the hillclimb

`autoconf@2.72` is the migrated py-torch frontier node after `hwloc@2.13.0`.
In the captured `SPACK_ROOT_PKG=py-torch` graph, the nearby entries are:

```text
51  hwloc     2.13.0  autotools  native; ABI parity green
52  perl      5.42.0  generic    native in ledger
53  autoconf  2.72    autotools  native; prefix parity green
54  automake  1.18.1  autotools  next unresolved frontier
```

The package-local reference graph was captured inside the CUDA insula with
Bazel's vendored Spack:

```bash
VASO_NATIVE=1 \
SPACK_ROOT_PKG='autoconf@2.72' \
VASO_LOCK_OUT=/workspace/experiment/autoconf_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/autoconf_build_graph.json \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_autoconf_native //tools:hermetic_native_deps_guard_test //synthetic:autoconf_prefix_parity //tools:native_build_mechanism_guard_unit_test //tools:abi_parity_unit_test' \
VASO_SPACK_TIMEOUT=1200 \
./run.sh
```

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/autoconf/package.py
```

Source provenance from the Spack recipe:

- package class: `Autoconf(AutotoolsPackage, GNUMirrorPackage)`
- version: `2.72`
- upstream source URL: `https://ftp.gnu.org/gnu/autoconf/autoconf-2.72.tar.gz`
- SHA256:
  `afb181a76e1ee72832f6581c0eddf8df032b83e2e0239ef79ebedc4467d92d6e`
- concrete dependency edges: `m4@1.4.8:` as build/run dependency and `perl`
  as build/run dependency
- build directory: `spack-build`
- package-specific patch surface for `2.72`: `bin/autom4te.in` is temporarily
  rewritten from `#! @PERL@` to `#! /usr/bin/env perl` during the build while
  preserving the file timestamp; after install, the installed `bin/autom4te`
  shebang is rewritten to the concrete Perl path.

The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/autoconf-2.72-g3k5nlb5iymq6oe7ywhqq5hgp2gfhljy
```

The stable public prefix surface for this migration is executable and data
oriented:

```text
bin/autoconf
bin/autoheader
bin/autom4te
bin/autoreconf
bin/autoscan
bin/autoupdate
bin/ifnames
share/autoconf/Autom4te/C4che.pm
share/autoconf/autoconf/autoconf.m4
share/info/autoconf.info
share/man/man1/autoconf.1
```

## Build recipe

`native/autoconf/autoconf.bzl` mirrors Spack's out-of-tree Autotools flow:

```text
patch bin/autom4te.in to #! /usr/bin/env perl, preserving mtime
mkdir -p <src>/spack-build
cd <src>/spack-build
PATH=<m4-prefix>/bin:<perl-prefix>/bin:$PATH
M4=<m4-prefix>/bin/m4
PERL=<perl-prefix>/bin/perl
../configure --prefix=<prefix>
make V=1
make install
rewrite installed bin/autom4te shebang to <perl-prefix>/bin/perl
find <prefix> -type f -name '*.la' -delete
```

The repository rule fetches the same upstream tarball by SHA256 and refuses to
run unless the hermetic insula has set `VASO_IN_INSULA=1`. Its dependencies are
not discovered from the host: `m4_prefix_file` and `perl_prefix_file` are
mandatory Bazel labels, read by the repository rule, validated by the shell
build, and passed through Autotools build-tool channels.

This is the Autotools dependency-prefix verifier case:

```text
native/autoconf/autoconf.bzl: autotools: M4_PREFIX, PERL_PREFIX
```

`//tools:hermetic_native_deps_guard_test` checks that the m4 and Perl prefixes
enter through Bazel-owned files and mechanism-specific Autotools channels
rather than host discovery.

## Prefix and behavior gate target

`//synthetic:autoconf_prefix_parity` compares the native prefix against the
hermetic Spack reference. `autoconf` emits no public C library ABI, so the gate
covers:

- layout parity for the installed Autoconf executables and representative
  macro/info/man data;
- executable dynamic dependency parity for `bin/autoconf`, `bin/autoheader`,
  `bin/autom4te`, and `bin/autoreconf`;
- prefix-normalized installed script data for `bin/autoconf`,
  `bin/autoheader`, `bin/autom4te`, `bin/autoreconf`, `bin/autoscan`,
  `bin/autoupdate`, and `bin/ifnames`, including explicit m4 and Perl
  dependency-prefix aliases;
- byte-identical `share/autoconf/Autom4te/C4che.pm`,
  `share/autoconf/autoconf/autoconf.m4`, `share/info/autoconf.info`, and
  `share/man/man1/autoconf.1`;
- matching `autoconf --version` output;
- matching deterministic `configure` generation from a fixed `configure.ac`.

The smoke target prints:

```text
autoconf:2.72:ok
```

Current status: native and prefix/behavior-gated. The latest focused run passed
inside the CUDA insula with `rootfs mode: cuda-bundle`, used Bazel-owned Spack
`1.2.2`, flipped `spack_autoconf` to `@autoconf_native//:lib`, and passed:

```text
//synthetic:use_autoconf_native
//tools:hermetic_native_deps_guard_test
//synthetic:autoconf_prefix_parity
//tools:native_build_mechanism_guard_unit_test
//tools:abi_parity_unit_test
```
