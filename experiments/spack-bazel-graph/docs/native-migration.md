# Native migration: Spack as reference recipe, Bazel as execution substrate

## Thesis

`experiments/spack-bazel-graph/` proves a Spack concrete link-DAG can be
consumed *directly* by Bazel (see `README.md`). This document defines the next
move: **Spack becomes a reference recipe, not the definitive execution
substrate.** Packages are pulled into a Bazel-native build form one at a time,
each staying byte/ABI-compatible with the rest of the Spack graph, until the
big three build natively in this order:

> **PyTorch → Triton → JAX** (JAX likely riding upstream XLA/Bazel).

The full PyTorch build stays gated behind an explicit token; this document is
the design + skeleton, not a build.

## Invariants (never violated by a migration)

> Amended by `.scratch/execution-plan/spec.md` (2026-09-30): the CUDA-family
> packages are `buildable: false` rootfs externals, verified against
> `rootfs/cuda_ecosystem.lock.json`, and the single-version rule covers
> every CUDA library and toolchain component.

1. **Spack owns the DAG shape.** Migration flips a node's *provider*, never the
   graph topology. `spack_to_bazel.py` emits the concrete link-DAG; a flip only
   changes one node's `build` field.
2. **Prefix-identical, ABI-identical output.** A native package emits an install
   tree indistinguishable from the Spack prefix — same `include/`, same
   `lib/libX.so.N` + soname, same `lib/pkgconfig/*.pc` (wheel layout for python
   packages, below) — and registers as a Spack external, so unmigrated
   consumers `depends_on` it unchanged.
3. **Every flip is gated by ABI parity.** `tools/abi_parity.py` runs in the
   sealed CUDA insula and must pass (layout + soname/symbol + downstream
   link-and-run) before a node is considered migrated.
4. **Every native build has a mechanism-specific hermetic-deps guard.** Before
   parity, `//tools:hermetic_native_deps_guard_test` verifies that each native
   repository rule names its build mechanism and threads every dependency prefix
   through Bazel-owned inputs, not host discovery.
5. **ODR-sensitive provider families are single-version.** Protobuf, gRPC,
   Abseil, and Boost providers must not appear at multiple ABI versions in one
   lock. Native overrides for these packages use exact `name@version` keys, and
   companion packages such as `py-protobuf`/`protobuf` and
   `py-grpcio`/`grpc`/`grpc-cpp` must resolve to one compatible family version
   before any provider flip is applied.

## The coexistence machinery (Tasks 1–3, landed)

### `build:native|spack` dispatch (Task 1)

- Each node in `spack_graph.lock.json` carries a `build` field, defaulting to
  `"spack"`; `spack_to_bazel.py` emits it.
- `native_overrides.json` maps a Spack package name → a native Bazel rule label
  under a top-level `"native"` object. `spack_to_bazel.py --native-overrides`
  applies it, setting `build: "native"` and `native_prefix` on matched nodes
  **without editing the generated topology**.
- ODR-sensitive C++ provider families use exact override keys, for example
  `protobuf@21.12`, `py-protobuf@4.21.12`, and `boost@1.90.0` for the
  native PyTorch source target. Package-wide overrides for
  protobuf/gRPC/Abseil/Boost packages are rejected because an old native
  capture must not silently satisfy a newer Spack node. The protobuf family
  guard treats historical `py-protobuf@3.x` as matching `protobuf@3.x`, PyPI
  `py-protobuf@4.x` as matching hermetic Spack C++ `protobuf@21.x`, and
  `py-protobuf@6.x` as matching C++ `protobuf@32.x`.
- When a graph contains any of protobuf, gRPC, Abseil, or Boost in multiple
  places, capture the family as a single version set before exposing a native
  provider to downstream C++ consumers. Do not mix a native `protobuf` with a
  different Spack `py-protobuf`, `grpc`, `grpc-cpp`, or `abseil-cpp` line in
  the same migrated lock; that is an ODR and ABI hazard, not a convenience
  edge.
- `tools/spack_repo.bzl`'s `spack_package` repository rule dispatches on
  `build`:
  - `spack` → today's prefix-symlink `cc_library` over the hermetic prefix;
  - `native` → `@spack_<pkg>//:lib` becomes an `alias` to the native target, so
    the `@spack_<pkg>//:lib` contract (and every `link_deps` edge) is unchanged.

Regression: an all-spack lock (the default) produces byte-identical generated
repos and the existing `//synthetic:use_zlib` consumer builds and runs
unchanged.

### Native prefix contract + ABI gate (Task 2)

The **prefix contract** a native package must satisfy:

- **C/C++ library package:**
  - `include/` — every public header at the same relative path;
  - `lib/` (or `lib64/`) — every shared object `libX.so.MAJOR.MINOR.PATCH` plus
    its `libX.so.MAJOR` and `libX.so` symlink chain, with the **same SONAME**
    and the **same set of exported dynamic symbols**; static `libX.a` where the
    reference ships one;
  - `lib/pkgconfig/*.pc` where the reference ships one.
- **Python package (wheel-based, e.g. torch):**
  - `prefix/artifacts/wheels/` — the built `torch-*.whl`;
  - `prefix/lib/site-packages/torch/{lib,include}` — the pip-installed payload
    (headers + shared objects) that C++ consumers link against, matching the
    vendor-libtorch install layout.
- **Data-only package (e.g. `ca-certificates-mozilla`):**
  - explicit data files named by the recipe, byte-identical by SHA256
    (`share/cacert.pem` for Mozilla CA certificates);
  - no fabricated C link contract when the Spack prefix has no libraries.

`tools/abi_parity.py` compares a *candidate* prefix against the *reference*
(Spack) prefix and emits a structured JSON verdict over three axes:

1. **layout** — the ABI-relevant path set must match (allowlist for benign
   extras);
2. **soname/symbol** — per shared lib, SONAME + exported symbols must match
   (`abidiff` when available, else `readelf -d` SONAME + `nm -D` symbol set);
3. **link-and-run** — a downstream consumer compiled+linked+run against each
   prefix must build, run, and print **identical** stdout.

It is wired as a Bazel `sh_test` (`//synthetic:zlib_ng_abi_parity`) that runs
in the sealed insula.

### Build-mechanism hermetic dependency guard

The ABI gate catches emitted-prefix drift. The earlier guard catches a more
basic class of mistake: a native build action accidentally finding headers,
libraries, or tools from the host instead of from Bazel/insula inputs. The
`//tools:hermetic_native_deps_guard_test` Bazel test runs inside the insula
alongside `//tools:hermetic_spack_guard_test` and checks all checked-in native
repository rules.
`//tools:native_build_mechanism_guard_unit_test` tests the guard semantics for
representative Autotools, CMake, Makefile, generic/data-only, Boost.Build, and
Python-wheel rules; the live guard then requires the checked-in native rule set
to include all of those mechanism classes. A package migration is not complete
until its native rule is in `//tools:hermetic_native_deps_guard_test` data and
the guard reports the rule under the same build mechanism recorded in the Spack
recipe deep dive.

The contract is intentionally build-mechanism-specific:

- **Autotools** rules may use `./configure`, but dependency prefixes must enter
  through explicit `*_prefix_file` label attrs, be read from Bazel runfiles,
  passed to the build script environment, validated in shell, and then consumed
  through `CPPFLAGS`, `LDFLAGS`, `LIBS`, `PKG_CONFIG_PATH`, concrete `--with-*`
  configure args, or pinned build-tool channels such as
  `M4=<m4-prefix>/bin/m4` and `PATH=<tool-prefix>/bin:$PATH`. Autotools
  packages with no non-toolchain dependency prefixes, such as `gperf`, still
  must be classified by the guard and must refuse to run outside
  `VASO_IN_INSULA=1`.
- **CMake** rules must keep dependency lookup on CMake channels such as
  `CMAKE_PREFIX_PATH`, `CMAKE_LIBRARY_PATH`, `CMAKE_INCLUDE_PATH`, or explicit
  `-D...` cache entries derived from Bazel-supplied prefixes.
- **Makefile** rules must pass dependency prefixes as explicit environment or
  make variables, validate those prefixes before invoking upstream make/config
  scripts, and avoid ambient package discovery.
- **Generic/data-only** rules are allowed only when they have no non-toolchain
  dependency prefixes. A generic rule that consumes `*_PREFIX` inputs fails the
  guard until it is classified under a real build mechanism with an explicit
  verifier for that dependency channel.
- **Boost.Build** rules must generate an explicit `user-config.jam`, pass it to
  `b2` with `--user-config`, and pin the build axes that Boost otherwise likes
  to discover or infer: `toolset=`, `cxxstd=`, `link=`, `threading=`, and
  `--layout=`.
- **Python wheel** rules, including the gated PyTorch skeleton, must validate
  the planned prefix interface inside the insula before any wheel build token
  can trigger an expensive build.

This guard is not a replacement for recipe deep dives or ABI parity. It is the
mechanical verifier that a migrated build uses hermetic dependency inputs
appropriate to its build system before the package-specific parity gate runs.

The formal ABI-prefix proof has the same execution rule. `formal/lean/` is
checked by `//formal/lean:lean_abi_parity_test`, which fetches Lean 4.34.0 as a
Bazel archive and runs `lake build` inside the insula. Host `lake`/`elan` builds
are developer diagnostics only, not accepted migration evidence.

### Native leaf proof — zlib-ng (Task 3)

`native/zlib_ng/zlib_ng.bzl`'s `zlib_ng_native` repository rule fetches the
pinned zlib-ng `2.3.3` source (same tag + sha256 the Spack `zlib-ng` package
pins), drives the **same** build Spack drives (autotools
`configure --zlib-compat && make && make install`, matching the Spack
`AutotoolsBuilder.configure_args()` for the `+compat` variant), and installs
into an in-repo `prefix/` tree. It exposes `@zlib_ng_native//:lib`.

Flipping the lock to `build:native` (via `native_overrides.json`, opt-in with
`VASO_NATIVE=1` in `run.sh`) makes `@spack_zlib_ng//:lib` an alias to it. The
synthetic consumer links against the native prefix unchanged, and
`//synthetic:zlib_ng_abi_parity` passes vs the Spack zlib-ng prefix
(layout/soname+symbol/link-and-run all green). This is scaffolding, not a
payoff — zlib-ng is a trivial leaf chosen to exercise the whole machinery
end-to-end.

### Closure steps beyond the leaf proof

The checked-in frontier has moved from the single-node `zlib-ng` leaf graph
through the `libxml2` closure and the installed `SPACK_ROOT_PKG=python` build
DAG. Native provider flips now cover the full Python-root closure, including:

- `python` (`native/python/`, `//synthetic:python_abi_parity`)
- `gettext` (`native/gettext/`, `//synthetic:gettext_abi_parity`)
- `tar` (`native/tar/`, `//synthetic:tar_prefix_parity`)
- `sqlite` (`native/sqlite/`, `//synthetic:sqlite_abi_parity`)
- `openssl` (`native/openssl/`, `//synthetic:openssl_abi_parity`)
- `perl` (`native/perl/`, `//synthetic:perl_prefix_parity`)
- `gdbm` (`native/gdbm/`, `//synthetic:gdbm_abi_parity`)
- `readline` (`native/readline/`, `//synthetic:readline_abi_parity`)
- `less` (`native/less/`, `//synthetic:less_prefix_parity`)
- `ncurses` (`native/ncurses/`, `//synthetic:ncurses_abi_parity`)
- `expat` (`native/expat/`, `//synthetic:expat_abi_parity`)
- `libbsd` (`native/libbsd/`, `//synthetic:libbsd_abi_parity`)
- `libmd` (`native/libmd/`, `//synthetic:libmd_abi_parity`)
- `bzip2` (`native/bzip2/`, `//synthetic:bzip2_abi_parity`)
- `diffutils` (`native/diffutils/`, `//synthetic:diffutils_prefix_parity`)
- `libffi` (`native/libffi/`, `//synthetic:libffi_abi_parity`)
- `berkeley-db` (`native/berkeley_db/`, `//synthetic:berkeley_db_abi_parity`)
- `libiconv` (`native/libiconv/`, `//synthetic:libiconv_abi_parity`)
- `pkgconf` (`native/pkgconf/`, `//synthetic:pkgconf_abi_parity`)
- `xz` (`native/xz/`, `//synthetic:xz_abi_parity`)
- `zlib-ng` (`native/zlib_ng/`, `//synthetic:zlib_ng_abi_parity`)
- `libxml2` (`native/libxml2/`, `//synthetic:libxml2_abi_parity`)
- `ca-certificates-mozilla` (`native/ca_certificates_mozilla/`,
  `//synthetic:ca_certificates_mozilla_prefix_parity`)
- `pigz` (`native/pigz/`, `//synthetic:pigz_prefix_parity`)
- `util-linux-uuid` (`native/util_linux_uuid/`,
  `//synthetic:util_linux_uuid_abi_parity`)
- `zstd` (`native/zstd/`, `//synthetic:zstd_abi_parity`)
- `utf8proc` (`native/utf8proc/`, `//synthetic:utf8proc_abi_parity`)
- `boost` (`native/boost/`, `//synthetic:boost_abi_parity`)
- `gperf` (`native/gperf/`, `//synthetic:gperf_prefix_parity`)
- `gzip` (`native/gzip/`, `//synthetic:gzip_prefix_parity`)
- `libsigsegv` (`native/libsigsegv/`, `//synthetic:libsigsegv_abi_parity`)
- `libunistring` (`native/libunistring/`, `//synthetic:libunistring_abi_parity`)
- `libidn2` (`native/libidn2/`, `//synthetic:libidn2_abi_parity`)
- `libyaml` (`native/libyaml/`, `//synthetic:libyaml_abi_parity`)
- `pcre2` (`native/pcre2/`, `//synthetic:pcre2_abi_parity`)
- `libedit` (`native/libedit/`, `//synthetic:libedit_abi_parity`)
- `nghttp2` (`native/nghttp2/`, `//synthetic:nghttp2_abi_parity`)
- `unzip` (`native/unzip/`, `//synthetic:unzip_prefix_parity`)
- `util-macros` (`native/util_macros/`, `//synthetic:util_macros_prefix_parity`)
- `fontsproto` (`native/fontsproto/`, `//synthetic:fontsproto_prefix_parity`)
- `libpciaccess` (`native/libpciaccess/`, `//synthetic:libpciaccess_abi_parity`)
- `xproto` (`native/xproto/`, `//synthetic:xproto_prefix_parity`)
- `xtrans` (`native/xtrans/`, `//synthetic:xtrans_prefix_parity`)
- `libfontenc` (`native/libfontenc/`, `//synthetic:libfontenc_abi_parity`)
- `hwloc` (`native/hwloc/`, `//synthetic:hwloc_abi_parity`)
- `autoconf` (`native/autoconf/`, `//synthetic:autoconf_prefix_parity`)
- `automake` (`native/automake/`, `//synthetic:automake_prefix_parity`)
- `libxcrypt` (`native/libxcrypt/`, `//synthetic:libxcrypt_abi_parity`)
- `coreutils` (`native/coreutils/`, `//synthetic:coreutils_prefix_parity`)
- `cuda` (`native/cuda/`, `//synthetic:use_cuda_boundary`)
- `cudnn` (`native/cudnn/`, `//synthetic:cudnn_abi_parity`)
- `cusparselt` (`native/cusparselt/`, `//synthetic:cusparselt_abi_parity`)
- `curl` (`native/curl/`, `//synthetic:curl_abi_parity`)
- `cmake` (`native/cmake/`, `//synthetic:cmake_prefix_parity`)
- `bdftopcf` (`native/bdftopcf/`, `//synthetic:bdftopcf_prefix_parity`)
- `lua` (`native/lua/`, `//synthetic:lua_abi_parity`)

`docs/recipes/<pkg>.md` records each Spack build-system deep dive, including
build-only tool nodes such as `pkgconf` and non-ELF data-prefix nodes such as
`ca-certificates-mozilla`.

CUDA is the first SDK/rootfs boundary rather than a source-build migration:

