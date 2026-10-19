# Foundry: hermetic Bazel builds inside insula

Foundry builds PyTorch, JAX, Triton, and their dependencies using adapted
Spack recipes and dependency graphs. Build execution is always inside insula.
All tools and dependency inputs must be under the selected rootfs; Bazel
additionally declares what each repository rule and action consumes.

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

The tooling lock is `../insula/rootfs/foundry_tools.lock.json`. The installed
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
foundry/run.sh --bazel-build //native/pytorch:pytorch_action_prefix
```

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

`../insula/rootfs/build_rootfs.sh` provisions the rootfs. Foundry's
`insula.sh` is the canonical build/release execution entrypoint.
`tools/insula_context.sh` prepares build/release-specific projections and
calls the generic launcher. Existing `../scripts/insula/insula.sh` callers
retain a compatibility adapter. Generic insula commands require neither a
build profile nor a package lock.

## Deploy published wheels

`../tools/publish/published-artifacts.json` pins the private GHCR wheel releases
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
records the tested artifact digests and single-GPU checks on both CUDA lines.

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

`../insula/` owns estate placement, rootfs construction, and generic execution.
Compatibility paths under `rootfs/` and `tools/{estate,overlay_root}.py` expose
the same implementation to existing Bazel labels and callers. The ecosystem
lock remains at its existing input path and is linked from insula, preserving
native input identity.

A profile selects packages and compiler policy; a CUDA line selects an
ecosystem family. The current compiler policy uses rootfs GCC for torch and
Triton's host code, and rootfs Clang for jaxlib. LLVM library/source identity
is a separate contract. `tools/compiler_pathways.json`, selected plans, and
`docs/toolchain-and-profiles.md` record the details.

## Verification and build stability

Read `../docs/agents/build-stability.md` for native changes. Run the impact
check and plan-key snapshot before a build; use an owned output base and
preserve fixed sandbox paths. `../docs/development.md` describes host checks;
`../docs/verification.md` documents operational acceptance. GPU qualification
requires the corresponding workload/release evidence.

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
