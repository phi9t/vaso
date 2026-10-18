# Journal: building native PyTorch and Triton from the Spack graph

2026-09-29T00:00Z → 2026-10-02T03:50:41Z · branch
`vaso/insula-spack-bazel-graph` · 311 commits on the target in the measured
window (100 / 132 / 53 / 26 per day; last included commit `8eb856a`)
[commit: 8eb856a]

## What we set out to do

Build a full-featured **PyTorch 2.14.0** and its **Triton** natively. That
means building them as Bazel actions from pinned sources, inside the hermetic
insula, from the dependency graph Spack resolves. They had to run on 8× B200
(sm_100) and match the official CUDA wheel's features. JAX came along as the
third member of the "triumvirate"; how that went is at the end.

## Where we are

| | cu130 (CUDA 13.0.3) | cu129 (CUDA 12.9.1) |
|---|---|---|
| torch 2.14.0 (gcc 13.3 + nvcc) | built; current-head core gates pass | built in 24m36s current-head/default-jobs run; core gates pass |
| Triton 675c598 on LLVM 35901313 (gcc 13.3) | built; `import`, `torch.compile`, runtime-compiler check pass | same |
| torchvision 0.29 / torchaudio 2.11 | code landed; insula proof pending | same |
| live workloads W1/W2 | written; no `run_workloads` execution in the trace window | same |
| final gate (`triumvirate-gate.sh --profile torch`) | written; syntax/focused checks observed, no final profile run in the trace window | same |

The core gates are import, feature parity, B200 matmul, cuDNN conv (cuDNN
9.24 reported as 92400), NCCL and gloo across 2 GPUs, and flash SDPA. Stage 1
is done when the final gate passes on both lines. The current-head proof dirs
show torch `2.14.0`, CUDA `13.0`/`12.9`, B200, cuDNN `92400`, NCCL/gloo and
SDPA passing on both lines. [log:
np13-cu130-core-current-head-20261001T113120Z/gates-cu130.json] [log:
np13-cu129-core-current-head-20261001T115732Z/gates-cu129.json]

## By the numbers

The trace digest scanned 564 rollout files and 59 top-level worker run dirs;
after the `cwd` and time-window filters it kept 96 vaso-scoped rollout
sessions and 58 worker runs. Aggregate rows below come from
`trace-digest.json` generated at `2026-10-02T04:12:52.586595Z`; the command is
in "Where to read more." [trace: trace-digest.json @
2026-10-02T04:12:52.586595Z]

**TRAE sessions**

| Session | Window | Active time | Turns | Execs / failed | `apply_patch` failures | Compactions | Peak context | Total tokens at last event / window delta |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| old interactive TRAE (`01a0d267...`) | 09-29 00:00:00 → 09-30 19:23:16 | 26h39m36s | 42 | 5,335 / 510 | 36 | 114 | 234,572 | 3,278,848,606 / 917,688,282 |
| app-server TRAE thread (`01a0f3c6...`) | 09-30 19:24:35 → 10-02 03:50:37 | 10h36m22s | 8 | 5,674 / 596 | 29 | 114 | 242,718 | 986,193,137 / 986,170,979 |
| total interactive TRAE | 09-29 00:00:00 → 10-02 03:50:37 | 37h15m58s | 50 | 11,009 / 1,106 | 65 | 228 | 242,718 | 4,265,041,743 / 1,903,859,261 |

The old session's first and last compactions in the measured window were
`2026-09-29T00:07:36.852Z` and `2026-09-30T19:14:49.483Z`; the app-server
thread's were `2026-09-30T19:37:28.795Z` and
`2026-10-02T03:38:50.975Z`. The peak context event was the app-server thread at
`2026-10-01T15:10:45.805Z`. [trace:
01a0d267-c5e5-7963-8886-662443861a1a @ 2026-09-30T19:14:49.483Z] [trace:
01a0f3c6-274a-79c3-a0ac-11bde36a3189 @ 2026-10-01T15:10:45.805Z]

