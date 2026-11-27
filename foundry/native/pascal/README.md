# Pascal native Bazel builds

This additive graph builds complete PyTorch 2.15 and Triton 3.8 source wheels
inside the qualified CUDA 12.6 / SM61 / CPython 3.12 Insula capsule. Both
actions use the unchanged Foundry plan executor, uv as the build frontend,
declared recursive source archives and patches, and Bazel-owned outputs/cache.
Each uncached action extracts a clean source tree; historical CMake/Ninja build
trees are never adopted.

The complete existing Pascal capsule is an explicit tool/provider boundary:
GCC/libc/linker, CPython and headers, CUDA/cuDNN/NCCL/CUPTI, CMake/Ninja,
Python build dependencies, LLVM/MLIR, JSON headers, vendor diagnostics and uv.
Their producer identity and scoped prefixes are recorded in
capsule.providers.json and rootfs.identity.json. These are existing binary
tool/dependency providers, not newly source-built Bazel packages. The modern
cu129/cu130 source-built dependency graph remains unchanged. Pascal retains
its exact existing feature selection and recursive third-party source inputs;
it is not promoted to the modern full-feature release matrix.

## Capsule preparation

Use install_builder.py against a **new mutable capsule stage**, providing
the exact recursive Torch source archive and the repo-pinned standalone
Bazel 7.7.0 binary plus the pinned zstd Debian package (provided through
--zstd-deb). It checks archive acquisition identities, copies the
unchanged shared executor, creates both native plans, and installs
/opt/vaso/pascal/bin/build-native. It rejects a published capsule. Files
replace staged paths atomically so a hardlinked parent capsule is preserved.
The caller then publishes the stage using the normal Insula bundle workflow.

No compilation requires a GPU. The capsule supplies every tool and dependency;
the build invocation admits no extra dependency mounts. Insula keeps its
shared host-network contract, while uv and both package builds remain offline.
All Bazel actions use no-sandbox; Insula owns the execution boundary.

## Packaged interface

    /opt/vaso/pascal/bin/build-native --target torch --phase prepare --request-id example-prepare
    /opt/vaso/pascal/bin/build-native --target torch --phase build --jobs 2 --request-id example-build
    /opt/vaso/pascal/bin/build-native --target triton --phase build --jobs 2 --request-id example-triton
    /opt/vaso/pascal/bin/build-native --target torch --phase status

The prepare phase performs real Bazel loading/analysis without native compilation.
Build either executes the complete source-wheel action or restores its valid
Bazel cache entry, then checks the native wheel extension/version and records
the final artifact size/SHA256. The actual Bazel spawn log distinguishes native
execution from cache reuse. Native compile output and Bazel events are retained
per attempt. Request IDs, job counts, timestamps and outer owner receipts do
not enter native action identity.

State is under /workspace/native-build/<target>. The status.json file records the
current request; requests/<request_id>.json identifies one exclusive invocation.
Every attempt has its own receipt, and last-success.json
preserves the most recent successful build. The outer standalone Insula
owner separately records process cleanup and final exit status. A build
receipt is not GPU runtime qualification. Relocation audit remains explicitly
deferred for these additive Pascal wheels; no modern reproducibility or
cross-host bitwise-equivalence claim is implied.

Run the focused standard-library suite through uv:

    uv run --offline --no-project --no-python-downloads --no-config -- python -B foundry/native/pascal/native_build_test.py
