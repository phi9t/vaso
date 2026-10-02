# cusparselt@0.8.1-cuda120

## Position in the hillclimb

`cusparselt` is the py-torch frontier node immediately after native CMake. In
the captured `SPACK_ROOT_PKG=py-torch` graph it appears as:

```text
62  cusparselt  0.8.1-cuda120  generic
```

The focused reference graph used for this migration is:

```bash
SPACK_ROOT_PKG='cusparselt@0.8.1-cuda120 ^cuda@12.9.1'
```

The reference run used Bazel's vendored `@spack_dist//:spack` inside the
isolated CUDA 12.9.1 insula and wrote `cusparselt_spack_graph.lock.json` plus
`cusparselt_build_graph.json`. The reference lock keeps `spack_cusparselt` as
`build: "spack"`, with a single link dependency on `spack_cuda`.

## Hermetic Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path in the isolated estate:

```text
/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/cusparselt/package.py
```

Source provenance from the Spack recipe:

- package class: `Cusparselt(Package)`
- Spack build-system bucket: `generic`
- concrete Linux install mechanism: NVIDIA binary archive, copied with
  `install_tree("lib", prefix.lib)` and `install_tree("include", prefix.include)`
- upstream source URL:
  `https://developer.download.nvidia.com/compute/cusparselt/redist/libcusparse_lt/linux-x86_64/libcusparse_lt-linux-x86_64-0.8.1.1_cuda12-archive.tar.xz`
- SHA256: `b34272e683e9f798435af05dc124657d1444cd0e13802c3d2f3152e31cd898a3`
- dependency channel: `cuda@12`, build and run

The concrete focused node has:

```text
build: compiler-wrapper, cuda, gcc, glibc, gcc-runtime
link: cuda, gcc-runtime, glibc
```

The hermetic Spack build log shows no patches and only this install body:

```python
mkdirp(prefix.lib)
mkdirp(prefix.include)
install_tree("lib", prefix.lib)
install_tree("include", prefix.include)
```

The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/cusparselt-0.8.1-cuda120-hm255yktrxc555nv46bgt5aluknf3m2a
```

The ABI-relevant installed surface for this migration is:

```text
include/cusparseLt.h
lib/libcusparseLt.so -> libcusparseLt.so.0
lib/libcusparseLt.so.0 -> libcusparseLt.so.0.8.1.1
lib/libcusparseLt.so.0.8.1.1
lib/libcusparseLt_static.a
```

Header/version contract:

```text
CUSPARSELT_VER_MAJOR 0
CUSPARSELT_VER_MINOR 8
CUSPARSELT_VER_PATCH 1
CUSPARSELT_VER_BUILD 1
```

The shared library SONAME is:

```text
libcusparseLt.so.0
```

## Native build recipe

`native/cusparselt/cusparselt.bzl` mirrors the concrete Spack install flow:

```text
download libcusparse_lt-linux-x86_64-0.8.1.1_cuda12-archive.tar.xz
validate CUDA_PREFIX/bin/nvcc from @cuda_native//:prefix_path.txt
validate include/cusparseLt.h
validate lib/libcusparseLt.so, lib/libcusparseLt.so.0,
         lib/libcusparseLt.so.0.8.1.1, and lib/libcusparseLt_static.a
copy archive lib/ to prefix/lib/
copy archive include/ to prefix/include/
emit binary_archive.json and prefix_path.txt
```

The corresponding mechanism verifier is the binary-archive dependency-prefix
case:

```text
native/cusparselt/cusparselt.bzl: binary-archive: CUDA_PREFIX
```

This verifies the rule refuses host-root execution, consumes CUDA through a
mandatory Bazel prefix-file label, checks `CUDA_PREFIX/bin/nvcc`, validates the
source archive layout before copying, and records the CUDA dependency in
`binary_archive.json`.

## Gates

The smoke target is:

```text
//synthetic:use_cusparselt_native
```

It validates the native prefix layout, symlink chain, static archive, header
version constants, and binary-archive manifest. It intentionally avoids GPU
initialization.

`//synthetic:cusparselt_abi_parity` compares the native prefix against the
hermetic Spack reference. It covers:

- five ABI-relevant layout paths;
- byte-identical `include/cusparseLt.h`;
- byte-identical `lib/libcusparseLt_static.a`;
- SONAME parity for `libcusparseLt.so.0.8.1.1`;
- exported dynamic-symbol parity for 42 symbols.

Current status: native cuSPARSELt is gated inside the hermetic CUDA insula. The
focused verification command was:

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

The run used Bazel's `@spack_dist//:spack` inside the isolated CUDA 12.9.1
insula, reported hermetic Spack version `1.2.2`, passed
`//synthetic:use_cusparselt_native`, passed
`//tools:hermetic_native_deps_guard_test`, and passed
`//synthetic:cusparselt_abi_parity`.

The parity JSON reported `ok: true`: five layout paths, byte-identical header
and static archive, SONAME `libcusparseLt.so.0`, and matching exported symbol
sets with 42 symbols.
