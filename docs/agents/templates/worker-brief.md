# Worker Brief: <effort> / <tickets>

You are a headless traecli worker. Nobody will answer questions mid-run. When
you're blocked, write the question under the ticket's `## Comments`, commit
it, and stop.

## Context

- Repo and worktree: `<worktree>` on branch `<worker-branch>`. It is yours
  alone. Don't touch other worktrees or the primary checkout.
- Read first: `CONSTITUTION.md`, `docs/agents/agentic-engineering.md`,
  `<effort spec>`, and the tickets below.
- Inputs (pinned, read-only): `$VASO_AGENT_IO_ROOT/inputs/…`

## Tickets (in order)

1. `<ticket path>`: <one-line goal>. Verifier: `<command>`.

## Rules

- Commit explicit paths (`git add <paths>`), one commit per ticket slice,
  imperative subject. Never `git add -A`, `--amend`, `rebase`, `reset --hard`,
  `push` or `merge`.
- Disk I/O: `TMPDIR` and `$VASO_BAZEL_OB` are already pinned. Every Bazel
  command uses `--output_base="$VASO_BAZEL_OB"`. Never write under `/tmp` or
  `/var/tmp`. There is no network: use the pinned inputs.
- Tests before behavior changes (a red test first), and keep existing guards
  green. Record evidence (commands, trimmed output, `date -u` timestamps) under
  each ticket's `## Comments`.
- Don't mark a ticket resolved: set `Status: ready-for-review`. The lead
  resolves it.

## Stop condition

Stop when every ticket above is committed and ready for review, or when you're
blocked. Your last message lists each commit (sha and subject), the verifier
results, and open questions.
