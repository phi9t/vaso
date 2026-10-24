# Insula

Rootfs, execution, and estate projection.

- [Insula guide](../docs/insula.md)
- [Code tour](../docs/code-tour.md#follow-a-generic-command): trace a command into the rootfs
- [Rootfs preparation and restore](../docs/code-tour.md#follow-rootfs-preparation-and-restore): locate provisioning, tooling, and bundle identity checks
- [Architecture and data ownership](../docs/architecture.md)
- [Operator workflow](../docs/operator-guide.md)
- [Documentation index](../docs/README.md)

Release restoration uses `tools/release/restore.py` to compose
`insula/rootfs/prepare.sh restore` and probe both lines with `insula/run.sh`.
A legacy root without a preparation receipt must be retained and restored into
a fresh estate: preparation verifies the matching manifests and tooling as one
result. See [candidate qualification](../docs/release-qualification.md).
