# Rust Workspace And Shared Types

Status: resolved
Type: task

## Requirement

Implement Task 1 from the accepted insula Rust Temporal implementation plan.

## Scope

- Create a root Cargo workspace.
- Create `crates/insula-types`.
- Define the shared serde-compatible insula request, status, evidence, and
  endpoint records.
- Add focused JSON round-trip coverage.

## Exclusions

- Do not add Temporal dependencies in this task.
- Do not implement runtime process spawning, `bwrap`, `tmux`, supervisor, PTY,
  or CLI behavior in this task.
- Do not convert or remove the existing Python Vaso package.

## Verification

- `cargo test -p insula-types`
- `cargo test --workspace`
- `python3 -m pytest -q`

## Comments

- Baseline note: ambient `pytest -q` resolves through an old Python 2-era
  pytest path on this host and fails before implementation. Use
  `python3 -m pytest -q` for the repository Python tests.

## Answer

Implemented the Task 1 Rust workspace slice:

- added a top-level Cargo workspace;
- added `crates/insula-types`;
- defined the shared serde-compatible insula request, lifecycle, evidence, and
  endpoint records;
- added a JSON round-trip test for `InsulaWorkflowState`;
- kept `Cargo.lock` as the workspace lockfile and ignored Cargo `target/`
  outputs.

Fresh verification passed:

- `cargo fmt --all --check`
- `cargo test -p insula-types`
- `cargo test --workspace`
- `python3 -m pytest -q`

Independent task review found no issues and approved both spec compliance and
task quality.

## Repeat Verification

Repeated the focused verification loop five times using locked Rust
dependencies and an external Cargo target directory:

- `cargo fmt --all --check`
- `cargo test -p insula-types --locked`
- `cargo test --workspace --locked`
- `python3 -m pytest -q`

All five runs passed. The loop did not mutate tracked source files or create
unexpected worktree changes beyond the intended new files.

Additional checks:

- Plain `cargo test --workspace` passed and writes only ignored `target/`
  output.
- Plain `python3 -m pytest -q` passed.
- Plain `pytest -q` failed before running tests because this host resolves
  `pytest` through an old Python 2-era install. Use `python3 -m pytest -q`
  unless the host Python tooling is repaired.
- `git diff --check` reported no whitespace errors.
- A forbidden-scope text scan found no production Rust references to Temporal,
  `bwrap`, process spawning, PTY, supervisor, or attach bridge behavior in this
  Task 1 slice.
