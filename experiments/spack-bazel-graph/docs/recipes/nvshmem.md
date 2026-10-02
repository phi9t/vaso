# nvshmem@3.4.5

## Position in the native PyTorch closure

NVSHMEM is required for the full-feature native PyTorch v2.14 build because
`USE_NVSHMEM` defaults on with CUDA enabled. Probe 5 found no ambient NVSHMEM
provider in the CUDA 12.9 rootfs, so ticket 19 requires a declared native
provider.

PyTorch v2.14.0's official cu12 wheel line pins:

```text
nvidia-nvshmem-cu12==3.4.5
```

The selected native provider is therefore `nvshmem@3.4.5`, implemented from
the official PyTorch wheel-pinned NVIDIA payload. This gives the native
PyTorch action the same `NVSHMEM_HOME`-compatible include/lib surface that
PyTorch's CMake already recognizes.

## Hermetic Spack evidence

All Spack facts here come from Bazel's vendored hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path in the isolated estate:

```text
/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/nvshmem/package.py
```

Source provenance from that recipe:

- package class: `Nvshmem(MakefilePackage, CMakePackage, CudaPackage)`
- Spack build-system bucket for `@3.4.5`: `cmake`
- upstream source URL:
  `https://developer.download.nvidia.com/compute/redist/nvshmem/3.4.5/source/nvshmem_src_cuda12-all-all-3.4.5.tar.gz`
- SHA256:
  `40c1d4c255dd7395e04df41b181c4afdf2e0724c06b6fabde58bf2f8f532b0e5`
- default dependency channels: CUDA, MPI, UCX, GDRCopy and NCCL, with Python
  bindings, examples, Hydra launcher, tests and TXZ packaging disabled by the
  Spack CMake recipe

## PyTorch source evidence

Local PyTorch v2.14.0 source evidence:

- `CMakeLists.txt`: `USE_NVSHMEM` defaults on when CUDA is on.
- `.github/scripts/generate_binary_build_matrix.py`: the cu12, cu13.0 and
  cu13.2 wheel lines all pin `nvidia-nvshmem-cu12/cu13==3.4.5`.
- `caffe2/CMakeLists.txt`: PyTorch searches `NVSHMEM_HOME` or the installed
  wheel path for `include/nvshmem.h`, host library `nvshmem_host` or
  `libnvshmem_host.so.3`, and static device library `nvshmem_device`.

## Native build recipe

`native/nvshmem/nvshmem.bzl` installs the wheel-pinned binary payload inside
the insula:

```text
download nvidia_nvshmem_cu12-3.4.5-py3-none-manylinux2014_x86_64.manylinux_2_17_x86_64.whl
verify the wheel SHA256
extract the wheel with /usr/bin/python3 inside the insula
copy nvidia/nvshmem/* into prefix/
validate include/nvshmem.h
validate include/non_abi/nvshmem_version.h reports 3.4.5
validate lib/libnvshmem_host.so.3
validate lib/libnvshmem_device.a
copy dist-info metadata under prefix/info/wheel/
emit binary_archive.json and prefix_path.txt
```

Source payload:

- URL:
  `https://files.pythonhosted.org/packages/b5/09/6ea3ea725f82e1e76684f0708bbedd871fc96da89945adeba65c3835a64c/nvidia_nvshmem_cu12-3.4.5-py3-none-manylinux2014_x86_64.manylinux_2_17_x86_64.whl`
- SHA256:
  `042f2500f24c021db8a06c5eec2539027d57460e1c1a762055a6554f72c369bd`
- wheel root copied to prefix: `nvidia/nvshmem`

The corresponding mechanism verifier is the binary-archive dependency-prefix
case:

```text
native/nvshmem/nvshmem.bzl: binary-archive: CUDA_PREFIX
```

## Gates

The smoke target is:

```text
//synthetic:use_nvshmem_native
```

It validates the native prefix layout, wheel/archive manifest, CUDA dependency
provenance, PyTorch-visible version header, and a downstream C++ compile/link
against `libnvshmem_host.so.3` without initializing NVSHMEM or requiring a GPU
runtime rendezvous.
