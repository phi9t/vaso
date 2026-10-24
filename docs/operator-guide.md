# Operator guide

Use the shipped wheels to run workloads, or prepare a separate estate for native
builds. Both paths execute inside insula. The preview's complete release-specific
recipe is still being prepared; these are the existing operator entrypoints.
Read [status and evidence](status.md) before interpreting a run as qualification.
The [estate lifecycle](estate.md) explains persistent state and restore choices.

Commands below run from the repository root. Replace example paths and digests
with owned storage and the selected artifact identities.

## Prerequisites

Prepare a Linux x86-64 host with Bubblewrap/user-namespace support, Bash, and
Python 3.11 or newer for host orchestration. Installing the pinned acquisition
CLI needs `curl`, gzip-capable `tar`, `sha256sum`, and `install`. Preparing a rootfs also needs `zstd`, Linux `flock` and GNU coreutils,
including `readlink` and `mktemp`. Construction additionally needs Docker. These host tools prepare storage; workloads and native builds use
tools inside the rootfs.

GPU execution also needs accessible NVIDIA devices, a compatible host driver,
and `nvidia-smi` for capacity discovery unless capacity is declared explicitly.
Preview qualification uses one B200 on the tested host. Select an idle physical
device and check ownership across estates before qualification.

Acquisition needs registry/network access and private GHCR credentials. The
puller reads `GHCR_TOKEN` or `$VASO_ESTATE_ROOT/publish/ghcr-token`; the file must
have mode `600`. Store credentials outside the checkout as described in
[publication](agents/publication.md).
Normal execution and builds use the prepared rootfs and offline input closure.

Set an estate on storage with room for rootfs bundles, installed files, caches,
and outputs. Native builds need substantially more space than wheel deployment.

```sh
export VASO_ESTATE_ROOT=/path/to/owned-estate
```

Use a fresh estate for this restore walkthrough. Run the command blocks in the
same Bash session, stop if any command fails, and keep the checkout fixed while
a build uses it. For scripted execution, start with `set -euo pipefail`.

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
The rootfs pull returns the archive name in its JSON `archive` field. The archive
is directly under `downloads/rootfs-cu129/`; its name and checksum also appear
as `archive` and `archive_sha256` in the inventory's matching rootfs entry.
Set `ROOTFS_ARCHIVE` to that full restored path and `ROOTFS_ARCHIVE_SHA256` to
the 64-character checksum. Materialize it into a new destination:

```sh
insula/rootfs/prepare.sh restore \
  --bundle "$ROOTFS_ARCHIVE" \
  --line cu129 --digest "$ROOTFS_ARCHIVE_SHA256" --fetch
```

The OCI manifest digest and archive SHA-256 identify different objects. Preserve
the restored sidecar and bundle metadata. Preparation verifies the archive,
selected ecosystem and installed tooling before publishing the complete line
directory. `--fetch` authorizes acquiring missing pinned tooling; omit it when
the tooling inputs already exist in `sources/foundry-tools`. Registry transport
remains the separate pull above, with its own credentials.

The default destination is `$VASO_ESTATE_ROOT/rootfs-lines/<line>`. Repeating the
same selection verifies and reuses it. Preparation refuses a mismatched or damaged
destination without repair or replacement. This includes older roots without a
`rootfs-preparation.json` selection receipt. Recover using a fresh `--dest` line
directory; do not replace a rootfs used by live execution. Preparation stages
beside that destination and never clones or edits an existing base.

Each attempt retains `result.json`, external command logs and, when verification
runs, its ecosystem report under `$VASO_ESTATE_ROOT/agents/rootfs-prepare/`.
The command prints the final result and attempt path as JSON. Failed attempts
remain inspectable after retry. SIGINT/SIGTERM stop owned construction processes
and remove owned staging; retry reconciles an owner killed without a terminal
outcome after surviving extraction/construction releases its lock. A host crash
or SIGKILL can leave a running record until that retry. See the
[publication guide](agents/publication.md) for bundle and credential details.

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
insula/run.sh --line cu129 \
  --workspace /path/to/project -- /bin/sh -c 'pwd'
```

See the [Insula guide](insula.md) for projections, environment, network settings,
leases, and the sourced interface. `--print-argv` inspects a request; it is not
execution evidence.

## Build and qualify

For serial unattended builds across CUDA lines and profiles, use the
[variant runner](foundry.md#build-variants-unattended).
It checks the prepared estate, acquires or reuses pinned inputs, runs the
selected builds, and retains stage logs and a final result. Use its documented
detached launch and `--status` command to follow a run after disconnection.

The [Foundry guide](foundry.md) covers rootfs construction, pinned tool and module
provisioning, source acquisition, and offline build execution. Wheel deployment
does not provision the full native build closure.

For a prepared build estate, the release gate and reproducibility entrypoints are:

```sh
tools/agents/triumvirate-gate.sh --release --profile torch --line cu129 \
  --acceptance-out "$VASO_ESTATE_ROOT/acceptance-torch-cu129.json"
tools/release/determinism_check.sh --profile torch --line cu129 \
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

For candidate-bound repeated execution, use the
[candidate qualification runner](release-qualification.md). It runs the approved
single-GPU preview policy and retains the deferred eight-GPU protocol.

Keep failures and retries as separate records. Missing inputs, denied access,
digest mismatches, or busy GPUs are failed prerequisites, not passing tests.
Do not overwrite verified artifacts with an unverified acquisition. Release
cutting and publication require the complete evidence set and promotion authority.

## Inspect a checkout without a GPU estate

Use the host tests in [development](development.md) to check a checkout without
prepared artifacts. The canonical commands expose `--help` without an estate:

```sh
insula/rootfs/prepare.sh --help
insula/run.sh --help
foundry/build.sh --help
```

Execution and builds require their documented prepared inputs. For a disposable
CPU execution check against an existing rootfs, use
[real Bubblewrap acceptance](verification.md#real-insula-operational-acceptance).
