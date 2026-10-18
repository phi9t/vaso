# Cross-Agent Collaboration: Lead and Follower

How two coding agents work the same effort in one repository at the same time,
without editing each other's working tree. Any agent can take either role,
whether Claude Code, TRAE, Codex or another. The human keeps decision and merge
authority (`docs/agents/agentic-engineering.md`, `CONSTITUTION.md`).

The machinery is `scripts/agents/relay.py`, tested in
`tests/test_agent_relay.py`. The templates are in `docs/agents/templates/`.

## When to use it

Use it when one agent owns a long-running execution environment and another
agent does planning, tooling, integration and review in parallel. The
execution environment might be the sealed insula, a GPU box, or a checkout with
hours-long builds. If both agents only edit code, give them separate tickets and
worktrees under the normal gated path instead.

## Roles

| | Lead | Follower |
|---|---|---|
| **Works in** | Its own worktrees only (`.worktrees/<name>`) | The primary checkout, and the long-running environment (for example `./run.sh`) |
| **Owns** | Specs and tickets, guards and tooling, the landing branch, review of follower commits | Captures, builds and insula proofs, its tickets |
| **Integrates by** | Keeping the landing branch rebased on the target and green | Fast-forwarding the landing branch into the target at turn boundaries |
| **Never** | Edits the follower's tree or the target ref; runs the follower's environment (for example the shared Bazel output base) | Merges non-ff or rebases the lead's branch; edits `.worktrees/*` |

The human decides direction, authorizes landing, relays the first message to the
follower, and settles anything either agent escalates.

## Invariants

1. **One writer per working tree.** No agent edits, restores, stashes or moves
   files in a tree another agent is using. That includes "helpful" cleanup and
   syncing into it while it's mid-turn.
2. **The target branch moves only by fast-forward.** The follower commits to it;
   landing is `git merge --ff-only <landing>`. If the fast-forward refuses, the
   lead rebases. The follower never force-merges.
3. **Durable intent lives in the tracker** (`.scratch/<effort>/`, see
   `docs/agents/issue-tracker.md`), not in chat or agent memory. Messages
   between agents are files, and the human relays only the first message.
4. **Guards are the contract.** Rules that matter are tests that fail on both
   agents' commits. Examples: exact ODR keys, provider version parity, the
   Python ABI literal ratchet. Neither agent negotiates them in prose.
5. **Only discard what you have proven superseded.** Before the follower drops
   dirty files, the lead records their content hashes (`relay.py manifest`);
   `relay.py land` refuses if anything else is dirty or the content has
   changed. A follower may opt in to
   `relay.py land --allow-dirty-non-overlap` only for tracked dirty paths that
   do not overlap the landing diff; superseded manifest entries, overlapping
   tracked paths and untracked paths the landing would create still refuse.
6. **The landing ref only ever points at verified commits.** The follower
   can fast-forward at any moment, so rebase and verify a detached HEAD and move
   the branch last (`relay.py rebase` does this). Never commit known-red state
   to the landing branch, even briefly.
7. **Green means the lead ran it on the exact tip.** Rerun the check after every
   rebase. A pass on a different or half-rebased tree doesn't count. Bazel's
   content-addressed cache hits are valid, because they're keyed on the exact
   inputs; hand-copied "it passed earlier" results are not.

## Artifacts

In `.scratch/<effort>/`:

- `spec.md` and `issues/NN-*.md` are the tracker. Specs stay human-authored.
  Issue status and evidence are written through `relay.py ticket`; free-form
  coordination notes still append under `## Comments`.
- `.scratch/<effort>/ledger.jsonl` is the append-only issue ledger. `relay.py
  ticket set <issue.md> --status <status>` appends a locked status record and
  re-renders only the issue's `Status:` line. `relay.py ticket note <issue.md>
  --evidence '...'` appends a locked evidence record with a `date -u`
  timestamp and re-renders only the generated `## Evidence (ledger)` block
  bounded by `<!-- relay-ticket-ledger:start -->` and
  `<!-- relay-ticket-ledger:end -->`. Do not hand-edit that generated block.
- `lead.md` is the lead state, built from the `lead-state.md` template. It is
  everything a new lead needs to take over (see "Lead handover").
