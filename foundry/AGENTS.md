# Foundry

`README.md` describes the build interface; `../docs/architecture.md` records
ownership and data contracts. Foundry calls insula for execution. Package
selection, plans, patches, toolchain requirements, and artifact checks belong here.

Before changing native inputs or build arguments, consult
`../docs/agents/build-stability.md` for context. Do not run eager integrity scans as a build prerequisite.
`tools/plan_key_snapshot.py --audit` is a deferred, explicit audit only.
Keep native paths, labels, plan contents,
and sandbox mount paths stable during structural changes.

Recipes under `docs/recipes/` are provenance and package-specific references.
Build acceptance concerns successful hermetic builds and resulting artifacts;
it does not require new Spack captures or migration-ledger progress.
