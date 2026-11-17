# CUDA developer-provider header closure

The CUDA 12.6 Pascal source build needs **all nested NCCL developer headers**,
not only `nccl.h` and `nccl_device.h`. PyTorch's `ProcessGroupNCCL.cpp` includes
`nccl_device.h`, which includes `nccl_device/coop.h` and additional nested files.
Flattening the top-level include directory can pass CMake and thousands of
compilation tasks before failing in the distributed backend.

`profiles/cu126-sm61-r4.json` pins the complete 44-file NCCL 2.29.3 include tree.
`SourceBuild.inputs()` rejects missing files, digest mismatches and symlinks
escaping the frozen rootfs before starting the native build. Existing profile
r1/r2/r3 identities remain unchanged for forensic inspection and exact resumes.

## Preparing a new rootfs

Materialize developer headers recursively before sealing the rootfs. The generic
helper `provider_headers.py` accepts an exact source manifest and copies the
entire tree into a caller-owned unsealed rootfs. It preflights all collisions,
checks each copied file, and refuses to change an existing sealed manifest.
It never overwrites a conflicting provider or mutates a running rootfs.

The rootfs builder should apply this to all declared provider include trees,
then produce the complete rootfs manifest. CUDA compiler, NCCL library and
NCCL headers must come from mutually compatible, pinned package inputs.

## Explicit recovery of a stopped build

`recover_headers.py` supports a narrow, auditable recovery: an old rootfs may be
superseded by a new rootfs that adds missing NCCL/NVSHMEM headers while preserving
every previous file digest. The source archive, profile, compiler/library bytes,
and build-driver identity must remain identical. Recovery uses a new estate,
retains the old guest path so Ninja dependencies stay meaningful, and copies
rather than modifies the stopped donor source/build directory.

A `recovery-pending.json` protects interruption/retry. Completed recovery writes
`recovery.json` with the old/new input IDs, added headers and copied object count.
This is a recorded recovery, not a cold reproducibility claim or a silent cache
hit. The normal one-writer build lock applies. Complete wheel validation and
independent GPU/runtime qualification are still required.

Do not use this mechanism to change source patches, compiler versions, linker
libraries or native flags. Those changes require a new native build identity.

## Runtime qualification and shared devices

`foundry/qualify-source.sh` accepts only a completed wheel with matching build
inputs, metadata and SHA256. It verifies the exact imported package path/version
before GPU tests. A build estate can set `gpu_lease_estate` in site JSON to the
host-wide lease authority, preventing independent build estates from claiming
the same physical GPUs. The lease store is runtime/site configuration, not a
compiler input.

Tests cover CPU-reference forward/backward and AdamW updates, NCCL, FSDP2,
tensor-parallel layers, two-stage/four-microbatch 1F1B, Kineto CUDA capture and
native Flight Recorder records. FSDP/TP/PP component tests run separately on the
two GTX 1070 Ti GPUs; they are not an eight-GPU composed scaling benchmark.