- `<follower>-handoff.md` holds instructions for the follower, built from the
  `follower-handoff.md` template: a landing protocol at the top, ownership,
  freeze and guards, ordered work, and an `## Acknowledgement` section.
- `reviews.md` is the lead's one-line-per-commit review log of follower commits.

Lead-private state lives in the lead's scratch space, never in the repo: the
`sync-tracker` state file and the backups of superseded follower state.

## Protocol

### 1. Bootstrap (lead)

1. Inspect the follower's tree read-only: `git status`, recent commits, running
   processes. Identify its in-flight work and what it overlaps with.
2. Write the spec, tickets and handoff. Name the landing branch
   (`vaso/<effort>`) and create the lead worktree from the target tip.
3. If the follower has in-flight work the lead is taking over, snapshot it
   read-only (`git diff` plus copies of untracked files into lead scratch),
   harden it on the landing branch, then record the manifest:
   ```sh
   scripts/agents/relay.py manifest --follower <follower checkout> \
     --landing vaso/<effort> --tracker .scratch/<effort> \
     --superseded <paths…> --out .scratch/<effort>/landing-manifest.json
   ```
4. Commit the tracker on the landing branch. Until the first landing, keep an
   identical untracked copy in the follower checkout so the follower can read
   it, using guarded sync:
   ```sh
   scripts/agents/relay.py sync-tracker --lead-worktree .worktrees/<effort> \
     --follower <follower checkout> --tracker .scratch/<effort> \
     --state <lead scratch>/sync-state.json
   ```
5. Ask the human to relay one message to the follower: "Read
   `.scratch/<effort>/<follower>-handoff.md` and follow its landing protocol at
   turn start, every turn boundary and after each commit." The message is
   delivered when the follower's current turn ends, not before.

### 2. Steady state

**Lead: watch.**

```sh
scripts/agents/relay.py watch --follower <follower checkout> \
  --target <target branch> --tracker .scratch/<effort> \
  --handoff .scratch/<effort>/<follower>-handoff.md \
  --follower-pid <pid> --busy-cmd '<exits 0 while mid-turn>'
```

The busy probe depends on the follower. TRAE CLI holds a `systemd-inhibit`
child during a turn: `pstree -p <pid> | grep -q systemd-inhibit`. Put the probe
you use in `lead.md`.

**Lead: runbook.**

| Event | Action |
|---|---|
| `TARGET_MOVED` to a follower commit | Review it against the tickets, freeze and guards, and add a line to `reviews.md`. Run `relay.py rebase --lead-worktree … --target … --verify '<host test suite>'`. It moves the landing branch only after verify passes. On `REBASE_CONFLICT` or `VERIFY_FAILED`, fix on the detached HEAD (keep both sides for additive files, and add follow-up fixes such as allowlists), rerun the verify, then `git checkout -B <landing>`. |
| `TARGET_MOVED` to the landing tip | The follower landed. Check the reflog shows a fast-forward, the tree holds only expected untracked files, the acknowledgement is present, and the pre-merge copy is gone. |
| `FOLLOWER busy -> idle` | A turn boundary. Make sure the landing branch is committed, rebased and green now; the follower may land within seconds. |
| `TRACKER_CHANGED` | If it matches the lead's sync state, the lead caused it; ignore it. Otherwise the follower edited the tracker: read it, and never overwrite it. |
| `ACK_CHANGED` | Read the acknowledgement and check its HEAD and test result. |
| `FOLLOWER_DELETED_TRACKED` | Check whether the target or landing branch still needs the file. If it does, ask the human to relay `git restore -- <path>` right away, and fix whatever instruction caused it. If the follower commits the deletion, restore the file by committing it again on the landing branch. |
| `FOLLOWER_EXITED` | Stop. Report to the human. |

**Follower: choose work at every slice start; land at turn start, at every turn
boundary, and after each commit.**

1. At turn start, run `relay.py land --dry-run`. If it is OK, land before
   starting new work.
