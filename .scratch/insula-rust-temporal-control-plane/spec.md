# Insula Rust Temporal Control Plane

Status: ready-for-agent

## Outcome

Add the first Rust production slice for insula in Vaso while preserving the
existing Python package. This branch implements only the shared type crate from
the accepted insula plan.

## Source Of Intent

- Design:
  `/data02/home/philip.yang/workspace/linux_kernel/Documentation/mts-research/11-insula-temporal-rust-control-plane.md`
- Implementation plan:
  `/data02/home/philip.yang/workspace/linux_kernel/Documentation/mts-research/12-insula-temporal-rust-implementation-plan.md`

## Route

Gated path. This is new infrastructure and a public Rust workspace boundary, so
it is not a short-path change. The Matt owner skills are not directly invocable
in this harness; apply the route from `docs/agents/skill-orchestration.md`
directly and record progress in this tracker plus the SDD ledger.

## Acceptance Criteria

- A top-level Cargo workspace coexists with the current Python project.
- `crates/insula-types` builds as a Rust library with no Temporal dependency
  and no process-spawning behavior.
- The crate exposes serde-compatible shared records for rootfs profiles, attach
  policy, tmux policy, lifecycle, root/tmux records, attach endpoints, rootfs
  evidence, and workflow state.
- The shared state round-trips through JSON in a focused Rust test.
- Existing Python tests still pass under the Python 3 test command.

## Authority Boundary

The agent may edit and test this isolated branch. Commit, push, pull-request
creation, and merge each require explicit human authorization.