- `cuda` (`native/cuda/`, `//synthetic:use_cuda_boundary`) validates the
  hermetic insula SDK, rootfs provenance manifest, toolkit image
  `cuda:12.9.1`, `nvcc` compiler version `12.9.86`, and Spack-declared
  installer build deps (`coreutils`, `gzip`, `libxml2`) via Bazel-native prefix
  files. The rule is listed in `native_overrides.json` after passing inside the
  isolated CUDA 12.9.1 estate at
  `$VASO_ESTATE_ROOT`. Since CUDA is supplied by
  the sealed insula image, the no-install lock path records `spack_cuda` as
  `build: "native"` with `native_prefix: "@cuda_native//:lib"` and no Spack
  prefix.

cuDNN is the first binary-archive CUDA library migration:

- `cudnn` (`native/cudnn/`, `//synthetic:cudnn_abi_parity`) reproduces
  Spack's `Cudnn(Package)` install, whose concrete action is
  `install_tree(".", prefix)` from NVIDIA's redistributable archive. It uses the
  exact URL and SHA256 from Bazel's hermetic `@spack_dist//:spack` v1.2.2 for
  `cudnn-linux-x86_64-9.21.0.82_cuda12-archive.tar.xz`, refuses to evaluate
  outside `VASO_IN_INSULA=1`, consumes `@cuda_native//:prefix_path.txt`, and
  validates `CUDA_PREFIX/bin/nvcc`, `include/cudnn_version.h`, and
  `lib/libcudnn.so` before copying the archive payload into `prefix/`.
- The mechanism-specific guard reports
  `native/cudnn/cudnn.bzl: binary-archive: CUDA_PREFIX`, so this flip has an
  explicit hermetic dependency verifier for its build mechanism rather than a
  generic prefix copy.
- The focused parity run for
  `SPACK_ROOT_PKG='cudnn@9.21.0.82-12 ^cuda@12.9.1'` used
  `VASO_SPACK_INSTALL_ARGS='--only package'` so hermetic Spack installed the
  cuDNN reference while treating CUDA as the external SDK derived from the
  sealed insula. `//synthetic:cudnn_abi_parity` compared 57 layout paths,
  byte-identical `include/cudnn_version.h`, and matching SONAME/exported-symbol
  sets across `libcudnn`, `libcudnn_adv`, `libcudnn_cnn`,
  `libcudnn_engines_precompiled`, `libcudnn_engines_runtime_compiled`,
  `libcudnn_engines_tensor_ir`, `libcudnn_graph`, `libcudnn_heuristic`, and
  `libcudnn_ops`.

The next frontier graph is captured without installing or building PyTorch:

```text
py_torch_build_graph.json
root spec: py-torch cuda_arch=80,90,100 ^openblas~fortran ^font-util fonts:=encodings
nodes: 168
build systems: autotools=70, cmake=13, generic=19, makefile=5, meson=7,
               perl=1, python_pip=53
```

The graph-only path is:

```bash
SPACK_ROOT_PKG='py-torch cuda_arch=80,90,100 ^openblas~fortran ^font-util fonts:=encodings' \
VASO_GRAPH_ONLY=1 \
VASO_GRAPH_NO_PREFIX=1 \
VASO_SPACK_INSTALL=0 \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/py_torch_build_graph.json \
./run.sh
```

It still runs inside the insula through Bazel's `@spack_dist//:spack`; the
`--graph-no-prefix` mode prevents install-prefix resolution, and `--graph-only`
exits before any consumer/parity tests or native package builds run. This is
the accepted way to inspect a large future frontier without mutating the Spack
store with an expensive root install.

That PyTorch graph exposed one rootfs/toolchain requirement: even topology-only
concretization needs a Fortran-capable compiler because the BLAS/LAPACK surface
pulls Fortran providers. The CUDA rootfs builder now installs `gfortran`, and
the Bazel-owned Spack wrapper advertises `languages='c,c++,fortran'` only when
`/usr/bin/gfortran` is present inside the insula. This keeps the compiler facts
derived from the hermetic rootfs, not the host.

The first non-toolchain package in the `py-torch` graph is now:

```text
9  boost  1.90.0  generic/Boost.Build  native; ABI parity green
```

See `docs/recipes/boost.md` for the Boost.Build recipe capture, native provider,
and ABI evidence. The native provider is `@boost_native//:lib`, built by
`native/boost/boost.bzl`. It applies Spack's `bootstrap-compiler.patch`, pins
the concrete Boost.Build options, installs `-mt` symlinks, and passes the
hermetic ABI gate:

```bash
SPACK_ROOT_PKG='boost@1.90.0 +atomic +chrono +exception +shared +system +thread +multithreaded ~charconv ~container ~context ~contract ~conversion ~date_time ~fiber ~filesystem ~graph ~icu ~iostreams ~json ~locale ~log ~math ~mpi ~numpy ~program_options ~python ~random ~regex ~serialization ~singlethreaded ~stacktrace ~taggedlayout ~test ~timer ~url ~versionedlayout ~wave cxxstd=11 visibility=hidden' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_LOCK_OUT=/workspace/experiment/boost_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/boost_build_graph.json \
./run.sh
```

That run installs the Spack reference prefix through Bazel's hermetic
`@spack_dist//:spack`, then `//synthetic:boost_abi_parity` compares 16019
ABI-relevant layout paths, SONAME/exported symbols, and a Boost.Thread/Chrono
C++ link-and-run consumer.

The next py-torch frontier package after Boost is also native:

```text
14  gperf  3.3  autotools  native; prefix parity green
```

See `docs/recipes/gperf.md` for the Autotools recipe capture, native provider,
and parity evidence. `native/gperf/gperf.bzl` runs `configure --prefix`, `make`,
and `make install` inside the insula, using the same `gperf-3.3` source tarball
and SHA256 as hermetic Spack. The package has no non-toolchain dependency
prefixes, so its mechanism-specific hermetic-deps verifier is the
Autotools/no-dependency case in `//tools:hermetic_native_deps_guard_test`: the
rule is classified as Autotools, insula-gated, and prevented from relying on a
host Spack or host-discovered dependency prefix.

The parity run:

```bash
SPACK_ROOT_PKG='gperf@3.3' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SPACK_TIMEOUT=1800 \
VASO_LOCK_OUT=/workspace/experiment/gperf_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/gperf_build_graph.json \
./run.sh
```

installs the reference prefix through Bazel's hermetic `@spack_dist//:spack`,
flips `spack_gperf` to `@gperf_native//:lib`, and runs
`//synthetic:gperf_prefix_parity`. The gate compares the four-file
executable/doc layout, `bin/gperf` dynamic dependencies, `gperf --version`, and
perfect-hash C code generation behavior.

The next py-torch frontier package after gperf is also native:

```text
15  gzip  1.14  autotools  native; prefix parity green
```

See `docs/recipes/gzip.md` for the Autotools recipe capture, native provider,
and parity evidence. `native/gzip/gzip.bzl` runs the same out-of-tree
`configure --prefix`, `make`, and `make install` flow as hermetic Spack, with
the same source tarball and SHA256. Like gperf, gzip has no non-toolchain
dependency prefixes, so the mechanism-specific verifier is the
Autotools/no-dependency case in `//tools:hermetic_native_deps_guard_test`.

The parity run:

```bash
SPACK_ROOT_PKG='gzip@1.14' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SPACK_TIMEOUT=1800 \
VASO_LOCK_OUT=/workspace/experiment/gzip_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/gzip_build_graph.json \
./run.sh
```

installs the reference prefix through Bazel's hermetic `@spack_dist//:spack`,
flips `spack_gzip` to `@gzip_native//:lib`, and runs
`//synthetic:gzip_prefix_parity`. The gate compares the 24-file executable/doc
layout, executable dynamic dependencies, `gzip --version`, and deterministic
`gzip -n -c` compression output.

The next py-torch frontier package after gzip is also native:

```text
23  libsigsegv  2.15  autotools  native; ABI parity green
```

See `docs/recipes/libsigsegv.md` for the Autotools recipe capture, native
provider, and ABI evidence. `native/libsigsegv/libsigsegv.bzl` runs
`configure --prefix --enable-shared`, `make`, and `make install` inside the
insula, removes libtool archives to match Spack, and exposes `@libsigsegv_native//:lib`
with the observed `-lsigsegv` link contract.

The parity run:

```bash
SPACK_ROOT_PKG='libsigsegv@2.15' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_libsigsegv_native' \
VASO_SPACK_TIMEOUT=1800 \
VASO_LOCK_OUT=/workspace/experiment/libsigsegv_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/libsigsegv_build_graph.json \
./run.sh
```

installs the reference prefix through Bazel's hermetic `@spack_dist//:spack`,
flips `spack_libsigsegv` to `@libsigsegv_native//:lib`, and runs
`//synthetic:libsigsegv_abi_parity`. The gate compares the five-file
header/library layout, SONAME `libsigsegv.so.2`, 12 exported symbols, and a
downstream C link-and-run consumer.

The next py-torch frontier package after libsigsegv is also native:

```text
24  libunistring  1.4.2  autotools  native; ABI parity green
```

See `docs/recipes/libunistring.md` for the Autotools recipe capture, native
provider, and ABI evidence. `native/libunistring/libunistring.bzl` consumes the
Bazel-built `@libiconv_native//:prefix_path.txt`, validates `LIBICONV_PREFIX`
inside the build script, and threads it through `CPPFLAGS`, `LDFLAGS`, `LIBS`,
and `--with-libiconv-prefix`. This is the Autotools dependency-prefix verifier
case in `//tools:hermetic_native_deps_guard_test`.

The parity run:

```bash
SPACK_ROOT_PKG='libunistring@1.4.2' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_libunistring_native' \
VASO_SPACK_TIMEOUT=1800 \
VASO_LOCK_OUT=/workspace/experiment/libunistring_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/libunistring_build_graph.json \
./run.sh
```

installs the reference prefix through Bazel's hermetic `@spack_dist//:spack`,
flips `spack_libunistring` to `@libunistring_native//:lib`, and runs
`//synthetic:libunistring_abi_parity`. The gate compares the 24-entry
header/library layout, SONAME `libunistring.so.5`, 749 exported symbols, and a
downstream C link-and-run consumer with explicit libiconv reference/candidate
link prefixes.

The next py-torch frontier package after libunistring is also native:

```text
25  libidn2  2.3.8  autotools  native; ABI parity green
```

See `docs/recipes/libidn2.md` for the Autotools recipe capture, native
provider, and ABI evidence. `native/libidn2/libidn2.bzl` consumes the
Bazel-built `@libunistring_native//:prefix_path.txt` and
`@libiconv_native//:prefix_path.txt`, validates both prefixes inside the build
script, and threads them through `CPPFLAGS`, `LDFLAGS`, and `LIBS`. This is the
Autotools dependency-prefix verifier case in
`//tools:hermetic_native_deps_guard_test`.

The parity run:

```bash
SPACK_ROOT_PKG='libidn2' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_libidn2_native' \
VASO_SPACK_TIMEOUT=1800 \
VASO_LOCK_OUT=/workspace/experiment/libidn2_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/libidn2_build_graph.json \
./run.sh
```

installs the reference prefix through Bazel's hermetic `@spack_dist//:spack`,
flips `spack_libidn2` to `@libidn2_native//:lib`, and runs
`//synthetic:libidn2_abi_parity`. The gate compares the 6-entry
header/library layout, SONAME `libidn2.so.0`, 23 exported symbols, and matching
`idn2_lookup_u8` link-and-run output
`libidn2:2.3.8:xn--bcher-kva.example`.

The next py-torch frontier package after libidn2 is also native:

```text
26  libyaml  0.2.5  autotools  native; ABI parity green
```

See `docs/recipes/libyaml.md` for the Autotools recipe capture, native
provider, and ABI evidence. `native/libyaml/libyaml.bzl` runs Spack's observed
in-source `configure --prefix`, `make V=1`, and `make install` flow, removes
libtool archives to match the emitted prefix, and has no non-toolchain
dependency prefixes. This is the Autotools/no-dependency verifier case in
`//tools:hermetic_native_deps_guard_test`.

The parity run:

```bash
SPACK_ROOT_PKG='libyaml' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_libyaml_native' \
VASO_SPACK_TIMEOUT=1800 \
VASO_LOCK_OUT=/workspace/experiment/libyaml_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/libyaml_build_graph.json \
./run.sh
```

installs the reference prefix through Bazel's hermetic `@spack_dist//:spack`,
flips `spack_libyaml` to `@libyaml_native//:lib`, and runs
`//synthetic:libyaml_abi_parity`. The gate compares the 6-entry header/library
layout, SONAME `libyaml-0.so.2`, 58 exported symbols, and matching
`yaml_parser_load` link-and-run output `libyaml:0.2.5:mapping`.

The next py-torch frontier package after libyaml is also native:

```text
27  lzo  2.10  autotools  native; ABI parity green
```

See `docs/recipes/lzo.md` for the Autotools recipe capture, native provider,
and ABI evidence. `native/lzo/lzo.bzl` runs Spack's observed in-source
`configure --prefix --disable-dependency-tracking --enable-shared
--enable-static`, `make V=1`, and `make install` flow, removes libtool archives
to match the emitted prefix, and has no non-toolchain dependency prefixes. This
is another Autotools/no-dependency verifier case in
`//tools:hermetic_native_deps_guard_test`.

The parity run:

```bash
SPACK_ROOT_PKG='lzo' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_lzo_native' \
VASO_SPACK_TIMEOUT=1800 \
VASO_LOCK_OUT=/workspace/experiment/lzo_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/lzo_build_graph.json \
VASO_FORCE_FETCH_REPOS='@lzo_native' \
./run.sh
```

installs the reference prefix through Bazel's hermetic `@spack_dist//:spack`,
flips `spack_lzo` to `@lzo_native//:lib`, and runs
`//synthetic:lzo_abi_parity`. The gate compares the 18-entry header/library
layout, SONAME `liblzo2.so.2`, 116 exported symbols, prefix-normalized
`lzo2.pc`, and matching compression/decompression link-and-run output
`lzo:2.10:26:ok`.

The next py-torch frontier package after lzo is also native:

```text
28  m4  1.4.21  autotools  native; prefix parity green
```

See `docs/recipes/m4.md` for the Autotools recipe capture, native provider,
and prefix/behavior evidence. `native/m4/m4.bzl` runs Spack's observed
out-of-tree `spack-build` flow with `--enable-c++` and
`--with-libsigsegv-prefix`, consumes `diffutils` and `libsigsegv` through
mandatory Bazel prefix-file inputs, and validates those inputs before
configure. This is the Autotools/explicit-dependency verifier case in
`//tools:hermetic_native_deps_guard_test`.

The parity run:

```bash
SPACK_ROOT_PKG='m4' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_m4_native' \
VASO_SPACK_TIMEOUT=1800 \
VASO_LOCK_OUT=/workspace/experiment/m4_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/m4_build_graph.json \
./run.sh
```

installs the reference prefix through Bazel's hermetic `@spack_dist//:spack`,
flips `spack_m4` to `@m4_native//:lib`, and runs
`//synthetic:m4_prefix_parity`. The gate compares the five-entry
executable/doc layout, exact SHA256s for selected info/man files, `bin/m4`
dynamic dependencies, and matching `m4 --version` plus macro expansion output.

The next py-torch frontier package is:

```text
29  bison  3.8.2  autotools  native; prefix parity green
```

See `docs/recipes/bison.md` for the Autotools recipe capture, native provider,
and prefix/behavior evidence. `native/bison/bison.bzl` runs Spack's observed
out-of-tree `spack-build` flow, consumes `diffutils` and `m4` through mandatory
Bazel prefix-file inputs, validates `diff`, `cmp`, and `m4` before configure,
and pins Bison's build-tool dependency with `M4=<m4-prefix>/bin/m4` plus a
native-prefix `PATH`. This is the Autotools/pinned-build-tool verifier case in
`//tools:hermetic_native_deps_guard_test`.

The parity run:

```bash
SPACK_ROOT_PKG='bison' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_bison_native' \
VASO_SPACK_TIMEOUT=1800 \
VASO_LOCK_OUT=/workspace/experiment/bison_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/bison_build_graph.json \
VASO_FORCE_FETCH_REPOS='@bison_native' \
./run.sh
```

installs the reference prefix through Bazel's hermetic `@spack_dist//:spack`,
flips `spack_bison` to `@bison_native//:lib`, and runs
`//synthetic:bison_prefix_parity`. The gate compares the nine-entry
executable/static-archive/doc layout, exact SHA256s for selected data files,
static `liby.a` member/symbol parity, `bin/bison` and `bin/yacc` dynamic
dependencies, and matching `bison --version` plus parser-generation behavior.

