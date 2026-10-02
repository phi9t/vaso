# Agent hygiene kit

Origin: the 2026-09-29 → 10-02 retro (`.scratch/process-improvement/spec.md`,
F6–F7, plan items C7/C8). Those four days logged 193 missing-path errors, 92
failed edits (91 of them against a stale view of the file), 42 broken jq
filters, 42 throwaway-script errors, stale steps replayed twice (deleting
tracked files), hand-typed timestamps that drifted by up to 28 minutes, and
dirty trees of 23–34 files carried across 90-minute builds.

These rules go into every TRAE kickoff and every worker brief, by reference.

## Before acting

1. **Check paths before using them.** Run `ls`/`test -e`, or use `git ls-files`
   for repo paths. Skill and doc paths change; never use one from memory.
2. **Read the file before editing it.** After a compaction, or after any
   checkout move (rebase, land, auto-land), re-read before patching. When an
   edit fails with "expected lines not found", re-read the file, then retry
   once. Never retry the same patch blindly.
3. **Never replay a step from memory** (`rm`, `git checkout --`, a long
   command) after a compaction or handoff. Re-derive it from the current
   ticket or spec text.
4. **Know where you are.** Bazel runs inside the insula via
   `run.sh`/`scripts/insula/insula.sh`, or on the host only through
   `standing-verify.sh` with an explicit `--output_base`. `bazel` isn't on
   the host PATH.

## While working

5. **Prefer the existing tools over throwaway scripts.** Use
   `last-result.sh`, `trace_digest.py`, `relay.py`, `relay.py ticket` and the
   guards' CLIs. Before writing a `jq` filter or an inline Python parser,
   print the input's shape: `head -c 2000`, or `python3 -c 'import json; print(list(json.load(open(F))))'`.
6. **Quote patterns.** Pass `rg -e '--flag'` / `grep -e`, never a bare
   pattern that starts with `-`.
7. **Commit proved pieces before long runs.** Keep at most 10 tracked dirty
   files when a build of more than 20 minutes starts. Commit through
   `scripts/agents/verified-commit.sh`.
8. **Don't write evidence timestamps by hand.** Use the timestamp in the log
   or the result JSON, or `relay.py ticket note`, which stamps `date -u`
   itself.
9. **Keep subagent fan-out bounded.** TRAE uses at most 2 concurrent
   subagents, which avoids "agent thread limit reached". Retry model "invalid
   content" errors once, then simplify the request.

## When stuck

10. **A red you didn't cause** goes in the shared-red registry (the standing
    verify does this). Don't work around it locally. If another agent already
    reported it, the lead is paged automatically.
11. **A refusal from relay, the gate or the commit gate is information, not a
    retry signal.** Read the reason. `NOTHING_TO_LAND` means there's nothing
    to do.
12. **Blocked:** write the blocker to the ticket (`relay.py ticket note`),
    then take the next unblocked, unclaimed ticket (`relay.py claims`). Never
    idle-poll.

## Lead parameter review (C8)

Any numeric resource or budget parameter the lead proposes must cite the host
or measurement it comes from (`nproc`, RAM, GPU count, a measured build time).
Examples: build jobs, `MAX_JOBS`, timeouts, GPU counts, token budgets,
lease sizes. Research and review agents may suggest these values, but they are
never adopted without that check. Example of the failure: `max_jobs=8` on a
216-core host turned an 18-minute torch build into a 93-minute one.