2. Before starting a new work slice, run:
   ```sh
   scripts/agents/next-package.py --tracker .scratch/<effort> \
     --graph experiments/spack-bazel-graph/py_torch_lean_nx_build_graph.json
   ```
   Work the reported ticket/package first. If you intentionally skip to a
   later ticket or a later package, append a one-line `ORDERING:` reason under
   the tracker comments before doing that work; the line names the skipped
   ticket/package and why the graph/tracker order is being overridden.
   The selector treats a package as finished when target history contains a
   `Re-seat <package>`/`Reseat <package>` commit, or when that package ticket
   has its own `package-scoped re-seat is <package>` done marker.
3. Commit your own work before landing whenever possible. By default, tracked
   files may only differ from HEAD if the manifest lists them as superseded.
   Untracked files are left alone unless the landing branch would create the
   same path. When the only remaining dirty tracked files are unrelated to the
   incoming landing diff, use `--allow-dirty-non-overlap`; `land` prints
   `DIRTY_NON_OVERLAP kept=<n>` and preserves those files.
4. Land:
   ```sh
   scripts/agents/relay.py land --manifest .scratch/<effort>/landing-manifest.json \
     --tracker .scratch/<effort> --verify '<host test suite>'
   ```
   Use `--landing vaso/<effort>` instead of `--manifest` once there is no
   superseded state. Run `--dry-run` first if in doubt.
5. After each commit, run `relay.py land --dry-run` again. If it is OK, land
   immediately instead of waiting for the next human turn.
6. On `REFUSED`, stop. Do what it says: commit, or wait for the lead's rebase.
   Never work around it.
7. Record status and evidence with `relay.py ticket set|note`; never hand-type
   evidence timestamps or edit the generated evidence block. Append free-form
   notes such as `ORDERING:` or `BLOCKED:` under `## Comments`.
8. Append the time, HEAD and verify result under `## Acknowledgement` on the
   first landing, then commit it with your next commit.

## Execution I/O hygiene

All agent disk I/O goes to a declared, disk-backed, per-agent root on the
estate volume; shared host scratch is never used implicitly.

- **Host verifies:** run them through `relay.py land|rebase --agent <name>
  --io-root "$VASO_ESTATE_ROOT"`, and use `"$VASO_BAZEL_OB"` as Bazel's
  `--output_base`. `TMPDIR` is pinned to `<root>/agents/<name>/tmp`.
  tmpfs/ramfs roots are refused, which matters because `/tmp` and `/var/tmp`
  are both tmpfs on the shared host.
- **Idle Bazel servers exit after 15 minutes** (`.bazelrc` `startup
  --max_idle_secs=900`).
- **Estate placement** checks the seat's real filesystem (`tools/estate.py`).
- **Insula:** the Bazel output base, caches and Spack store live under
  `/vaso/cache` on the estate disk. Per-run `TMPDIR` under `/vaso/tmp` and a
  bounded private `/tmp` are tracked in the effort's ticket 13.
- **Vigilance:** the lead's watch includes `--io-health /tmp /var/tmp`.
  Cleaning another agent's state needs the human's approval.

## Failure modes seen in practice (and the rule each one produced)

