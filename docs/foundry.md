# Foundry: hermetic Bazel builds inside insula

Foundry builds PyTorch, JAX, Triton, and their dependencies using adapted
Spack recipes and dependency graphs. Build execution is always inside insula.
All tools and dependency inputs must be under the selected rootfs; Bazel
additionally declares what each repository rule and action consumes.

Commands below run from the repository root. Paths in the module table are
relative to `foundry/`.

## Build variants unattended

`foundry/build.sh` prepares pinned input caches and runs the selected wheel
builds serially through `run.sh` inside insula. It accepts `--line cu129|cu130|all`
and `--profile torch|jax|all`; defaults are `cu129` and `torch`. The Torch profile
includes PyTorch, torchvision, torchaudio, and Triton. The JAX profile builds
the JAX release-wheel target.

Start with an estate containing rootfs bundles matching the checkout's ecosystem
lock and their pinned Foundry tools. The runner checks capacity, lock identity,
tooling, and execution readiness before building. It does not construct or
silently replace rootfs bundles.

Reuse a prepared estate's hash-verified acquired inputs:

```sh
foundry/build.sh --estate-root "$VASO_ESTATE_ROOT" \
  --line all --profile all --seed-estate /path/to/input-estate
```

The runner reads source/resource pins and repository dependencies from the
checked-in module declarations and selected native package definitions. It seeds
the module and native repository caches, including auxiliary patches/resources.
For JAX it also prepares the declared nested archives and Python wheels in the
local distdir. `--prepare-only` runs this preparation without native builds.
Missing local inputs are listed together in the run's `missing-inputs.json`.

Add `--fetch` to allow separate acquisition phases for declared pinned HTTPS
inputs. Downloads use insula's fetch phase and validate hashes before entering
the cache. Native builds remain offline. Curated archives with placeholder URLs
must be supplied locally, for example through `--seed-estate`; the runner cannot
reconstruct them by downloading a different upstream archive.

To survive terminal disconnection, launch from the repository root:

```sh
mkdir -p "$VASO_ESTATE_ROOT/agents/build-variants"
nohup foundry/build.sh --estate-root "$VASO_ESTATE_ROOT" \
  --line all --profile all --seed-estate /path/to/input-estate \
  > "$VASO_ESTATE_ROOT/agents/build-variants/launcher.log" 2>&1 < /dev/null &
```

Each invocation keeps a unique directory under `agents/build-variants/` with
stage logs, an append-only `events.jsonl`, atomic `summary.json`, and a final
plain-text `result.txt`. The stable `agents/build-variants/latest.json` points at
the newest run and records its phase, current stage, update time, and matrix
state counts. Inspect it without taking the build lock:

```sh
foundry/build.sh --estate-root "$VASO_ESTATE_ROOT" --status
```

When attached to a terminal or tmux, the runner prints colored stage transitions,
30-second elapsed-time and log-tail heartbeats, a final matrix table, and the
release wheel names, sizes, and Bazel-reported digests. `--status-interval`
changes the heartbeat interval; `--color auto|always|never` controls ANSI color.
The same state is written without ANSI escapes for unattended launchers. If a
runner dies before recording a terminal state, the next lock owner marks that
run `abandoned` instead of leaving it indefinitely `running`.

Successful variants include target names, output base, Bazel build-event paths,
and structured wheel artifacts. The runner enables the native action tokens,
requests all outputs, closes stdin, and never prompts. A failed preparation or
build stops the matrix, preserves a bounded log tail in the result, and exits
nonzero. Acquisition may try the next declared mirror, retaining earlier failed
attempts. Passing preparation is not a successful build. A completed build
matrix is not runtime qualification or a promoted release.

Only one variant runner owns an estate at a time; the existing Foundry leases
still coordinate execution. Keep the checkout fixed for the entire run. The
runner checks source fingerprints between stages and rejects changes. Re-running
can reuse valid cached outputs, but does not erase prior failed run records.

## Build interface

With `VASO_ESTATE_ROOT` set to an owned estate, provision a fresh CUDA rootfs,
then its pinned Foundry tools and module inputs.
Acquisition happens before offline execution:

```sh
insula/rootfs/build_rootfs.sh --line cu129
python3 -B insula/rootfs/foundry_tools.py \
  --rootfs "$VASO_ESTATE_ROOT/rootfs-lines/cu129/rootfs" \
  --cache "$VASO_ESTATE_ROOT/sources/foundry-tools" --fetch
python3 -B foundry/tools/bcr_mirror.py --estate-root "$VASO_ESTATE_ROOT"
python3 -B foundry/tools/bcr_mirror.py --estate-root "$VASO_ESTATE_ROOT" --check
```

When reusing an already acquired module mirror in a new estate, run
`bcr_mirror.py --estate-root "$VASO_ESTATE_ROOT" --offline` to seed its
repository cache. `--check` is read-only and does not seed it. See the
[estate lifecycle](estate.md) for restoration and cache distinctions.

The tooling lock is `insula/rootfs/foundry_tools.lock.json`. The installed
`/opt/vaso/foundry-tools.json` records acquired identities and installed file
digests. Execution validates that closure and never selects or copies host
Bazel or Bubblewrap binaries into the sandbox. Existing immutable CUDA roots
can be cloned with `foundry_tools.py --base OLD_ROOT --rootfs NEW_ROOT`;
copy the base's `rootfs-bundle.json` beside the clone as its CUDA-layer identity.
Keep the original root unchanged. A populated tools prefix is verified and
never repaired during execution.

From the repository root, with a prepared estate and source caches:

```sh
export VASO_ESTATE_ROOT=/path/to/estate
export VASO_CUDA_LINE=cu129
export VASO_PROFILE=torch
foundry/run.sh --bazel-build //native/pytorch:pytorch_action \
  --//native/pytorch:token=build-native-pytorch
```