See `docs/recipes/nasm.md` for the Autotools recipe capture, native provider,
and prefix/behavior evidence. `native/nasm/nasm.bzl` runs Spack's observed
out-of-tree `spack-build` flow with the release tarball's generated
`configure`, `make V=1`, and `make install` path. NASM has no non-toolchain
dependency prefixes, so this is the Autotools/no-dependency verifier case:
`//tools:hermetic_native_deps_guard_test` reports
`native/nasm/nasm.bzl: autotools: no dep prefixes`, while the repository rule
still refuses to run unless `VASO_IN_INSULA=1`.

The parity run:

```bash
SPACK_ROOT_PKG='nasm' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_nasm_native' \
VASO_SPACK_TIMEOUT=1800 \
VASO_LOCK_OUT=/workspace/experiment/nasm_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/nasm_build_graph.json \
VASO_FORCE_FETCH_REPOS='@nasm_native' \
./run.sh
```

seats the CUDA insula, installs/reuses the reference prefix through Bazel's
hermetic `@spack_dist//:spack`, flips `spack_nasm` to
`@nasm_native//:lib`, and runs `//synthetic:nasm_prefix_parity`. The gate
compares the four-file executable/doc layout, exact SHA256s for selected
manpages, `bin/nasm` and `bin/ndisasm` dynamic dependencies, and matching
`nasm -v`, `ndisasm -v`, plus deterministic assembly behavior.

The next py-torch frontier package after NASM is also native:

```text
31  openblas  0.3.33  makefile  native; ABI parity green
```

See `docs/recipes/openblas.md` for the Makefile recipe capture, native
provider, and ABI evidence. `native/openblas/openblas.bzl` runs Spack's
observed Makefile flow for the concrete `~fortran +dynamic_dispatch
threads=none +locking +shared ~static` variant, applies the same upstream
patch by SHA256, and installs the OpenBLAS prefix under Bazel control. OpenBLAS
has no non-toolchain dependency prefixes, so this is the Makefile/no-dependency
verifier case: `//tools:hermetic_native_deps_guard_test` reports
`native/openblas/openblas.bzl: makefile: no dep prefixes`, while the repository
rule still refuses to run unless `VASO_IN_INSULA=1`.

The parity run:

```bash
SPACK_ROOT_PKG='openblas@0.3.33 ~fortran threads=none' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_openblas_native //tools:hermetic_native_deps_guard_test //tools:abi_parity_unit_test' \
VASO_SPACK_TIMEOUT=3600 \
VASO_LOCK_OUT=/workspace/experiment/openblas_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/openblas_build_graph.json \
VASO_FORCE_FETCH_REPOS='@openblas_native' \
./run.sh
```

seats the CUDA insula, installs/reuses the reference prefix through Bazel's
hermetic `@spack_dist//:spack`, flips `spack_openblas` to
`@openblas_native//:lib`, and runs `//synthetic:openblas_abi_parity`. The gate
compares the header/library/pkg-config/CMake layout, SONAME
`libopenblas.so.0`, 17,488 exported dynamic symbols, static archive
member/symbol parity, prefix-normalized `openblas.pc`, byte-identical CMake
metadata, and matching downstream `cblas_dgemm()` output.

The next py-torch frontier package after OpenBLAS is also native:

```text
32  pcre2  10.44  autotools  native; ABI parity green
```

See `docs/recipes/pcre2.md` for the Autotools recipe capture, native provider,
and ABI evidence. `native/pcre2/pcre2.bzl` runs Spack's observed
`configure --enable-pcre2-16 --enable-pcre2-32`, `make V=1`, and `make
install` flow inside the CUDA insula, and pins the optional dependency decisions
that hermetic Spack recorded as disabled: no JIT, no zlib/bzip2 for
`pcre2grep`, and no readline/libedit for `pcre2test`. Pcre2 has no
non-toolchain dependency prefixes, so this is the Autotools/no-dependency
verifier case: `//tools:hermetic_native_deps_guard_test` reports
`native/pcre2/pcre2.bzl: autotools: no dep prefixes`, while the repository rule
still refuses to run unless `VASO_IN_INSULA=1`.

The parity run:

```bash
SPACK_ROOT_PKG='pcre2@10.44' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_pcre2_native //tools:hermetic_native_deps_guard_test //tools:abi_parity_unit_test' \
VASO_SPACK_TIMEOUT=1800 \
VASO_LOCK_OUT=/workspace/experiment/pcre2_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/pcre2_build_graph.json \
VASO_FORCE_FETCH_REPOS='@pcre2_native' \
./run.sh
```

seats the CUDA insula, installs/reuses the reference prefix through Bazel's
hermetic `@spack_dist//:spack`, flips `spack_pcre2` to
`@pcre2_native//:lib`, and runs `//synthetic:pcre2_abi_parity`. The gate
compares the 25-entry header/library/pkg-config/tool layout, SONAMEs for
`libpcre2-8.so.0`, `libpcre2-16.so.0`, `libpcre2-32.so.0`, and
`libpcre2-posix.so.3`, exported-symbol parity for all four shared libraries,
static archive member/symbol parity, prefix-normalized `pcre2-config` and
pkg-config metadata, executable dependency parity for `pcre2grep` and
`pcre2test`, and matching downstream `pcre2_compile()`/`pcre2_match()` output.

The next py-torch frontier package after Pcre2 is also native:

```text
36  libedit  3.1-20251016  autotools  native; ABI parity green
```

See `docs/recipes/libedit.md` for the Autotools recipe capture, native
provider, and ABI evidence. `native/libedit/libedit.bzl` runs Spack's observed
`autoreconf`, configure, `make V=1`, and `make install` flow inside the CUDA
insula. It consumes native `ncurses` and `pkgconf` prefixes, pins
`PKG_CONFIG`, `PKG_CONFIG_PATH`, `CPPFLAGS`, and `LDFLAGS`, and carries
Spack's cache args that force `tgetent` resolution through `libtinfo`:
`ac_cv_lib_curses_tgetent=no`, `ac_cv_lib_termcap_tgetent=no`, and
`ac_cv_lib_ncurses_tgetent=no`. This is the Autotools dependency-prefix
verifier case: `//tools:hermetic_native_deps_guard_test` reports
`native/libedit/libedit.bzl: autotools: NCURSES_PREFIX, PKGCONF_PREFIX`.

The parity run:

```bash
SPACK_ROOT_PKG='libedit@3.1-20251016' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_libedit_native //tools:hermetic_native_deps_guard_test //tools:native_build_mechanism_guard_unit_test //tools:abi_parity_unit_test' \
VASO_SPACK_TIMEOUT=1800 \
VASO_LOCK_OUT=/workspace/experiment/libedit_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/libedit_build_graph.json \
VASO_FORCE_FETCH_REPOS='@libedit_native' \
./run.sh
```

seats the CUDA insula, installs/reuses the reference prefix through Bazel's
hermetic `@spack_dist//:spack`, flips `spack_libedit` to
`@libedit_native//:lib`, and runs `//synthetic:libedit_abi_parity`. The gate
compares the 10-entry header/library/pkg-config/manpage layout, SONAME
`libedit.so.0`, 226 exported dynamic symbols, static archive member/symbol
parity, prefix-normalized `libedit.pc`, byte-identical selected manpages, and
matching downstream `history_init()`/`history()` output.

The next py-torch frontier package is also native:

```text
37  nghttp2  1.67.1  autotools  native; ABI parity green
```

See `docs/recipes/nghttp2.md` for the Autotools recipe capture, native
provider, and ABI evidence. `native/nghttp2/nghttp2.bzl` runs Spack's observed
`configure --enable-lib-only`, `make V=1`, and `make install` flow inside the
CUDA insula. It consumes native `diffutils` and `pkgconf` tool prefixes,
validates both before configure, pins `PKG_CONFIG`, places their `bin`
directories at the front of `PATH`, and carries every negative optional
dependency flag from the Spack recipe (`libxml2`, `jansson`, `zlib`, OpenSSL,
libev/libcares/libevent, jemalloc, systemd, mruby, neverbleed, boost, wolfssl,
and cunit disabled). This is the Autotools build-tool-prefix verifier case:
`//tools:hermetic_native_deps_guard_test` reports
`native/nghttp2/nghttp2.bzl: autotools: DIFFUTILS_PREFIX, PKGCONF_PREFIX`.

The parity run:

```bash
SPACK_ROOT_PKG='nghttp2@1.67.1' \
VASO_NATIVE=1 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_nghttp2_native //tools:hermetic_native_deps_guard_test //synthetic:nghttp2_abi_parity' \
VASO_LOCK_OUT=/workspace/experiment/nghttp2_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/nghttp2_build_graph.json \
VASO_FORCE_FETCH_REPOS='@nghttp2_native' \
./run.sh
```

seats the CUDA insula, installs/reuses the reference prefix through Bazel's
hermetic `@spack_dist//:spack`, flips `spack_nghttp2` to
`@nghttp2_native//:lib`, and runs `//synthetic:nghttp2_abi_parity`. The gate
compares the 12-entry header/library/pkg-config/doc/manpage layout, SONAME
`libnghttp2.so.14`, 181 exported dynamic symbols, static archive member/symbol
parity for 26 members and 421 symbols, prefix-normalized `libnghttp2.pc`,
byte-identical selected docs/manpages, and matching downstream
`nghttp2_version()`/header-validation output `nghttp2:1.67.1:h2`.

## Native UnZip (py-torch frontier)

`unzip@6.0` is a MakefilePackage executable-prefix node at py-torch topo index
40. It has no non-toolchain dependency prefixes; the concrete node depends on
the Spack compiler wrapper and `gmake` for build, and on `gcc-runtime`/`glibc`
for link/runtime.

The native provider is `@unzip_native//:lib`. It is built entirely inside the
CUDA insula, refuses evaluation without `VASO_IN_INSULA=1`, and mirrors the
hermetic Spack v1.2.2 build:

```text
apply configure-cflags.patch and strip.patch from the pinned spack-packages
  commit materialized by Bazel's hermetic Spack
apply the Fedora security patches listed in the Spack recipe
make -f unix/Makefile \
  'LOC=-Wno-error=implicit-function-declaration -Wno-error=implicit-int -DLARGE_FILE_SUPPORT' \
  generic
make -f unix/Makefile \
  'LOC=-Wno-error=implicit-function-declaration -Wno-error=implicit-int -DLARGE_FILE_SUPPORT' \
  prefix=<prefix> install
```

This is the Makefile no-dependency-prefix verifier case:

```text
native/unzip/unzip.bzl: makefile: no dep prefixes
```

The parity run:

```bash
SPACK_ROOT_PKG='unzip@6.0' \
VASO_NATIVE=1 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_unzip_native //tools:hermetic_native_deps_guard_test //synthetic:unzip_prefix_parity' \
VASO_LOCK_OUT=/workspace/experiment/unzip_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/unzip_build_graph.json \
VASO_FORCE_FETCH_REPOS='@unzip_native' \
./run.sh
```

seats the CUDA insula, installs/reuses the reference prefix through Bazel's
hermetic `@spack_dist//:spack`, flips `spack_unzip` to
`@unzip_native//:lib`, and runs `//synthetic:unzip_prefix_parity`. The gate
compares the 10-entry executable/manpage layout, dynamic dependencies for
`funzip`, `unzip`, `unzipsfx`, and `zipinfo`, script classification for
`zipgrep`, byte-identical selected manpages, and matching `unzip -v`,
`zipinfo -h`, and invalid-archive `unzip -t` behavior. The smoke target creates
a ZIP archive inside the test sandbox and prints `unzip:6.0:ok`.

## Native util-macros (py-torch frontier)

`util-macros@1.20.2` is a data-only X.Org Autotools macro package at py-torch
topo index 42. `util-linux-uuid` at index 41 was already native, so this is the
next not-yet-native frontier node after `unzip`.

The native provider is `@util_macros_native//:lib`. It is built entirely inside
the CUDA insula, refuses evaluation without `VASO_IN_INSULA=1`, and mirrors the
hermetic Spack v1.2.2 build:

```text
./configure --prefix=<prefix>
make V=1
make install
```

The emitted prefix contains only:

```text
share/aclocal/xorg-macros.m4
share/pkgconfig/xorg-macros.pc
share/util-macros/INSTALL
```

This is the Autotools no-dependency-prefix verifier case:

```text
native/util_macros/util_macros.bzl: autotools: no dep prefixes
```

The focused parity run:

```bash
VASO_NATIVE=1 \
SPACK_ROOT_PKG='util-macros@1.20.2' \
VASO_LOCK_OUT=/workspace/experiment/util_macros_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/util_macros_build_graph.json \
VASO_FORCE_FETCH_REPOS='@util_macros_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_util_macros_native //tools:hermetic_native_deps_guard_test //synthetic:util_macros_prefix_parity' \
VASO_SPACK_TIMEOUT=1200 \
./run.sh
```

seats the CUDA insula, installs/reuses the reference prefix through Bazel's
hermetic `@spack_dist//:spack`, flips `spack_util_macros` to
`@util_macros_native//:lib`, and runs `//synthetic:util_macros_prefix_parity`.
The gate compares the three-entry data layout, byte-identical
`xorg-macros.m4`, prefix-normalized `xorg-macros.pc`, byte-identical `INSTALL`,
and the expected empty ELF ABI axis. The smoke target prints
`util-macros:1.20.2:ok`.

## Native fontsproto (py-torch frontier)

`fontsproto@2.1.3` is a header/pkg-config X.Org Autotools protocol package at
py-torch topo index 43. `util-macros` at index 42 is already native, so this
advances the next not-yet-native frontier node.

The native provider is `@fontsproto_native//:lib`. It is built entirely inside
the CUDA insula, refuses evaluation without `VASO_IN_INSULA=1`, consumes
`@pkgconf_native//:prefix_path.txt` and
`@util_macros_native//:prefix_path.txt`, and mirrors the hermetic Spack v1.2.2
build:

```text
./configure --prefix=<prefix>
make V=1
make install
```

The emitted prefix contains only:

```text
include/X11/fonts/FS.h
include/X11/fonts/FSproto.h
include/X11/fonts/font.h
include/X11/fonts/fontproto.h
include/X11/fonts/fontstruct.h
include/X11/fonts/fsmasks.h
lib/pkgconfig/fontsproto.pc
share/doc/fontsproto/fsproto.xml
```

This is the Autotools dependency-prefix verifier case:

```text
native/fontsproto/fontsproto.bzl: autotools: PKGCONF_PREFIX, UTIL_MACROS_PREFIX
```

The focused parity run:

```bash
VASO_NATIVE=1 \
SPACK_ROOT_PKG='fontsproto@2.1.3' \
VASO_LOCK_OUT=/workspace/experiment/fontsproto_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/fontsproto_build_graph.json \
VASO_FORCE_FETCH_REPOS='@fontsproto_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_fontsproto_native //tools:hermetic_native_deps_guard_test //synthetic:fontsproto_prefix_parity' \
VASO_SPACK_TIMEOUT=1200 \
./run.sh
```

seats the CUDA insula, installs/reuses the reference prefix through Bazel's
hermetic `@spack_dist//:spack`, flips `spack_fontsproto` to
`@fontsproto_native//:lib`, verifies the Autotools dependency channels with
`//tools:hermetic_native_deps_guard_test`, and runs
`//synthetic:fontsproto_prefix_parity`. The gate compares the eight-entry
header/pkg-config/XML layout, byte-identical installed headers, a
prefix-normalized `fontsproto.pc`, byte-identical `fsproto.xml`, and the
expected empty ELF ABI axis. The smoke target prints
`fontsproto:2.1.3:ok`.

## Native libpciaccess (py-torch frontier)

`libpciaccess@0.17` is an X.Org Autotools library at py-torch topo index 44.
`fontsproto` at index 43 is already native, so this advances the next
not-yet-native frontier node.

The native provider is `@libpciaccess_native//:lib`. It is built entirely
inside the CUDA insula, refuses evaluation without `VASO_IN_INSULA=1`, consumes
`@pkgconf_native//:prefix_path.txt` and
`@util_macros_native//:prefix_path.txt`, and mirrors the hermetic Spack v1.2.2
build:

```text
autoreconf
./configure --prefix=<prefix>
make V=1
make install
```

The emitted prefix contains:

