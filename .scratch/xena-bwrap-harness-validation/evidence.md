# Bwrap rootfs validation — devx_b200_dbg

Date: 2026-09-07
Scope: bounded experiment using Vaso's existing renderer; no tracked source changes.

## Result

Real unprivileged Bwrap rootfs execution works on this host. All six coding
harnesses performed actual shell-tool work: create `hello.sh`, execute it with
`/bin/sh`, and produce `result.txt` containing `xena-harness-ok`.
This is experimental compatibility evidence, not Xena Linux Backend Qualification
or Vaso's signed B200/GPU release gate. No GPU devices were projected or used.

| Harness | Version | Successful coding evidence |
| --- | --- | --- |
| Codex | 0.146.1 | Authenticated remote model |
| Claude | 2.1.146 | Authenticated remote model; local provider fixture also passed |
| Cursor Agent | 2026.05.09-0afadcc | Authenticated remote model |
| deepseek-harness | 0.1.2-rc.1 | Deterministic localhost provider; real Bash tool |
| pi | 0.84.2 | Deterministic localhost provider; real Bash tool |
| TraeCLI | 0.202.3 internal | Authenticated remote model |

The dsh/pi fixture sends a synthetic tool call, consumes the real result, and
returns completion. Those runs do not verify remote provider authentication.
The host's saved Claude/Cursor credentials were stale. Successful retries used
credentials already verified in the preceding macOS work, injected into each
bounded guest environment rather than mounted from a host credential directory.
No credential values belong in this report or tracked artifacts.

## What was executed

- Bubblewrap 0.8.0, pinned executor `/usr/bin/bwrap`.
- Existing `src/vaso/bwrap.py` `build_bwrap_argv` and `BwrapPlan`.
- Dedicated Ubuntu 24.04.2 rootfs exported from the already-local image
  `sha256:7fd106f3a608232fdef448731b509067f72d88ea6289659e39f6e6fc7d98b723`.
  Docker only materialized the filesystem; guests ran directly through Bwrap.
  The temporary export container was removed after export.
- Read-only rootfs at `/`; explicit read-only CLI/runtime binds; private writable
  `/workspace/fixture` and `/state`; fresh `/tmp`, `/run`, `/proc`, and `/dev`.
- The existing renderer additionally mounts host `/sys` read-only. This is not
  complete host-information isolation. No host `/` or host home was mounted.
- Online runs explicitly projected DNS configuration and shared the host network
  namespace. Offline runs added `--unshare-net`.
- Mount, PID, user, and offline network namespace identities differed from host
  identities. Environment clearing removed an actual host sentinel.
- Vaso's current unit suite ran inside this rootfs: **24 passed**. A standalone
  Python runtime and only source/test/fixture paths were mounted read-only;
  private auth/evidence directories were not projected through a whole-repo bind.

## Behavioral boundary findings

| Probe | Observed result |
| --- | --- |
| Write under `/usr` | Denied with `EROFS` |
| Read/write an unmounted host canary path | Absent (`ENOENT`); host canary unchanged |
| Workspace and private-state writes | Succeeded |
| Host IPv4/IPv6 loopback listener, online | Reachable positive controls |
| Same listener, offline | Connections failed in separate network namespace |
| Nested `.git`, `.xena`, `.codex`, `.agents`, `.trae` writes | **All succeeded: metadata protection gap** |
| Writable mount containing an outside hardlink | **Outside owned canary changed: inode-alias gap** |
| Timed-out guest with a delayed `setsid` child | No delayed marker after waiting past its deadline |

Loopback probes establish the tested namespace separation; they are not exhaustive
Internet/network qualification. One delayed-marker test does not establish general
process lifecycle or resource containment.

## Important scaffold limitations

The checked-out Vaso CLI does not materialize or execute a rootfs yet. Its Tier 0
implementation checks host-directory writes without launching Bwrap; Tier 1 is a
not-run plan. This experiment explicitly invoked the renderer and executor rather
than treating either CLI tier as containment proof.

The renderer defaults to online networking. Writable bind mounts protect neither
recursive metadata names nor outside inode aliases. The exported rootfs and runtime
sources remain mutable by the host account. Export hashes and image identity do not
constitute a complete immutable runtime attestation or safe materialization pipeline.
Prototype logs have wall-clock time limits but no production disk/CPU/memory budget.

Earlier experiment plans recorded the renderer argv digest, whose first argument
is `bwrap`; actual execution pinned `/usr/bin/bwrap`. Those historical fields are now
explicitly labeled `renderer_argv_sha256`, with no invented executed digest.
Later plans record both renderer and exact executed argv digests.

## Recommended implementation sequence

1. Implement the real rootfs materialization/selection/execution seam already
   specified by Vaso's approved release-readiness work. Reject missing/incomplete
   roots and have Tier 0 run real positive and negative guest probes.
2. Default to offline networking and record explicit online grants. Keep a dedicated
   rootfs and exact runtime projections; make `/sys` projection an explicit choice.
3. Resolve workspace inode aliases through managed copying and controlled export.
   Define how recursive metadata protection is enforced; read-only mounts for only
   existing metadata directories cannot prevent every future nested creation.
4. Bind run evidence to exact executed argv, verified immutable inputs, runtime
   identities, and bounded output/preparation/resource/lifecycle contracts.
5. Promote all six harness recipes into repeatable Linux acceptance tests. Maintain
   separate startup, fixture-backed tool-loop, and authenticated-model statuses.
6. Use that evidence to design Xena's rootfs-backed Linux integration. Do not promote
   the current host-root Bwrap path or claim Linux qualification from this prototype.

## Evidence and reproduction

Remote evidence root:
`/data02/home/philip.yang/workspace/vaso/.vaso/experiments/xena-20260907`

`probe.py` uses the existing Vaso renderer and takes a fresh run label:

```bash
ssh devx_b200_dbg 'python3 workspace/vaso/.vaso/experiments/xena-20260907/probe.py fresh-smoke offline /bin/sh -c "printf rootfs-ok"'
ssh devx_b200_dbg 'python3 workspace/vaso/.vaso/experiments/xena-20260907/provider_fixture.py pi fresh-pi'
ssh devx_b200_dbg 'python3 workspace/vaso/.vaso/experiments/xena-20260907/provider_fixture.py dsh fresh-dsh'
```

Each run has a plan, stdout/stderr, and result. Authenticated successful runs are
`codex-coding`, `traecli-coding`, `claude-coding-current-auth`, and
`cursor-coding-current-auth`; provider runs are `pi-fixture`, `dsh-fixture`, and
`claude-fixture`. Other key records: `input-identities.json`, `network-results.json`,
`extra-probes.json`, `namespaces.json`, and `runs/repo-tests/stdout.log`.
The prototype scripts and sensitive raw evidence stay under ignored `.vaso/`.

Independent separate-context evidence review passed after digest-label correction;
latest Claude/Cursor successes, 24 passing rootfs tests, and redacted environment
records were checked independently. The stated boundary limitations remain.
