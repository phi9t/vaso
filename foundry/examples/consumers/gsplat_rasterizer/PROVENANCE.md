# gsplat Rasterizer Consumer

This consumer builds the CUDA rasterization kernels from
`nerfstudio-project/gsplat` tag `v1.5.3`, commit
`937e29912570c372bed6747a5c9bf85fed877bae`.

`diff-gaussian-rasterization` was intentionally not used. That implementation is
distributed under the Inria/MPII Gaussian-Splatting licence, whose
non-commercial restriction is not open source. `gsplat` is Apache-2.0, so it can
serve as the source project for this feature-complete stress test.

The `gsplat` source archive is pinned in `../bcr-manifest.tsv` by sha256
`8a24428b8ea2ce7c3e10fcf5aa20e20fe503b8329c96db797f4eab703729aac3`.
Its header-only `glm` dependency is pinned once in the same manifest at version
`1.0.1`.

No upstream source patches are applied. The local `bindings.cpp` is a thin
pybind11 shim that exports only the projection, tile-intersection, and rasterize
ops used by the verifier. The target passes `--expt-relaxed-constexpr` and
suppresses nvcc diagnostics `20012`, `20013`, and `20015` because CUDA 13
otherwise rejects constexpr host/device calls emitted through gsplat's GLM and
Torch headers; upstream's extension build also enables relaxed constexpr.

The stored reference image is generated independently of the CUDA kernel by
`tests/generate_reference_image.py`. It renders the fixed one-Gaussian 8x8 scene
on the CPU with NumPy, using pixel-center samples, opacity `0.8`, RGB color
`[0.25, 0.5, 0.9]`, center `(4.0, 4.0)`, unit screen-space conic, and gsplat's
`1/255` alpha cutoff. Regenerate or check it with:

```sh
python3 tests/generate_reference_image.py --check tests/reference_image.pt
python3 tests/generate_reference_image.py > tests/reference_image.pt
```
