# Status and evidence

The first versioned preview is being prepared. Existing artifacts and passing
runs do not yet establish a complete, promotable Vaso release.

## Scope

The intended preview serves operators who can follow the documentation and
have access to the private GHCR packages. It covers `cu129` and `cu130`, with
Torch/Triton and JAX profiles, using one B200 on the tested Linux x86-64 host.
Multi-GPU features and other hardware configurations are unqualified. Launched MPI process groups
are outside initial support; backend availability is not launched-MPI evidence.

All process tools and dependencies resolve inside the selected insula rootfs.
Bazel separately declares build inputs. Native build proof and deployment
qualification are distinct requirements.

## Recorded deployment evidence

| Record | What it establishes | Limit |
|---|---|---|
| [Artifact inventory](../tools/publish/published-artifacts.json) | Digest-pinned identities for two rootfs bundles and four wheel releases | An inventory is not a release verdict |
| [Fresh-estate verification](../tools/publish/fresh-estate-verification.json) | Independent pulls, hash verification, offline wheel installation, and single-GPU checks on both lines | Does not repeat native build proof or full qualification |
| [Multi-GPU qualification](../tools/publish/multigpu-qualification.json) | 81/82 workloads, 26/26 runtime gates, and four consumers passed, including co-residence coverage | CUDA 13 JAX tensor parallelism hit a collective timeout |
| [CUDA 13 JAX full-profile retry](../tools/publish/cu130-jax-qualification-retry.json) | Ten workloads, three gates, and one consumer passed with fresh environment and caches | The earlier failure remains unresolved |
| [Candidate 2 single-GPU rounds](releases/single-gpu-qualification-20261022.json) | All 18 stages passed across three rounds on both CUDA lines: 198 workload executions, 66 gates, and 12 consumers | Cooperative device lease and monitoring; multi-GPU cases omitted; earlier failed attempts retained |

The original run observed other GPU work. Its estate-local lease did not
establish exclusive ownership across the physical host. Controlled multi-GPU repeatability
is required before adding multi-GPU support; passing retries cannot erase the failed attempt or prove its
cause fixed. These reports do not qualify broader hardware.

## Release requirements

One Vaso release will pin the complete source and component set, with checksums,
dependency selections, provenance, SBOM/license metadata, native build proof,
repeatable runtime qualification, and fresh-estate restoration evidence.
Missing or mismatched required evidence blocks promotion.

Build proof requires actual clean offline execution inside insula and ABI/consumer
checks. Reproducibility compares unpacked wheel and dependency-prefix contents
across independent runs using the declared comparison policy. It does not promise
identical ZIP archive bytes. Matching historical proof may be reused with verified
content identity and truthful provenance; metadata alone must not trigger rebuilds.

The approved repeatability policy requires three rounds of the single-GPU matrix
with fresh caches followed by two rounds with verified cache reuse. The user
deferred multi-GPU support and its CUDA 13 JAX diagnostic gates. Every attempt is retained; unexplained
failures block promotion. [Candidate qualification](release-qualification.md)
documents the executable single-GPU protocol and the deferred multi-GPU gates.

[Candidate 2](releases/preview-candidate-2.json) freezes source, all six
component identities, and the revised single-GPU policy. Its
[checksum](releases/preview-candidate-2.json.sha256)
is independent of subsequent implementation changes. Restored payload checks and all 2,931 pinned Python runtime files per line
passed on 2026-10-22; this was verification of an existing GHCR-restored estate,
not a new registry acquisition. The three-round single-GPU attempt completed
successfully with fresh caches followed by two verified reuse rounds. This
qualifies the recorded runtime attempt, not the complete release.

Torch-profile reproducibility now passes on both CUDA lines. The
[controlled proof](releases/native-reproducibility-20261023.json) compares two
independent builds from the same fixed source and release stamp. All four
wheels per line and their dependency prefixes match, including
`libtorch_cuda.so` and `libtorch_nvshmem.so`. Build events confirm actual Torch,
Torchvision, and Torchaudio execution in both estates. Matching unchanged
actions could reuse caches; this was not a full cold dependency rebuild.

PyTorch initialized `CMAKE_CUDA_FLAGS` before enabling CUDA, so CMake ignored
the seeded `CUDAFLAGS`. Passing those flags explicitly through `CMAKE_ARGS`
fixed the [earlier differences](releases/native-reproducibility-20261022.json).
The comparison policy and empty allowlist remain unchanged. The new wheels
installed offline. Nine single-GPU runtime gates and the Torch/Triton consumer
passed on each line. Initial estate-preparation failures and their recovery
remain recorded outside Git; estate materialization now creates `sources/`.

The Torch CUDA/NVSHMEM reproducibility blocker is closed. Complete release
qualification remains open: JAX proof reuse still needs its missing manifest
closure and verified artifact relationship. Candidate 2's three-round runtime
evidence identifies its original wheels; it does not qualify these new builds.
A revised candidate needs matching repeated qualification and restore evidence
before promotion. No release promotion or reproducibility exception was granted.

The complete release-specific operator recipe remains implementation work. Current guides
identify the implemented entrypoints and their prerequisites rather than claim
a one-command installation or completed release qualification.

## Operational limits

- GHCR artifacts are private; public download access is not promised.
- Native builds require provisioned tools, input caches, and owned disk capacity.
- Nightly verification is operator-triggered; the repository installs no schedule.
- The Python Tier-1 preflight remains plan-only, rather than the production
  execution interface.
- Security claims are limited to tested Bubblewrap/rootfs behavior; Vaso makes
  no blanket isolation guarantee for arbitrary hostile code.
