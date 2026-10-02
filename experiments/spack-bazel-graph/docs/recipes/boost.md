# boost frontier recipe

## Position in the hillclimb

`boost` is the first py-torch frontier non-toolchain node in the generated
`SPACK_ROOT_PKG=py-torch` graph:

```text
0  ca-certificates-mozilla  generic    native
1  compiler-wrapper         generic    toolchain
2  compiler-wrapper         generic    toolchain
3  gcc                      autotools  toolchain
4  gcc                      autotools  toolchain
5  gcc                      autotools  toolchain
6  glibc                    autotools  toolchain
7  gcc-runtime              generic    toolchain
8  gcc-runtime              generic    toolchain
9  boost                    generic    native; ABI parity green
```

This graph was generated inside the CUDA insula from Bazel's vendored Spack:

```bash
SPACK_ROOT_PKG='py-torch cuda_arch=80,90,100 ^openblas~fortran ^font-util fonts:=encodings' \
VASO_GRAPH_ONLY=1 \
VASO_GRAPH_NO_PREFIX=1 \
VASO_SPACK_INSTALL=0 \
VASO_SPACK_TIMEOUT=900 \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/py_torch_build_graph.json \
VASO_LOCK_OUT=/workspace/experiment/py_torch_spack_graph.lock.json \
./run.sh
```

`VASO_GRAPH_ONLY=1` means this is topology and recipe discovery only: no
PyTorch install and no Boost native provider flip.

The native build and ABI gate were verified separately inside the same insula
path with:

```bash
SPACK_ROOT_PKG=python \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_LOCK_OUT=/workspace/experiment/boost_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/boost_build_graph.json \
./run.sh
```

This command installs the Spack Boost reference prefix with Bazel's hermetic
`@spack_dist//:spack`, flips Boost to `@boost_native`, and runs
`//synthetic:boost_abi_parity`. The downstream C++ consumer printed the same
output on both sides:

```text
boost:1_90:42:Success
```

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/boost/package.py
```

Source provenance from the Spack recipe:

- package class: `Boost(Package)`
- build system: Spack `generic`
- upstream source URL:
  `https://archives.boost.io/release/1.90.0/source/boost_1_90_0.tar.bz2`
- SHA256:
  `49551aff3b22cbc5c5a9ed3dbc92f0e23ea50a0f7325b0d198b705e8ee3fc305`
- native strategy: package-specific `bootstrap.sh` plus `b2 install`; this is
  not Autotools, CMake, Ninja, Meson, or Bazel even though it has configure-like
  bootstrap flags

The concrete `py-torch` graph has Boost as `boost@1.90.0` with only toolchain
dependencies:

```text
build: compiler-wrapper, gcc
link: gcc-runtime, glibc
```

The concrete variant surface is narrow:

```text
+atomic +chrono +exception +shared +system +thread +multithreaded
~charconv ~container ~context ~contract ~conversion ~date_time ~fiber
~filesystem ~graph ~icu ~iostreams ~json ~locale ~log ~math ~mpi ~numpy
~program_options ~python ~random ~regex ~serialization ~singlethreaded
~stacktrace ~taggedlayout ~test ~timer ~url ~versionedlayout ~wave
cxxstd=11 visibility=hidden
```

The graph also records one applied patch by digest:

```text
a440f9696d3bbb77e7eab1516c004730f622e59c71d39960b472026ef92f88e8
```

For Boost 1.90.0 under hermetic Spack v1.2.2, that digest corresponds to
`bootstrap-compiler.patch`, which keeps `bootstrap.sh` from clearing the
selected C++ compiler when it builds the B2 engine. The native rule vendors that
patch under `native/boost/bootstrap-compiler.patch` and applies it before
bootstrap.

## Build recipe

Boost's Spack package is `generic` because it implements `install()` directly.
The effective phases are:

1. Determine the Boost.Build toolset from the Spack compiler. For the current
   GCC-based insula this resolves to `gcc`.
2. Write `user-config.jam` so Boost.Build uses Spack's compiler wrapper instead
   of auto-discovering a host compiler:

   ```text
   using gcc : : <spack_cxx> ;
   ```