| Seen | Rule |
|---|---|
| The lead waited for the follower to go idle so it could land into the follower's tree. The follower committed every few minutes and its turns ran for hours. | The follower lands, and the lead only keeps the branch ready (invariant 1). |
| A naive `git merge --ff-only` refused because the follower's tree held superseded dirty files and an untracked tracker copy. | Use a manifest and move the tracker aside (`relay.py land`). The failure is safe, but it stalls landing. |
| A capture registered only package-local tests, so shared guards that scan declared Bazel `data` never saw its files (a false green). | Guards need a coverage check that compares scanned files against the source tree (for example `python_abi_literal_guard --coverage-anchor`). |
| Tests run on a half-finished rebase looked meaningful. | Rerun only after `git status` is clean and the rebase is complete (invariant 7). |
| Both agents edited the same JSON field (ledger `front`) and the rebase conflicted. | Give each shared file an owner, and have the other agent append only. Resolve by keeping the owner's text and re-appending your own line. |
| The lead's own tracker sync raised `TRACKER_CHANGED`. | Compare against the sync state before acting. |
| The follower was mid-landing (tracker moved aside) while the lead tried to sync. | `sync-tracker` refuses when the follower copy is missing or changed. It never recreates a copy. |
| The follower's uncommitted acknowledgement sat in a file the lead was about to change. | While the follower has a tracker file dirty, the lead must not change that file on the landing branch, or the next fast-forward refuses. |
| The follower did frozen work before the handoff reached it. | Record it in `reviews.md` as pre-notice. Allowlist the debt with a ticket pointer instead of blocking landing. Reverting is the human's call. |
| The lead's rebase moved the landing ref to a commit it knew needed a follow-up fix (a new guard on top of the follower's flip, allowlist not yet committed). The follower landed it within 30 seconds, and the target was red until the fix landed. | Rebase detached, verify, then move the ref (invariant 6). Fixes go in before the ref moves. |
| The follower created a fresh Bazel output base under `/tmp` for every verify. Thirteen of them used all `/tmp` inodes on a shared host, with 13 idle server JVMs. | Every agent uses one reused, disk-backed output base per role and shuts it down after verifying. The lead's watch should include a host-health check; cleaning another agent's state needs the human's approval. |
| The lead's own verify harness passed its command through an unquoted heredoc. The outer shell expanded `"$VASO_BAZEL_OB"` to empty, and Bazel built its output base (109,075 files) inside the source tree. `git add -A` committed it to the landing tip, which was pulled back before the follower landed it. | A verify must never write into the checkout: `relay.py` fails a verify that dirties the tree (`VERIFY_DIRTIED_TREE`). Run verifies through `relay.py`, not ad-hoc harnesses, and quote the `--verify` string with single quotes. Stage paths explicitly, never `git add -A`. |
| `git` output was `.strip()`-ed, which corrupted porcelain status parsing. | Parse `git status --porcelain -z` unstripped (covered in `tests/test_agent_relay.py`). |
| The follower re-ran a pre-landing "delete these untracked files" instruction after those files had become tracked, deleting the ODR gate's checker. It happened at every sync: a long-running follower thread replays commands from its own context and doesn't re-read an updated handoff file. | Never put one-shot destructive commands (`rm`, `restore`) in a handoff; put them behind a tool that verifies state (`relay.py land` with a manifest). When a protocol changes, have the human send the follower a direct message, because file edits don't reach a thread's context. The watch reports tracked deletions. |
| The follower amended its own commit on the target twice (to drop a duplicate trailer) after the lead had reviewed it and rebased the landing branch onto it. The landing branch stopped being a fast-forward of the target, and the lead had to re-rebase. The trees were identical, so no harm this time. | Commits on the target are immutable once made. Fix mistakes with a follow-up commit, never `--amend`, reset or rebase. The watch reports `TARGET_REWRITTEN` when a target move drops the old tip. |
| Unquoted `$VAR` in zsh doesn't word-split, so the command silently didn't run. | Run multi-word commands through `bash -c` or arrays, and assert expected output. |

## Lead handover

Any agent can take over as lead, or be added as a second lead on a different
effort, by reading the effort's `lead.md` and doing this:

1. Read `CONSTITUTION.md`, `docs/agents/agentic-engineering.md`, this file, and
   the effort's `spec.md`, `lead.md`, handoff and `reviews.md`.
2. Check that the landing branch is an ancestor-or-descendant of the target
   (`git merge-base --is-ancestor`), that the lead worktree is clean, and that
   the verify command passes on the tip.
3. Start `relay.py watch` with the parameters in `lead.md`.
4. Append a handover line to `lead.md`: date, agent, the landing tip it
   verified, and anything unresolved it inherited.

The previous lead must first commit or park its work, stop its watch, and
record in `lead.md` any open item that exists only in its context.

## Autoland (the lead's verify-and-land step without a model)

When the follower's commits are routine (for example one package re-seat per
commit, each with its own insula proof), the lead's per-commit rebase, verify
and move can run as a shell loop, so model tokens go only to real attention.
The follower keeps its interactive session, and the human still watches and
steers it there. The loop replaces only the lead's step.

```sh
export VASO_ESTATE_ROOT=<estate>
scripts/agents/autoland.sh                        # run from the lead worktree, e.g. in a tmux pane
watch -n 30 scripts/agents/autoland-status.sh     # read-only dashboard, another pane
```

