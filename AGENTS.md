# Working in Vaso

Choose the exploration, implementation, and verification approach that fits
the requested outcome. Carry authorized work through to a verified result.
Use judgment for reversible choices; reuse authority already granted.

Strive for unattended, repeatable end-to-end operation. For workflow changes,
apply the unattended-execution standard in `docs/development.md`.

## Get the build working before integrity machinery

Prioritize a basic working build and repeated unconstrained end-to-end success.
Do not eagerly hash individual source, header, metadata, or installed rootfs
files. Do not substitute broad eager per-file inventory/stat scans or other
premature integrity machinery for hashing. Such work may only be introduced
later, after unconstrained success has been achieved repeatedly and a concrete
need has been established. Existing archive-level checks may remain at the
acquisition boundary; do not add unnecessary verification work. Use only the
small functional checks needed to run the requested workload. Preserve active
builds and never rewrite their checkouts or receipts in place.

## Hermeticity and the execution boundary

In Vaso, hermetic means the Insula capsule supplies the explicitly scoped
sources, dependencies, tools, and toolchains used by the Bazel graph, instead
of relying on ambient host installations. Preserve the full Spack-derived
PyTorch dependency graph and its declared source/build inputs; a reduced
prebuilt-prefix smoke test is not an equivalent production build.

All Bazel actions must use `no-sandbox`: Insula is the execution boundary.
Do not add another Bazel sandbox or treat its absence as a defect. Hermeticity
here does not mean network isolation; retain the existing shared host-network
contract. This does not authorize bypassing permissions, denied access, or
changing security settings. Capsule inputs and estate projections must remain
explicitly scoped under the existing access contract.

## Product and code map

- `insula/`: rootfs, execution, and estate projection. Tools, toolchains, and
  dependencies used by a process must be under its rootfs.
- `foundry/`: hermetic Bazel builds of PyTorch, JAX, Triton, and dependencies,
  executed inside insula. Spack recipes/graphs are adaptation references;
  automating adaptation is optional.
- `tools/`: development coordination, CI, Bazel experiments, and publication.
- `docs/architecture.md` and `GLOSSARY.md`: module interfaces and data ownership.
- `docs/development.md`: coding conventions and verification commands.

Read references relevant to the change. Skills are available methods; there
is no required skill sequence or universal planning/review ceremony.

## Constraints when applicable

- Preserve existing work and coordinate one writer per working tree. Keep a
  checkout fixed while a live build uses it. Shared execution/integration:
  `docs/agents/cross-agent-collaboration.md`.
- Native inputs, toolchains, locks, or build arguments:
  `docs/agents/build-stability.md`. A native rebuild needs an intentional
  source/version, toolchain, or flags change.
- Public publication: `docs/agents/publication.md`. Publish sanitized snapshots,
  never private source history, with user authority for publication.
- Coordinated work needing a durable handoff: `.scratch/<effort>/`;
  `docs/agents/issue-tracker.md` documents the tracker and ledger.

Report what was verified and any material limits. Keep current contracts in
product docs and code; keep incident history out of the default instructions.