```text
include/pciaccess.h
lib/libpciaccess.a
lib/libpciaccess.so
lib/libpciaccess.so.0
lib/libpciaccess.so.0.11.1
lib/pkgconfig/pciaccess.pc
```

This is the Autotools dependency-prefix verifier case:

```text
native/libpciaccess/libpciaccess.bzl: autotools: PKGCONF_PREFIX, UTIL_MACROS_PREFIX
```

The focused parity run:

```bash
VASO_NATIVE=1 \
SPACK_ROOT_PKG='libpciaccess@0.17' \
VASO_LOCK_OUT=/workspace/experiment/libpciaccess_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/libpciaccess_build_graph.json \
VASO_FORCE_FETCH_REPOS='@libpciaccess_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_libpciaccess_native //tools:hermetic_native_deps_guard_test //synthetic:libpciaccess_abi_parity' \
VASO_SPACK_TIMEOUT=1200 \
./run.sh
```

seats the CUDA insula, installs/reuses the reference prefix through Bazel's
hermetic `@spack_dist//:spack`, flips `spack_libpciaccess` to
`@libpciaccess_native//:lib`, verifies the Autotools dependency channels with
`//tools:hermetic_native_deps_guard_test`, and runs
`//synthetic:libpciaccess_abi_parity`. The gate compares the six-entry
header/library/pkg-config layout, byte-identical `pciaccess.h`, a
prefix-normalized `pciaccess.pc`, SONAME `libpciaccess.so.0`, 58 exported
dynamic symbols, and matching downstream link-and-run output. The smoke target
prints `libpciaccess:0.17:null-iterator-ok`.

## Native xproto (py-torch frontier)

`xproto@7.0.31` is a header/pkg-config X.Org Autotools protocol package at
py-torch topo index 45. `libpciaccess` at index 44 is already native, so this
advances the next not-yet-native frontier node.

The native provider is `@xproto_native//:lib`. It is built entirely inside the
CUDA insula, refuses evaluation without `VASO_IN_INSULA=1`, consumes
`@pkgconf_native//:prefix_path.txt` and
`@util_macros_native//:prefix_path.txt`, and mirrors the hermetic Spack v1.2.2
build:

```text
autoreconf
./configure --prefix=<prefix>
make V=1
make install  # serial, matching Spack's package override
```

The emitted prefix contains 28 header/pkg-config/XML entries, including:

```text
include/X11/X.h
include/X11/Xproto.h
include/X11/Xprotostr.h
include/X11/keysymdef.h
lib/pkgconfig/xproto.pc
share/doc/xproto/x11protocol.xml
```

This is the Autotools dependency-prefix verifier case:

```text
native/xproto/xproto.bzl: autotools: PKGCONF_PREFIX, UTIL_MACROS_PREFIX
```

The focused parity run:

```bash
VASO_NATIVE=1 \
SPACK_ROOT_PKG='xproto@7.0.31' \
VASO_LOCK_OUT=/workspace/experiment/xproto_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/xproto_build_graph.json \
VASO_FORCE_FETCH_REPOS='@xproto_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_xproto_native //tools:hermetic_native_deps_guard_test //synthetic:xproto_prefix_parity' \
VASO_SPACK_TIMEOUT=1200 \
./run.sh
```

seats the CUDA insula, installs/reuses the reference prefix through Bazel's
hermetic `@spack_dist//:spack`, flips `spack_xproto` to
`@xproto_native//:lib`, verifies the Autotools dependency channels with
`//tools:hermetic_native_deps_guard_test`, and runs
`//synthetic:xproto_prefix_parity`. The gate compares the 28-entry
header/pkg-config/XML layout, byte-identical representative installed headers,
a prefix-normalized `xproto.pc`, byte-identical `x11protocol.xml`, and the
expected empty ELF ABI axis. The smoke target prints `xproto:7.0.31:ok`.

The next not-yet-native py-torch frontier node is `xtrans@1.6.0` at topo
index 46.

## Native xtrans (py-torch frontier)

`xtrans@1.6.0` is a header/source-include/pkg-config X.Org Autotools transport
package at py-torch topo index 46. `xproto` at index 45 is already native, so
this advances the next not-yet-native frontier node. The immediate topo entries
after xtrans, `xz` and `zlib-ng`, were migrated earlier.

The native provider is `@xtrans_native//:lib`. It is built entirely inside the
CUDA insula, refuses evaluation without `VASO_IN_INSULA=1`, consumes
`@pkgconf_native//:prefix_path.txt` and
`@util_macros_native//:prefix_path.txt`, and mirrors the hermetic Spack v1.2.2
build:

```text
autoreconf
./configure --prefix=<prefix>
make V=1
make install
```

The emitted prefix contains:

```text
include/X11/Xtrans/Xtrans.c
include/X11/Xtrans/Xtrans.h
include/X11/Xtrans/Xtransint.h
include/X11/Xtrans/Xtranslcl.c
include/X11/Xtrans/Xtranssock.c
include/X11/Xtrans/Xtransutil.c
include/X11/Xtrans/transport.c
share/aclocal/xtrans.m4
share/doc/xtrans/xtrans.xml
share/pkgconfig/xtrans.pc
```

This is the Autotools dependency-prefix verifier case:

```text
native/xtrans/xtrans.bzl: autotools: PKGCONF_PREFIX, UTIL_MACROS_PREFIX
```

The focused parity run:

```bash
VASO_NATIVE=1 \
SPACK_ROOT_PKG='xtrans@1.6.0' \
VASO_LOCK_OUT=/workspace/experiment/xtrans_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/xtrans_build_graph.json \
VASO_FORCE_FETCH_REPOS='@xtrans_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_xtrans_native //tools:hermetic_native_deps_guard_test //synthetic:xtrans_prefix_parity' \
VASO_SPACK_TIMEOUT=1200 \
./run.sh
```

seats the CUDA insula, installs/reuses the reference prefix through Bazel's
hermetic `@spack_dist//:spack`, flips `spack_xtrans` to
`@xtrans_native//:lib`, verifies the Autotools dependency channels with
`//tools:hermetic_native_deps_guard_test`, and runs
`//synthetic:xtrans_prefix_parity`. The gate compares the 10-entry
header/source-include/pkg-config/macro/XML layout, byte-identical
representative installed files, a prefix-normalized `xtrans.pc`, and the
expected empty ELF ABI axis. The smoke target prints `xtrans:1.6.0:ok`.

## Native libfontenc (py-torch frontier)

`libfontenc@1.1.8` is an X.Org Autotools library at py-torch topo index 49.
`xtrans` at index 46 is already native; the intervening `xz` and `zlib-ng`
nodes were migrated earlier, so this advances the next not-yet-native frontier
node.

The native provider is `@libfontenc_native//:lib`. It is built entirely inside
the CUDA insula, refuses evaluation without `VASO_IN_INSULA=1`, consumes
`@pkgconf_native//:prefix_path.txt`, `@util_macros_native//:prefix_path.txt`,
`@xproto_native//:prefix_path.txt`, and `@zlib_ng_native//:prefix_path.txt`,
and mirrors the hermetic Spack v1.2.2 build:

```text
autoreconf
./configure --prefix=<prefix>
make V=1
make install
```

The emitted prefix contains:

```text
include/X11/fonts/fontenc.h
lib/libfontenc.a
lib/libfontenc.so
lib/libfontenc.so.1
lib/libfontenc.so.1.0.0
lib/pkgconfig/fontenc.pc
```

This is the Autotools dependency-prefix verifier case:

```text
native/libfontenc/libfontenc.bzl: autotools: PKGCONF_PREFIX, UTIL_MACROS_PREFIX, XPROTO_PREFIX, ZLIB_PREFIX
```

The focused parity run:

```bash
VASO_NATIVE=1 \
SPACK_ROOT_PKG='libfontenc@1.1.8' \
VASO_LOCK_OUT=/workspace/experiment/libfontenc_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/libfontenc_build_graph.json \
VASO_FORCE_FETCH_REPOS='@libfontenc_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_libfontenc_native //tools:hermetic_native_deps_guard_test //synthetic:libfontenc_abi_parity' \
VASO_SPACK_TIMEOUT=1200 \
./run.sh
```

seats the CUDA insula, installs/reuses the reference prefix through Bazel's
hermetic `@spack_dist//:spack`, flips `spack_libfontenc` to
`@libfontenc_native//:lib`, verifies the Autotools dependency channels with
`//tools:hermetic_native_deps_guard_test`, and runs
`//synthetic:libfontenc_abi_parity`. The gate compares the six-entry
header/library/pkg-config layout, byte-identical `fontenc.h`, a
prefix-normalized `fontenc.pc`, SONAME `libfontenc.so.1`, 15 exported dynamic
symbols, and matching downstream link-and-run output. The smoke target prints
`libfontenc:iso10646-1:encodings-dir`.

### hwloc@2.13.0

`docs/recipes/hwloc.md` captures the hermetic Spack v1.2.2 recipe and prefix
for `hwloc@2.13.0`, taken only from the Bazel-owned `@spack_dist//:spack`
inside the CUDA insula. The package is an Autotools hardware-topology
library/tool node with concrete variants `+pci`, `+libxml2`, `~cuda`, `~nvml`,
`~gl`, `~libudev`, `~opencl`, `~rocm`, `~level_zero`, and
`libs=shared,static`. Its Spack DAG edges remain `libpciaccess`, `libxml2`,
`ncurses`, and build-time `pkgconf`; only the provider flips.

`native/hwloc/hwloc.bzl` applies the same `2.13.0` patch and mirrors Spack's
captured configure contract:

```text
--disable-cairo --disable-nvml --disable-gl --disable-cuda
--enable-libxml2 --disable-libudev --enable-pci
--enable-shared --enable-static --disable-levelzero
--disable-opencl --disable-rsmi
```

It refuses to evaluate outside `VASO_IN_INSULA=1`, consumes
`@libpciaccess_native`, `@libxml2_native`, `@ncurses_native`, and
`@pkgconf_native` through mandatory prefix files, validates the prefixes in the
build script, pins `PKG_CONFIG`, and threads dependency prefixes through
`PKG_CONFIG_PATH`, `CPPFLAGS`, `LDFLAGS`, and `LIBS`.

The mechanism-specific dependency verifier reports:

```text
native/hwloc/hwloc.bzl: autotools: LIBPCIACCESS_PREFIX, LIBXML2_PREFIX, NCURSES_PREFIX, PKGCONF_PREFIX
```

The focused parity run:

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

seats the CUDA insula, installs/reuses the reference prefix through Bazel's
hermetic `@spack_dist//:spack`, flips `spack_hwloc` to
`@hwloc_native//:lib`, verifies the Autotools dependency channels with
`//tools:hermetic_native_deps_guard_test`, and runs
`//synthetic:hwloc_abi_parity`. The gate compares 33 ABI-relevant layout
entries, byte-identical public/config headers, prefix-normalized `hwloc.pc`
including explicit dependency-prefix aliases, SONAME `libhwloc.so.15`, 210
exported dynamic symbols, static archive member/symbol parity, executable
NEEDED sets for representative tools, deterministic `hwloc-info`/`hwloc-calc`
behavior, and matching synthetic-topology downstream link-and-run output
`hwloc:api=0x00020c00:depth=4:cores=2:pus=2`.

### autoconf@2.72

`docs/recipes/autoconf.md` captures the hermetic Spack v1.2.2 recipe and
prefix for `autoconf@2.72`, taken only from the Bazel-owned
`@spack_dist//:spack` inside the CUDA insula. The package is an Autotools
build/run tool prefix. Its Spack DAG edges remain `m4` and `perl`; only the
provider flips.

`native/autoconf/autoconf.bzl` mirrors Spack's `spack-build`
configure/make/install flow. It refuses to evaluate outside
`VASO_IN_INSULA=1`, consumes `@m4_native` and `@perl_native` through mandatory
prefix files, validates the prefixes in the build script, pins `M4`, `PERL`,
and `PATH`, preserves Spack's build-time `bin/autom4te.in` shebang workaround,
and then rewrites installed `bin/autom4te` back to the concrete native Perl
path.

The mechanism-specific dependency verifier reports:

```text
native/autoconf/autoconf.bzl: autotools: M4_PREFIX, PERL_PREFIX
```

The focused parity run:

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

seats the CUDA insula, installs/reuses the reference prefix through Bazel's
hermetic `@spack_dist//:spack`, flips `spack_autoconf` to
`@autoconf_native//:lib`, verifies the Autotools dependency channels with
`//tools:hermetic_native_deps_guard_test`, and runs
`//synthetic:autoconf_prefix_parity`. The gate compares executable layout and
NEEDED parity, prefix-normalized installed script data for the seven Autoconf
tools including explicit `m4` and `perl` dependency-prefix aliases,
byte-identical representative macro/info/man data, `autoconf --version`
behavior, and deterministic `configure` generation. The smoke target prints
`autoconf:2.72:ok`.

### automake@1.18.1

`docs/recipes/automake.md` captures the hermetic Spack v1.2.2 recipe and
prefix for `automake@1.18.1`, taken only from the Bazel-owned
`@spack_dist//:spack` inside the CUDA insula. The package is an Autotools
build/run tool prefix. Its Spack link-DAG edge remains `perl`; the native
runtime behavior also puts the already-native Autoconf prefix on `PATH` so
`autom4te` is resolved the same way Spack resolves it.

`native/automake/automake.bzl` mirrors Spack's `spack-build`
configure/make/install flow. It refuses to evaluate outside
`VASO_IN_INSULA=1`, consumes `@autoconf_native` and `@perl_native` through
mandatory prefix files, validates those prefixes in the build script, pins
`PERL`, and puts the native Autoconf and Perl tool bins at the front of
`PATH`. It applies Spack's `bin/aclocal.in` and `bin/automake.in`
`#!/usr/bin/env perl` shebang patch and deletes any libtool archives from the
emitted prefix.

The mechanism-specific dependency verifier reports:

```text
native/automake/automake.bzl: autotools: AUTOCONF_PREFIX, PERL_PREFIX
```

The focused parity run:

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

seats the CUDA insula, installs/reuses the reference prefix through Bazel's
hermetic `@spack_dist//:spack`, flips `spack_automake` to
`@automake_native//:lib`, verifies the Autotools dependency channels with
`//tools:hermetic_native_deps_guard_test`, and runs
`//synthetic:automake_prefix_parity`. The gate compares 22 installed
executable/macro/module/doc paths, executable NEEDED parity for `aclocal` and
`automake`, prefix-normalized installed scripts/modules with explicit Autoconf
and Perl dependency-prefix aliases, byte-identical representative macro and
documentation payloads, `automake --version` and `aclocal --version`, and
matching `aclocal` plus `automake --add-missing --foreign` behavior in a
temporary project. The smoke target prints `automake:1.18.1:ok`.

### libxcrypt@4.5.2

`docs/recipes/libxcrypt.md` captures the hermetic Spack v1.2.2 recipe and
prefix for `libxcrypt@4.5.2`, taken only from the Bazel-owned
`@spack_dist//:spack` inside the CUDA insula. The package is an Autotools C
library. Its Spack build dependency on Perl is satisfied by the already-native
Perl prefix; the concrete link contract is `-lcrypt` with headers under
`include/`.

`native/libxcrypt/libxcrypt.bzl` mirrors Spack's configure/make/install flow.
It refuses to evaluate outside `VASO_IN_INSULA=1`, consumes `@perl_native`
through a mandatory prefix file, validates that prefix in the build script,
pins `PERL`, and puts the native Perl bin directory at the front of `PATH`.
It reproduces Spack's `ac_cv_path_python3_passlib=not found`,
`--disable-werror`, and `--disable-obsolete-api` configure arguments, then
deletes libtool archives from the emitted prefix.

The mechanism-specific dependency verifier reports:

```text
native/libxcrypt/libxcrypt.bzl: autotools: PERL_PREFIX
```

The focused parity run:

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

seats the CUDA insula, installs/reuses the reference prefix through Bazel's
hermetic `@spack_dist//:spack`, flips `spack_libxcrypt` to
`@libxcrypt_native//:lib`, verifies the Autotools dependency channel with
`//tools:hermetic_native_deps_guard_test`, and runs
`//synthetic:libxcrypt_abi_parity`. The gate compares the seven-entry
header/library/pkg-config layout, SONAME `libcrypt.so.2`, 9 exported dynamic
symbols, static archive member/symbol parity for 36 members and 116 symbols,
prefix-normalized `libcrypt.pc` and `libxcrypt.pc`, and matching downstream
`crypt()` link-and-run output `libxcrypt:4.5.2:116`.