**Workers and subagents**

| Scope | Count | Runtime / activity | Outcome |
|---|---:|---|---|
| briefed top-level workers | 58 runs, all with `brief.md` | 17h05m40s total runtime | 52 success, 6 still had no `exit_code` by the cutoff |
| worker rollout sessions | 66 sessions | 4,220 exec commands, 427 failed commands, 754 ok patch/file changes, 27 failed patch/file changes, 23 compactions | peak context 239,613 |
| research/subagent rollouts | 28 sessions | 1h28m21s active time, 635 exec commands, 34 failed commands | peak context 192,467 |

Worker rows are from digest fields `worker_runs`, `totals.worker_*`, and
`totals.session_totals_by_role.{worker,subagent}`. [trace: trace-digest.json @
2026-10-02T04:12:52.586595Z]

**Torch build attempts**

| Line | Attempt | Duration | Outcome |
|---|---|---:|---|
| cu130 | first full action output | 5,602.144s (93m22s) | wheel built, then Bazel rejected the output tree at `third_party/ittapi/rust/ittapi-sys/LICENSES` with `Too many levels of symbolic links` [log: np14-pytorch-action-cu130-20261001TCONT2.log] |
| cu130 | resumed long retry | 5,535.889s (92m16s) | success [log: np14-pytorch-action-cu130-20261001T0628Z.log] |
| cu130 | short configuration/probe retries before the successful retry | 13 failed runs, 389.461s total | all failed before a full compile; representative path/prefix/scikit logs are `np14-pytorch-action-cu130-pathspec-20261001T024054Z.log`, `np14-pytorch-action-cu130-python-build-prefix-20261001T015723Z.log`, and `np14-pytorch-action-cu130-scikit100-20261001T0305Z.log` [log: np14-pytorch-action-cu130-pathspec-20261001T024054Z.log] |
| cu129 | `MAX_JOBS=96` build | 1,554.476s (25m54s) | success [log: np14-pytorch-action-cu129-maxjobs96-20261001T082121Z.log] |
| cu130 | current-head `MAX_JOBS=96` rebuild | 1,116s wrapper / 1,114.451s Bazel | success [log: np14-pytorch-action-cu130-current-head-maxjobs96-20261001T111226Z.log] |
| cu129 | current-head default-jobs rebuild | 1,476s wrapper / 1,474.814s Bazel | success; the action resolved `max_jobs=96` [log: np14-pytorch-action-cu129-current-head-defaultjobs-20261001T113234Z.log] |

**Triton builds**

| Compiler path | Line | Attempts and duration | Outcome |
|---|---|---:|---|
| clang | cu130 | 4.180s, 3.693s, 63.156s failures; then 77.159s success | missing input, output path, then `clang++` linker failure before the first pass [log: l4-cu130-triton-action-20261001T130009Z.log] [log: l4-cu130-triton-action-20261001T131100Z.log] [log: l4-cu130-triton-action-20261001T131800Z.log] |
| clang | cu130 | 18.239s and 19.378s follow-up builds | output-path/current-head successes [log: l4-cu130-triton-action-outputpath-20261001T141500Z.log] [log: l4-cu130-triton-action-current-20261001T143000Z.log] |
| clang | cu129 | 78.494s | success, then `import` and `torch.compile` proofs [log: l4-cu129-triton-action-20261001T142135Z.log] [log: l4-cu129-import-triton-20261001T142701Z.log] [log: l4-cu129-torch-compile-20261001T142715Z.log] |
| gcc | cu130 | 246.202s | success, with runtime compiler resolution pinned to `/usr/bin/gcc` and `/usr/bin/g++` [log: b1-triton-gcc-cu130-20261002T0139Z.log] [log: b1-cu130-runtime-compiler-resolution-20261002T014701Z.log] |
| gcc | cu129 | 125.230s | success, with the same runtime compiler resolution [log: b1-triton-gcc-cu129-build-20261002T014738Z.log] [log: b1-cu129-runtime-compiler-resolution-20261002T015236Z.log] |

