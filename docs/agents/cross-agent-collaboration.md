# Shared execution and integration

Use this reference when agents share an effort or a long-running execution
estate. Independent work does not need a lead/follower ceremony.

One writer owns each working tree. Coordinate scope and file ownership before
concurrent edits. Each worker uses an isolated worktree and owned disk-backed
scratch/output bases. Keep the checkout and source tip fixed while a live
build uses them. Never change another worker's files or state to make a test pass.

The integrating agent verifies the candidate tip, then advances the shared
target by fast-forward. If it diverges, reconcile in the integration worktree
and reverify. Do not rewrite the shared target or infer publication authority
from a worker brief. The user's authority for the effort governs integration.

## Relay tooling

`tools/agents/relay.py --help` describes the optional coordination machinery.
It supports watching state, rebasing and verifying an integration candidate,
synchronizing tracker files, recording content manifests, and landing.

```sh
python3 tools/agents/relay.py rebase --lead-worktree <worktree> \
  --target <branch> --verify '<verification command>'
python3 tools/agents/relay.py land --landing <branch> \
  --tracker .scratch/<effort> --verify '<verification command>'
```

The landing ref points only at a verified tip. Rerun verification after
reconciliation or rebase. Cached results are evidence only when keyed to the
same inputs. Autoland requires a passed gate summary for the exact tip, with
the tool's bounded no-gate allowlist for intervening support/docs changes.

When replacing dirty state, first record content hashes with `relay.py manifest`.
Landing refuses changed or overlapping unaccounted files. `--require-file PATH`
enforces required landing content. `sync-tracker` detects concurrent edits;
use it instead of blindly copying another agent's tracker.

For coordinated issues, use `relay.py ticket set` and `ticket note` to update
status and evidence through the ledger. `issue-tracker.md` defines its scope.
A monitor's summary is a pointer to evidence; the integrating agent checks
actual commits, inputs, logs, and results.

## Optional workers and handoffs

`tools/agents/traecli_worker.py` launches bounded headless TRAE work. Its CLI
owns model defaults, isolation, waits, and event mechanics. Use it when the
user authorizes that worker; its availability does not require delegation.
The brief names the outcome, owned paths, inputs, granted authority,
verification, and stop condition. Templates under `templates/` are optional.

Use `worker-wait.sh` or the launcher's `wait` command for worker events. A
supervisor observes and reports; the integration owner changes branches.
Worker follow-ups use a new brief/run after the existing run releases ownership.
The optional `trae_ctl.py` app-server control channel is separate from these
headless runs; never send prompts to a task without user authorization.

`VASO_AGENT_IO_ROOT`, `TMPDIR`, and `VASO_BAZEL_OB` should identify the worker's
owned disk-backed area. Preserve run evidence and distinguish a failed
prerequisite from a passed acceptance run. Authority to integrate does not
permit deleting another agent's state or publishing private history.