`curl@8.20.0` at py-torch topo index 60 is now native. `cuda@12.9.1` at topo
index 58 and `cudnn@9.21.0.82-12` at topo index 59 were already native in the
ledger, so this advances the frontier past the CUDA/cuDNN boundary.

curl is the next Autotools frontier node after the CUDA/cuDNN boundary:

- `curl` (`native/curl/`, `//synthetic:curl_abi_parity`) reproduces Spack's
  `Curl(AutotoolsPackage)` build for `curl@8.20.0 +nghttp2 tls=openssl
  libs=shared,static`, consuming already-native `nghttp2`, `openssl`,
  `pkgconf`, and `zlib-ng` prefixes through mandatory Bazel prefix files.
- The mechanism-specific guard reports
  `native/curl/curl.bzl: autotools: NGHTTP2_PREFIX, OPENSSL_PREFIX,
  PKGCONF_PREFIX, ZLIB_PREFIX`, so the Autotools dependency lookup is pinned
  through explicit `PKG_CONFIG`, `PKG_CONFIG_PATH`, and `--with-*` configure
  arguments. The rule deliberately does not export extra `CPPFLAGS`,
  `LDFLAGS`, or `LIBS` because that changed `libcurl.pc` and `curl-config`
  metadata relative to the hermetic Spack prefix.
- The native build sets Autoconf cache variable `enable_symbol_hiding=no`
  before `./configure`. Hermetic Spack's compiler wrapper requested symbol
  hiding but the probe concluded it would not be used; native gcc would
  otherwise hide most of `libcurl.so.4`'s exported symbols. This keeps ABI
  parity without changing visible `curl-config --configure` output.
- The native build sets `LD_RUN_PATH` before `make`, binding runtime lookup to
  the native zlib-ng, OpenSSL, and nghttp2 prefixes instead of rootfs system
  libraries.
- The focused reference run for
  `SPACK_ROOT_PKG='curl@8.20.0 +nghttp2 tls=openssl ^openssl@3.6.1
  ^nghttp2@1.67.1 ^zlib-ng@2.3.3'` used Bazel's hermetic
  `@spack_dist//:spack` inside the CUDA 12.9.1 insula and produced
  `curl_spack_graph.lock.json` plus `curl_build_graph.json`.
- The focused native run used the same hermetic Spack and isolated estate:

```bash
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='curl@8.20.0 +nghttp2 tls=openssl ^openssl@3.6.1 ^nghttp2@1.67.1 ^zlib-ng@2.3.3' \
VASO_NATIVE=1 \
VASO_LOCK_OUT=/workspace/experiment/curl_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/curl_native_build_graph.json \
VASO_FORCE_FETCH_REPOS='@curl_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_curl_native //tools:hermetic_native_deps_guard_test' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

That run passed `//synthetic:spack_selfcheck` with hermetic Spack `1.2.2`,
`//tools:hermetic_spack_guard_test`,
`//tools:hermetic_native_deps_guard_test`,
`//synthetic:use_curl_native`, and `//synthetic:curl_abi_parity`. The parity
JSON reported `ok: true`, 21 layout paths, shared/static `libcurl` symbol
parity with 1080 exported dynamic symbols, prefix-normalized `libcurl.pc`,
`libcurl.m4`, and `curl-config`, executable dependency checks, matching
`curl --version` and `curl-config --feature`, and matching downstream output
`curl:8.20.0:ssl:http2`.

`cmake@3.31.11` at py-torch topo index 61 is now native. It is a Spack
`Package`/generic recipe, but its concrete Linux build mechanism is CMake's own
`./bootstrap` script followed by `make install`, so the native verifier treats
it as a CMake-mechanism build.

- `cmake` (`native/cmake/`, `//synthetic:cmake_prefix_parity`) reproduces
  Spack's `cmake@3.31.11 +ownlibs +ncurses ~doc ~qtgui` bootstrap flow,
  consuming already-native `curl`, `ncurses`, and `zlib-ng` prefixes through
  mandatory Bazel prefix files.
- The mechanism-specific guard reports
  `native/cmake/cmake.bzl: cmake: CURL_PREFIX, NCURSES_PREFIX, ZLIB_PREFIX`.
  The rule refuses to evaluate unless `VASO_IN_INSULA=1`, validates
  `curl.h`, `bin/curl`, `ncurses.h`, `zlib.h`, and the dependency libraries,
  then threads dependency lookup through `CMAKE_PREFIX_PATH`,
  `CMAKE_LIBRARY_PATH`, `CMAKE_INCLUDE_PATH`, `PKG_CONFIG_PATH`,
  `LD_LIBRARY_PATH`, and explicit install RPATH settings.
- CMake exposes no public C ABI in the Spack recipe (`libs` and `headers` are
  empty), so `@cmake_native//:lib` is an empty `cc_library` and the prefix is
  carried by `@cmake_native//:prefix` plus `prefix_path.txt`.
- The focused reference run for
  `SPACK_ROOT_PKG='cmake@3.31.11 +ownlibs +ncurses ~doc ~qtgui'` used Bazel's
  hermetic `@spack_dist//:spack` inside the CUDA 12.9.1 insula and produced
  `cmake_spack_graph.lock.json` plus `cmake_build_graph.json`.
- The focused native run used the same hermetic Spack and isolated estate:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='cmake@3.31.11 +ownlibs +ncurses ~doc ~qtgui' \
VASO_NATIVE=1 \
VASO_LOCK_OUT=/workspace/experiment/cmake_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/cmake_native_build_graph.json \
VASO_FORCE_FETCH_REPOS='@cmake_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_cmake_native //tools:hermetic_native_deps_guard_test' \
VASO_SPACK_TIMEOUT=3600 \
./run.sh
```

That run passed `//synthetic:spack_selfcheck` with hermetic Spack `1.2.2`,
`//tools:hermetic_spack_guard_test`,
`//tools:hermetic_native_deps_guard_test`,
`//synthetic:use_cmake_native`, and `//synthetic:cmake_prefix_parity`. The
parity JSON reported `ok: true`, nine selected layout paths, byte-identical
selected data files, empty shared-library ABI axis, executable dependency
parity for `cmake`, `ctest`, `cpack`, and `ccmake`, and matching `--version`
behavior for all four executables.

`cusparselt@0.8.1-cuda120` at py-torch topo index 62 is now native. It is a
Spack `Package`/generic recipe, but the concrete install mechanism is NVIDIA's
binary archive copied into `prefix.lib` and `prefix.include`, so the native
verifier treats it as a binary-archive build.

- `cusparselt` (`native/cusparselt/`,
  `//synthetic:cusparselt_abi_parity`) reproduces Spack's
  `install_tree("lib", prefix.lib)` and `install_tree("include", prefix.include)`
  flow from the exact hermetic-Spack-pinned archive.
- The mechanism-specific guard reports
  `native/cusparselt/cusparselt.bzl: binary-archive: CUDA_PREFIX`. The rule
  refuses to evaluate unless `VASO_IN_INSULA=1`, consumes
  `@cuda_native//:prefix_path.txt`, validates `CUDA_PREFIX/bin/nvcc`, validates
  `include/cusparseLt.h`, `lib/libcusparseLt.so`,
  `lib/libcusparseLt.so.0`, `lib/libcusparseLt.so.0.8.1.1`, and
  `lib/libcusparseLt_static.a`, then emits `binary_archive.json`.
- The focused reference run for
  `SPACK_ROOT_PKG='cusparselt@0.8.1-cuda120 ^cuda@12.9.1'` used Bazel's
  hermetic `@spack_dist//:spack` inside the CUDA 12.9.1 insula and produced
  `cusparselt_spack_graph.lock.json` plus `cusparselt_build_graph.json`.
- The focused native run used the same hermetic Spack and isolated estate:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='cusparselt@0.8.1-cuda120 ^cuda@12.9.1' \
VASO_SPACK_INSTALL_ARGS='--only package' \
VASO_NATIVE=1 \
VASO_LOCK_OUT=/workspace/experiment/cusparselt_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/cusparselt_native_build_graph.json \
VASO_FORCE_FETCH_REPOS='@cusparselt_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_cusparselt_native //tools:hermetic_native_deps_guard_test' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

That run passed `//synthetic:spack_selfcheck` with hermetic Spack `1.2.2`,
`//tools:hermetic_spack_guard_test`,
`//tools:hermetic_native_deps_guard_test`,
`//synthetic:use_cusparselt_native`, and
`//synthetic:cusparselt_abi_parity`. The parity JSON reported `ok: true`, five
layout paths, byte-identical `cusparseLt.h`, byte-identical
`libcusparseLt_static.a`, SONAME `libcusparseLt.so.0`, and matching exported
symbol sets with 42 symbols.

`eigen@5.0.1` at py-torch topo index 63 is now native. It is a Spack
`CMakePackage` and a header-only C++ dependency in this graph.

- `eigen` (`native/eigen/`, `//synthetic:eigen_prefix_parity`) reproduces
  Spack's CMake install flow with tests, BLAS, LAPACK, IPO, nightly, and ROCm
  disabled, emitting the same `include/eigen3`, pkg-config, and CMake package
  metadata prefix.
- The mechanism-specific guard reports
  `native/eigen/eigen.bzl: cmake: CMAKE_PREFIX`. The rule refuses to evaluate
  unless `VASO_IN_INSULA=1`, consumes `@cmake_native//:prefix_path.txt`,
  validates `CMAKE_PREFIX/bin/cmake`, and threads lookup through `PATH` and
  `CMAKE_PREFIX_PATH`.
- Eigen exposes no library ABI in this migration: `@eigen_native//:lib` is a
  header-only `cc_library`, while `@eigen_native//:prefix` and
  `prefix_path.txt` carry the installed prefix.
- The focused reference run for `SPACK_ROOT_PKG='eigen@5.0.1'` used Bazel's
  hermetic `@spack_dist//:spack` inside the CUDA 12.9.1 insula and produced
  `eigen_spack_graph.lock.json` plus `eigen_build_graph.json`.
- The focused native run used the same hermetic Spack and isolated estate:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='eigen@5.0.1' \
VASO_LOCK_OUT=/workspace/experiment/eigen_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/eigen_native_build_graph.json \
VASO_NATIVE=1 \
VASO_FORCE_FETCH_REPOS='@eigen_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_eigen_native //tools:hermetic_native_deps_guard_test' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

That run passed `//synthetic:spack_selfcheck` with hermetic Spack `1.2.2`,
`//tools:hermetic_spack_guard_test`,
`//tools:hermetic_native_deps_guard_test`,
`//synthetic:use_eigen_native`, and `//synthetic:eigen_prefix_parity`. The
parity JSON reported `ok: true`, 604 layout paths, byte-identical selected
headers and CMake metadata, prefix-normalized `share/pkgconfig/eigen3.pc`,
empty shared-library ABI axis, and matching downstream C++ output
`eigen:5.0.1-dev:1.5   1   4:1`.

`libevent@2.1.12 +openssl` at py-torch topo index 64 is now native. It is a
Spack `AutotoolsPackage` and exposes the event, event_core, event_extra,
event_openssl, and event_pthreads C link surface.

- `libevent` (`native/libevent/`, `//synthetic:libevent_abi_parity`)
  reproduces Spack's Autotools install flow from the exact hermetic-Spack
  pinned `2.1.12-stable` release archive, emits the same headers, shared
  libraries, static archives, pkg-config metadata, and `event_rpcgen.py`, and
  deletes libtool archives to match the Spack prefix.
- The mechanism-specific guard reports
  `native/libevent/libevent.bzl: autotools: OPENSSL_PREFIX, PKGCONF_PREFIX, ZLIB_PREFIX`.
  The rule refuses to evaluate unless `VASO_IN_INSULA=1`, consumes
  `@openssl_native//:prefix_path.txt`, `@pkgconf_native//:prefix_path.txt`, and
  `@zlib_ng_native//:prefix_path.txt`, validates each prefix before configure,
  pins `PKG_CONFIG`, and threads dependency lookup through `PKG_CONFIG_PATH`,
  `CPPFLAGS`, and `LDFLAGS`.
- `zlib-ng` is explicit in the native provider because the hermetic Spack
  configure log found `zlib.h` and linked `-lz`; the native build therefore
  routes that discovery through the already-native zlib-ng prefix instead of a
  rootfs library.
- The focused reference run for `SPACK_ROOT_PKG='libevent@2.1.12 +openssl'`
  used Bazel's hermetic `@spack_dist//:spack` inside the CUDA 12.9.1 insula and
  produced `libevent_spack_graph.lock.json` plus `libevent_build_graph.json`.
- The focused native run used the same hermetic Spack and isolated estate:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='libevent@2.1.12 +openssl' \
VASO_LOCK_OUT=/workspace/experiment/libevent_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/libevent_native_build_graph.json \
VASO_NATIVE=1 \
VASO_FORCE_FETCH_REPOS='@libevent_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_libevent_native //tools:hermetic_native_deps_guard_test //tools:native_build_mechanism_guard_unit_test //tools:abi_parity_unit_test' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

That run passed `//synthetic:spack_selfcheck` with hermetic Spack `1.2.2`,
`//tools:hermetic_spack_guard_test`,
`//tools:hermetic_native_deps_guard_test`,
`//tools:native_build_mechanism_guard_unit_test`,
`//tools:abi_parity_unit_test`, `//synthetic:use_libevent_native`, and
`//synthetic:libevent_abi_parity`. The parity JSON reported `ok: true`, 57
layout paths, SONAME/exported-symbol parity for all five shared libraries,
static archive member/symbol parity for all five `.a` files, prefix-normalized
pkg-config parity for all five `.pc` files, executable presence/NEEDED parity
for `bin/event_rpcgen.py`, and matching downstream C output
`libevent:2.1.12-stable:pthreads`.

## Native libjpeg-turbo frontier gate

`libjpeg-turbo@3.1.3` at py-torch topo index 65 is now native. It is a
`CMakePackage` image-codec dependency with a small public C ABI surface and an
installed tool prefix.

New migration artifacts:

- `libjpeg-turbo` (`native/libjpeg_turbo/`,
  `//synthetic:libjpeg_turbo_abi_parity`)
- `docs/recipes/libjpeg-turbo.md`

The native provider mirrors the concrete hermetic Spack recipe: shared and
static libraries enabled, jpeg8 ABI mode disabled, PIC enabled, IPO disabled,
and NASM supplied through a Bazel-native prefix rather than discovered from the
rootfs. The CMake dependency verifier records:

```text
native/libjpeg_turbo/libjpeg_turbo.bzl: cmake: CMAKE_PREFIX, NASM_PREFIX
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, consumes
`@cmake_native//:prefix_path.txt` and `@nasm_native//:prefix_path.txt`,
validates `CMAKE_PREFIX/bin/cmake` and `NASM_PREFIX/bin/nasm`, and threads
dependency lookup through `CMAKE_PREFIX_PATH`, `CMAKE_PROGRAM_PATH`, and
`-DCMAKE_ASM_NASM_COMPILER`.

The focused reference and native runs for
`SPACK_ROOT_PKG='libjpeg-turbo@3.1.3'` used Bazel's hermetic
`@spack_dist//:spack` inside the CUDA 12.9.1 insula and produced
`libjpeg_turbo_spack_graph.lock.json`, `libjpeg_turbo_build_graph.json`,
`libjpeg_turbo_native_spack_graph.lock.json`, and
`libjpeg_turbo_native_build_graph.json`.

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='libjpeg-turbo@3.1.3' \
VASO_LOCK_OUT=/workspace/experiment/libjpeg_turbo_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/libjpeg_turbo_native_build_graph.json \
VASO_NATIVE=1 \
VASO_FORCE_FETCH_REPOS='@libjpeg_turbo_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_libjpeg_turbo_native' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

That run passed `//synthetic:spack_selfcheck` with hermetic Spack `1.2.2`,
`//tools:hermetic_spack_guard_test`,
`//tools:hermetic_native_deps_guard_test`,
`//tools:native_build_mechanism_guard_unit_test`,
`//synthetic:use_libjpeg_turbo_native`, and
`//synthetic:libjpeg_turbo_abi_parity`. The parity JSON reported `ok: true`,
25 layout paths, SONAME/exported-symbol parity for `libjpeg.so.62.4.0` and
`libturbojpeg.so.0.4.0`, 191 exported `libjpeg` symbols, 80 exported
`libturbojpeg` symbols, static archive parity, pkg-config/CMake metadata
parity, executable parity for the six installed tools, matching version
behavior for `cjpeg`, `djpeg`, and `jpegtran`, and matching downstream C output
`libjpeg-turbo:62:samp=7:cs=5`.