The token explicitly enables the expensive native action; it is not a credential.

Select `VASO_PROFILE=jax` for the JAX graph. `run.sh --bazel-build` builds the
requested targets from the checked-in profile lock; it does not need a new
Spack capture or automated adaptation. Inspect each package's `BUILD.bazel`
for wheel and verification targets and explicit expensive-build controls.

JAX's nested Bazel build resolves Python wheels only from the pinned local
distdir. `run.sh --jaxlib-prefetch` provisions the archive and Python wheel
closure before offline execution. `VASO_JAXLIB_MAX_JOBS` selects up to 96 CPU
slots through lease-owned runtime state; this scheduling choice is outside
native input identity.

Release qualification combines native builds, ABI/provenance checks, wheel
packaging, and GPU workloads:

```sh
tools/agents/triumvirate-gate.sh --release --profile torch --line cu129 \
  --acceptance-out /path/to/owned/acceptance.json
```

Single-host distributed qualification uses numeric loopback rendezvous and
loopback interfaces, so it does not require DNS in the isolated network.

`insula/rootfs/build_rootfs.sh` provisions the rootfs. Foundry's
`insula.sh` is the canonical build/release execution entrypoint.
`tools/insula_context.sh` prepares build/release-specific projections and
calls the generic launcher. Existing `scripts/insula/insula.sh` callers
retain a compatibility adapter. Generic insula commands require neither a
build profile nor a package lock.

## Deploy published wheels

The [artifact inventory](../tools/publish/published-artifacts.json) pins the private GHCR wheel releases
and rootfs bundles by digest. Pull them with `artifact_store.py`, unpack the
bundle into a new estate's `rootfs-lines/<line>`, and run:

```sh
export VASO_ESTATE_ROOT=/path/to/fresh-estate
foundry/insula.sh --line cu129 --release "$VASO_ESTATE_ROOT/downloads/release-torch-cu129" \
  --gpus 1 -- python3 -c \
  'import torch, triton; assert torch.cuda.is_available(); print(torch.__version__, triton.__version__)'
```

The release runner uses the bundled `/opt/vaso/python` interpreter, creates a
new venv, and installs the release's hashed requirements offline. It creates
lease state on the fresh estate. A published deployment requires no Bazel
output base, source-build prefixes, or host Python packages. GPU driver/device
projections remain explicit runtime inputs. Use a separate release directory
and venv for the JAX profile.

The committed [deployment verification report](../tools/publish/fresh-estate-verification.json)
records digest pulls and single-GPU checks on both CUDA lines. The
[multi-GPU report](../tools/publish/multigpu-qualification.json) and
[CUDA 13 JAX retry](../tools/publish/cu130-jax-qualification-retry.json) retain
the original timeout and later passing attempt separately. See
[status and evidence](status.md) for the limits of these results.

## Module and data ownership

| Path | Contract |
|---|---|
| `MODULE.bazel`, `tools/graph_repo.bzl` | Materialize checked graph nodes as Bazel repositories |
| `locks/` | Profile package, version, source, and dependency selections |
| `native/` | Adapted package rules, patches, and shared plan executor |
| `plans/` | Committed plans, schema, and native input/key snapshots |
| `toolchains/` | Declared compiler/tool requirements and input closures |
| `workloads/` | Checks of built artifacts, ABI, functionality, and GPUs |
| `examples/` | Consumers exercising prefix/wheel interfaces |
| `tools/` | Build-contract validation and graph materialization |
| `docs/recipes/` | Package-specific reference recipes and provenance |

`insula/` owns estate placement, rootfs construction, and generic execution.
Compatibility paths under `rootfs/` and `tools/{estate,overlay_root}.py` expose
the same implementation to existing Bazel labels and callers. The ecosystem
lock remains at its existing input path and is linked from insula, preserving
native input identity.

A profile selects packages and compiler policy; a CUDA line selects an
ecosystem family. The current compiler policy uses rootfs GCC for torch and
Triton's host code, and rootfs Clang for jaxlib. LLVM library/source identity
is a separate contract. `tools/compiler_pathways.json`, selected plans, and
[toolchain decision history](reference/foundry/toolchain-and-profiles.md) record the details.

## Verification and build stability

Read [build stability](agents/build-stability.md) for native changes. Run the impact
check and plan-key snapshot before a build; use an owned output base and
preserve fixed sandbox paths. [development](development.md) describes host checks;
[verification](verification.md) documents operational acceptance. GPU qualification
requires the corresponding workload/release evidence.

## Consumer examples

[Consumer examples](../foundry/examples/consumers/) exercise native prefixes
and wheels outside their package definitions: C/C++, Rust/Python, Python wheel
use, CUTLASS GEMM, rasterization, scatter, and NVSHMEM paths. They provide checks
and examples, rather than a published downstream SDK.

## Reference material

Spack is a source of recipe knowledge. Checked-in definitions must build
successfully and hermetically; adaptation does not need automation. Keep
recipe provenance and graph-to-Bazel materialization. Capture/frontier tools,
`migration_ledger.json`, and `formal/build_migration/` remain optional historical
adaptation tools, outside the normal `--bazel-build` acceptance path. Older
migration/RFC documents describe their dated scope, not additional products.

`VASO_BAZEL_OB` and `VASO_BAZEL_DISK_CACHE` can select absolute, owned host
backing directories on separate disk volumes. Their sandbox paths remain
`/vaso-bazel-ob` and `/vaso/lines/<line>/cache/bazel/disk-cache`; changing host
placement does not change native identity. Keep the estate's sandbox mount
points as directories, since an absolute host symlink cannot resolve inside
the rootfs.
