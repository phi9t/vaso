# Status and evidence

The first versioned preview is being prepared. Existing artifacts and passing
runs do not yet establish a complete, promotable Vaso release.

## Scope

The intended preview serves operators who can follow the documentation and
have access to the private GHCR packages. It covers `cu129` and `cu130`, with
Torch/Triton and JAX profiles, on the tested Linux x86-64/eight-B200 baseline.
Other hardware configurations are unqualified. Launched MPI process groups
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

The original run observed other GPU work. Its estate-local lease did not
establish exclusive ownership across the physical host. Controlled repeatability
is still required; passing retries cannot erase the failed attempt or prove its
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

Exact repeatability and promotion policy remain unsettled. The complete
release-specific operator recipe is also implementation work. Current guides
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