The gcc rebuild provenance shows both wheels' `.comment` sections carrying
GCC 13.3.0, and records the cu130/cu129 wheel hashes. [log:
b1-triton-gcc-provenance-20261002T015333Z.log]

**Insula proof logs**

| Line | Wrapper logs with `estate root` and `insula tmp` markers | Status split |
|---|---:|---|
| cu130 | 106 | 91 ok, 3 failed, 12 diagnostic |
| cu129 | 41 | 37 ok, 2 failed, 2 diagnostic |
| no CUDA-line in filename/log | 24 | 16 ok, 1 failed, 7 diagnostic |

This is a count of TRAE proof-log wrappers, not unique semantic gates. The
current-head core gate logs are representative examples of the counted
wrappers. [log: np13-cu130-core-gates-current-head-20261001T113120Z.log] [log:
np13-cu129-core-gates-current-head-20261001T115732Z.log]

**Target commits**

Classification rule: `TRAE` means the commit body names `TRAE CLI`;
`worker-integrated` means the subject also appears in a successful top-level
worker commit command; otherwise it is counted as lead-authored. The measured
range ends at `8eb856a`. [commit: 8eb856a]

| Day | Total | TRAE | lead | worker-integrated |
|---|---:|---:|---:|---:|
| 2026-09-29 | 100 | 43 | 38 | 19 |
| 2026-09-30 | 132 | 59 | 22 | 51 |
| 2026-10-01 | 53 | 42 | 4 | 7 |
| 2026-10-02 through 03:50:41Z | 26 | 8 | 11 | 7 |
| total | 311 | 152 | 75 | 84 |

## The arc, day by day

**09-29: the frontier.**
- Native providers kept converging on Python 3.13 under the
  frontier-convergence effort; the day closed with 100 target commits in the
  measured history. [commit: 56eecdf] [commit: 9050e75]
- TRAE (the interactive insula agent) and the lead worked a relay. TRAE owned
  the target branch and the insula; the lead specced, reviewed and
  integrated.

**09-30: the foundations were re-laid three times in one day.**
1. *Source and spec.*
   - The PyTorch v2.14.0 recursive source was captured once: 65 submodules
     (29 vendored, 10 replaced by system providers, 26 unused) in one
     reproducible 677 MB archive. [commit: 6b85a09]
   - The full-featured spec ran with decisions D1–D8: full features, and NCCL
     on. [commit: 75dba68]
2. *The CUDA versions were wrong.*
   - The first library pins came from PyTorch CI's **12.6** line.
   - The rootfs had cuDNN 9.10.2 and NCCL 2.27.3, while the graph downloaded
     a second cuDNN (9.21) and built a second NCCL (2.29.3).
   - The human's rule became **one copy of everything**.
   - We pinned PyTorch CI's exact sets for **CUDA 12.9.1 and 13.0.3**:
     cuDNN 9.24.0.43, NCCL 2.30.7, cuSPARSELt 0.8.1.1, cuDSS 0.7.1.4,
     NVSHMEM 3.4.5. [commit: 45b3b83]
   - Docker's disk was full, so a worker freed 776 GB of other projects'
     stopped containers and build cache. Then it built and verified two rootfs
     lines (15 GB and 20 GB, about 10 min each, plus GPU checks). [trace:
     traecli-ops/20260930T164013Z @ 2026-09-30T16:40:13Z] [commit: 7cd9229]
3. *One LLVM.*
   - Torch's Triton pins LLVM June-2026; JAX 0.9.0's XLA pins January-2026,
     **17,496 commits apart**. A deep dive measured every JAX release and found
     0.10.2's LLVM **795 commits** from Triton's.
   - The human chose one LLVM at `35901313`. We built it once (8.5 min,
     5.8 GB) into both rootfs lines. [commit: 4018558]
   - We ported Triton across 795 commits: 30 files, +333/−271 lines; lit
     tests 277 passed, 2 unsupported. [commit: 53a1614]
   - That evening the original TRAE session degraded: 18% context, several
     compactions, and clobbered graph files. We moved it to a **fresh thread on
     a traecli app-server** that the lead can steer programmatically. [trace:
     01a0d267-c5e5-7963-8886-662443861a1a @ 2026-09-30T19:14:49.483Z]
     [trace: 01a0f3c6-274a-79c3-a0ac-11bde36a3189 @
     2026-09-30T19:24:36.080Z]