The cockpit TUI replaces the two-pane watch setup when a full-screen terminal
is available, and is the preferred way to run autoland for a live effort. Its
default mode runs the autoland loop in-process in Rust; `--attach` follows an
existing script-run or cockpit-run log/state without starting a loop:

```sh
export VASO_ESTATE_ROOT=<estate>
cargo run --offline --manifest-path crates/autoland-tui/Cargo.toml             # spawn the loop and watch it
cargo run --offline --manifest-path crates/autoland-tui/Cargo.toml -- --attach # follow an existing loop
```

`autoland-tui` resolves the same settings natively in Rust, then shows loop
events, follower tracker comments, follower log tail, target commits, idle and
review gauges, land history, the follower tmux pane, worker runs and the relay
outbox in one ratatui cockpit. Every child receives an explicit Rust-built
environment and runs as a direct argv subprocess; the cockpit does not execute
the repository shell/Python scripts. `q`, SIGINT and SIGTERM stop and reap the
verify children the cockpit started; in `--attach` mode the cockpit only exits
the dashboard. Use `--once --tab N` for read-only headless renders.

- Each new target tip is rebased in a detached HEAD, verified through the
  configured argv steps, and only then moved to the landing branch on
  `VERIFY_PASSED`.
- The loop exits, and hands back to the lead, on `RED` (a failed verify or a
  conflict), `REWRITTEN` (the target dropped its old tip), `STALL` (no target
  move, tracker edit or follower log for `STALL_MIN` minutes), `GONE` (the
  follower process exited), `IO` (`/tmp` or `/var/tmp` at 80% or more) and
  `REVIEW` (after `BATCH_HOURS`, for a batch review of the commits and proof
  logs). Paste the last line to the lead.
- The loop logs non-exiting `BACKLOG n commits, oldest m min` attention events
  when the landing branch has at least `BACKLOG_COMMITS` unlanded commits
  (default 3) or the oldest unlanded commit is at least `BACKLOG_MINUTES` old
  (default 30). Backlog events are rate-limited by
  `BACKLOG_RATE_LIMIT_MIN` (default 30) and appear in
  `autoland-status.sh` and the cockpit attention queue.
- Settings are environment variables with the same names as
  `scripts/agents/autoland-env.sh`. The cockpit resolves the repo layout by
  reading `.git` and `commondir`, finds the follower process by its working
  directory, and pins `TMPDIR` and `VASO_BAZEL_OB` under the agent I/O root.
  Events go to `<estate>/agents/<agent>/autoland.log`, and a heartbeat goes to
  `autoland.state`.
- Stopping the loop with Ctrl-C is always safe.
- Pair it with a standing rule for the follower: it never ends a turn to wait
  for the lead. After each commit it runs `relay.py land --dry-run`, lands if
  that is OK, and otherwise continues.

### Cockpit

The cockpit is the human's full-screen control surface for a lead/follower
effort. It keeps the autoland loop running by default, mirrors the follower's
tmux pane when `FOLLOWER_PANE` is known, shows worker runs from the estate, and
keeps an always-visible attention queue. The cockpit discovers `FOLLOWER_PANE`
from `FOLLOWER_PID` by matching the tmux pane process or one of its descendants;
set `FOLLOWER_PANE=none` when the cockpit must run read-only without tmux
access.

The outbox is the only path for lead-to-follower text. Agents may write drafts
with `scripts/agents/relay-outbox.sh draft` or the cockpit's native outbox file
format; either path is interoperable. The cockpit shows drafts on the Outbox
tab. The human presses `s` and confirms in the modal that shows the exact text.
The send path re-captures the pane immediately before sending, refuses unless
the prompt is idle, loads the message from a file under the agent I/O root with
`tmux load-buffer`, pastes it with `paste-buffer -p -d`, sends Enter, marks the
draft sent and appends to `outbox.log`.

Claude should not run chatty monitors while the cockpit is open. Keep one
background `scripts/agents/lead-wait.sh` armed against `autoland.log`; it
persists its byte offset and exits only for `RED`, `REWRITTEN`, `STALL`,
`GONE`, `IO`, `BACKLOG` or `REVIEW`, ignoring ordinary `START`, `MOVED` and
`LANDED` lines. After handling an event, re-arm it. On `RED` or `REWRITTEN`,
diagnose and fix forward or draft an outbox note. On `STALL`, read the TRAE
mirror tail and draft a nudge or redirect. On `BACKLOG`, land or nudge the
follower to land. On `REVIEW`, run
`scripts/agents/review-batch.sh`, append the result to `reviews.md`, deep-read
at least one commit, and advance the review mark with `--mark` only after the
batch is actually reviewed. On `GONE` or `IO`, report to the human.

