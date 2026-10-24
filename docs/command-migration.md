# Command migration

The module-owned commands below are implemented. Use the [operator guide](operator-guide.md)
for prerequisites and examples, and [architecture](architecture.md) for ownership.
No installed `vaso` umbrella command is supported.

| Operation | Current command | Migration and retained behavior |
|---|---|---|
| Prepare a rootfs | `insula/rootfs/prepare.sh restore|build` | Replaces manual bundle/build/tooling/manifest handoff; stages and verifies the complete selection, preserves failed attempts and existing destinations |
| Run a generic command | `insula/run.sh --rootfs DIR --workspace PROJECT -- COMMAND` | Canonical execution command; `--line` selects a prepared estate rootfs; no Foundry package policy |
| Prepare/build packages | `foundry/build.sh --line cu129 --profile torch` | Pinned input preparation and offline serial matrix; `--prepare-only` records preparation separately from builds |
| Inspect a build | `foundry/build.sh --estate-root DIR --status` | Reads retained outcomes without launching a build; failed attempts remain independently inspectable |
| Direct Bazel and gates | `foundry/run.sh --bazel-build`, `--bazel-aquery`, `--insula-cmd` | Specialist operations retain targets, queries, native arguments and sandbox paths |
| Use shipped wheels | `foundry/insula.sh --release DIR --bundle DIR -- COMMAND` | Retains wheel-consumer/release projections; replaces `scripts/insula/insula.sh` forwarding route |
| Restore and qualify a candidate | `tools/release/{candidate,restore,qualify,cut}.py` | Support ownership with maintained `qualification_recipe.json`; candidate, recipe and evidence identities remain checked |
| Check determinism | `tools/release/determinism_check.sh` | Retains the Foundry matrix and separate evidence attempts |
| Cold matrix rebuild | `foundry/full_rebuild.sh` | Retains distinct cold-estate proof semantics and adjacent tests |
| Read invocation ledgers | `foundry/tools/run_result.py` | Foundry and CI use the owning implementation; `insula/execution/result.py` removed |
| Readiness and resource safety | Shell entrypoints load `insula/execution/vaso/` | Production doctor, lease-state and atomic-write helpers remain; unused installed scaffold CLI and distribution removed |

## Retired scaffold and aliases

The installed `vaso` CLI implemented local host-layout checks and a tier-1
plan that always reported `not_run`; it never ran its planned Bazel smoke.
Its CLI, configuration, paths, Bubblewrap plan, run-record and validation modules,
corresponding tests, `harness/vaso` import link, unused Python wheel build metadata,
and `setup.py` have been removed. No deployed installer was identified; supported
shell commands load the runtime directly from the checkout. Unknown external
usage cannot be established by repository searches and must migrate to the
module-owned commands.

`harness/smoke` had only fixture existence/content checks and no live Bazel
caller. It was removed with the scaffold. Real CPU acceptance, Foundry language
checks and hermetic/native suites remain with their owners. Their evidence
claims are separate; host fixtures do not prove native execution or GPU readiness.

Current callers and tests use the owning execution and result routes.
`foundry/rootfs/bundle.sh` and `foundry/tools/overlay_root.py` had no retained
consumer after migration and were removed. The generic implementation
`insula/execution/insula.sh` remains a sourced execution library and exposes
its existing direct entrypoint; `insula/run.sh` is the documented command.
Dated references and immutable qualification reports retain historical paths.

## Intentional survivors

| Retained path | Identified consumer or constraint |
|---|---|
| Foundry rootfs links for build, LLVM cache, apt resolution, ecosystem verification and their tests | `foundry/BUILD.bazel` exports and `foundry/tools/BUILD.bazel` test labels/runfiles; removal needs a reviewed label/runfiles migration |
| `foundry/tools/estate.py`, `estate_test.py` | `//tools:estate_unit_test` consumes both files; source-tree callers use `insula/estate/estate.py` |
| `foundry/rootfs/cuda_ecosystem.lock.json` | Physical ecosystem lock remains at its native-input path; insula links to it |
| `scripts/agents/triumvirate-gate.sh` | Live qualification gate and native-input tracking use its physical path |
| `tools/agents/standing-verify.sh`, `verified-commit.sh`, relay and red registry | Optional coordination has live tests and callers; standing verification needs durable failure history and insula Bazel checks |
| `tools/ci/{nightly,ci-loop}.sh` | Live scheduled orchestration; uses the Foundry result owner directly |
| `tools/bazel-lab/`, `foundry/synthetic/` | Native-signature diagnostics and hermetic dependency consumers retain distinct checks |
| Foundry formal/frontier/migration records | Optional provenance and exported metadata; ordinary builds do not require replaying migration history |
| Foundry bootstrap/seal helpers | Preparation and installed-tool verification callers retain these component checks |

No blanket compatibility interval applies. Retained links have concrete consumers;
review their labels, runfiles and input closure before removal. The inventory does
not establish that every optional experiment has a replacement, so optional support
has not been deleted wholesale.

## Native identity and verification

Preserve `foundry/native/`, `plans/`, `locks/`, `toolchains/`, Bazel labels,
committed input bytes, shared native arguments, `CC=gcc` and `CXX=g++`.
Keep `/vaso-bazel-ob`, `/workspace/experiment`, sibling insula/Foundry views,
and `/vaso/build/<package>-<line>/src`: prefixes, runfiles and device `__FILE__`
can depend on these paths. Host storage and attempt metadata remain outside
content identity. See [build stability](agents/build-stability.md).

[Development](development.md) documents host tests, collection, fast native
impact and plan-key checks. [Executable verification](verification.md) documents
real CPU namespace acceptance. Rootfs fixture checks do not prove live
construction; native execution, reproducibility, ABI, GPU qualification and
promotion retain their separate evidence requirements. Historical baseline
captures remain in the external effort evidence and the dated reference records.
