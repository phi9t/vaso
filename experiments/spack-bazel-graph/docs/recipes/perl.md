# perl native recipe

## Position in the hillclimb

`perl` is topo index 27 in the `python` root graph. The intervening nodes
through `util-linux-uuid` are already migrated, and Perl is now the native
frontier that makes the next unmigrated node `openssl`:

```text
23 util-linux-uuid  autotools
24 xz               autotools
25 zlib-ng          autotools
26 libxml2          autotools
27 perl             generic
28 openssl          generic
```

The generated lock keeps Perl's Spack DAG edges to the runtime/build libraries
that its bundled XS modules probe during `Configure` and `make`:

```json
{
  "package": "perl",
  "version": "5.42.0",
  "build": "spack",
  "link_deps": [
    "spack_berkeley_db",
    "spack_bzip2",
    "spack_gdbm",
    "spack_less",
    "spack_zlib_ng"
  ],
  "link_libs": [],
  "include_dirs": []
}
```

## Spack evidence

All evidence here comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
The reference prefix, package recipe, concrete spec, build log, and build
environment are from the hermetic `/vaso/cache/spack` store inside the insula.
Do not use an ambient host Spack checkout for this node.

Reference prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/perl-5.42.0-zz66ic2agmfzijr3vx3leipbhgyvkt4f
```

Source provenance from the Spack recipe:

- package class: `Perl(Package)`
- upstream source URL:
  `http://www.cpan.org/src/5.0/perl-5.42.0.tar.gz`
- Perl source SHA256:
  `e093ef184d7f9a1b9797e2465296f55510adb6dab8842b0c3ed53329663096dc`
- resource: `cpanm`
  `http://search.cpan.org/CPAN/authors/id/M/MI/MIYAGAWA/App-cpanminus-1.7042.tar.gz`
- `cpanm` SHA256:
  `9da50e155df72bce55cb69f51f1dbb4b62d23740fb99f6178bb27f22ebdf8a46`
- concrete variants: `+cpanm +opcode +open +shared +threads`
- build system: Spack `generic`; Perl does not use Autotools and is built by
  its own `Configure` script

Spack's source cache contains both archives under the Bazel-owned Spack cache:

```text
/vaso/cache/spack/source-cache/_source-cache/archive/e0/e093ef184d7f9a1b9797e2465296f55510adb6dab8842b0c3ed53329663096dc.tar.gz
/vaso/cache/spack/source-cache/_source-cache/archive/9d/9da50e155df72bce55cb69f51f1dbb4b62d23740fb99f6178bb27f22ebdf8a46.tar.gz
```

## Build recipe

Spack applies one Linux-relevant patch before configure:

```python
os.chmod("lib/perlbug.t", 0o644)
filter_file("!/$B/", "! (/(?:$B|PATH)/)", "lib/perlbug.t")
```

The current concrete spec is not `%intel@19.1.3`, so the Intel-specific
`hints/linux.sh` patch does not apply.

Spack stages the `cpanm` resource into `spack-src/cpanm/cpanm`, then runs these
package phases:

```text
==> perl: Executing phase: 'configure'
./Configure -des \
  -Dprefix=<perl-prefix> \
  -Dlocincpth=<gdbm-prefix>/include \
  -Dloclibpth=<gdbm-prefix>/lib \
  '-Accflags=-DAPPLLIB_EXP="<perl-prefix>/lib/perl5"' \
  -Duseshrplib \
  -Dusethreads

==> perl: Executing phase: 'build'
make

==> perl: Executing phase: 'install'
make install

cd cpanm/cpanm
<perl-prefix>/bin/perl Makefile.PL
make
make install
```

Perl's `Configure` script manipulates file descriptors and invokes make near
the end, so Spack clears `MAKEFLAGS` for the configure phase and lets the build
phase invoke `make` normally.

Package-relevant environment from the hermetic build:

```text
BUILD_BZIP2=0
BUILD_ZLIB=0
BZIP2_INCLUDE=<bzip2-prefix>/include
BZIP2_LIB=<bzip2-prefix>/lib
ZLIB_INCLUDE=<zlib-ng-prefix>/include
ZLIB_LIB=<zlib-ng-prefix>/lib
PKG_CONFIG_PATH=<bzip2>/lib/pkgconfig:<zlib-ng>/lib/pkgconfig:<readline>/lib/pkgconfig:<ncurses>/lib/pkgconfig
SPACK_STORE_INCLUDE_DIRS=<ncurses>/include:<readline>/include:<zlib-ng>/include:<gdbm>/include:<bzip2>/include:<berkeley-db>/include
SPACK_STORE_LINK_DIRS=<gcc-runtime>/lib:<ncurses>/lib:<readline>/lib:<zlib-ng>/lib:<gdbm>/lib:<bzip2>/lib:<berkeley-db>/lib
SPACK_STORE_RPATH_DIRS=<perl>/lib:<perl>/lib64:<gcc-runtime>/lib:<ncurses>/lib:<readline>/lib:<zlib-ng>/lib:<gdbm>/lib:<bzip2>/lib:<berkeley-db>/lib
SPACK_DISABLE_NEW_DTAGS=--disable-new-dtags
```

The native provider must reproduce the above with Bazel-selected native
dependency prefixes. `gdbm` is explicitly passed through Perl's `-Dlocincpth`
and `-Dloclibpth`; zlib-ng and bzip2 are selected through `ZLIB_*`,
`BZIP2_*`, and the bundled Compress-Raw module build probes. The native build
passes `-Dnoextensions=DB_File` because the hermetic Spack reference prefix does
not install `DB_File.pm` or `auto/DB_File/DB_File.so`; Berkeley DB remains a DAG
edge inherited from the Spack topology, but it is not part of the emitted Perl
prefix contract for this concrete spec.

## Emitted prefix contract

Perl emits an executable/runtime prefix rather than a single public C library:

- 32 files under `bin/`, including `perl`, `perl5.42.0`, `cpan`, `cpanm`,
  `perldoc`, `prove`, and `xsubpp`
- `lib/5.42.0/...` core modules, POD, generated config, and architecture
  directory
- `lib/5.42.0/x86_64-linux-thread-multi/CORE/libperl.so`
- 50 dynamically loaded XS extension `.so` files under
  `lib/5.42.0/x86_64-linux-thread-multi/auto`
- `lib/site_perl/5.42.0/App/cpanminus.pm` and the `cpanm` support files

Important dynamic links in the reference prefix:

- `bin/perl` and `bin/perl5.42.0`: NEEDED `libperl.so`, `libc.so.6`
- `CORE/libperl.so`: NEEDED `libm.so.6`, `libcrypt.so.1`, `libc.so.6`,
  `ld-linux-x86-64.so.2`
- `GDBM_File.so`: NEEDED `libgdbm.so.6`
- `NDBM_File.so`: NEEDED `libgdbm_compat.so.4`
- `Compress/Raw/Zlib/Zlib.so`: NEEDED `libz.so.1`
- `Compress/Raw/Bzip2/Bzip2.so`: NEEDED `libbz2.so.1.0`

The ABI gate's `nm -D --defined-only` filter reports 1340 exported symbols for
`CORE/libperl.so` in the hermetic Spack reference.

## Prefix and ABI gate

`//synthetic:perl_prefix_parity` compares `@perl_native//:prefix` against the
hermetic Spack reference prefix:

- layout: all headers and shared objects discovered under `lib/`, plus selected
  executables
- ABI: SONAME/exported-symbol parity for `libperl.so` and every installed XS
  extension `.so`
- executable contract: NEEDED sets for `bin/perl`, `bin/perl5.42.0`, and
  `bin/cpanm`
- behavior: run `perl` inside the insula to validate version/config,
  `GDBM_File`, `NDBM_File`, `Compress::Raw::Zlib`, `Compress::Raw::Bzip2`,
  `open`, `Opcode`, and `App::cpanminus` version output

Verified native status:

```text
SPACK_ROOT_PKG=python VASO_NATIVE=1 VASO_SPACK_TIMEOUT=600 VASO_FORCE_FETCH_REPOS=@perl_native ./run.sh
rootfs mode: cuda-bundle (base root: $HOME/.vaso-estate/rootfs)
//synthetic:perl_prefix_parity PASSED in 0.6s
```

The gate reported layout parity with `candidate_count = reference_count = 55`,
SONAME/exported-symbol parity for `CORE/libperl.so` and every installed XS
extension, matching NEEDED sets for `bin/perl`, `bin/perl5.42.0`, and
`bin/cpanm`, and matching behavior output for the three Perl execution specs.

Current status: native provider green inside the CUDA insula.
