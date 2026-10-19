# Technical references and history

Start with the current [architecture](../architecture.md),
[Insula guide](../insula.md), and [Foundry guide](../foundry.md).
The articles below explain implementation choices and dated investigations;
they do not add requirements to the current product contracts.

## Build rationale

- [Toolchains and profiles](foundry/toolchain-and-profiles.md): adopted design
  history. Its early restriction on loading Torch and JAX together predates
  the co-residence evidence summarized in [current status](../status.md).
- [PyTorch dependency and ODR analysis](foundry/pytorch-odr-dependencies.md).
- [PyTorch Python/protobuf compatibility](foundry/rfcs/pytorch-python-protobuf-compatibility.md).
- [Triton/JAX roadmap](foundry/rfcs/triton-jax-roadmap.md): historical proposal.
- [Native migration](foundry/native-migration.md): adaptation history.
- [Torch/Triton buildout journal](foundry/journal/2026-10-02-torch-triton-buildout.md).
- [Bazel lab](bazel-lab.md): executable action-key and cache experiments inside insula.

[Package recipe notes](../../foundry/docs/recipes/) remain beside Foundry because
provenance checks consume them as source inputs. Their paths and bytes are
preserved; they are reference data, not a separate operator guide.

## Other references

- [Original rootfs design](../rootfs-container-infra-design.md).
- [GPU wheel research](../research/astral-gpu-wheels.md): dated upstream investigation.
- [Rootfs validation plan](../superpowers/plans/2026-08-20-rootfs-validation-slice.md).
- [Historical release cut](../releases/ddb68ccf.md).

Private effort records and Foundry's migration ledgers/formal models retain
their evidence role. Historical procedures are not current release policy.
