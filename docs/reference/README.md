# Technical references and history

Current product contracts are in `../architecture.md`, `../../insula/README.md`,
and `../../foundry/README.md`. Read the references relevant to the package or
operation being changed:

- `../../foundry/docs/toolchain-and-profiles.md`: compiler/profile contract.
- `../../foundry/docs/pytorch-odr-dependencies.md`: dependency and ABI rationale.
- `../../foundry/docs/recipes/`: package recipes and source provenance.
- `../../tools/bazel-lab/README.md`: executable build/cache experiments.
- `../research/`: dated research, revalidate upstream claims when relying on them.
- `../releases/`: immutable release identity records.

`../rootfs-container-infra-design.md`, `../superpowers/plans/`, Foundry's migration
notes, RFCs, journals, formal migration models, and `.scratch/` effort records
retain their dated design/evidence role. Their historical procedures do not
add requirements to current `AGENTS.md`. Their original locations preserve
citations, Bazel labels, and provenance consumers.
