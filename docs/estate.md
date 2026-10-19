# Estate lifecycle: contents, persistence, and restore

An estate is the host directory tree backing insula execution. It holds rootfs
files, acquired inputs, writable sandbox state, build outputs, and execution
records. Insula projects selected parts into a process's filesystem; Foundry
selects the build graph and runs Bazel there.

The estate persists because its backing files remain on disk after the process
exits. It is not a running service, a single container image, or an automatic
backup. The checkout, estate, rootfs, and release artifacts have separate lives.

## What lives where

This is the current build-estate layout. Entries may be created by different
entrypoints; a freshly materialized estate starts mostly empty.

```text
estate/
  rootfs-lines/
    cu129/
      rootfs/                         prepared filesystem
      rootfs-bundle.json              CUDA-layer and ecosystem identity
    cu130/                           second CUDA line
  sources/
    bcr/                             pinned module registry, archives, originals
    native/distdir/                  native source archives
    pytorch/distdir/                 PyTorch archives
    triton/distdir/                  Triton archives
    jax/distdir/                     JAX and nested-build inputs
    bazel/distdir/                   acquired Bazel inputs
  home/kvothe/                       persistent sandbox home
  vaso/
    cache/bazel/repository-cache/     Bazel's acquired-file cache
    lines/<line>/
      cache/bazel/output-base/       configured repositories and build outputs
      cache/bazel/disk-cache/        cached action results
      state/native/                 native build state
      state/stamps/                 orchestration stamps
    state/                           other durable execution state
    runs/                            canonical run-record namespace
    traces/                          derived indexes and exports
    tmp/                             scratch backing
  agents/<owner>/
    logs/                            Foundry stage logs
    results/                         Foundry run-result JSON
  agents/leases/                     local coordination state
  workspace/                         explicit workspace backing
  run/<run-id>/                      wheel-run venv, home, caches, and entrypoint
```

`home/kvothe` is the existing sandbox-home name, not the operator's host account.
The model also materializes compatibility directories such as `opt-vaso/` and
`vaso/tools/bin/`. Current Foundry execution obtains its tools from the selected
rootfs's `/opt/vaso`; an estate-side directory does not supply ambient host tools.

Acquisition and publication may add `downloads/`, `releases/`, or `publish/`.
These are workflow storage, not prerequisites created with complete contents
by the estate model. The implementation is [estate.py](../insula/estate/estate.py).

## How the host becomes a process filesystem

| Host backing | Typical build-process path | Behavior |
|---|---|---|
| `rootfs-lines/<line>/rootfs` | `/` | Prepared base mounted read-only |
| `sources/` | `/vaso/sources` | Explicit acquired-input projection |
| `vaso/` | `/vaso` | Persistent writable state and outputs |
| `agents/` | `/vaso/agents` | Logs, results, and coordination |
| `home/kvothe/` | `/home/kvothe` | Persistent writable home |
| Selected Bazel output base | `/vaso-bazel-ob` | Fixed sandbox name for owned build output |
| Checkout and Foundry sources | `/workspace/repo`, `/workspace/experiment` | Explicit source projections |
| Host NVIDIA driver/devices | `/run/nvidia-driver`, `/dev/nvidia*` | Explicit runtime inputs |

These mappings describe Foundry's context. Generic insula and shipped-wheel
execution prepare their own projections. For example, a wheel run exposes its
release and per-run state under `/run/vaso/`, and creates its venv in the host
estate's `run/<run-id>/`. See the [Insula](insula.md) and [Foundry](foundry.md)
guides for the corresponding interfaces.

All relevant tools and dependencies resolve inside the resulting rootfs,
including explicitly projected inputs. Bazel additionally declares build inputs.
A read-only sandbox bind does not prevent another host process from modifying
the backing files; coordinate their ownership separately.

## Create a usable estate

Commands below run from the repository root. Choose an owned disk-backed path
with enough space for the intended workload:

```sh
export VASO_ESTATE_ROOT=/path/to/owned-estate
export VASO_CUDA_LINE=cu129
python3 -B insula/estate/estate.py \
  --root "$VASO_ESTATE_ROOT" --required-gib 100 --materialize
```