**10-01: torch builds, and the process gets hardened.**
- **The first torch build took 93m22s**, then Bazel rejected the output tree.
  The build directory moved to the estate, so retries resumed. [log:
  np14-pytorch-action-cu130-20261001TCONT2.log] [commit: 6ffa9e1]
- `max_jobs=8` on a 216-core host was the reviewer's (our) mistake; per-run
  jobs cut later current-head runs to 18m36s on cu130 and 24m36s on cu129. [log:
  np14-pytorch-action-cu130-current-head-maxjobs96-20261001T111226Z.log] [log:
  np14-pytorch-action-cu129-current-head-defaultjobs-20261001T113234Z.log]
- The core gates passed on both lines, and so did native Triton plus
  `torch.compile`. The first complete core-gate pass was cu129 at 08:49:02Z;
  cu130 followed at 09:21Z after three feature-parity failures. [log:
  np13-cu129-core-20261001T084902Z/gates-cu129.json] [log:
  np13-cu130-core-20261001T0921Z/gates-cu130.json]
- A TRAE commit turned the target **red** after focused tests were treated as
  enough verification. The fixes:
  - `standing-verify.sh` (one verify for everyone);
  - `verified-commit.sh` (the only way TRAE commits);
  - `relay.py land` now prints `NOTHING_TO_LAND` instead of a refusal that
    TRAE kept "retrying". [commit: 7750592]