## Native libpng frontier gate

`libpng@1.6.58` at py-torch topo index 66 is now native. It is a
`CMakePackage` PNG library dependency with a shared/static `libpng16` ABI and
generated config metadata. The frontier has advanced to `freetype@2.14.2` at
topo index 67.

New migration artifacts:

- `libpng` (`native/libpng/`, `//synthetic:libpng_abi_parity`)
- `docs/recipes/libpng.md`

The native provider mirrors the concrete hermetic Spack recipe: shared and
static libraries enabled, PIC disabled, IPO disabled, and zlib-api concretized
to the already-native zlib-ng prefix rather than discovered from the rootfs.
The CMake dependency verifier records:

```text
native/libpng/libpng.bzl: cmake: CMAKE_PREFIX, ZLIB_PREFIX
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, consumes
`@cmake_native//:prefix_path.txt` and `@zlib_ng_native//:prefix_path.txt`,
validates `CMAKE_PREFIX/bin/cmake`, `ZLIB_PREFIX/include/zlib.h`, and
`ZLIB_PREFIX/lib/libz.so`, and threads dependency lookup through
`CMAKE_PREFIX_PATH`, `CMAKE_LIBRARY_PATH`, `CMAKE_INCLUDE_PATH`, `ZLIB_ROOT`,
and `ZLIB_LIBRARY`.

The focused reference and native runs for `SPACK_ROOT_PKG='libpng@1.6.58'`
used Bazel's hermetic `@spack_dist//:spack` inside the CUDA 12.9.1 insula and
produced `libpng_red_spack_graph.lock.json`, `libpng_red_build_graph.json`,
`libpng_native_spack_graph.lock.json`, and `libpng_native_build_graph.json`.

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='libpng@1.6.58' \
VASO_LOCK_OUT=/workspace/experiment/libpng_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/libpng_native_build_graph.json \
VASO_NATIVE=1 \
VASO_FORCE_FETCH_REPOS='@libpng_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_libpng_native' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

That run passed `//synthetic:spack_selfcheck` with hermetic Spack `1.2.2`,
`//tools:hermetic_spack_guard_test`,
`//tools:hermetic_native_deps_guard_test`,
`//tools:native_build_mechanism_guard_unit_test`,
`//synthetic:use_libpng_native`, and `//synthetic:libpng_abi_parity`. The
parity JSON reported `ok: true`, 22 layout paths, SONAME/exported-symbol
parity for `libpng16.so.16.58.0`, 256 exported dynamic symbols, static archive
member/symbol parity for `libpng.a` and `libpng16.a` with 391 symbols,
pkg-config/CMake metadata parity, matching `libpng-config --version` and
`libpng16-config --version` behavior, and matching downstream C output
`libpng:1.6.58:header=10658`.

## Native freetype frontier gate

`freetype@2.14.2` at py-torch topo index 67 is now native. The hermetic Spack
recipe class supports both Autotools and CMake, but the concrete reference
recipe uses Autotools because the CMake path does not install
`freetype-config`. The native provider therefore replays the Autotools
configure/make/install flow and preserves the shared/static library,
pkg-config, aclocal, and `freetype-config` prefix surface.

Files landed for this frontier:

- `native/freetype/` (`@freetype_native//:lib`)
- `synthetic/use_freetype.c`
- `//synthetic:use_freetype_native`
- `//synthetic:freetype_abi_parity`
- `docs/recipes/freetype.md`

The dependency channels are explicit. `bzip2`, `libpng`, `pkgconf`, and
`zlib-ng` are consumed through Bazel prefix files. `zlib-ng` is present even
though the focused Spack DAG's direct freetype edge is libpng, because
`libpng*.pc` carries a private zlib requirement and static freetype metadata
must not discover rootfs zlib.

The mechanism-specific dependency verifier reports:

```text
native/freetype/freetype.bzl: autotools: BZIP2_PREFIX, LIBPNG_PREFIX, PKGCONF_PREFIX, ZLIB_PREFIX
```

This is the Autotools verifier case: the rule refuses host execution, validates
the four prefixes in the shell build script, pins `PKG_CONFIG` to the native
pkgconf prefix, and threads discovery through `PKG_CONFIG_PATH`, `CPPFLAGS`,
and `LDFLAGS`.

The focused reference and native runs for `SPACK_ROOT_PKG='freetype@2.14.2'`
used Bazel's hermetic `@spack_dist//:spack` release inside the isolated CUDA
12.9.1 insula and produced `freetype_spack_graph.lock.json`,
`freetype_build_graph.json`, `freetype_native_spack_graph.lock.json`, and
`freetype_native_build_graph.json`.

The focused native verification command used a temporary one-package override
file containing `{"native":{"mkfontscale":"@mkfontscale_native//:lib"}}` so the
run exercised this new provider without sweeping unrelated native parity gates:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='freetype@2.14.2' \
VASO_LOCK_OUT=/workspace/experiment/freetype_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/freetype_native_build_graph.json \
VASO_NATIVE=1 \
VASO_FORCE_FETCH_REPOS='@freetype_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_freetype_native' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

That run reported hermetic Spack version `1.2.2` and passed
`//tools:hermetic_spack_guard_test`,
`//tools:hermetic_native_deps_guard_test`,
`//tools:native_build_mechanism_guard_unit_test`,
`//synthetic:use_freetype_native`, and `//synthetic:freetype_abi_parity`. The
smoke target printed `freetype:2.14.2`. The ABI gate passed for SONAME
`libfreetype.so.6`, 220 exported dynamic symbols, static archive parity for
`lib/libfreetype.a`, `freetype2.pc`, `freetype2.m4`, `freetype-config`
behavior, and matching downstream C output.

## Native libXfont frontier gate

`libxfont@1.5.4` at py-torch topo index 68 is now native. The hermetic Spack
recipe is an X.Org `AutotoolsPackage`, and the native provider replays the
same configure/make/install flow while preserving the legacy X11 font header
set, shared/static library surface, and `xfont.pc` metadata.

Files landed for this frontier:

- `native/libxfont/` (`@libxfont_native//:lib`)
- `synthetic/use_libxfont.c`
- `//synthetic:use_libxfont_native`
- `//synthetic:libxfont_abi_parity`
- `docs/recipes/libxfont.md`

The dependency channels are explicit. `fontsproto`, `freetype`, `libfontenc`,
`pkgconf`, `util-macros`, `xproto`, and `xtrans` are direct Spack recipe
dependencies. `bzip2`, `libpng`, and `zlib-ng` are also mandatory native
prefix inputs because the native freetype/libpng pkg-config closure and
libXfont's static link metadata carry private requirements; these must not be
resolved from the rootfs.

The mechanism-specific dependency verifier reports:

```text
native/libxfont/libxfont.bzl: autotools: BZIP2_PREFIX, FONTSPROTO_PREFIX, FREETYPE_PREFIX, LIBFONTENC_PREFIX, LIBPNG_PREFIX, PKGCONF_PREFIX, UTIL_MACROS_PREFIX, XPROTO_PREFIX, XTRANS_PREFIX, ZLIB_PREFIX
```

This is the Autotools verifier case: the rule refuses host execution, validates
all ten dependency prefixes in the shell build script, pins `PKG_CONFIG` to the
native pkgconf prefix, and threads discovery through `PKG_CONFIG_PATH`,
`ACLOCAL_PATH`, `CPPFLAGS`, `LDFLAGS`, and `LIBS`.

The focused reference and native runs for `SPACK_ROOT_PKG='libxfont@1.5.4'`
used Bazel's hermetic `@spack_dist//:spack` release inside the isolated CUDA
12.9.1 insula and produced `libxfont_spack_graph.lock.json`,
`libxfont_build_graph.json`, `libxfont_native_spack_graph.lock.json`, and
`libxfont_native_build_graph.json`.

The focused native verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='libxfont@1.5.4' \
VASO_LOCK_OUT=/workspace/experiment/libxfont_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/libxfont_native_build_graph.json \
VASO_NATIVE=1 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_libxfont_native' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

That run reported hermetic Spack version `1.2.2` and passed
`//tools:hermetic_spack_guard_test`,
`//tools:hermetic_native_deps_guard_test`,
`//tools:native_build_mechanism_guard_unit_test`,
`//synthetic:use_libxfont_native`, and `//synthetic:libxfont_abi_parity`. The
smoke target printed `libxfont:1:libxfont-native-smoke`. The ABI gate passed
for the 20-path installed layout, SONAME `libXfont.so.1`, 210 exported dynamic
symbols, static archive parity for `lib/libXfont.a`, prefix-normalized
`xfont.pc`, and matching downstream C output.

## Native bdftopcf frontier gate

`bdftopcf@1.1.2` at py-torch topo index 69 is now native. The hermetic Spack
recipe is an X.Org `AutotoolsPackage`, and the native provider replays the
same configure/make/install flow while preserving the executable-only
`bin/bdftopcf` plus manpage prefix surface.

Files landed for this frontier:

- `native/bdftopcf/` (`@bdftopcf_native//:lib`)
- `synthetic/use_bdftopcf.sh`
- `//synthetic:use_bdftopcf_native`
- `//synthetic:bdftopcf_prefix_parity`
- `docs/recipes/bdftopcf.md`

The dependency channels are explicit. `fontsproto`, `libxfont`, `pkgconf`,
`util-macros`, and `xproto` are direct Spack recipe dependencies. `bzip2`,
`freetype`, `libfontenc`, `libpng`, `xtrans`, and `zlib-ng` are also mandatory
native prefix inputs because the native libXfont/freetype/libpng pkg-config
closure carries private requirements; these must not be resolved from the
rootfs.

The mechanism-specific dependency verifier reports:

```text
native/bdftopcf/bdftopcf.bzl: autotools: BZIP2_PREFIX, FONTSPROTO_PREFIX, FREETYPE_PREFIX, LIBFONTENC_PREFIX, LIBPNG_PREFIX, LIBXFONT_PREFIX, PKGCONF_PREFIX, UTIL_MACROS_PREFIX, XPROTO_PREFIX, XTRANS_PREFIX, ZLIB_PREFIX
```

This is the Autotools verifier case: the rule refuses host execution, validates
all eleven dependency prefixes in the shell build script, pins `PKG_CONFIG` to
the native pkgconf prefix, and threads discovery through `PKG_CONFIG_PATH`,
`ACLOCAL_PATH`, `CPPFLAGS`, and `LDFLAGS`.

The focused reference and native runs for `SPACK_ROOT_PKG='bdftopcf@1.1.2'`
used Bazel's hermetic `@spack_dist//:spack` release inside the isolated CUDA
12.9.1 insula and produced `bdftopcf_spack_graph.lock.json`,
`bdftopcf_build_graph.json`, `bdftopcf_native_spack_graph.lock.json`, and
`bdftopcf_native_build_graph.json`.

The focused native verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='bdftopcf@1.1.2' \
VASO_LOCK_OUT=/workspace/experiment/bdftopcf_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/bdftopcf_native_build_graph.json \
VASO_NATIVE=1 \
VASO_FORCE_FETCH_REPOS='@bdftopcf_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_bdftopcf_native //tools:hermetic_native_deps_guard_test //tools:abi_parity_unit_test' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

That run reported hermetic Spack version `1.2.2` and passed
`//tools:hermetic_spack_guard_test`,
`//tools:hermetic_native_deps_guard_test`, `//tools:abi_parity_unit_test`,
`//synthetic:use_bdftopcf_native`, and `//synthetic:bdftopcf_prefix_parity`.
The smoke target printed `bdftopcf:1.1.2:ok`. The prefix gate passed for the
two-path executable/manpage layout, byte-identical manpage, empty
shared-library ABI axis, executable dependency parity for `bin/bdftopcf`,
matching `bdftopcf -v` behavior, and matching BDF-to-PCF output size/SHA256.

## Native lua frontier gate

`lua@5.3.6` at py-torch topo index 70 is now native. The hermetic Spack
recipe is a MakefilePackage with `+shared fetcher=curl` and a LuaRocks
resource; the native provider replays the upstream Lua Makefile build,
constructs the versioned shared `liblua` surface Spack emits, installs
LuaRocks, and preserves the Lua CLI, C ABI, pkg-config, and LuaRocks prefix
contract.

Files landed for this frontier:

- `native/lua/` (`@lua_native//:lib`)
- `synthetic/use_lua.c`
- `synthetic/use_lua.sh`
- `//synthetic:use_lua_native`
- `//synthetic:use_lua_prefix_native`
- `//synthetic:lua_abi_parity`
- `docs/recipes/lua.md`

The dependency channels are explicit. `ncurses` and `readline` feed Lua's
Makefile compile/link interface and the public C link contract. `curl` and
`unzip` are LuaRocks runtime/tool-prefix requirements from the Spack recipe.
All four prefixes are mandatory Bazel `prefix_path.txt` inputs and are
validated in the shell build script before invoking upstream make or
LuaRocks' configure script.

The mechanism-specific dependency verifier reports:

```text
native/lua/lua.bzl: makefile: CURL_PREFIX, NCURSES_PREFIX, READLINE_PREFIX, UNZIP_PREFIX
```

This is the Makefile verifier case: the rule refuses host execution, validates
all dependency prefixes in the shell build script, puts native `curl` and
`unzip` at the front of `PATH`, and threads `readline`/`ncurses` through
Makefile `MYCFLAGS`, `MYLDFLAGS`, and `MYLIBS` variables rather than ambient
rootfs discovery.

The focused reference and native runs for `SPACK_ROOT_PKG='lua@5.3.6'` used
Bazel's hermetic `@spack_dist//:spack` release inside the isolated CUDA 12.9.1
insula and produced `lua_spack_graph.lock.json`, `lua_build_graph.json`,
`lua_native_spack_graph.lock.json`, and `lua_native_build_graph.json`.

The focused native verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='lua@5.3.6' \
VASO_LOCK_OUT=/workspace/experiment/lua_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/lua_native_build_graph.json \
VASO_NATIVE=1 \
VASO_FORCE_FETCH_REPOS='@lua_native' \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_lua_native //synthetic:use_lua_prefix_native //synthetic:lua_abi_parity //tools:hermetic_native_deps_guard_test //tools:native_build_mechanism_guard_unit_test //tools:abi_parity_unit_test' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

That run reported hermetic Spack version `1.2.2` and passed
`//tools:hermetic_spack_guard_test`,
`//tools:hermetic_native_deps_guard_test`,
`//tools:native_build_mechanism_guard_unit_test`,
`//tools:abi_parity_unit_test`, `//synthetic:use_lua_native`,
`//synthetic:use_lua_prefix_native`, and `//synthetic:lua_abi_parity`. The
prefix smoke target printed `lua:5.3.6:luarocks:3.11.1:ok`. The ABI gate
passed for the 20-path installed layout, SONAME `liblua.so.5.3`, 147 exported
dynamic symbols, static archive parity for `lib/liblua.a`,
prefix-normalized pkg-config and LuaRocks config data, executable dependency
parity for `bin/lua` and `bin/luac`, matching Lua/LuaRocks version behavior,
and matching downstream C output.

## Native mkfontscale frontier gate

`mkfontscale@1.2.3` at py-torch topo index 71 is now native. The hermetic
Spack recipe is an X.Org `AutotoolsPackage`, and the native provider replays
the same configure/make/install flow while preserving the executable-only
`mkfontscale`/`mkfontdir` plus manpage prefix surface.

Files landed for this frontier:

- `native/mkfontscale/` (`@mkfontscale_native//:lib`)
- `synthetic/use_mkfontscale.sh`
- `//synthetic:use_mkfontscale_native`
- `//synthetic:mkfontscale_prefix_parity`
- `docs/recipes/mkfontscale.md`

The dependency channels are explicit. `freetype`, `libfontenc`, `pkgconf`,
`util-macros`, and `xproto` are direct Spack recipe dependencies. `bzip2`,
`libpng`, and `zlib-ng` are also mandatory native prefix inputs because the
native freetype/libpng/pkg-config closure carries private requirements; these
must not be resolved from the rootfs.

