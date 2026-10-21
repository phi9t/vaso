# Operator guide

Use the shipped wheels to run workloads, or prepare a separate estate for native
builds. Both paths execute inside insula. The preview's complete release-specific
recipe is still being prepared; these are the existing operator entrypoints.
Read [status and evidence](status.md) before interpreting a run as qualification.
The [estate lifecycle](estate.md) explains persistent state and restore choices.

Commands below run from the repository root. Replace example paths and digests
with owned storage and the selected artifact identities.

## Prerequisites

Prepare a Linux host with Bubblewrap/user-namespace support, Bash, and Python for
host orchestration. GPU execution also needs accessible NVIDIA devices and a
compatible host driver. The tested deployment baseline is Linux x86-64 with eight
B200 GPUs. Reserve physical GPUs across estates before qualification.

Acquisition needs registry/network access and private GHCR credentials. Store
credentials outside the checkout as described in [publication](agents/publication.md).
Normal execution and builds use the prepared rootfs and offline input closure.

Set an estate on storage with room for rootfs bundles, installed files, caches,
and outputs. Native builds need substantially more space than wheel deployment.

```sh
export VASO_ESTATE_ROOT=/path/to/owned-estate
```

## Restore a rootfs and wheels

The [artifact inventory](../tools/publish/published-artifacts.json) lists the
current immutable OCI digests. Select the matching CUDA line and profile; do
not substitute a mutable tag or mix line-specific wheel closures.

Set `ROOTFS_OCI_DIGEST` and `RELEASE_OCI_DIGEST` to the selected full
`sha256:…` references from the inventory. Install the pinned acquisition CLI
and pull each artifact into its own directory:

```sh
tools/publish/install_oras.sh
tools/publish/artifact_store.py pull \
  --artifact rootfs --line cu129 \
  --digest "$ROOTFS_OCI_DIGEST" \
  --target-dir "$VASO_ESTATE_ROOT/downloads/rootfs-cu129"
tools/publish/artifact_store.py pull \
  --artifact release --profile torch --line cu129 \
  --digest "$RELEASE_OCI_DIGEST" \
  --target-dir "$VASO_ESTATE_ROOT/downloads/release-torch-cu129"
```

Pulling verifies recorded file hashes and reassembles split rootfs archive layers.
Set `ROOTFS_ARCHIVE` to the restored archive path and `ROOTFS_ARCHIVE_SHA256`
to its 64-character checksum, then materialize the rootfs:

```sh
insula/rootfs/bundle.sh unbundle \
  --bundle "$ROOTFS_ARCHIVE" \
  --dest "$VASO_ESTATE_ROOT/rootfs-lines/cu129" \
  --line cu129 --digest "$ROOTFS_ARCHIVE_SHA256"
```

The OCI manifest digest and archive SHA-256 identify different objects. Preserve
the restored sidecar and bundle metadata. See the [publication guide](agents/publication.md)
for bundle and credential details.

Run a shipped-wheel smoke check:

```sh
foundry/insula.sh --line cu129 \
  --release "$VASO_ESTATE_ROOT/downloads/release-torch-cu129" \
  --gpus 1 -- python3 -c \
  'import torch, triton; assert torch.cuda.is_available(); print(torch.__version__, triton.__version__)'
```

The runner uses bundled Python and installs hashed requirements offline. It needs
no old Bazel output base or native prefixes. Select the matching JAX release in a
separate environment for JAX; select the corresponding rootfs and wheel release
for `cu130`. The [Foundry guide](foundry.md) describes deployment behavior.

## Enter generic insula execution

A prepared rootfs can run a command without a build profile:

```sh
insula/execution/insula.sh --line cu129 \
  --workspace /path/to/project -- /bin/sh -c 'pwd'
```

See the [Insula guide](insula.md) for projections, environment, network settings,
leases, and the sourced interface. `--print-argv` inspects a request; it is not
execution evidence.

## Build and qualify

For serial unattended builds across CUDA lines and profiles, use the
[variant runner](foundry.md#build-variants-unattended).

The [Foundry guide](foundry.md) covers rootfs construction, pinned tool and module
provisioning, source acquisition, and offline build execution. Wheel deployment
does not provision the full native build closure.

For a prepared build estate, the release gate and reproducibility entrypoints are:

```sh
tools/agents/triumvirate-gate.sh --release --profile torch --line cu129 \
  --acceptance-out "$VASO_ESTATE_ROOT/acceptance-torch-cu129.json"
scripts/release/determinism_check.sh --profile torch --line cu129 \
  --work-dir "$VASO_ESTATE_ROOT/determinism/torch-cu129" \
  --out "$VASO_ESTATE_ROOT/determinism/torch-cu129.json"
```

These commands can execute expensive native builds. Follow [build stability](agents/build-stability.md)
and preserve existing input identity; missing evidence does not justify a
metadata-only rebuild. Determinism compares contents from independent runs;
a fresh output base alone does not prove native actions avoided cache hits.

After both profile output bases exist, run the cross-framework decoder workload
without rebuilding native packages:

```sh
foundry/workloads/nanogpt_speedrun/run.sh \
  --estate-root "$VASO_ESTATE_ROOT" --line cu129 --mode benchmark
```

Retain the attempt's complete `attestation/` directory as a CI artifact, including
on failure. Replay it without a GPU:

```sh
python3 -B foundry/workloads/nanogpt_speedrun/attest.py \
  --verify-bundle /absolute/path/to/attempt/attestation
```

The [speedrun guide](../foundry/workloads/nanogpt_speedrun/README.md) documents
prerequisites, stage deadlines, bounded resume, evidence packaging, matrix scope,
and the rules for numerical and performance claims. Attestations are local and
unsigned; native build/release qualification remains separate.

`tools/ci/nightly.sh` wraps both profiles and both CUDA lines. It is
operator-triggered and flocked, runs release gates under leases, and only runs
determinism after those gates pass. Estate-local leases do not guarantee global
physical-GPU ownership.

Keep failures and retries as separate records. Missing inputs, denied access,
digest mismatches, or busy GPUs are failed prerequisites, not passing tests.
Do not overwrite verified artifacts with an unverified acquisition. Release
cutting and publication require the complete evidence set and promotion authority.

## Inspect a checkout without a GPU estate

The Python CLI supplies a local host-layout preflight:

```sh
PYTHONPATH=insula/execution python3 -m vaso.cli --help
PYTHONPATH=insula/execution python3 -m vaso.cli validate --tier 0
```

Tier 0 creates ignored `.vaso/` backing state and a run record. It does not enter
production insula. Use [development](development.md) for lightweight tests and
[executable verification](verification.md) for real Bubblewrap acceptance.