## Briefed worker (headless traecli worker with a light supervisor)

A variant of the follower role for bounded, well-specified tickets. It's
called a *briefed worker* because the written brief is the worker's entire
context: memories, hooks and traecli-managed commits are off, so nothing but
the brief and the repository shapes the run. The lead
launches a non-interactive `traecli exec` worker in its **own** worktree with a
written brief. A light supervisor watches it: by default
`scripts/agents/worker-wait.sh`, a zero-token shell wait that exits on
`EXITED`, `STALLED` or an autoland attention event, or a cheap Haiku subagent
when the lead wants per-commit reports mid-run. The
lead reviews, verifies and lands the worker's commits through its landing
branch. Nobody types into the worker, and the worker has no protocol state to
replay: each run starts fresh from a brief.

The machinery is `scripts/agents/traecli_worker.py`, tested in
`tests/test_traecli_worker.py`. The Claude Code skill is
`.claude/skills/briefed-worker/`.

### The canonical invocation

This is how we run traecli workers, and `traecli_worker.py` hard-codes it as
the default:

```sh
traecli exec -m GPT-5.5 -p vaso-worker \
  -c model_reasoning_effort=xhigh -c model_reasoning_summary=detailed \
  -c features.memories=false -c features.hooks=false \
  -c features.plugin_hooks=false -c features.codex_git_commit=false \
  -C <worker worktree> -s workspace-write --json -o <run>/last-message.md \
  --add-dir <io-root>/agents/<worker> --add-dir <git common dir> --add-dir <worktree git dir> \
  --add-dir ~/.cache/bazel \
  --shell-tool-timeout 3h -   # the brief arrives on stdin
```

- **Model and effort:** GPT-5.5 at `xhigh` effort with `detailed` reasoning
  summaries. Don't downgrade either for real tickets. A throwaway smoke test
  may use `model_reasoning_effort=low`.
- **Isolation:** these four features are disabled:
  - `memories`, so traecli's cross-project memory file never enters the run;
  - `hooks` and `plugin_hooks`, so no user-level or plugin hook injects
    instructions;
  - `codex_git_commit`, so the worker commits explicit paths itself, as the
    brief says.

  Plugins, and with them the skills, stay on, because the repository workflow
  loads them. Check the effective state with
  `traecli features -c features.<name>=false list`.
- **Git in the sandbox:** the worktree's own git dir (`.git/worktrees/<name>`,
  which holds `index.lock` and `HEAD.lock`) is read-only unless it is itself
  an `--add-dir`. The common `.git` alone isn't enough. A worker that finds
  git blocked must stop, not route around it with plumbing
  (`GIT_INDEX_FILE`, `commit-tree`, `update-ref`); `score` flags plumbing.
- **Plugins:** `-c plugins."x".enabled=false` is ignored. The
  `vaso-worker` profile (`scripts/agents/vaso-worker.traecli.toml`, installed
  as `~/.trae/vaso-worker.traecli.toml`, launched with `--profile vaso-worker`)
  turns off the workflow plugins. It is the default, because in tuning it
  kept quality equal while using about half the input tokens
  (`.scratch/briefed-worker-tuning/`). `launch` refuses a missing or drifted
  installed profile.
- **Model:** GPT-5.5 at xhigh. Seed-2.1-Turbo (`--model Seed-2.1-Turbo`) is
  an opt-in for mechanical, well-oracled tickets: in tuning it was fastest,
  but its evidence was thinner. Don't use `high` effort (it cost more in
  tuning) or Seed-Evolving.
- **Sandbox:** `workspace-write`, with no network. The lead pre-fetches
  external inputs, pinned by hash, into `<io-root>/agents/<worker>/inputs/`.
- **Config:** everything goes through `-c`. `~/.trae/traecli.toml` is never
  edited for a worker.