The mechanism-specific dependency verifier reports:

```text
native/mkfontscale/mkfontscale.bzl: autotools: BZIP2_PREFIX, FREETYPE_PREFIX, LIBFONTENC_PREFIX, LIBPNG_PREFIX, PKGCONF_PREFIX, UTIL_MACROS_PREFIX, XPROTO_PREFIX, ZLIB_PREFIX
```

This is the Autotools verifier case: the rule refuses host execution,
validates all eight dependency prefixes in the shell build script, pins
`PKG_CONFIG` to the native pkgconf prefix, and threads dependency lookup
through `PKG_CONFIG_PATH`, `ACLOCAL_PATH`, `CPPFLAGS`, `LDFLAGS`, and `LIBS`.

The focused reference and native runs for `SPACK_ROOT_PKG='mkfontscale@1.2.3'`
used Bazel's hermetic `@spack_dist//:spack` release inside the isolated CUDA
12.9.1 insula and produced `mkfontscale_spack_graph.lock.json`,
`mkfontscale_build_graph.json`, `mkfontscale_native_spack_graph.lock.json`, and
`mkfontscale_native_build_graph.json`.

The focused native verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='mkfontscale@1.2.3' \
VASO_LOCK_OUT=/workspace/experiment/mkfontscale_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/mkfontscale_native_build_graph.json \
VASO_NATIVE=1 \
VASO_NATIVE_OVERRIDES=.tmp_mkfontscale_native_overrides.json \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_mkfontscale_native //tools:hermetic_native_deps_guard_test //tools:abi_parity_unit_test' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

That run reported hermetic Spack version `1.2.2` and passed
`//tools:hermetic_spack_guard_test`,
`//tools:hermetic_native_deps_guard_test`, `//tools:abi_parity_unit_test`,
`//synthetic:use_mkfontscale_native`, and
`//synthetic:mkfontscale_prefix_parity`. The smoke target printed
`mkfontscale:1.2.3:ok`. The prefix gate passed for the four-path installed
layout, prefix-normalized `bin/mkfontdir`, exact manpage hashes, empty
shared-library ABI axis, executable dependency parity for `bin/mkfontscale`,
matching `mkfontscale -v` behavior, and matching empty-directory
`fonts.scale`/`fonts.dir` output SHA256.

## Native mkfontdir frontier gate

`mkfontdir@1.0.7` at py-torch topo index 72 is now native. The hermetic Spack
recipe is an X.Org `AutotoolsPackage` whose installed payload is a wrapper
script plus a manpage. The wrapper delegates to `mkfontscale`, so the native
provider preserves the Spack topology edge to `spack_mkfontscale` and the
parity gate supplies the matching reference/candidate mkfontscale prefixes at
runtime.

Files landed for this frontier:

- `native/mkfontdir/` (`@mkfontdir_native//:lib`)
- `synthetic/use_mkfontdir.sh`
- `//synthetic:use_mkfontdir_native`
- `//synthetic:mkfontdir_prefix_parity`
- `docs/recipes/mkfontdir.md`

The dependency channels are explicit. `mkfontscale` is a runtime tool prefix;
`pkgconf` and `util-macros` drive the X.Org Autotools configure surface. All
three prefixes are mandatory Bazel `prefix_path.txt` inputs and are validated
in the shell build script before configure. The native rule pins
`PKG_CONFIG`, puts native `pkgconf` and `mkfontscale` on `PATH`, and threads
macro/pkg-config lookup through `PKG_CONFIG_PATH` and `ACLOCAL_PATH`.

The mechanism-specific dependency verifier reports:

```text
native/mkfontdir/mkfontdir.bzl: autotools: MKFONTSCALE_PREFIX, PKGCONF_PREFIX, UTIL_MACROS_PREFIX
```

The focused reference and native runs for `SPACK_ROOT_PKG='mkfontdir@1.0.7'`
used Bazel's hermetic `@spack_dist//:spack` release inside the isolated CUDA
12.9.1 insula and produced `mkfontdir_spack_graph.lock.json`,
`mkfontdir_build_graph.json`, `mkfontdir_native_spack_graph.lock.json`, and
`mkfontdir_native_build_graph.json`.

The focused native verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='mkfontdir@1.0.7' \
VASO_LOCK_OUT=/workspace/experiment/mkfontdir_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/mkfontdir_native_build_graph.json \
VASO_NATIVE=1 \
VASO_NATIVE_OVERRIDES=.tmp_mkfontdir_native_overrides.json \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_FORCE_FETCH_REPOS='@mkfontdir_native' \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_mkfontdir_native //tools:hermetic_native_deps_guard_test //tools:native_build_mechanism_guard_unit_test //tools:abi_parity_unit_test' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

That run reported hermetic Spack version `1.2.2` and passed
`//tools:hermetic_spack_guard_test`,
`//tools:hermetic_native_deps_guard_test`,
`//tools:native_build_mechanism_guard_unit_test`,
`//tools:abi_parity_unit_test`,
`//synthetic:use_mkfontdir_native`, and `//synthetic:mkfontdir_prefix_parity`.
The smoke target printed `mkfontdir:1.0.7:ok`. The prefix gate passed for the
two-path wrapper/manpage layout, prefix-normalized wrapper content, exact
manpage hash, empty shared-library ABI axis, matching `mkfontdir -v` output
through `mkfontscale 1.2.3`, and matching empty-directory `fonts.dir`
size/SHA256.

## Native font-util lean frontier gate

`font-util@1.4.1` is native for the lean X.Org font-resource contract. The
contract is a Spack topology decision: focused and PyTorch frontier graphs use
`font-util@1.4.1 fonts:=encodings`, so Spack replaces the default font set and
concretizes only the `encodings` resource. Native `font-util` matches that
reference; it does not install the broader default X.Org font resource payload.

Files landed for this frontier:

- `native/font_util/` (`@font_util_native//:lib`)
- `synthetic/use_font_util.sh`
- `//synthetic:use_font_util_native`
- `//synthetic:font_util_prefix_parity`
- `docs/recipes/font-util.md`

The native prefix's `share/fonts/X11` top-level directories are exactly
`encodings` and `util`: `util` comes from `font-util` itself, and `encodings`
comes from the single selected `encodings-1.0.4` resource. The PyTorch graph
probe with
`SPACK_ROOT_PKG='py-torch cuda_arch=80,90,100 ^openblas~fortran ^font-util fonts:=encodings'`
is now the canonical `py_torch_build_graph.json` capture, with 168 nodes and
`parameters.fonts = ["encodings"]` for `font-util`.

`run.sh` now protects that choice for future graph captures. Direct
`font-util` roots get `fonts:=encodings` appended when no font-resource
variant is present, and `py-torch` roots get `^font-util fonts:=encodings`
appended. Additive `fonts=...` specs, broad replacement
`fonts:=encodings,...` specs, and `VASO_LEAN_FONT_RESOURCES=0` are refused as
silent graph-widening paths unless `VASO_ALLOW_BROAD_FONT_RESOURCES=1` is set
for an intentional broad-font probe.

The dependency channels are explicit. Autoconf and Automake drive the resource
`autoreconf` phase; `bdftopcf`, `mkfontdir`, and `mkfontscale` are the X.Org
font tools used while installing the selected resource; `pkgconf` and
`util-macros` drive Autotools discovery. All seven prefixes are mandatory Bazel
`prefix_path.txt` inputs and are validated before configure.

The mechanism-specific dependency verifier reports:

```text
native/font_util/font_util.bzl: autotools: AUTOCONF_PREFIX, AUTOMAKE_PREFIX, BDFTOPCF_PREFIX, MKFONTDIR_PREFIX, MKFONTSCALE_PREFIX, PKGCONF_PREFIX, UTIL_MACROS_PREFIX
```

The focused reference and native runs for
`SPACK_ROOT_PKG='font-util@1.4.1 fonts:=encodings'` used Bazel's hermetic
`@spack_dist//:spack` release inside the isolated CUDA 12.9.1 insula and
produced `font_util_lean_spack_graph.lock.json`,
`font_util_lean_build_graph.json`, `font_util_lean_native_spack_graph.lock.json`,
and `font_util_lean_native_build_graph.json`.

The focused native verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='font-util@1.4.1 fonts:=encodings' \
VASO_LOCK_OUT=/workspace/experiment/font_util_lean_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/font_util_lean_native_build_graph.json \
VASO_NATIVE=1 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_font_util_native //synthetic:font_util_prefix_parity //tools:hermetic_native_deps_guard_test //tools:native_build_mechanism_guard_unit_test //tools:abi_parity_unit_test' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

That run reported hermetic Spack version `1.2.2` and passed
`//tools:hermetic_spack_guard_test`,
`//tools:hermetic_native_deps_guard_test`,
`//tools:native_build_mechanism_guard_unit_test`,
`//tools:abi_parity_unit_test`, `//synthetic:use_font_util_native`, and
`//synthetic:font_util_prefix_parity`. The smoke target printed
`font-util:1.4.1:fonts=encodings:ok`. The prefix gate passed for the selected
tool/data layout, prefix-normalized `fontutil.pc`, exact selected encoding-map
hashes, prefix-normalized generated `ucs2any.1`, empty shared-library ABI axis,
executable dependency parity for `bdftruncate` and `ucs2any`, matching
`bdftruncate 0x3200` behavior, and matching `ucs2any` usage output with return
code `0`.

## Native perl-data-dumper frontier gate

`perl-data-dumper@2.173` is now native for the lean PyTorch frontier. It is the
first CPAN/PerlPackage node after native `font-util`.

This does not widen the font payload. The PyTorch frontier remains constrained
with:

```bash
SPACK_ROOT_PKG='py-torch cuda_arch=80,90,100 ^openblas~fortran ^font-util fonts:=encodings'
```

That `fonts:=encodings` replacement keeps only the `encodings` resource for
`font-util`; the native `perl-data-dumper` provider consumes only the native
Perl prefix.

New migration artifacts:

- `native/perl_data_dumper/` (`@perl_data_dumper_native//:lib`)
- `synthetic/use_perl_data_dumper.sh`
- `//synthetic:use_perl_data_dumper_native`
- `//synthetic:perl_data_dumper_abi_parity`
- `docs/recipes/perl-data-dumper.md`

The native provider mirrors the concrete hermetic Spack ExtUtils::MakeMaker
flow inside the CUDA insula:

```text
$PERL_PREFIX/bin/perl Makefile.PL INSTALL_BASE=$PREFIX
make
make install
```

The build-mechanism verifier records the new Perl channel:

```text
native/perl_data_dumper/perl_data_dumper.bzl: perl: PERL_PREFIX
```

The focused reference and native runs for
`SPACK_ROOT_PKG='perl-data-dumper@2.173'` used Bazel's hermetic
`@spack_dist//:spack` release inside the isolated CUDA 12.9.1 insula and
produced `perl_data_dumper_spack_graph.lock.json`,
`perl_data_dumper_build_graph.json`,
`perl_data_dumper_native_spack_graph.lock.json`, and
`perl_data_dumper_native_build_graph.json`.

The focused native verification command was:

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

That run reported hermetic Spack version `1.2.2`, generated a 17-node focused
graph, and passed `//synthetic:spack_selfcheck`,
`//tools:hermetic_spack_guard_test`,
`//tools:hermetic_native_deps_guard_test`,
`//tools:native_build_mechanism_guard_unit_test`,
`//tools:abi_parity_unit_test`, `//synthetic:use_perl_data_dumper_native`, and
`//synthetic:perl_data_dumper_abi_parity`. The smoke target printed
`perl-data-dumper:2.173:ok`. The parity gate compared the native prefix against
the hermetic Spack reference prefix for `Dumper.pm` and `Dumper.so`, including
SONAME/null parity, exported-symbol parity, and matching downstream Perl smoke
behavior.

## Native flex frontier gate

`flex@2.6.3` is native for the PyTorch frontier after native `bison` and
`findutils`. The focused graph keeps the concrete Spack variants `~nls +lex`;
the native rule reproduces that as:

```text
../configure --prefix=<prefix> --disable-nls
make V=1
make install
```

Files landed for this frontier:

- `native/flex/` (`@flex_native//:lib`)
- `synthetic/use_flex.sh`
- `//synthetic:use_flex_native`
- `//synthetic:flex_prefix_parity`
- `docs/recipes/flex.md`

All recipe evidence came from Bazel's vendored Spack `v1.2.2` inside the CUDA
insula. The native repository rule refuses to evaluate without
`VASO_IN_INSULA=1`, downloads the same `flex-2.6.3` source archive by the Spack
SHA256, and consumes its Autotools build inputs only through mandatory Bazel
prefix files:

```text
BISON_PREFIX
DIFFUTILS_PREFIX
FINDUTILS_PREFIX
M4_PREFIX
```

The mechanism verifier records:

```text
native/flex/flex.bzl: autotools: BISON_PREFIX, DIFFUTILS_PREFIX, FINDUTILS_PREFIX, M4_PREFIX
```

The focused native verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='flex@2.6.3' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_GRAPH_ONLY=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_FORCE_FETCH_REPOS='@flex_native' \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_flex_native //synthetic:flex_prefix_parity //tools:hermetic_native_deps_guard_test //tools:native_build_mechanism_guard_unit_test //tools:abi_parity_unit_test //tools:lean_font_resources_test' \
VASO_LOCK_OUT=/workspace/experiment/spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/flex_native_build_graph.json \
VASO_SPACK_TIMEOUT=1200 \
./run.sh
```

That run used the CUDA insula (`rootfs mode: cuda-bundle`) and Bazel-owned
Spack only. It passed `//synthetic:use_flex_native`,
`//synthetic:flex_prefix_parity`, `//tools:hermetic_native_deps_guard_test`,
`//tools:native_build_mechanism_guard_unit_test`,
`//tools:abi_parity_unit_test`, and `//tools:lean_font_resources_test`. The
smoke target printed `flex:2.6.3:ok`; the parity gate matched the Spack
reference for executable layout/dependencies, `libfl` SONAME and exported
symbols, static archive member/symbol parity for `libfl.a` and `libl.a`,
version behavior for `flex` and `lex`, and deterministic scanner generation.

The lean font guard is now strict in default mode: direct `font-util` roots get
`fonts:=encodings`, `py-torch` roots get `^font-util fonts:=encodings`, and
both additive `fonts=...` and broad replacement `fonts:=encodings,...` specs
are refused unless `VASO_ALLOW_BROAD_FONT_RESOURCES=1` is set for an explicit
probe.

## Native krb5 frontier gate

`krb5@1.22.2` is native for the PyTorch frontier after native `flex`. The
PyTorch graph remains the lean-font capture:

```bash
SPACK_ROOT_PKG='py-torch cuda_arch=80,90,100 ^openblas~fortran ^font-util fonts:=encodings'
```

This frontier does not add font resources. The X.Org font payload remains
native `font-util@1.4.1` plus only the `encodings` resource selected by the
replacement `fonts:=encodings` constraint.

Files landed for this frontier:

- `native/krb5/` (`@krb5_native//:lib`)
- `synthetic/use_krb5.c`
- `//synthetic:use_krb5_native`
- `//synthetic:krb5_abi_parity`
- `docs/recipes/krb5.md`

All recipe evidence came from Bazel's vendored Spack `v1.2.2` inside the CUDA
insula. The native repository rule refuses to evaluate without
`VASO_IN_INSULA=1`, downloads the same `krb5-1.22.2` source archive by the
Spack SHA256, applies Spack's configure-script patch, and builds from krb5's
nested `src` Autotools directory with the Spack recipe arguments:

```text
--without-system-verto --without-keyutils --disable-static CFLAGS=-fcommon
```

The native rule consumes every non-toolchain dependency through mandatory Bazel
prefix files and validates those channels before configure:

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

The mechanism verifier records:

```text
native/krb5/krb5.bzl: autotools: BISON_PREFIX, DIFFUTILS_PREFIX, FINDUTILS_PREFIX, GETTEXT_PREFIX, LIBEDIT_PREFIX, NCURSES_PREFIX, OPENSSL_PREFIX, PERL_PREFIX, PKGCONF_PREFIX
```

Ruling: do not pass OpenSSL, libedit, or ncurses through a global `LIBS`
environment override. MIT krb5 writes global `LIBS` into `bin/krb5-config`;
Spack's generated script has only `LIBS='-lresolv '`. The native rule instead
uses include/library search paths and pkg-config channels so `krb5-config`
remains prefix-normalized identical to the hermetic Spack reference.

