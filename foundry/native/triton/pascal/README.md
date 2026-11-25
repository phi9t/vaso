# Experimental Pascal Triton source wheel

This additive source-wheel route leaves the CUDA 12.9/13.0 Bazel release
matrix unchanged. It uses the existing Vaso Insula source-build and two-GPU
admission entrypoints, the PyTorch 2.15 CUDA 12.6 SM61 rootfs, and the
Triton 3.8.0 upstream LLVM distribution. Proton is off. Keep both upstream compiler backends: shared Gluon bindings reference AMD dialect definitions even when runtime execution targets NVIDIA only. This is not a
qualified replacement for the mainline unified-LLVM Bazel target.

Provide a site JSON (outside the repository):

```json
{
  "rootfs": "/path/to/sealed-cu126-rootfs",
  "estate_root": "/path/to/estate",
  "inputs_dir": "inputs/triton38-cu126-sm61",
  "work_dir": "build/triton38-cu126-sm61",
  "torch_install": "build/qualified-torch/installed",
  "vendor_tools": "inputs/nvidia/bin",
  "gpu_lease_estate": "/path/to/shared-gpu-lease-estate"
}
```

All relative paths are below the selected estate's `vaso` directory.
Provision the pinned Triton archive, its exact upstream LLVM binary prefix,
nlohmann JSON v3.11.3 headers, pybind11 3.0.2 and lit 18.1.8 in
`python-build-deps`, and explicit NVIDIA vendor tools before preparation.
The source archive and LLVM identity are recorded in `profile.json`.
Acquire and verify LLVM at the acquisition boundary. The prototype consumes
a prepared dependency bundle; dependency acquisition is not yet a Foundry
Bazel action. The existing CUDA 12.6 rootfs supplies ptxas, libdevice,
CUDA headers, CMake, Ninja, Python and GCC. Build steps do not download
dependencies. The qualification install must provide the exact qualified
Pascal Torch version. Do not write to a live build's source tree or reuse its
work directory for different inputs.

```sh
foundry/build.sh --pascal-triton --site /path/to/site.json prepare
foundry/build.sh --pascal-triton --site /path/to/site.json --jobs 2 build
foundry/build.sh --pascal-triton --site /path/to/site.json status
foundry/build.sh --pascal-triton --site /path/to/site.json qualify
```

The build uses a single-owner lock and persistent status/logs. Qualification
installs the produced wheel into a fresh directory, acquires both GPUs
through the shared lease authority, and executes signed INT8 dot with
nonzero accumulators as well as FP32 dot, RMSNorm, masked addition and
inline DP4A on each device. A `--control --wheel <estate-relative-wheel>`
qualification checks the unmodified wheel without the known failing INT8
dot path; it never marks a release qualified. Runtime attempts are bounded
to 300 seconds, below the existing GPU lease TTL. A failed or interrupted
attempt is not a qualification; the shared lease controller reclaims dead
local holders on subsequent admission.

The small checked-in signed INT8 patch is a temporary downstream bridge
until the maintained upstream-based Triton fork is published and the
profile can consume its pinned revision. The patch changes integer
promotion, not automatic packed DP4A code generation.