- The captures had silently drifted to **Python 3.14** (Spack's default).
  Fixed by requiring 3.13.13 in the Spack config, plus a one-Python guard.
  The visible repair window ran from a failed cu130 graph at 16:23Z to passing
  3.13 graph captures at 16:56Z/16:57Z. [log:
  python-pin-cu130-py-torch-214-full-20261001T162303Z.log] [log:
  python313-py_torch_214_full-cu130-20261001T165615Z.log] [log:
  python313-py_torch_214_full-cu129-20261001T165721Z.log] [commit: a2b67e8]
- The human set the acceptance bar: **live workloads** (real trainers, real
  Triton kernels, a Triton custom op inside autograd) and **one final gate**
  that captures every check. [commit: 005a636] [commit: 9822eeb] [commit:
  637309c]

**10-02: the toolchain, decided.**
- jaxlib against nvcc and clang 23 hit NVIDIA's host-compiler gate and then a
  compiler ICE. [commit: d974784]
- The human split the work into **profiles**:
  - the torch profile ships now, all **gcc 13.3**;
  - JAX gets its own clang profile;
  - torch and JAX are never built or loaded together;
  - a unified toolchain is a later round.
  [commit: ebcfb9e] [commit: a51c1ae] [commit: 97c1769]
- A compiler-family deep dive confirmed:
  - all-gcc is impossible, because jaxlib refuses gcc;
  - all-clang is blocked by torch's CUDA build;
  - **our Triton had been clang-compiled against a gcc-built LLVM**, a
    hidden cross.
- Triton was rebuilt with gcc and proved on both lines. [commit: e427391]
  [commit: 19c64da]
- Eleven ABI invariants (one libstdc++, one OpenMP runtime, hidden internals,
  pinned runtime compilers, ...) became mechanical checks in the final gate.
- The rule became **one compiler plus one LLVM library**, with a newer LLVM
  allowed only as a tool. [commit: 79228d5]

## Decisions that shaped the build

| Decision | Why |
|---|---|
| Only Bazel sets toolchain settings; the toolchain comes from the rootfs only | Empty `CC=` pins had broken configure; toolchain drift was invisible |
| One copy of each CUDA library, from the rootfs | Same-soname duplicates (`libnccl.so.2`, `libcudnn.so.9`) mean the loader picks |
| Two CUDA lines, PyTorch CI's exact pins | 12.6 pins were wrong for 12.9; 13.0 is the official stable |
| One compiler plus one LLVM library | Each project pinned its own LLVM; mixing compilers hides ABI crossings |
| Python 3.13.13 everywhere | Unpinned solves pick 3.14 |
| MAGMA and oneDNN off | Human decision: not used |
| No Spack ABI reference for torch | "As long as the torch build works we are happy"; runtime gates are the acceptance |
| Torch profile on gcc, JAX profile on clang, unified later | No single family builds all three today |

## What went wrong, and what we changed

- **Wrong version source.** We read the 12.6 pins as "cu12". Now we read the
  exact CI install block for the exact line. [commit: 45b3b83]
- **Overlapping agent work.** The traces confirm a P0 recovery handoff where
  the previous TRAE WIP had 34 files, clobbered generated graph outputs, and a
  worker branch already had the same CE-6/7/8 tickets green. Now tickets are
  claimed in the plan before a worker starts, and TRAE's goal skips claimed
  tickets. [trace: 01a0f3c6-274a-79c3-a0ac-11bde36a3189 @
  2026-09-30T19:24:36.080Z]
- **"Focused tests passed" was not verification.** Workers ran Python tests
  directly and missed Bazel failures. TRAE committed and turned the target
  red. Now there is one standing verify, and a commit gate that runs it.
  [commit: 7750592]
- **A cached guard hid real violations.** The I/O-hygiene live test was green
  only from Bazel's cache. Uncached, it found three bare `mktemp` calls. The
  test is now `no-cache`. [commit: 5f0b120] [commit: 3a154ab]
- **A long-lived agent degrades.** After many compactions, TRAE overwrote
  checked-in graphs and ran `bazel` on the host. Now we rotate on degraded
  behaviour (not on context %), use an app-server thread with goals and
  token budgets, and steer mid-turn. [trace:
  01a0d267-c5e5-7963-8886-662443861a1a @ 2026-09-30T19:14:49.483Z]
- **Silent defaults.** Spack's Python default (3.14), nvcc's
  `-allow-unsupported-compiler` and inductor's runtime `CXX` would each have
  changed the toolchain without anyone choosing it. Each is now pinned or
  guarded. [commit: a2b67e8] [commit: d974784] [commit: 590c40d]

## Notable incidents (from the traces)

- The old interactive TRAE session compacted 114 times inside the measured
  window; the last compaction before rotation was at 19:14:49Z, nine minutes
  before the app-server thread started. [trace:
  01a0d267-c5e5-7963-8886-662443861a1a @ 2026-09-30T19:14:49.483Z]
- The fresh app-server thread itself compacted 114 times across eight turns by
  the cutoff, with peak context at 242,718 tokens. [trace:
  01a0f3c6-274a-79c3-a0ac-11bde36a3189 @ 2026-10-01T15:10:45.805Z]
- P0 recovery began with 34 files of prior WIP and three clobbered generated
  graph files to restore, while the worker branch already carried tested
  CE-6/7/8 commits. [trace: 01a0f3c6-274a-79c3-a0ac-11bde36a3189 @
  2026-09-30T19:24:36.080Z]
- The first full cu130 torch action consumed 93m22s before failing at Bazel's
  output-tree validation, not during compilation. [trace:
  01a0f3c6-274a-79c3-a0ac-11bde36a3189 @ 2026-10-01T05:18:32.308Z] [log:
  np14-pytorch-action-cu130-20261001TCONT2.log]
- From the first full torch action start inferred from the trace
  (`03:44:59Z`) to the first passing core gate was about 5h04m for cu129 and
  about 5h36m for cu130. [trace:
  01a0f3c6-274a-79c3-a0ac-11bde36a3189 @ 2026-10-01T05:18:32.308Z] [log:
  np13-cu129-core-20261001T084902Z/gates-cu129.json] [log:
  np13-cu130-core-20261001T0921Z/gates-cu130.json]
- The cu130 core gate failed feature parity three times before the 09:21Z pass;
  import, matmul, cuDNN, NCCL/gloo, SDPA, and collect-env were already green in
  those failed runs. [log: np13-cu130-core-20261001T0747Z/gates-cu130.json]
  [log: np13-cu130-core-20261001T0900Z/gates-cu130.json] [log:
  np13-cu130-core-20261001T0921Z/gates-cu130.json]
- The Triton native action failed first on action layout and then on a
  `clang++` linker error before the 13:18Z clang build passed. [log:
  l4-cu130-triton-action-20261001T130009Z.log] [log:
  l4-cu130-triton-action-20261001T131100Z.log] [log:
  l4-cu130-triton-action-20261001T131800Z.log]
- The 3.14 drift was visible as a failed graph run at 16:23Z; the corrected
  3.13.13 graph captures for both torch lines landed about half an hour later.
  [log: python-pin-cu130-py-torch-214-full-20261001T162303Z.log] [log:
  python313-py_torch_214_full-cu130-20261001T165615Z.log] [log:
  python313-py_torch_214_full-cu129-20261001T165721Z.log]
- The final gate work produced syntax and focused Bazel checks in workers, but
  the digest has zero `run_workloads` timeline entries before the cutoff, so
  the journal keeps W1/W2 and the profile gate as written but not live-run.
  [trace: trace-digest.json @ 2026-10-02T04:12:52.586595Z] [commit: 637309c]

## How the work was organised

- **Human:** decisions, authorizations, scope.
- **Lead (Claude):** specs, reviews, worker briefs, integration through
  `lead-wip`, and the landing branch kept verified.
- **TRAE** (traecli in the insula): target-side proofs, native builds,
  captures, and relay landings.
- **Briefed workers** (headless traecli): 58 top-level briefed runs in separate
  worktrees during the measured window (52 success, 6 still without
  `exit_code`), covering:
  - rootfs builder and verifier, LLVM, the Triton port;
  - Spack recipes, torch-build fixes, vision/audio;
  - workloads, the final gate, compiler and ABI enforcement.
- **Research subagents:** the CUDA pins, the LLVM alignment (now a reusable
  skill and `scripts/triumvirate/llvm_alignment.py`), the review of the torch
  build code, compiler-family safety.

## What's left for torch and Triton (Stage 1)

1. B7: the native jaxlib dependency closure (scipy is the heavy one).
2. B8: the W1/W2 live workloads on both lines.
3. B9: torchvision/torchaudio in the insula.
4. B10: `triumvirate-gate.sh --profile torch` on cu130 and cu129, then the
   NP-17 flip.

Then come the JAX-only profile (Stage 2) and the unified-toolchain round
(Stage 3, `.scratch/unified-toolchain/spec.md`).

## Where to read more

- `docs/toolchain-and-profiles.md`: the design and the invariants.
- `.scratch/execution-plan/spec.md`: the plan, decisions and goal.
- `.scratch/cuda-ecosystem/`, `.scratch/one-llvm/`,
  `.scratch/native-pytorch-build/`, `.scratch/live-workloads/`: specs,
  research and evidence.
- Regenerate the trace digest:

```bash
TMPDIR=$VASO_ESTATE_ROOT/agents/traecli-workloads/tmp \
  /usr/bin/python3 scripts/agents/trace_digest.py \
  --since 2026-09-29T00:00:00Z \
  --until 2026-10-02T03:50:41Z \
  --cwd-prefix $REPO_ROOT \
  --rollout-list $VASO_ESTATE_ROOT/agents/traecli-workloads/journal/rollouts.txt \
  --worker-run-list $VASO_ESTATE_ROOT/agents/traecli-workloads/journal/worker-runs.txt \
  --output $VASO_ESTATE_ROOT/agents/traecli-workloads/journal/trace-digest.json
```
