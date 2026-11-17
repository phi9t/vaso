# Pinned PyTorch source-wheel builds

`foundry/build.sh --source` is the explicit Vaso source-wheel path for the
PyTorch 2.15 / CUDA 12.6 / Pascal profile. It preserves the established 2.14
Bazel release actions. This path executes PyTorch's PEP517/CMake/Ninja wheel
build inside Insula with an externally provisioned, sealed developer rootfs;
it does not claim the existing Bazel dependency graph has been migrated.

```sh
foundry/build.sh --source --site /path/to/site.json --jobs 3 build
foundry/build.sh --source --site /path/to/site.json status
foundry/build.sh --source --site /path/to/site.json --resume build
```

The external site JSON provides `estate_root`, `rootfs`, and `source_archive`.
The source archive SHA256 and compiler flags are owned by
`profiles/cu126-sm61-r3.json`, not supplied by ambient environment. All tools
and providers must be inside the selected rootfs at `/opt/vaso`; the rootfs
manifest is `opt/vaso/build-manifest.json`. This source profile uses CPython
3.12, CUDA 12.6, SM61, system NCCL, cuDNN and Kineto. Optional CPU-quantization,
FlashAttention, sparse-Lt, NVSHMEM and vendor backends are deliberately off.

The controller checks source archive identity, uses safe extraction, verifies
critical submodule files, and owns one writer lock per profile. Preparation is
idempotent. Resume requires identical source/profile/rootfs manifest identities.
Each attempt has a separate log and an atomic `status.json`; interruptions and
timeouts terminate the owned process group. No GPU is required for compilation.

A produced wheel is marked `runtime_qualified=false` until separate CUDA/NCCL,
training and profiler tests pass. Build success must not be inferred from a
successful compiler probe, generated Ninja graph or availability of an API name.
The side-by-side CUDA13 CUPTI experiment is not enabled for SM61: CUDA13.3's
published CUPTI Activity and Callback support begins at compute capability 7.5.

The r2 profile carries one exact, hash-checked Kineto source edit: formatting
device JSON braces with the already-used fmt library avoids GCC 12's
`-Wrestrict` diagnostic on literal-plus-rvalue-string concatenation. The
original warning-as-error flags remain intact. Patch preimage and postimage
are part of the profile; an upstream source mismatch fails before compilation.
The initial failed profile and its logs are retained separately.

The r3 profile additionally verifies required CUDA/CUPTI/cuDNN/NCCL/NVML and
Python development headers before starting an expensive source build. NVML
headers are a build input, not permission to update the host NVIDIA driver.

## Metadata identity migration

The v2 source-build receipt stores exact UTF-8 profile, rootfs-manifest, driver, and entry-script bytes instead of hashing individual small files. Source edits compare literal pre/postimages; archive and wheel SHA-256 checks remain. Old v1 receipts intentionally do not resume under the new driver: create a fresh named build rather than rewriting an active receipt. Existing running builds and their checkouts are not modified by this migration.
