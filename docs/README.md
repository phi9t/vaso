# Vaso documentation

Vaso has two product surfaces: insula owns rootfs execution and estate
projection; Foundry owns hermetic Bazel builds inside insula.

## Using Vaso

- [Operator guide](operator-guide.md): acquisition, execution, and build entrypoints.
- [Insula guide](insula.md): rootfs layout, generic commands, and projections.
- [Estate lifecycle](estate.md): backing storage, build inputs, backups, and restore.
- [Foundry guide](foundry.md): pinned tools, offline inputs, builds, and wheel use.
- [Status and evidence](status.md): tested combinations, qualification results,
  and current limits.

## Developing Vaso

- [Architecture](architecture.md): module interfaces, identities, and data ownership.
- [Glossary](../GLOSSARY.md): product vocabulary.
- [Development](development.md): coding conventions and verification commands.
- [Executable verification](verification.md): acceptance commands and guard regressions.
- [Build stability](agents/build-stability.md): native identity and rebuild constraints.
- [Shared execution](agents/cross-agent-collaboration.md): shared estates and worktrees.
- [Local effort records](agents/issue-tracker.md): tickets, evidence, and ledgers.
- [Publication](agents/publication.md): privacy rules, sanitized snapshots,
  private GHCR artifacts, and retained evidence.

## Deep dives and history

[Technical references](reference/README.md) index the build rationale, recipe
provenance, and experiment catalog. Dated design and research documents retain
their original scope; use current product guides for supported behavior.

- [Original rootfs design](rootfs-container-infra-design.md).
- [GPU wheel research](research/astral-gpu-wheels.md).
- [Rootfs validation plan](superpowers/plans/2026-08-20-rootfs-validation-slice.md).
- [Historical release cut](releases/ddb68ccf.md): artifact identities for that cut,
  not a verdict on the current preview.

Module READMEs link here. Package recipe notes remain at their original Foundry
paths because build-provenance checks consume them as inputs.