Materialization creates the layout. It does not install a rootfs, populate
sources, or build packages. The placement tool defaults to 40 GiB free; Foundry
execution readiness normally requires 100 GiB. Neither threshold estimates the
space needed for a complete native build. Check every volume holding roots,
archives, work trees, output bases, and caches, especially when paths are links.

There are two ways to supply a rootfs:

- **Restore a pinned bundle:** follow the [operator restore recipe](operator-guide.md#restore-a-rootfs-and-wheels).
  Verify the OCI digest, file hashes, archive checksum, and selected CUDA line.
- **Build a rootfs:** `insula/rootfs/build_rootfs.sh --line cu129` constructs
  the line from the ecosystem lock. Its provisioning path uses Docker and
  network acquisition. This is separate from normal Bubblewrap execution.

For native builds, also provision pinned Foundry tools and the complete source,
module, and nested-build closure. Follow [Foundry provisioning](foundry.md#build-interface).
A deployment rootfs and wheels alone do not supply that closure.

Before building, verify the selected rootfs tools and prepare the module cache:

```sh
python3 -B insula/rootfs/foundry_tools.py \
  --rootfs "$VASO_ESTATE_ROOT/rootfs-lines/$VASO_CUDA_LINE/rootfs" --verify \
  >/dev/null
python3 -B foundry/tools/bcr_mirror.py \
  --estate-root "$VASO_ESTATE_ROOT" --offline
python3 -B foundry/tools/bcr_mirror.py \
  --estate-root "$VASO_ESTATE_ROOT" --check
```

Here `--offline` uses already materialized pinned mirror files to seed the
estate's Bazel repository cache; it cannot acquire missing network inputs.
`--check` only verifies the mirror. A passing check does not seed a newly
created estate's cache. Initial acquisition without `--offline` is a separate,
explicit provisioning step. Native archives and JAX's nested closure also need
their own provisioning; module-cache seeding is not a complete build-readiness
verdict.

Run the execution preflight against the checkout's actual lock:

```sh
insula/execution/doctor.sh preflight \
  --estate-root "$VASO_ESTATE_ROOT" --line "$VASO_CUDA_LINE" \
  --rootfs-lock foundry/rootfs/cuda_ecosystem.lock.json
```

The selected bundle's recorded lock hash must match. Do not rewrite a manifest
to claim compatibility. Select a matching verified bundle or make an intentional
rootfs change and reverify it. A version label, existing shell binary, or matching
CUDA line alone does not establish compatibility.

## What persists after a command

| Material | Preserve for | Recovery |
|---|---|---|
| Checkout, locks, plans, patches, and recipe provenance | Reproducing the declared build | Restore the exact source snapshot |
| Rootfs bundle, manifest, sidecar, and tool/runtime manifests | Reconstructing the execution filesystem | Pull verified pinned artifacts or reprovision from pinned inputs |
| Source/module/nested archives | Offline builds | Restore the input closure or explicitly reacquire it |
| Release wheels and their dependency closure | Running shipped packages | Pull pinned release artifacts and install offline |
| Logs, results, failed attempts, and acceptance evidence | Audit and release decisions | Back up privately with identity and integrity records |
| Output bases, action caches, and runtime venvs | Faster reuse and local continuity | Optional warm backup; validate before reuse |
| Scratch directories and temporary files | Current execution only | Recreate; do not treat as proof or durable artifacts |
| Leases, processes, and GPU reservations | Live coordination | Re-establish ownership; a backup cannot restore a live reservation |
| Credentials | Authorized acquisition/publication | Provision separately outside source and public artifacts |

Some native state is scratch, while output bases can contain valuable prefixes.
Classify actual files before deleting anything. A cache hit is useful retrieval
evidence, but does not prove a native action executed cleanly in the current run.
Run records and native stamps are not substitutes for the corresponding artifacts
and content identities.

The private GHCR store persists rootfs bundles and wheel release directories.
It does not currently publish a whole-estate archive, the complete native source
closure, all caches, or execution history. See [artifact publication](agents/publication.md#artifact-store).

## Persist portable artifacts

With a verified prepared rootfs, create its content-addressed archive:

```sh
insula/rootfs/bundle.sh --line "$VASO_CUDA_LINE" \
  --estate-root "$VASO_ESTATE_ROOT" \
  --out-dir "$VASO_ESTATE_ROOT/publish/rootfs-$VASO_CUDA_LINE"
```

The bundler includes `rootfs/` and `rootfs-bundle.json`, emits an archive checksum
and sidecar, and preserves the filesystem metadata its format specifies. It
excludes the rest of the estate. Ensure the selected rootfs contains the required
tool/runtime additions before packaging it; an older bundle does not gain later
local additions automatically.

`artifact_store.py push-rootfs` and `push-release` publish those separately,
under the existing privacy and publication rules. Preserve the returned OCI
digests and checksums in the release record. A bundle digest identifies bytes;
it does not independently establish native or GPU qualification.

## Restore: fresh deployment, fresh build, or warm checkpoint

**Fresh deployment:** restore the source recipe, pull rootfs and wheel artifacts
by digest into a new estate, unbundle the rootfs, and use `foundry/insula.sh
--release ...`. It creates a new environment from hashed wheel requirements.
The published runtime rootfs supplies Python. No old build output base is needed.
The [operator guide](operator-guide.md) gives the commands.

**Fresh native build:** restore the exact source snapshot, matching rootfs and
pinned tooling, then the complete acquired input closure. Materialize new writable
state and seed the repository cache. Run readiness checks before offline Bazel
execution. Prior build proof may be reused only under the agreed content-identity
rules; recovering inputs is not proof that the native build or its consumers pass.

**Warm checkpoint:** stop writers and Bazel servers for the owned estate, quiesce
builds, and take a consistent private backup of desired state, outputs, caches,
and evidence. Preserve modes, symlinks, hardlinks, and required filesystem
metadata. Include any external backing trees and custom output/cache paths in
the backup inventory. Vaso currently has no single checkpoint/restore command
for this operation. Restoring a copied directory does not revive old processes
or make stale lease records authoritative; establish new ownership explicitly.

Restore warm state into an unused location. Verify source, rootfs lock and tool
identities, output/input integrity, capacity, and all link destinations before
reuse. Keep the old usable estate until the restored one passes relevant checks.
Bundle extraction replaces its destination after staging, so unbundle into a
fresh line directory rather than a live rootfs.

## Moving backing storage and sharing inputs

Rootfs and acquired sources can be shared as immutable inputs while each estate
owns writable state and outputs. Stop affected execution before changing an
estate's rootfs selection. A source symlink is a dependency on its target, not a
backup: copying the link alone will not restore its contents on another host.
Shared provisioning must also be coordinated; the mirror provisioner can write
registry metadata even when operating offline.

`VASO_BAZEL_OB` and `VASO_BAZEL_DISK_CACHE` select explicit host backing paths.
Their sandbox paths remain fixed; keep the sandbox mountpoints as directories.
Absolute host links inside projected writable state may be unresolvable inside
insula. Never borrow another live build's output base or work tree.

Changing host placement alone must not change native identity. Changing rootfs
contents, toolchains, sources, or flags can change it. Recheck the selected input
closure instead of bypassing capacity or lock checks. See [build stability](agents/build-stability.md).

## Diagnosing incomplete restoration

| Symptom | Check and recovery |
|---|---|
| Missing prepared rootfs | Inspect the explicitly selected estate and line; materialization alone does not install one |
| Estate capacity failure | Check real target volumes and move owned backing storage or make room |
| Rootfs lock mismatch | Select a matching verified rootfs; do not edit its manifest hash |
| `rules_cc` download disabled | Seed the new estate's repository cache from the pinned mirror; `--check` alone is insufficient |
| Missing native or nested archive | Provision the named pinned input before rerunning the offline build |
| Imports resolve to old build prefixes | Restore the complete matching wheel/runtime closure in a fresh environment |
| Passing retry after an earlier failure | Retain both records; decide acceptance from the complete required evidence |

Implementation references: [estate model](../insula/estate/estate.py),
[Foundry execution adapter](../foundry/tools/insula_context.sh),
[build entrypoint](../foundry/run.sh), [rootfs bundler](../insula/rootfs/bundle.sh),
[module mirror](../foundry/tools/bcr_mirror.py), and
[artifact store](../tools/publish/artifact_store.py).
