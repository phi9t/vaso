# Simplified workflow acceptance

The simplified insula and Foundry workflows passed bounded acceptance on
2026-10-23 (America/Los_Angeles). This records implementation acceptance;
[release qualification](../status.md#release-requirements) remains open.
The [structured record](simplification-acceptance-20261023.json) identifies
source revisions and hashes of detailed evidence retained outside Git.

## Starting state and observed outcomes

Verification used source `c26eb9fb6b610c6c22edbf48c4523332fb680c19`, an explicitly
owned disk-backed estate and temporary storage, Python 3.11+, Bash, zstd,
Bubblewrap and available user namespaces. Runtime commands used tools inside
their selected rootfs. No Docker build, network acquisition, credentials,
registry operation, native rebuild, GPU run, publication or promotion was needed.
All attempts and raw host paths remain in the estate's simplification evidence,
under `evidence/acceptance`; earlier slice evidence remains beside it.

A self-contained local fixture exercised `insula/rootfs/prepare.sh restore`
with an explicit bundle digest, tooling lock, cache, line and estate. Verified
reuse preserved the prepared destination. `insula/run.sh --line cu129` then ran
a real shell from that prepared rootfs in the same estate, checked the read-only
workspace projection, and produced a checked output through `/vaso`. A command
exited 23 with retained output; a separate retry passed. The fixture contains a
real shell, loader and libraries, with synthetic CUDA and build-tool metadata.
It proves preparation-to-CPU execution, not a production CUDA bundle or build.

A separate disposable acceptance run used the existing prepared CUDA rootfs.
It passed readiness, mount/argv projection, exit-status propagation, interruption
and retry, lease recovery, and cleanup through the generic execution command.
The evidence retains each child command's stdout, stderr and status independently
of the launching terminal. CPU acceptance does not establish GPU isolation or
runtime qualification.

The full host suite passed with real CPU acceptance enabled: 718 tests. The
collection guard confirms those 718 IDs. Layout, committed plan keys and shell
syntax passed. The four Torch/JAX × cu129/cu130 build orchestration variants,
input preparation, offline invocation, artifacts, status, invalid inputs,
interruption, failure retention and retry are covered at the agreed command
executor fixture seam. These runs did not execute native Bazel builds.

Current prepare/run/build and candidate/restore/qualify help commands were
checked. Empty build status reports no recorded runs. Invalid selection fails
before execution. Missing rootfs preparation produces a durable failed result,
and `foundry/build.sh --status` reports that retained failure. Public preparation
fixtures cover restore/build staging, wrong identities, preservation, failed
acquisition authority, interruption and safe retry. Release fixtures cover
candidate identity, restore failures, policy/recipe rejection and cache admission.

## Review follow-up

After the recorded acceptance, review fixes at `d3f24570` strengthened the
preparation handoff: preparation and execution both require an executable
`/bin/sh` resolved inside the rootfs, including before reuse, and preparation
subprocesses receive closed stdin. Valid absolute aliases and merged `/usr`
remain supported. Command regressions cover missing or nonexecutable shells,
Bash-only trees, permission drift, host-only aliases, and caller input withheld
from the external Docker fixture. The focused preparation/execution suite
passed 92 tests, and disposable real CPU acceptance passed at that source.
Detailed red/green and CPU evidence is retained under `evidence/review-fixes`.
The earlier source revision and 718-test result above remain the historical
acceptance record; these follow-up checks do not replace its full-suite result.

## Maintainer exercises

| Exercise | Owner and caller impact | Final-source verification |
|---|---|---|
| Validate execution workspace before estate materialization | `insula/execution/insula.sh`; public `insula/run.sh` | Replayed the workspace validation slice: missing, file and empty workspace fail with recovery guidance and no estate. Original red/green logs remain in `evidence/execution`. |
| Change existing Torch-profile fixture membership to Triton alone | `foundry/tools/build_selection.py`; `foundry/build.sh` input discovery, native target/token selection and status | Disposable fixture edit selected only Triton sources, target, token and checked wheel artifact; durable status reported built. No production profile or native pin changed. |
| Remove W1b from one maintained recipe fixture row | `tools/release/qualification_recipe.json`; qualification planning, command generation and evidence identity | Final policy tests observed changed coverage/command and consumed recipe digest. Changed recipe evidence was rejected even when the plan matched. No GPU execution or production recipe change. |

The focused execution/build/qualification replay passed 64 tests. The exercises
use existing owners and seams; they leave no artificial production changes.
Final qualification plans and complete command bodies equal both retained
before/after reports for policy v1/v2 × device absent/0/7, including expected
v2 rejection without an explicit device. Acquisition/restore argv parity remains
recorded separately in the release slice.

## Native identity and evidence applicability

The baseline is `6d377df4a40023b26f8d8adc98588a1a275dfcc7`. Exact Git modes,
paths and blob identities match both it and proof source
`dbe7eb9a7005a3a1ff284cc46e8ee27b3334d0c2` across 668 entries: native definitions
and labels, plans, locks, toolchains, module inputs, bootstrap/graphs, plan
generator, deterministic native arguments, physical ecosystem lock and retained
gate. Ordered pinned source closures match: 113 Torch-profile assets and 204
JAX-profile assets. No native input change justified a rebuild.

Fast impact against the baseline returns conservative exit 10 (`may-rebuild`)
for `foundry/run.sh`. Its only baseline changes select the ledger implementation
and update prerequisite recovery help; native argument blocks are identical.
The older proof caller also differs in estate-placement validation and overlay
error propagation. Exact comparison accounts for those support changes. This
is content/argv-source evidence, not a passing fast scan or a live action query.
No current action key, generated/external input digest snapshot or aquery is
claimed. Existing physical native compatibility paths remain documented in
[architecture](../architecture.md#compatibility-and-native-stability).

The [retained Torch reproducibility proof](native-reproducibility-20261023.json)
keeps its original source and wheel identities. Acceptance rehashed both build
summaries, each line's retained build events and four wheel artifacts, raw
comparisons and focused runtime result records, and unchanged comparator and
allowlist bytes. Native source identity remains applicable by matching content;
the overall support source identity changes and is not relabeled. The original
proof's prepared-cache conditions and focused runtime limits remain in force.
JAX's missing manifest/artifact closure, candidate-bound repeat qualification,
fresh restoration, unexplained-failure disposition and promotion remain with
their release owners. No old runtime result qualifies newly identified wheels.

## Documentation and privacy

The [command migration table](../command-migration.md), operator guide, code tour
and ownership references describe the delivered module commands. Changed tracked
text was checked with the existing external sanitization rules. Acceptance additions
are clean; the broader scan has six context-reviewed matches for one ordinary
programming term in declarations, documentation and argument variable names.
These matches are not host identifiers or credentials; blind replacement would
change code. Publication owners must preserve this distinction when preparing a
snapshot; the official sanitized-snapshot verification flow still applies before
any publication. Rules and raw paths are not committed. Local Markdown links resolve. Vendor bytes and
historical evidence remain intact. No source history was published.
