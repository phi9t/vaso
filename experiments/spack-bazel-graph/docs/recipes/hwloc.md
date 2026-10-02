# hwloc frontier recipe

## Position in the hillclimb

`hwloc@2.13.0` is the next migrated py-torch frontier node after
`libfontenc@1.1.8`. In the captured `SPACK_ROOT_PKG=py-torch` graph, the
nearby entries are:

```text
49  libfontenc  1.1.8   autotools  native; ABI parity green
50  libxml2     2.13.9  autotools  native in ledger
51  hwloc       2.13.0  autotools  native; ABI parity green
52  perl        5.42.0  generic    native in ledger
53  autoconf    2.72    autotools  next unresolved frontier
```

The package-local reference graph was captured inside the CUDA insula with
Bazel's vendored Spack:

```bash
VASO_NATIVE=0 \
SPACK_ROOT_PKG='hwloc@2.13.0' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_LOCK_OUT=/workspace/experiment/hwloc_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/hwloc_build_graph.json \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/hwloc/package.py
```

Source provenance from the Spack recipe:

- package class: `Hwloc(AutotoolsPackage, CudaPackage, ROCmPackage)`
- version: `2.13.0`
- upstream source URL:
  `https://download.open-mpi.org/release/hwloc/v2.13/hwloc-2.13.0.tar.gz`
- SHA256:
  `1514a5253f0a5c23bc006d3bdd30a6f6125c9a8dc9b5fa4984913d1fff45315d`
- patch:
  `https://github.com/open-mpi/hwloc/commit/f7f1f76573ce505dae73568c912d2b2efdbf0f71.patch?full_index=1`
- patch SHA256:
  `b4db98b39733435273e57b8229ee834ce50d2785641d1587d8039598752b1a3d`

The concrete node has `+pci`, `+libxml2`, `~cuda`, `~nvml`, `~gl`,
`~libudev`, `~opencl`, `~rocm`, `~level_zero`, and
`libs=shared,static`. It depends on `libpciaccess`, `libxml2`, and `ncurses`
for build/link and on `pkgconf` for build.

## Build recipe

Spack applies the patch, runs Autotools, and configures with the captured
arguments:

```text
autoreconf
./configure --prefix=<prefix> \
  --disable-cairo \
  --disable-nvml \
  --disable-gl \
  --disable-cuda \
  --enable-libxml2 \
  --disable-libudev \
  --enable-pci \
  --enable-shared \
  --enable-static \
  --disable-levelzero \
  --disable-opencl \
  --disable-rsmi
make V=1
make install
```

The observed configure summary has PCI support from `libpciaccess`, full XML
input/output from `libxml2`, no plugin support, and terminal support through
`libtinfo`. The generated `hwloc.pc` includes:

```text
Requires.private: libxml-2.0 pciaccess
Libs.private: -lm -L<libpciaccess>/lib -lpciaccess -L<libxml2>/lib -lxml2 -lpthread
```

The stable public prefix surface includes:

```text
bin/hwloc-annotate
bin/hwloc-bind
bin/hwloc-calc
bin/hwloc-compress-dir
bin/hwloc-diff
bin/hwloc-distrib
bin/hwloc-gather-cpuid
bin/hwloc-gather-topology
bin/hwloc-info
bin/hwloc-ls -> lstopo-no-graphics
bin/hwloc-patch
bin/hwloc-ps
bin/lstopo -> lstopo-no-graphics
bin/lstopo-no-graphics
sbin/hwloc-dump-hwdata
include/hwloc.h
include/hwloc/*.h
include/hwloc/autogen/config.h
lib/libhwloc.a
lib/libhwloc.so -> libhwloc.so.15.10.2
lib/libhwloc.so.15 -> libhwloc.so.15.10.2
lib/libhwloc.so.15.10.2
lib/pkgconfig/hwloc.pc
share/hwloc/*.dtd
```

`native/hwloc/hwloc.bzl` mirrors the same source, patch, configure flags, and
install flow. The repository rule refuses to run unless the hermetic insula has
set `VASO_IN_INSULA=1`, reads the native `libpciaccess`, `libxml2`, `ncurses`,
and `pkgconf` prefixes through mandatory Bazel `*_prefix_file` attrs, validates
those prefixes in the build script, pins `PKG_CONFIG`, and threads dependency
prefixes through `PKG_CONFIG_PATH`, `CPPFLAGS`, `LDFLAGS`, and `LIBS` before
configure. Spack's emitted prefix has no libtool archives, so the native rule
removes `.la` files after install.

This is the Autotools dependency-prefix verifier case:

```text
native/hwloc/hwloc.bzl: autotools: LIBPCIACCESS_PREFIX, LIBXML2_PREFIX, NCURSES_PREFIX, PKGCONF_PREFIX
```

`//tools:hermetic_native_deps_guard_test` checks that every prefix enters
through Bazel-owned files and mechanism-specific Autotools channels rather
than host discovery.

## Prefix and behavior gates

`//synthetic:hwloc_abi_parity` compares the native prefix against the hermetic
Spack reference:

```text
/vaso/cache/spack/opt/spack/linux-icelake/hwloc-2.13.0-evmtmomeygiy2nkpcdisexreqyn7cse6
```

The gate covers:

- 33 ABI-relevant layout entries;
- byte-identical `include/hwloc.h` and `include/hwloc/autogen/config.h`;
- static archive member and global-symbol parity for `lib/libhwloc.a`;
- prefix-normalized `lib/pkgconfig/hwloc.pc`, including explicit native
  dependency-prefix aliases for `libpciaccess` and `libxml2`;
- SONAME parity for `lib/libhwloc.so.15.10.2` (`libhwloc.so.15`);
- exported dynamic symbol parity for 210 symbols;
- executable dynamic dependency parity for `hwloc-info`, `hwloc-calc`, and
  `lstopo-no-graphics`;
- deterministic executable behavior for `hwloc-info --version` and
  `hwloc-calc --input 'pack:1 core:2 pu:1' --input-format synthetic
  --number-of pu all`;
- downstream link-and-run parity through a synthetic topology C consumer, with
  native/reference `libpciaccess`, `libxml2`, and `ncurses` link prefixes
  available on both sides.

`//synthetic:use_hwloc_native` validates that downstream users can compile,
link, and run against the native prefix, printing:

```text
hwloc:api=0x00020c00:depth=4:cores=2:pus=2
```

Current status: native and ABI-gated. The latest focused run passed inside the
CUDA insula:

```bash
VASO_NATIVE=1 \
SPACK_ROOT_PKG='hwloc@2.13.0' \
VASO_LOCK_OUT=/workspace/experiment/hwloc_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/hwloc_build_graph.json \
VASO_FORCE_FETCH_REPOS='@hwloc_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_hwloc_native //tools:hermetic_native_deps_guard_test //synthetic:hwloc_abi_parity' \
VASO_SPACK_TIMEOUT=1200 \
./run.sh
```

The run used rootfs mode `cuda-bundle`, forced `@hwloc_native` to build inside
the insula, installed/reused the reference prefix through Bazel's hermetic
`@spack_dist//:spack`, flipped `spack_hwloc` to `@hwloc_native//:lib`, and
passed the smoke, mechanism guard, and ABI parity gates.
