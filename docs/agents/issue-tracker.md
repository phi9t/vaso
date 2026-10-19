# Local effort records

Use `.scratch/<effort>/` when work needs durable intent, coordination, or a
handoff. A trivial edit does not need a ticket tree. `spec.md` records the
outcome, scope, authority, and acceptance; `issues/NN-<slug>.md` holds individual
issues when useful. Keep research and historical evidence outside active policy.

An issue has a `Status:` line near the top. Common states are `open`, `claimed`,
`blocked`, `ready-for-review`, and `resolved`; the complete existing vocabulary
and required evidence fields are owned by `foundry/tools/tracker_lint.py`.
Dependencies use `Blocked by: NN, NN`. Preserve existing records and statuses.

For coordinated work, use the ledger commands rather than editing generated
status/evidence blocks:

```sh
python3 tools/agents/relay.py ticket set .scratch/<effort>/issues/01-example.md --status claimed
python3 tools/agents/relay.py ticket note .scratch/<effort>/issues/01-example.md --evidence 'command and observed result'
```

The append-only `ledger.jsonl` renders the status and bounded
`Evidence (ledger)` section. Free-form discussion can append under `Comments`.
A map/frontier and lead/follower handoff are optional tools for efforts that
need them, not prerequisites for exploration.

Validate the intended scope with:

```sh
python3 -B foundry/tools/tracker_lint.py --tracker-root .scratch \
  --allowlist foundry/tools/tracker_lint_allowlist.txt
```

Missing roots and accidental empty scans fail. `--allow-empty` applies only to
an explicitly intended existing empty directory. Historical allowlists retain
reasons and are checked for stale entries.
