# Executable verification

Vaso uses the same command interfaces for local checks and CI. A successful
check must exercise its intended inputs. An absent input, an empty scan, or
an unavailable execution environment must not become a successful acceptance
result.

## Replay known mistakes

These cases run without CUDA, native builds, downloads, or a shared estate:

```sh
python3 -B foundry/tools/tracker_lint_test.py
PYTHONDONTWRITEBYTECODE=1 python3 -B foundry/tools/rebuild_impact_test.py
python3 -B -m pytest -q -p no:cacheprovider insula/execution/acceptance_test.py
```

For the full host suite, use a disk-backed `TMPDIR`: relay tests deliberately
reject RAM-backed estates. In a checkout nested below `.worktrees`, also set
`PYTEST_ADDOPTS=--rootdir=.` so nested pytest fixtures select their own root.

The two Foundry suites also run in the existing `//tools:all` standing gate;
the acceptance command's host tests are collected by the repository pytest
suite. Keep historical evidence in the existing tracker and test descriptions.
For a new recurring correction, reproduce the failure at the affected command
interface, show the old behavior failing that test, and make the same command
pass after the fix. Include a nearby valid case. Extend an existing suite
before creating another guard or registry.

| Invariant | Evidence | Executable cases |
|---|---|---|
| Missing tracker roots and accidental empty scans fail | Actual zero-file pass recorded in `.scratch/landing/follow-ups.md`; `5e91840f` repaired the live path | `TrackerLintTest.test_historical_missing_root_cannot_pass_with_zero_files`, explicit empty opt-in, missing/file-root rejection, and nonempty control |
| Renaming a native input cannot hide its deletion | Reviewed guard gap, follow-ups item 1 | `RebuildImpactFastTest.test_historical_native_input_rename_keeps_deleted_side_in_every_mode`, plus existing content-identical graph relocation controls |
| Relocation exemptions have a direction and a valid final root | Reviewed guard gaps, follow-ups item 2, and independent review of the identical-content shortcut | `RebuildImpactFastTest.test_historical_relocation_exemptions_reject_reverse_bad_depth_and_wrong_script`, `test_identical_relocation_cannot_bypass_final_script_location_and_depth`, plus forward controls in every comparison mode |

The rebuild fixtures reconstruct reviewed hazards in small Git repositories;
they do not claim that every fixture was a previously shipped defect. Their
worktree, index, and committed comparisons need no expensive build. Rule IDs
and fixture coverage remain owned by the existing guard-change protocol.

Run tracker lint against the intended scope:

```sh
python3 -B foundry/tools/tracker_lint.py \
  --tracker-root .scratch --allowlist foundry/tools/tracker_lint_allowlist.txt
```

Success reports the resolved root and file count. `--allow-empty` is only for
an existing, intentionally empty directory. It never permits a missing root
or suppresses stale allowlist errors. Avoid this option in the live tracker
gate.

## Real insula operational acceptance

Supply an existing rootfs and a new evidence directory on a writable volume:

```sh
python3 -B insula/execution/acceptance.py \
  --rootfs /path/to/existing/rootfs \
  --evidence-dir /path/to/new/acceptance-evidence
```

The command invokes `insula/run.sh` with real system Bubblewrap, the existing
shell owner, the estate and overlay planners, the doctor, and the lease CLI.
It creates its own private state below the evidence directory, clears inherited estate/launcher
overrides, and runs CPU-only checks. It never builds packages, installs
wheels, downloads sources, or acquires GPU leases.

| Behavior | Evidence and required observation |
|---|---|
| Readiness | Real doctor output, rendered bwrap argv, binary/source hashes, supplied rootfs path and optional sidecar hash; a command actually prints `ready` inside bwrap |
| Mounts and argv | `mounts.stdout` prints `mounts-ok`; writes to a read-only input fail; writable state persists; a literal argument containing shell syntax arrives unchanged |
| Exit propagation | `exit-status` records the requested exit code 23 through the launcher |
| Interruption and retry | SIGTERM to the public launcher stops its owned workload; output pipes close, writes stop, and a subsequent invocation prints `retry-ok` |
| Lease lifecycle | A live owned holder excludes another acquisition; after that holder terminates, acquisition succeeds; release leaves no active leases |
| Cleanup | `result.json` records removal of owned temporary state; evidence files remain |

The generic launcher does not automatically acquire the insula lease. This
suite tests its execution and the lease CLI separately; it does not establish
atomic lease integration for every production entrypoint.

Every command record includes argv, expected exit, observed exit, and retained
stdout/stderr. Missing prerequisites and readiness failures return nonzero
and leave a failed report. Existing evidence directories are refused. Re-run
with a new directory; never replace a failed report with a green skip.

The source manifest hashes the acceptance command, canonical run entrypoint,
shell owner, doctor wrapper, lease command, estate and overlay planners, and
runtime Python files under
`insula/execution/vaso/`. This conservative package inventory includes extracted
helpers and nested modules while excluding colocated tests. Keep it with the
logs when comparing attempts after a refactor.

Commands run with closed stdin and a 30-second deadline. Timeouts, communication
failures, and interruption stop the owned process group and retain its exit
status and output before state cleanup. Ctrl-C and SIGTERM produce a failed
report; the Python entrypoint restores the caller's SIGTERM handler afterward.

For a host-root stand-in, explicitly pass `--rootfs /`. The report labels it
`host-stand-in`. A supplied rootfs is labeled `supplied-rootfs`; a sidecar hash
identifies recorded metadata, not verification of every rootfs byte. This
suite qualifies neither CUDA/GPU behavior nor a release. Those remain the
responsibility of the existing release and determinism gates.

On a CI runner that permits user namespaces, run the same command and retain
the complete evidence directory even on failure. For the real-execution
integration test:

```sh
VASO_RUN_INSULA_ACCEPTANCE=1 VASO_ACCEPTANCE_ROOTFS=/path/to/existing/rootfs \
  PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest -q -p no:cacheprovider \
  insula/execution/acceptance_test.py
```

Ordinary pytest skips that explicitly enabled integration test. The standalone
acceptance command never skips a requested real run. Use its evidence
directory for retained CI artifacts; pytest's temporary files are disposable.