The focused native verification command was:

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

That run used the CUDA insula (`rootfs mode: cuda-bundle`) and Bazel-owned
Spack only. It passed `//synthetic:use_krb5_native`,
`//synthetic:krb5_abi_parity`, `//tools:hermetic_native_deps_guard_test`,
`//tools:native_build_mechanism_guard_unit_test`, and
`//tools:abi_parity_unit_test`. The smoke target printed
`krb5:1.22.2:gssapi-ok`; the ABI gate matched the Spack reference for 98-file
layout, all shared-library and plugin SONAME/exported-symbol surfaces,
prefix-normalized `bin/krb5-config`, prefix-normalized pkg-config metadata,
executable dependency parity for `krb5-config` and `klist`, matching
`krb5-config --version`, and downstream C link-and-run behavior.

## Native PMIx frontier gate

`pmix@6.1.0` is native for the PyTorch frontier after native `git`. In the
lean-font PyTorch graph it is topo index 86; in the focused
`SPACK_ROOT_PKG='pmix@6.1.0'` graph it is topo index 34.

Files landed for this frontier:

- `native/pmix/` (`@pmix_native//:lib`)
- `synthetic/use_pmix.c`
- `//synthetic:use_pmix`
- `//synthetic:use_pmix_native`
- `//synthetic:pmix_abi_parity`
- `docs/recipes/pmix.md`

All recipe evidence came from Bazel's vendored Spack `v1.2.2` inside the CUDA
insula. The native rule builds the same release tarball with PMIx's Autotools
path and Spack's concrete arguments:

```text
--enable-shared
--enable-static
--with-zlib=<zlib-ng-prefix>
--with-libevent=<libevent-prefix>
--with-hwloc=<hwloc-prefix>
--disable-python-bindings
--without-munge
```

The rule consumes every non-toolchain build input from mandatory Bazel-native
prefix files and validates them before configure:

```text
HWLOC_PREFIX
LIBEVENT_PREFIX
LIBICONV_PREFIX
LIBPCIACCESS_PREFIX
LIBTOOL_PREFIX
LIBXML2_PREFIX
PKGCONF_PREFIX
XZ_PREFIX
ZLIB_PREFIX
```

The extra `libiconv`, `libpciaccess`, `libxml2`, and `xz` prefixes are not
direct PMIx link edges; they are hwloc's private pkg-config closure. PMIx's
installed `pmix.pc` is part of the ABI contract for static consumers, so the
native rule rewrites only its `Libs.private`, `Cflags`, and
`Requires.private` lines from declared Bazel prefixes to match Spack's emitted
metadata after prefix normalization.

The focused native verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='pmix@6.1.0' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_FORCE_FETCH_REPOS='@pmix_native' \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_pmix //synthetic:use_pmix_native //synthetic:pmix_abi_parity //tools:hermetic_native_deps_guard_test //tools:native_build_mechanism_guard_unit_test //tools:abi_parity_unit_test' \
VASO_SPACK_TIMEOUT=900 \
./run.sh
```

That run used the CUDA insula (`rootfs mode: cuda-bundle`) and Bazel-owned
Spack only. It passed `//synthetic:use_pmix`, `//synthetic:use_pmix_native`,
`//synthetic:pmix_abi_parity`, `//tools:hermetic_native_deps_guard_test`,
`//tools:native_build_mechanism_guard_unit_test`, and
`//tools:abi_parity_unit_test`. The ABI verdict matched 128 ABI-relevant paths,
SONAME `libpmix.so.2`, 1311 exported symbols, prefix-normalized `pmix.pc`,
downstream `PMIx_Get_version()` output, `bin/pmix_info` dependency parity, and
`pmix_info --version` output.

## Native PyTorch (Task 4 — design + gated skeleton)

PyTorch is the first *non-trivial* migration and the **forcing function** to
build + seal the CUDA/Ubuntu≥24.04 rootfs bundle as the insula base root
(`rootfs/build_rootfs.sh`): a native torch build needs CUDA, cuDNN, NCCL, a
C++ toolchain, and Python all hermetic inside the insula.

### Hermetic recipe provenance boundary

All PyTorch-frontier recipe evidence comes from Bazel's vendored Spack tool,
not from a host `spack` checkout. `//tools:pytorch_recipe_provenance_test`
runs inside the insula, invokes `@spack_dist//:spack`, and scans the package
repository that this hermetic Spack materializes under
`/vaso/cache/spack/user/package_repos/.../builtin/packages`.

Current checked evidence for the pinned Spack release (`@spack_dist` reports
`1.2.2`):

- Present and inspected: `py-torch`, `py-triton`, `py-jax`, `py-jaxlib`.
- `py-torch` is the upstream Spack Python/CUDA/ROCm package class `PyTorch`;
  the live verifier asserts the `python` and `cuda` build-system surfaces and
  the current `2.12.0` recipe version are present.
- `py-triton` is present as the upstream Spack Python package `PyTriton`.
- `py-jax` and `py-jaxlib` are present; `py-jaxlib` carries the upstream
  Bazel/XLA-oriented build dependency surface.
- Absent by construction in this hermetic Spack universe:
  `vendor-libtorch` and `libtorch`.

That absence is intentional provenance, not a blocker for the skeleton. The
native PyTorch rule below treats upstream Spack `py-torch` as the available
reference recipe and keeps the vendor-specific `vendor-libtorch` ABI/install
interface as an explicitly encoded compatibility contract. If a downstream or
internal Spack repo is later added to the Bazel-vendored Spack configuration,
this verifier is the place that must be tightened to require and inspect that
recipe before allowing the PyTorch flip to advance.

### Interface, encoded from upstream PyTorch plus `vendor-libtorch/package.py`

PyTorch `v2.14.0` has moved wheel builds to the PEP 517/scikit-build-core
front-end declared in `pyproject.toml`; its `setup.py` rejects `bdist_wheel`
and says the wheel replacement is `python -m build --wheel --no-isolation`.
The native build therefore uses that upstream entrypoint while preserving the
an internal `vendor-libtorch` Spack package's environment and install-layout
contract exactly:

For PyTorch `v2.14.0`, upstream's vendored `third_party/protobuf` submodule is
commit `f0dc78d7e6e331b8c6bb2d5283e06aa26883ca7c`, tagged by protobuf as
`v3.21.12` and reporting C++ API/package version `3.21.12`. The matching PyPI
Python package line is `protobuf==4.21.12`. The hermetic Spack `py-torch`
recipe is built with `~custom-protobuf`, so the native skeleton uses the
system-prefix path: `BUILD_CUSTOM_PROTOBUF=OFF`,
`PROTOBUF_PROTOC_EXECUTABLE=<protobuf>/bin/protoc`, hermetic Spack
`protobuf@21.12` (upstream/protoc API `3.21.12`), and `py-protobuf@4.21.12` as
one ODR-sensitive family. Upstream PyTorch `v2.14.0` has no direct gRPC or
Abseil build input in this path; ONNX `v1.18.0` has an Abseil/protobuf fallback
(`abseil-cpp 20240722.1` plus protobuf `29.2`) that must not be mixed into the
top-level PyTorch protobuf provider family. Boost is present as `boost@1.90.0`
through the hermetic Spack closure, not as an upstream PyTorch build-system
requirement. Only the C++ protobuf flip is active today; the
`py-protobuf@4.21.12` native provider remains blocked until the Python side
uses a Spack-supported Python version or a compatible implementation mode.
The source/build-file evidence is recorded in
`docs/pytorch-odr-dependencies.md`.

Consumed **Spack prefixes** (unmigrated deps, wired as environment):

| Env var | Source (Spack prefix) |
|---|---|
| `CUDA_HOME` / `CUDA_TOOLKIT_ROOT_DIR` / `CUDA_PATH` | `cuda` |
| `CUDNN_ROOT` / `CUDNN_INCLUDE_DIR` / `CUDNN_LIBRARY` | `cudnn` |
| `NCCL_ROOT` / `NCCL_INCLUDE_DIR` / `NCCL_LIB_DIR` | `nccl` |
| `CMAKE` / `CMAKE_MAKE_PROGRAM` (ninja) | `cmake`, `ninja` |
| `PROTOBUF_PROTOC_EXECUTABLE` | `protobuf` |
| `CMAKE_PREFIX_PATH` | `python`, `cmake`, `protobuf` |
| C/C++ compiler (`CC`/`CXX`), OpenBLAS, gcc runtime | toolchain / `openblas` |

`gRPC`, `Abseil`, and Boost are deliberately not accepted as PyTorch-native
prefix inputs for this selected source path. Passing any of those as build
prefixes fails preflight rather than expanding `CMAKE_PREFIX_PATH` and risking
a second ODR-sensitive provider family in the process.

Build **feature flags** (from `internal/build_environment.py` +
`vendor-libtorch` `build()`):

```
USE_CUDA=1 USE_CUDNN=1
USE_NCCL=1 USE_DISTRIBUTED=1 USE_SYSTEM_NCCL=1 USE_STATIC_NCCL=0
USE_MKL=0 USE_MKLDNN=0
USE_MPI=0 USE_NUMA=0 USE_TENSORPIPE=0 USE_RPC=0 USE_TENSORRT=0 USE_XPU=0
USE_GFLAGS=0 USE_GLOG=0
BUILD_CUSTOM_PROTOBUF=OFF
TH_BINARY_BUILD=1
ATEN_STATIC_CUDA=0 USE_CUDA_STATIC_LINK=0 USE_STATIC_CUDNN=0
_GLIBCXX_USE_CXX11_ABI=1
COLORIZE_OUTPUT=0
TORCH_CUDA_ARCH_LIST="8.0;9.0;10.0"     # cuda_arch 80,90,100
BUILD_TEST=0 BUILD_BINARY=0 BUILD_CAFFE2_OPS=0 USE_STATIC_DISPATCH=0
PYTORCH_BUILD_VERSION=<mapped>  PYTORCH_BUILD_NUMBER=1
```

Warning demotion (append to `CFLAGS`/`CXXFLAGS`):
`-Wno-error=maybe-uninitialized -Wno-error=uninitialized`.

### Emitted prefix (matches vendor-libtorch install layout)

```
prefix/
  artifacts/wheels/torch-*.whl          # the built wheel
  lib/site-packages/torch/lib/          # pip --no-deps --prefix install payload
  lib/site-packages/torch/include/
```

i.e. `wheel → prefix/artifacts/wheels`, then `pip install --no-deps --prefix`
so the torch package (with `torch/{lib,include}`) lands under `site-packages`.
This is what an unmigrated C++/python consumer `depends_on`s, so the ABI gate's
python-package layout branch checks `artifacts/wheels/*.whl` +
`site-packages/torch/{lib,include}`.

### Skeleton behavior (this task) — plan/preflight only, no build

`native/pytorch/pytorch_native.bzl` provides `pytorch_native`, a repository
rule mirroring `zlib_ng_native`'s shape but **gated**: it *plans* the build —
resolving every input prefix, computing the full env from the interface above,
and writing a `build_plan.json` — and only executes `python -m build --wheel`
when the explicit token is present:

- `native/pytorch/plan.py` builds the env dict + preflight checks and emits the
  plan JSON. The checked dry-run interface validates every required prefix plus
  the concrete build surfaces it will hand to `setup.py`: `cuda/bin/nvcc`,
  `cudnn.h` plus a cuDNN libdir, `nccl.h` plus an NCCL libdir,
  `python/bin/python3`, `cmake/bin/cmake`, `ninja/bin/ninja`, OpenBLAS headers
  and a libdir, `protoc --version == libprotoc 3.21.12`, and the sealed
  CUDA-bundle rootfs. It runs as a **dry-run** by default so the input
  interface can be validated without a full build.
- `//native/pytorch:plan_test` exercises that dry-run contract: complete inputs
  produce the exact vendor-libtorch environment without building, missing tool or
  BLAS surfaces fail preflight loudly, and `--execute` without the trusted token
  is refused even when preflight passes.
- `//tools:pytorch_python_protobuf_compat_test` records the coherent Python,
  protobuf, PyTorch, and vendored-ONNX island: Python `3.13.x`,
  C++ protobuf `3.21.12`, Python protobuf `4.21.12`, PyTorch `2.14.0`, and
  vendored ONNX `v1.18.0` on external protobuf `3.21.12`. It rejects the known
  Python `3.14`/`py-protobuf@4.21.12` source-build failure, any protobuf family
  drift, ONNX external protobuf `>=4.22.0` because that requires Abseil and
  `utf8_range`, ONNX's protobuf `29.2`/Abseil `20240722.1` fallback, and
  standalone ONNX Python packaging because ONNX `v1.18.0` asks for
  `protobuf>=4.25.1`.
- The full build is authorized **only** when the trusted request carries the
  token `build-native-pytorch` (env `VASO_NATIVE_PYTORCH_TOKEN=build-native-pytorch`).
  Absent the token, the rule stops cleanly after writing the plan and emits an
  `alias` back to the Spack provider so the graph still resolves.
- `//tools:pytorch_recipe_provenance_test` verifies the recipe provenance
  boundary above before the Python/protobuf compatibility guard and
  `//native/pytorch:plan_test` validate the dry-run build interface.

This mirrors the torchtitan B200 discipline: high-cost GPU/CUDA launches
proceed only on an explicit trusted token, never on design-doc text alone.

### Sequencing to the big three

> **Superseded (2026-09-30).** The execution order, the gates and the
> amended invariants now live in `.scratch/execution-plan/spec.md`. This
> section is kept as history.

1. **PyTorch** — this design; forces the CUDA rootfs seal. Gate:
   `abi_parity.py` python-package branch vs the Spack `vendor-libtorch` prefix
   (wheel + `torch/{lib,include}` identical, and `import torch; torch.cuda` +
   a small kernel produce identical output).
2. **Triton** — consumes the migrated torch; native build of `py-triton`.
3. **JAX** — most naturally native (upstream builds under Bazel/XLA already);
   likely re-exports upstream XLA/`jaxlib` Bazel targets rather than reusing a
   Spack recipe.

At each step the DAG topology is untouched; only the flipped node's provider
changes, and the ABI gate guards the flip.

Phases 2–3 (Triton, JAX) and the decisions already shaping them (the PyTorch
closure is pruned with `^py-networkx~default`, LLVM moves to the Triton phase
at Triton's own pin, scipy returns with JAX) are tracked in
`docs/rfcs/triton-jax-roadmap.md`. Both phases will be specced later.

## gobject-introspection native recipe capture

The next buildable `py-torch` frontier node after gated `llvm@20.1.8` is
`gobject-introspection@1.86.0`. LLVM remains unflipped until an explicit
`build-native-llvm` authorization and its full ABI gates land, so this
checkpoint captures `gobject-introspection` as a native Meson repository rule
without adding it to `native_overrides.json`.

Recipe facts from Bazel's vendored hermetic Spack:

- source:
  `https://download.gnome.org/sources/gobject-introspection/1.86/gobject-introspection-1.86.0.tar.xz`
- SHA256:
  `920d1a3fcedeadc32acff95c2e203b319039dd4b4a08dd1a2dfd283d19c0b9ae`
- active build system: Meson
- direct prefixes: `bison`, `flex`, `glib-bootstrap`, `libffi`, `meson`,
  `ninja`, `pkgconf`, `py-setuptools`, and `python`
- GLib `.pc` closure prefixes passed explicitly for hermetic downstream
  pkg-config resolution: `libiconv`, `pcre2`, and `zlib-ng`
- Spack patch carried into the native rule: `setuptools.patch`
- build environment: `GI_SCANNER_DISABLE_CACHE=1`
- dependent environment contract:
  `GI_TYPELIB_PATH=<prefix>/lib/girepository-1.0` and
  `XDG_DATA_DIRS=<prefix>/share`

`native/gobject_introspection/gobject_introspection.bzl` follows the existing
Meson verifier contract: it refuses evaluation outside `VASO_IN_INSULA=1`, reads
all dependency prefixes from Bazel-owned `prefix_path.txt` files, pins
`PKG_CONFIG`, exposes Meson's Python package path, invokes Meson/Ninja through
their native prefixes, and passes Spack-standard Meson arguments including
`-Dwrap_mode=nodownload`.

The durable per-package notes live in `docs/recipes/gobject-introspection.md`.
