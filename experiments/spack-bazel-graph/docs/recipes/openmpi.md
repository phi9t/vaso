# OpenMPI frontier recipe

## Position in the hillclimb

`openmpi@5.0.10` is the next lean `py-torch` frontier node after native PMIx
and PRRTE:

```text
86  pmix     6.1.0   autotools  native; ABI parity green
87  prrte    4.1.0   autotools  native; ABI parity green
88  openmpi  5.0.10  autotools
```

The focused reference graph for `SPACK_ROOT_PKG='openmpi@5.0.10'` ends with:

```text
44  openmpi  5.0.10  autotools
```

Spack still owns the DAG shape. The native flip changes only
`spack_openmpi.build` to `native` and re-exports `@openmpi_native//:lib`; link
edges to `hwloc`, `libevent`, `numactl`, `openssh`, `pmix`, `prrte`, and
`zlib-ng` remain Spack-derived.

## Spack evidence

All recipe evidence comes from Bazel's vendored `@spack_dist//:spack` running
inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/openmpi-5.0.10-6fwe6c2lmx6q3ffyuryc643ix5ekdg4j
```

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/openmpi/package.py
```

Source provenance from that recipe:

- package class: `Openmpi(AutotoolsPackage)`
- upstream source URL used by the native rule:
  `https://download.open-mpi.org/release/open-mpi/v5.0/openmpi-5.0.10.tar.bz2`
- version `5.0.10` SHA256:
  `0acecc4fc218e5debdbcb8a41d182c6b0f1d29393015ed763b2a91d5d7374cc6`
- Spack-selected patch URL:
  `https://github.com/open-mpi/ompi/commit/aa024ac73d624611cfe3af6f541b5d28dedf07bb.patch?full_index=1`
- patch SHA256:
  `646eb1a7382d628eb821715ca69fc5467a9a25aaddfe8290dbce008536dbfaa0`
- concrete variants: `+atomics`, `~cuda`, `~debug`, `fabrics=none`,
  `+fortran`, `~gpfs`, `~internal-hwloc`, `~internal-libevent`,
  `~internal-pmix`, `~ipv6`, `~java`, `~lustre`, `~memchecker`,
  `~openshmem`, `~rocm`, `~romio`, `romio-filesystem=none`, `+rsh`,
  `schedulers=none`, `~static`, `+vt`, and `+wrapper-rpath`

The hermetic Spack configure contract is:

```text
--enable-shared
--disable-silent-rules
--disable-sphinx
--disable-dependency-tracking
--enable-builtin-atomics
--disable-static
--enable-mpi1-compatibility
--without-psm
--without-psm2
--without-verbs
--without-mxm
--without-ucx
--without-ofi
--without-fca
--without-hcoll
--without-ucc
--without-xpmem
--without-cma
--without-knem
--without-alps
--without-lsf
--without-tm
--without-slurm
--without-sge
--without-loadleveler
--disable-memchecker
--with-libevent=<libevent-prefix>
--with-pmix=<pmix-prefix>
--with-prrte=<prrte-prefix>
--with-zlib=<zlib-ng-prefix>
--with-hwloc=<hwloc-prefix>
--disable-java
--disable-mpi-java
--disable-io-romio
--with-gpfs=no
--without-cuda
--without-rocm
--enable-wrapper-rpath
--disable-wrapper-runpath
--enable-mpi-fortran
CFLAGS=-DYY_BUF_SIZE=1048576
--disable-debug
```

## Native build

`native/openmpi/openmpi.bzl` defines `openmpi_native`, a Bazel repository rule
that declares `VASO_IN_INSULA` as an environment input and refuses to build
unless the hermetic insula sets `VASO_IN_INSULA=1`.

The rule consumes only Bazel-native dependency prefixes:

```text
AUTOCONF_PREFIX  <- @autoconf_native//:prefix_path.txt
AUTOMAKE_PREFIX  <- @automake_native//:prefix_path.txt
HWLOC_PREFIX     <- @hwloc_native//:prefix_path.txt
LIBEVENT_PREFIX  <- @libevent_native//:prefix_path.txt
LIBTOOL_PREFIX   <- @libtool_native//:prefix_path.txt
NUMACTL_PREFIX   <- @numactl_native//:prefix_path.txt
OPENSSH_PREFIX   <- @openssh_native//:prefix_path.txt
PERL_PREFIX      <- @perl_native//:prefix_path.txt
PKGCONF_PREFIX   <- @pkgconf_native//:prefix_path.txt
PMIX_PREFIX      <- @pmix_native//:prefix_path.txt
PRRTE_PREFIX     <- @prrte_native//:prefix_path.txt
ZLIB_PREFIX      <- @zlib_ng_native//:prefix_path.txt
```

The build action downloads the same release tarball, applies the same
Spack-selected patch by SHA256, pins Autotools/libtool/perl/pkgconf/OpenSSH
tools on `PATH`, routes `PKG_CONFIG_PATH`, `CPPFLAGS`, `LDFLAGS`, and
`LD_LIBRARY_PATH` through the declared native prefixes, and runs
`configure && make V=1 && make install`. It removes libtool archives after
install to match Spack's public prefix surface.

For wrapper metadata parity, `CC` and `CXX` are pinned to `/usr/bin/gcc` and
`/usr/bin/g++`, matching the compiler identity recorded by hermetic Spack
inside the rootfs. Fortran MPI bindings use `/usr/bin/gfortran` from the
insula rootfs.

The mechanism verifier records the dependency contract as:

```text
native/openmpi/openmpi.bzl: autotools: AUTOCONF_PREFIX, AUTOMAKE_PREFIX, HWLOC_PREFIX, LIBEVENT_PREFIX, LIBTOOL_PREFIX, NUMACTL_PREFIX, OPENSSH_PREFIX, PERL_PREFIX, PKGCONF_PREFIX, PMIX_PREFIX, PRRTE_PREFIX, ZLIB_PREFIX
```

## Prefix and ABI gate

The ABI-relevant prefix contract used by the gate is:

- headers under `include/`
- libraries: `lib/libmpi.so.40.40.7`,
  `lib/libmpi_mpifh.so.40.40.1`,
  `lib/libmpi_usempi_ignore_tkr.so.40.40.1`,
  `lib/libmpi_usempif08.so.40.40.3`,
  `lib/libopen-pal.so.80.0.5`, and `lib/openmpi/libompi_dbg_msgq.so`
- pkg-config metadata: `lib/pkgconfig/ompi.pc`
- wrapper metadata: `share/openmpi/mpicc-wrapper-data.txt` and
  `share/openmpi/mpifort-wrapper-data.txt`
- executables: `bin/mpicc`, `bin/mpifort`, `bin/mpirun`, and `bin/ompi_info`

`//synthetic:use_openmpi` links through the Spack-generated
`@spack_openmpi//:lib` facade and calls `MPI_Get_version()`, so the consumer
remains unchanged when the provider flips. `//synthetic:use_openmpi_native`
links directly against `@openmpi_native//:lib` as a native-prefix smoke test.

`//synthetic:openmpi_abi_parity` compares `@openmpi_native//:prefix` against
the hermetic Spack reference prefix for layout, SONAME/exported symbols,
prefix-normalized `ompi.pc`, wrapper metadata, downstream link-and-run,
executable dependency parity, `ompi_info --version`, and
`mpicc --showme:version` behavior.

The focused native verifier for `SPACK_ROOT_PKG='openmpi@5.0.10'` was run
through `run.sh` inside the CUDA insula with Bazel's hermetic Spack. It passed:

- `//synthetic:use_openmpi_native`
- `//synthetic:openmpi_abi_parity`
- `//synthetic:pmix_abi_parity`
- `//tools:hermetic_native_deps_guard_test`
- `//tools:native_build_mechanism_guard_unit_test`
- `//tools:abi_parity_unit_test`

The ABI verdict matched layout count `49`, SONAME/exported-symbol parity for
all six checked shared objects, prefix-normalized `ompi.pc`, byte-identical
wrapper metadata for the checked C and Fortran wrappers, executable dependency
parity, `ompi_info --version`, `mpicc --showme:version`, and downstream
link-and-run output `openmpi:mpi-3.1`.