- **Disk I/O:** `TMPDIR` and `VASO_BAZEL_OB` point under
  `<io-root>/agents/<worker>/`, which must be a disk-backed estate. Every run
  keeps `brief.md`, `meta.json`, `events.jsonl`, `stderr.log`,
  `last-message.md` and `exit_code` in `<io-root>/agents/<worker>/runs/<stamp>/`.

### Roles

| | Lead | Supervisor (monitor) | Worker |
|---|---|---|---|
| **Is** | The integrating agent (for example Claude Code) | `worker-wait.sh` (zero tokens) or a cheap subagent (Haiku) | `traecli exec`, GPT-5.5 at xhigh |
| **Does** | Writes the brief, launches, reviews each commit, rebases and verifies via `relay.py`, lands, re-arms the supervisor | Loops `traecli_worker.py wait`. On an event it reads `digest` and `status`, classifies, and returns a short report | Implements the brief's tickets in its worktree. Commits explicit paths, one commit per ticket slice. Stops at the brief's stop condition |
| **Never** | Edits the worker's tree while it runs | Writes, kills, runs git mutations or touches any tree (read-only) | Pushes, merges, amends, rebases, edits outside its worktree and the add-dirs, or writes to `/tmp` |

### Loop

1. **Lead:** create the worker's worktree from the verified landing tip
   (`git worktree add .worktrees/<worker> -b <worker-branch> <landing>`).
   Write the brief (template: `docs/agents/templates/worker-brief.md`) with
   scope, rules, verifiers and a stop condition. Pre-fetch its inputs, then run
   `traecli_worker.py launch`.
2. **Lead:** start the supervisor in the background with the prompt in
   `.claude/skills/briefed-worker/supervisor-prompt.md`, filled in with the run
   directory.
3. **Supervisor:** return on the first event batch, or after about 50 quiet
   minutes. It classifies each report as:
   - `PROGRESS`: a commit, or a clean exit;
   - `NEEDS_LEAD`: a stall, an error, a nonzero exit, a tracked deletion, an
     I/O event, or the worker asking a question;
   - `QUIET`.
4. **Lead:** on `WORKER_COMMITTED`, review the commit (`git show`) against the
   brief. On exit, check that `git merge-base --is-ancestor <landing>
   <worker-branch>`, then fast-forward the lead's integration branch to the
   worker branch. Run `relay.py rebase … --verify` so the landing ref only
   ever points at verified commits, and land. Re-arm the supervisor while the
   worker runs.
5. **Follow-ups:** a new brief and a new run in the same worktree, once it's
   clean. Never message a running worker. Stop it with `traecli_worker.py stop`
   and relaunch with a corrected brief.

### Rules

- One writer per tree: `launch` refuses a dirty worktree or one with a live
  worker (the lock is `<worktree git dir>/traecli-worker.json`).
- A billed worker run needs the human's go-ahead for the effort. The brief
  names its token budget, which is the ticket list and the stop condition.
- The supervisor's report is not evidence. The lead re-checks commits and
  runs the verify itself before landing.

## TRAE control channel (app-server)

TRAE runs as a thread on a token-authenticated `traecli app-server` (tmux window `trae-server`); the human's TUI attaches to it with `$VASO_ESTATE_ROOT/agents/trae/broker/attach.sh`. Broker state (port, token, thread id, logs) stays in the estate, never in git. The lead drives it with `scripts/agents/trae_ctl.py` (client: `scripts/agents/aps_client.py`):

- `trae_ctl.py status`: thread status, goal and context usage.
- `trae_ctl.py send FILE|-`: steers the active turn (about 1 s; the turn id is read from the rollout tail), or starts a turn when TRAE is idle. Prefer this over tmux paste.
- `trae_ctl.py new-thread --ticket ID --kickoff FILE [--goal FILE --budget N]`: one TRAE thread per ticket. It clears the old goal, interrupts the old turn, starts a fresh thread, sets the goal with a token budget, and logs the switch to `threads.log`. Afterwards, re-run `attach.sh` in the human's pane.
- `trae_ctl.py watch`: a zero-token wait. It exits on turn completion or failure, or on a goal status change.

Claim the ticket first (`relay.py claim <id> --agent trae`).
