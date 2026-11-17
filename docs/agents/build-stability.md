# Native build stability

A native package rebuilds only when its source/version, toolchain, or flags
change. Native torch, torchvision, torchaudio, Triton, and jaxlib builds take
hours; structural and support-tool changes must preserve their inputs.

## Keep early validation small

First achieve a basic working build and repeat unconstrained end-to-end success.
Inspect selected versions, flags, declared paths and command output when needed.
Do not eagerly hash source files, headers, metadata, installed files, or directory
trees. Do not replace hashing with broad eager inventory/stat scans. Historical
exact-input, recursive snapshot, and determinism workflows are deferred until
repeated success and a demonstrated need; they are not prerequisites for a build.
Existing archive checks at acquisition may remain, but add no unnecessary
integrity work. A cheap availability check is not proof of byte-level integrity.

Relevant declared inputs include plans, source archive pins, ordered patches,
executor/toolchain helpers, profile locks and build arguments. Preserve native
paths and labels during structural changes, but do not run recursive digest
comparisons merely to demonstrate that preservation. Do not claim unchanged
artifact contents based solely on metadata.

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