3. Compute bootstrap options. With the concrete variants above, the important
   options are:

   ```text
   --prefix=<boost-prefix>
   --with-toolset=gcc
   --with-libraries=atomic,chrono,exception,system,thread
   --without-icu
   ```

4. Run:

   ```text
   ./bootstrap.sh <bootstrap-options>
   ```

5. Strip the auto-generated `using gcc ...` line from `project-config.jam` so
   it cannot overwrite the explicit `user-config.jam` compiler selection.
6. Run `./b2 --clean`, then install one threading layout:

   ```text
   ./b2 install threading=multi \
     -j <make_jobs> \
     --user-config=<source>/user-config.jam \
     variant=release \
     --disable-icu \
     link=static,shared \
     --layout=system \
     toolset=gcc \
     cxxstd=11 \
     visibility=hidden
   ```

7. Because `+multithreaded` and `~taggedlayout` are selected, Spack creates
   `-mt` symlinks for installed libraries after the `b2 install`.

The disabled `+iostreams`, `+python`, `+mpi`, and `+icu` variants are important
because they explain why this early PyTorch-frontier Boost node has no migrated
package dependencies yet. If any of those variants change, the native rule must
thread dependency prefixes through Boost.Build's `-s <NAME>_INCLUDE` /
`-s <NAME>_LIBPATH`, `--with-python`, `using mpi`, or `-s ICU_PATH` channels.

## Native build status

`native/boost/boost.bzl` now provides `@boost_native//:lib`. The rule is a
package-specific Boost.Build provider, not an Autotools/CMake/Makefile rule.
It is wired through `native_overrides.json` as the Boost provider for future
py-torch frontier locks.

`//tools:hermetic_native_deps_guard_test` now requires `boost-build` mechanism
coverage. The Boost.Build verifier proves:

- `VASO_IN_INSULA=1` is required before bootstrap or `b2` runs;
- source is fetched by the same Spack-pinned SHA256;
- `user-config.jam` pins the C++ compiler path from the insula/Bazel toolchain;
- `bootstrap.sh` receives only variant-derived options;
- `b2` receives explicit `--user-config`, `link=static,shared`,
  `threading=multi`, `--layout=system`, `toolset=gcc`, `cxxstd=11`, and
  `visibility=hidden`;
- no Boost.Build auto-discovery is allowed to pull headers, libraries, Python,
  MPI, ICU, zlib, bzip2, xz, or zstd from the host.

The first smoke build exposed a stale assumption in the initial `cc_library`
surface: Boost 1.90.0 did not install `libboost_system` for this concrete
variant surface, while Boost.Build did install `libboost_container` and
`libboost_date_time` as dependencies of the selected libraries. The native
target now exposes the observed installed link surface:

```text
boost_atomic
boost_chrono
boost_container
boost_date_time
boost_exception
boost_thread
```

## Prefix and ABI gate target

For this concrete PyTorch-frontier spec, the native prefix must match Spack's
Boost install tree for:

- public headers under `include/boost/**`;
- shared and static libraries for the enabled compiled libraries:
  `boost_atomic`, `boost_chrono`, `boost_exception`, and `boost_thread`, plus
  Boost.Build-installed closure libraries observed in the native prefix:
  `boost_container` and `boost_date_time`;
- Boost.Build-produced `-mt` symlinks;
- SONAMEs and exported dynamic symbols for every installed shared library;
- a downstream C++ link-and-run test that uses at least `boost::system` and
  `boost::thread`.

Current status: native and ABI-gated. `//synthetic:boost_abi_parity` passed
inside the CUDA insula against the hermetic Spack reference prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/boost-1.90.0-lj7pfspuqvdq7i5bb563q4iz3eeor425
```

The gate covered 16019 ABI-relevant layout paths, matching SONAME/exported
symbols for the installed shared libraries (`boost_atomic`, `boost_chrono`,
`boost_container`, `boost_date_time`, `boost_thread`), and matching link-and-run
output from `synthetic/use_boost.cc`.
