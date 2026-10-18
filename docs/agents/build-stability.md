# Native build stability

A native package rebuilds only when its source/version, toolchain, or flags
change. Native torch, torchvision, torchaudio, Triton, and jaxlib builds take
hours; structural and support-tool changes must preserve their inputs.

## Inspect the actual inputs

From the repository root:

```sh
python3 -B foundry/tools/rebuild_impact.py --repo-root . --fast --base HEAD --worktree
python3 -B foundry/tools/plan_key_snapshot.py --check
```

Fast impact is conservative. If it reports a possible rebuild, inspect the
matched inputs and compare exact action signatures where needed: the action
key, argv, environment, and declared input paths. Compare actual input digests
and the tool/runfiles closure too. An unchanged `actionKey` alone is
insufficient. `tools/bazel-lab/` contains the supporting experiments.

Capture queries through the same Foundry entrypoint as builds; Bazel runs
inside insula with the selected profile and rootfs:

```sh
foundry/run.sh --bazel-aquery \
  'mnemonic("ZlibNgActionPrefix", deps(//native/zlib_ng:zlib_ng_action_prefix))' \
  --output=jsonproto --output_file=/vaso/agents/<owner>/actions.json
```

For retained JSON, use `rebuild_impact.py --exact --insula --package zlib-ng`
with `--aquery-json-base`, `--aquery-json-head`, and the corresponding
`--aquery-output-base-base` and `--aquery-output-base-head`. Those output bases
must still contain the queried inputs. Explicit output bases are authoritative
for generated and external inputs; a checkout link cannot substitute for a
missing snapshot input. `--snapshot-dir` writes both action
signatures and actual input digests to a new evidence directory. Missing
actions or unreadable inputs prevent a verified unchanged result. Materialized
directory inputs include nested names, file contents, executable bits, and
symlink targets and their contents. Host placement, owner, and timestamps
do not affect those digests; cycles and unsupported file types remain
unverified. Git source inputs include executable mode and resolve source
aliases in the selected snapshot. Run live
queries inside insula; retained JSON comparison itself does not execute Bazel.

Cached builds can leave intermediate files and directory contents
unmaterialized. Build the queried targets with `--remote_download_outputs=all`
before comparing their input contents. An aquery captures metadata and does
not fetch missing artifacts.

Release-wheel repair declares the native Torch directories whose libraries
it may vendor, including through platform-forwarding rules. A prefix path
in provider metadata does not materialize a cached output. Determinism runs
download all outputs and reject missing dependency-manifest evidence; their
directory snapshots preserve symlink types as well as contents.

Relevant inputs include committed plans, ordered patches/source digests,
`native/common/` executor and toolchain helpers, `toolchains/`, profile locks,
rootfs ecosystem identity, and build/gate arguments. Repository-rule prefixes
can change a consumer's input digest through `prefix_manifest.json` even when
its action key stays constant. Moves can change labels; retain native paths
and labels during structural work.

Host estate paths, output-base locations, job counts, planner metadata,
commit SHA/time, release suffixes, credentials, and ambient environment are
outside native identity. Put release stamping and provenance in cheap release
steps. A native-key change caused only by those values needs a fix before
landing; batching rebuilds does not fix the dependency.

Repository builds use `CC=gcc` and `CXX=g++`, set explicitly by the shared
native argument helper. Insula supplies their rootfs PATH. Compiler spelling
is part of generated OpenSSL build information and Python configuration;
callers must not substitute absolute paths or inherit ambient compiler names.
Native repository rules that execute builds declare both variables in
`repository_rule(environ=...)` so a compiler-setting change invalidates an
existing repository as well as taking effect in a fresh output base.

For an intentional rebuild, record the packages and source/version, toolchain,
or flags change in the commit/evidence. Batch necessary rebuilds between gate
runs. Keep a checkout fixed while a live build uses it, including for docs changes.

## Hermetic, clean execution

Tools, toolchains, and dependencies must be under the selected insula rootfs.
Bazel declares each action's inputs separately. Provision pinned sources in
advance; builds and standing tests run offline. Each native action is a cache
hit or a clean execution of its plan. Reusing another action's CMake/Ninja
work tree can silently mix flags.

Use one owner for native Bazel arguments: the committed plan and
`foundry/tools/native_determinism_args.sh`. Keep native input bytes stable
when changing callers. Inspect `tools/rebuild_impact.py` diagnostics rather
than adding another source of flags.

The fixed output-base mount `/vaso-bazel-ob` is part of the artifact contract
for prefix symlinks, shebangs, venvs, and pkg-config. CUDA wheel builds stage
sources at `/vaso/build/<package>-<line>/src` because nvcc lacks the host
compiler's prefix-map support for device `__FILE__`. Plans check ownership
and remove staging after install. Generated CMake configs locate themselves
with `CMAKE_CURRENT_LIST_DIR`, not an action-local install prefix.

For a leak failure, run `foundry/tools/leak_sweep.py` on the whole leftover
output before fixing it; address the leak class together. Never point tests
or nested Bazel at live line output bases, disk caches, work trees, releases,
or another agent's state. Use owned disk-backed scratch and output bases.

## Rootfs and version changes

Check source/version pins, compiler and LLVM identities, Python/ABI, runtime
libraries, feature scope, selected profiles/lines, and relevant acceptance
workloads when changing a package or rootfs version. Resolve product/version
choices outside the granted scope with the user. Inspect actual available
CPU/RAM/GPU/disk capacity before expensive runs; leave sufficient space for
sources, temporary trees, prefixes, caches, and evidence.

The rootfs implementation is `insula/rootfs/`; the ecosystem lock retains its
`foundry/rootfs/cuda_ecosystem.lock.json` input path for native stability.
Build stability incidents and old plans remain historical evidence under
`.scratch/` and Foundry's dated reference documents.
