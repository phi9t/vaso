# nccl@2.29.3-1

## Position in the native PyTorch closure

NCCL is required for the full-feature native PyTorch v2.14 build because D2
keeps distributed/NCCL enabled. Probe 4 found an ambient rootfs NCCL 2.27.3,
but PyTorch v2.14 pins NCCL v2.29.3-1 on the cu12 line and v2.30.7-1 on the
cu13 line. D5 therefore selects a declared native source provider at
`nccl@2.29.3-1` for the first CUDA 12.9 rootfs; the rootfs NCCL must not be
wired.

The focused reference graph for this provider is:

```bash
SPACK_ROOT_PKG='nccl@2.29.3-1 cuda_arch=100 ^cuda@12.9.1'
```

## Hermetic Spack evidence

All recipe evidence comes from Bazel's vendored hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path in the isolated estate:

```text
/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/nccl/package.py
```

Source provenance from that recipe:

- package class: `Nccl(MakefilePackage, CudaPackage)`
- Spack build-system bucket: `makefile`
- upstream source URL: `https://github.com/NVIDIA/nccl/archive/v2.29.3-1.tar.gz`
- SHA256: `d1dffc5e9dd059985704f98ff3d8b7e6cf62d20c10a181d427a4e3233f8148f1`
- dependency channel: `cuda@12:13`, build and run
- default fabrics: `verbs`; Spack records `rdma-core` as a run dependency

The Spack build targets for `cuda_arch=100` are:

```text
CUDA_HOME=<cuda prefix>
NVCC_GENCODE=--generate-code arch=compute_100,code=sm_100 --generate-code arch=compute_100,code=compute_100
```

For `nccl@2.29.3-1`, the Spack install target is:

```text
PREFIX=<prefix> src.install
```

## Native build recipe

`native/nccl/nccl.bzl` mirrors the concrete Spack build flow:

```text
download nccl v2.29.3-1 source tarball by SHA256
read CUDA_PREFIX from @cuda_native//:prefix_path.txt
validate VASO_IN_INSULA=1
validate CUDA_PREFIX/bin/nvcc
make -C <src> CUDA_HOME=<cuda prefix> NVCC_GENCODE=<spack gencode>
make -C <src> CUDA_HOME=<cuda prefix> NVCC_GENCODE=<spack gencode> PREFIX=<prefix> src.install
emit prefix_path.txt and source_build.json
```

The emitted prefix contract is the NCCL public install surface:

```text
include/nccl.h
lib/libnccl.so -> libnccl.so.2
lib/libnccl.so.2 -> libnccl.so.2.29.3
lib/libnccl.so.2.29.3
```

The corresponding mechanism verifier is the makefile dependency-prefix case:

```text
native/nccl/nccl.bzl: makefile: CUDA_PREFIX
```

This verifies the rule refuses host-root execution, consumes CUDA through a
mandatory Bazel prefix-file label, checks `CUDA_PREFIX/bin/nvcc`, and passes
the dependency prefix on the `make` command line.

## Gates

The smoke target is:

```text
//synthetic:use_nccl_native
```

It validates the native prefix layout, `nccl.h` version macros, and
`source_build.json`, including the makefile mechanism, CUDA dependency prefix,
`cuda_arch=["100"]`, and non-empty `NVCC_GENCODE`.

`//synthetic:nccl_abi_parity` compares the native prefix against the hermetic
Spack reference and compiles a downstream C++ consumer that calls
`ncclGetVersion()`. This is a cheap CPU-side link-and-run seam; the required
two-rank GPU all-reduce smoke remains ticket 19 follow-up evidence.
