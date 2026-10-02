# cudss@0.7.1

## Position in the native PyTorch plan

PyTorch v2.14.0 enables `USE_CUDSS` by default when CUDA is enabled. The CUDA
12.9 rootfs does not provide a declared graph-native cuDSS provider, so ticket
19 captures cuDSS as a native binary-archive provider for the first
full-feature native PyTorch build.

The selected provider is `cudss@0.7.1`: PyTorch's checked-in
`.ci/docker/common/install_cudss.sh` covers only older CUDA 12.1-12.4 images
and installs `0.3.0.9_cuda12`, while ticket 19 allows the official pin or the
latest cu12 redistributable. Hermetic Spack v1.2.2 pins cuDSS `0.7.1` as the
latest cu12 x86_64 redistributable.

## Hermetic Spack Evidence

All recipe evidence comes from Bazel-owned hermetic Spack under
`@spack_dist//:spack`, pinned to upstream Spack `v1.2.2`. Do not use an ambient
host Spack checkout for this package.

Hermetic recipe path in the isolated estate:

```text
/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/cudss/package.py
```

Source provenance from the Spack recipe:

- package class: `Cudss(Package)`
- Spack build-system bucket: `generic`
- concrete Linux install mechanism: NVIDIA binary archive, copied with
  `install_tree(".", prefix)`
- upstream x86_64 source URL:
  `https://developer.download.nvidia.com/compute/cudss/redist/libcudss/linux-x86_64/libcudss-linux-x86_64-0.7.1.4_cuda12-archive.tar.xz`
- SHA256:
  `946571d9ea164f948e402dd97a14541cb90fbec800336cfa7ae644af5937632f`
- dependency channel: `cuda@12:`

PyTorch source evidence:

- `CMakeLists.txt`: `USE_CUDSS` defaults on when `USE_CUDA` is on.
- `cmake/Modules/FindCUDSS.cmake`: PyTorch searches `CUDSS_ROOT`,
  `CUDSS_INCLUDE_DIR`, and `CUDSS_LIBRARY`, requiring `cudss.h` and
  `libcudss.so`.
- `cmake/public/cuda.cmake`: PyTorch links `torch::cudss` to
  `CUDSS_LIBRARY_PATH`.

## Native Build Recipe

`native/cudss/cudss.bzl` mirrors the concrete Spack install flow:

```text
download libcudss-linux-x86_64-0.7.1.4_cuda12-archive.tar.xz
validate CUDA_PREFIX/bin/nvcc from @cuda_native//:prefix_path.txt
validate include/cudss.h
validate libcudss.so under lib/ or lib64/
copy archive payload to prefix/
emit binary_archive.json and prefix_path.txt
```

The corresponding mechanism verifier is the binary-archive dependency-prefix
case:

```text
native/cudss/cudss.bzl: binary-archive: CUDA_PREFIX
```

The smoke target is:

```text
//synthetic:use_cudss_native
```

It validates the native prefix layout, header version constants, binary-archive
manifest, CUDA dependency provenance, and a downstream C++ link/run against
`libcudss.so` without initializing the GPU.
