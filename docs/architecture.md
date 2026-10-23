# Two product surfaces

Vaso has two products: insula and Foundry. All tools, toolchains, dependencies,
and other inputs relevant to execution must be under the rootfs seen by the
process. Host directories provide backing storage and explicit projections.
Bazel separately declares the files and tools each build action consumes.

## Code ownership

```text
insula/
  rootfs/        filesystem materialization, bundles, identity, validation
  estate/        host backing layout, placement, projection, path translation
  execution/     Bubblewrap launch, readiness, environment, leases, results
    vaso/        Python readiness and legacy host-preflight package
foundry/
  MODULE.bazel
  native/        adapted rules, patches, shared executor
  plans/         committed build plans, schemas, key snapshots
  locks/         package/dependency selections
  toolchains/    declared Bazel toolchain inputs
  workloads/     artifact, ABI, and GPU verification
  examples/      consumers
  tools/         build validation, graph materialization, build/release context
tools/
  agents/        optional development coordination
  ci/            verification orchestration
  bazel-lab/     build-system experiments
  publish/       delivery administration
  testing/       host test collection tools
scripts/release/ candidate selection, restore, qualification, and release cutting
```

Tests live beside their owner. `docs/development.md` records coding conventions
and verification; `docs/reference/` indexes the retained technical/history
references. Support tooling is outside the product interfaces.

## Foundry calls insula

Foundry selects sources, package graphs, plans, compiler requirements, Bazel
arguments, and outputs. Its context adapter prepares explicit argv,
environment, working directory, rootfs, read-only inputs, writable outputs,
and resource needs. Insula plans projections, checks readiness, executes,
and returns status/evidence. It does not select a torch/JAX profile or validate
a package lock for a generic command.

`insula/execution/insula.sh` implements generic execution. `foundry/insula.sh`
loads the Foundry adapter in `foundry/tools/insula_context.sh`; the old
`scripts/insula/insula.sh` forwards existing build/release callers to it.
Insula does not import that adapter.
“All builds under insula” describes execution; the two source directories
remain peers. The Python host-layout CLI is a legacy preflight scaffold,
not an independent production sandbox implementation.

## Rootfs and hermeticity

The rootfs is the complete filesystem presented to the process: its prepared
base plus explicitly declared source, dependency, state, and output projections.
Tools and dependency paths resolve within it. The standalone insula CLI clears the
environment, uses only rootfs tool paths, mounts the project read-only, and
isolates networking for the run phase. A fetch phase requires an explicit
SHA-256 pin and a network-fetch lease. Rootfs provisioning happens before
normal execution.

`insula/rootfs/foundry_tools.lock.json` selects the build orchestrator and its
verification tools by version and archive digest. Its provisioner prepares a
fresh `/opt/vaso` prefix in staging, verifies it, and installs it atomically.
Installed file digests are recorded in `/opt/vaso/foundry-tools.json`. Staging
failures leave the final prefix unchanged and remove partial files.
An existing populated prefix is verified rather than merged with new files.
With `--base`, the whole hard-linked clone and its tooling are prepared in
staging. Pinned inputs are checked before cloning, and the new rootfs appears
at its destination only after verification. Failed attempts retain the base and
leave no partial rootfs at the requested destination.
The CUDA bundle manifest identifies the base
layer; the tooling manifest identifies this addition. Execution verifies the
installed closure and never copies an ambient host Bazel into it. Offline
module inputs use the pinned BCR manifest and a read-only verification step.

The sourced execution interface accepts caller-defined projections and network
settings. Its caller owns those choices and the hermeticity of the resulting
request; standalone defaults do not override a prepared Foundry context.

The estate backs the rootfs, source trees, state, caches, and outputs. Record
content identity separately from host location, sandbox path, and access mode.
A read-only bind alone cannot stop another host process changing backing bytes.
Generated dependencies are declared outputs built inside insula and consumed
there through stable paths. Driver/device projections are explicit runtime
inputs to GPU execution.

Filesystem containment and Bazel hermeticity are both required. A path or
version string alone does not declare file contents to Bazel. Action keys,
input digests, tool closures, and resulting artifacts determine build evidence.
The generic CPU acceptance suite does not qualify a GPU/native build.

## Data model

| Owner | Data and implementation |
|---|---|
| Insula rootfs | CUDA ecosystem identity, rootfs manifests and bundle digests; `insula/rootfs/` |
| Insula estate | `VasoEstate` and line-specific backing paths; `insula/estate/estate.py` |
| Insula projection | Ordered host-to-sandbox mappings and access modes; `insula/estate/overlay_root.py`, `path_map.py` |
| Insula execution | Command argv/environment/cwd, namespace settings, leases, status/log/result evidence; `insula/execution/` |
| Foundry | Profile/line selection, sources/patches, locks, plans/schema, declared action dependencies, prefix/wheel artifacts, qualification |
| Support | Worker/CI coordination, adaptation history, publication administration; outside normal native input identity |

Release orchestration keeps a candidate selection separate from attempts and
promotion. `scripts/release/candidate.py` owns the immutable selection schema
and payload checks. Qualification policy selects Foundry workloads; the runner
owns attempts, while GPU monitoring and command execution have separate
implementations. The [workflow ownership table](release-qualification.md#maintaining-the-workflow)
identifies those files and the evidence they produce.

The rootfs identity is separate from a build profile. A profile selects a
compatible package/compiler/feature set; a CUDA line selects an ecosystem
family. `GLOSSARY.md` fixes the vocabulary. Native plan schemas and selected
compiler policy live with Foundry, rather than in one shared configuration
object spanning both products.

## Compatibility and native stability

`foundry/native/`, `plans/`, `locks/`, and `toolchains/` retain their paths,
labels, and input bytes. Existing sandbox paths, including `/vaso-bazel-ob`
and `/workspace/experiment`, remain stable for Foundry. Read-only sibling views
at `/workspace/insula` and `/workspace/foundry` keep relative compatibility
links valid from the legacy experiment mount. The supported
`scripts/insula/insula.sh` CLI and `harness/vaso` import path remain. Support
implementation lives under `tools/`; unused helper, CI, publication, testing,
agent, and Bazel-lab aliases have been removed.

Two files retain their original physical paths to preserve native-key-sensitive
inputs: `foundry/rootfs/cuda_ecosystem.lock.json` and
`scripts/agents/triumvirate-gate.sh`. Insula/tools expose them through links.
Other legacy rootfs/estate paths link to their owning implementation. There
is one copy of each implementation and each selected lock.

## Spack reference material

Keep manually maintained build definitions, recipe provenance, checked graph
locks, compatibility constraints, and `foundry/tools/graph_repo.bzl` graph
materialization. Adapting Spack recipes/graphs does not need automation.
Capture/frontier scheduling, provider-flip ledgers, and historical formal
migration models remain optional support. Normal adapted-target acceptance
concerns declared inputs and successful artifact/ABI/workload behavior; it
does not require a fresh Spack installation or replay of migration history.
