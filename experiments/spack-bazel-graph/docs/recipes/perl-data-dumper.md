# perl-data-dumper@2.173

## Position in the hillclimb

`perl-data-dumper@2.173` is the first CPAN/PerlPackage node in the lean
`py-torch` frontier after native `font-util`. The lean graph was generated with:

```bash
SPACK_ROOT_PKG='py-torch cuda_arch=80,90,100 ^openblas~fortran ^font-util fonts:=encodings'
```

The `font-util` constraint stays intentionally lean: only the `encodings`
resource is selected, so this slice does not broaden the font payload.

The focused all-Spack reference graph for this package has 17 nodes and only
one package-specific dependency edge:

```text
build: gmake
build/run: perl
```

`gmake` remains a build-tool topology node owned by the Bazel/insula build
action. The native provider consumes only `@perl_native//:prefix_path.txt` as a
package dependency input.

## Hermetic Spack evidence

All evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this node.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/perl-data-dumper-2.173-sef3sq74qwmihdxshgyh4bwkcofhsgb3
```

Source provenance from the Spack recipe:

- package class: `PerlDataDumper(PerlPackage)`
- upstream source URL:
  `https://cpan.metacpan.org/authors/id/X/XS/XSAWYERX/Data-Dumper-2.173.tar.gz`
- SHA256:
  `697608b39330988e519131be667ff47168aaaaf99f06bd2095d5b46ad05d76fa`

The hermetic Spack build phases are the standard ExtUtils::MakeMaker flow:

```text
<perl-prefix>/bin/perl Makefile.PL INSTALL_BASE=<prefix>
make
make install
```

The emitted prefix is intentionally small:

```text
lib/perl5/x86_64-linux-thread-multi/Data/Dumper.pm
lib/perl5/x86_64-linux-thread-multi/auto/Data/Dumper/Dumper.so
lib/perl5/x86_64-linux-thread-multi/auto/Data/Dumper/.packlist
lib/perl5/x86_64-linux-thread-multi/perllocal.pod
```

`Dumper.so` has no SONAME, needs only `libc.so.6`, and exports
`boot_Data__Dumper`.

## Native build recipe

`native/perl_data_dumper/perl_data_dumper.bzl` mirrors Spack's PerlPackage
flow inside the CUDA insula:

```text
download Data-Dumper-2.173.tar.gz
validate PERL_PREFIX points at the Bazel-built native Perl prefix
export PATH=<perl-prefix>/bin:$PATH
export LD_LIBRARY_PATH=<perl-prefix>/lib:<perl CORE lib>:$LD_LIBRARY_PATH
<perl-prefix>/bin/perl Makefile.PL INSTALL_BASE=<prefix>
make
make install
remove libtool archives
validate Dumper.pm and Dumper.so under lib/perl5/x86_64-linux-thread-multi
emit prefix_path.txt
```

The build-mechanism guard classifies this as `perl` and verifies the CPAN
channel specifically: `PERL_PREFIX` must come from a mandatory prefix-file
attribute, `Makefile.PL` must be invoked through `$PERL_PREFIX/bin/perl`,
`INSTALL_BASE="$PREFIX"` must be passed, and `make install` must be present.

## Gates

`//synthetic:use_perl_data_dumper_native` runs native Perl with `PERL5LIB`
pointing at the installed module prefix and proves `Data/Dumper.pm` is loaded
from `@perl_data_dumper_native//:prefix`, not from Perl's core module tree.

`//synthetic:perl_data_dumper_abi_parity` compares the native prefix against
the hermetic Spack reference. It covers:

- layout for `Dumper.so` plus explicit `Dumper.pm` data;
- prefix-normalized `Dumper.pm` content;
- SONAME/exported-symbol parity for `Dumper.so`;
- runtime smoke through the synthetic native Perl consumer.

The focused verification command is:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='perl-data-dumper@2.173' \
VASO_LOCK_OUT=/workspace/experiment/perl_data_dumper_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/perl_data_dumper_native_build_graph.json \
VASO_NATIVE=1 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_perl_data_dumper_native //synthetic:perl_data_dumper_abi_parity //tools:hermetic_native_deps_guard_test //tools:native_build_mechanism_guard_unit_test //tools:abi_parity_unit_test' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```
