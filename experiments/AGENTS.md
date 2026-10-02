# Experiments Agent Guidance

Follow the repository root `AGENTS.md`, `CONSTITUTION.md`, and
`docs/agents/agentic-engineering.md`.

## Native Package Captures

For `spack-bazel-graph` migration work, land each verified native package
capture as its own package-scoped change before starting the next package. A
package capture is not done while its source, tests, recipe notes, generated
lock/graph updates, or ledger entries remain only as an uncommitted dirty
worktree.
