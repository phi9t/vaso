# Development

Vaso has two product modules: insula and Foundry. Start with the
[code tour](code-tour.md) to follow a build, [architecture](architecture.md)
for ownership, and the [glossary](../GLOSSARY.md) for domain terms.
Use the nearest module reference and source implementation for current facts.

## Where to change behavior

| Change | Owner and entrypoint |
|---|---|
| Rootfs contents or tooling | `insula/rootfs/`; provision pinned inputs before execution |
| Estate layout or sandbox paths | `insula/estate/`; keep host placement separate from content identity |
| Launch, environment, readiness, or leases | `insula/execution/`; generic execution enters through `insula.sh` |
| Unattended build variants | `foundry/build.sh` → `foundry/tools/build_variants.py`; the [code tour](code-tour.md#make-a-change-in-its-owner) maps inputs, execution, reports, and presentation to their owners |
| Native package behavior | `foundry/native/<package>/` and the plan generator in `foundry/tools/gen_plans.py`; inspect native impact before regenerating plans |
| Runtime workloads and consumers | `foundry/workloads/`, `foundry/native/pytorch/gates.py`, and `foundry/examples/consumers/` |
| Candidate selection, restore, or repeated qualification | `scripts/release/`; see the ownership table in [candidate qualification](release-qualification.md#maintaining-the-workflow) |
| Scheduled-run orchestration or publication | `tools/ci/` and `tools/publish/`; product policy stays with insula or Foundry |

Prefer a change in the owning implementation and its adjacent tests. Keep shell
entrypoints thin. Add a shared helper when multiple callers need the same
behavior; keep workflow-specific policy with its caller. Historical designs and
incident records live behind the [reference index](reference/README.md), while
current interfaces belong in the product guides. Native-input compatibility
paths are documented in [architecture](architecture.md#compatibility-and-native-stability).

## Coding conventions

Keep interfaces small and put complexity behind the owning module. Foundry
selects the build; insula executes an explicit request. Keep package-specific
policy out of generic execution. Pass paths and environment explicitly.

Python targets 3.11 or newer and uses standard-library modules for host
tooling. Match nearby type annotations and immutable dataclasses for value
objects. Separate filesystem/process effects from data transformations where
that makes callers and tests simpler. Errors should identify the failing
input or command and preserve its exit status.

Shell entrypoints use Bash, arrays for argv, and quoted expansions. A sourced
library must return errors to its caller; do not rely on the caller enabling
`set -e`. Executable wrappers use `set -euo pipefail`. Keep helpers beside their
owner and derive checkout paths from the script location.

Bazel native rules consume declared files, tools, plans, and selected settings.
Keep host paths, timestamps, job counts, and release metadata out of native
input identity. Use existing schemas and the shared plan executor. See
`agents/build-stability.md` before changing anything reaching native actions.

Tests sit beside the behavior they exercise as `<name>_test.py`. Test observable
behavior through the command or module interface, including relevant failure
cases. Choose verification proportional to the change; prose edits do not
need new tests. Vendored code and historical records retain their own formats.

## Unattended execution

Treat unattended end-to-end runs as evidence of system quality. A supported
workflow should take a documented starting state through input preparation,
execution, output validation, and a durable result without an agent or human
repairing it mid-run. Move recurring operator knowledge into the owning code
and its interface; improve the complete path rather than only the latest command.

Declare configuration, pinned inputs, resource needs, credentials, and authority
before execution. Automate acquisition and prerequisite checks where appropriate.
Missing prerequisites should produce a specific failure and recovery path before
expensive work. Once launched, the run should complete or fail without prompts,
ambient machine state, or manual reconstruction of the next step.

Preserve the input identities, stage outcomes, logs, artifacts, and exit status.
Recovery should be safe to repeat; retries should be bounded and retain failed
attempts. Make progress, final status, and failure causes inspectable independently
of the launching terminal. Exercise the public entrypoint and relevant failure
paths when changing orchestration.

Unattended operation and determinism are separate claims. Given the same declared
inputs and conditions, aim for repeatable behavior and verifiable outputs; state
the comparison policy and resource assumptions. Distinguish a cached run from
cold input preparation, actual native execution, and artifact reproducibility.
Use evidence matching the claim. Reuse valid native proof by content identity
under the build-stability contract rather than rebuilding unchanged packages
for every support or documentation change.

## Verification

Use a disk-backed `TMPDIR`; estate-placement tests reject tmpfs. For a checkout
under `.worktrees/`, set `PYTEST_ADDOPTS=--rootdir=.` so nested collection uses
the intended root. From the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest -q -p no:cacheprovider
python3 -B tools/testing/collected_ids.py --check tools/testing/collected-ids.baseline.txt
python3 -B foundry/tools/rebuild_impact.py --repo-root . --fast --base HEAD --worktree
python3 -B foundry/tools/plan_key_snapshot.py --check
```

The host suite covers insula execution, estate placement, rootfs preparation,
build orchestration, support tooling, and packaging. Its test paths are defined
once in `pyproject.toml`; the collection guard uses the same configuration.
Rootfs tests use synthetic filesystem trees, local archives, and fake or mocked
build tools. They require no Docker daemon, network acquisition, or GPU.
Live bundle construction and GPU qualification use their acceptance workflows.
Foundry native suites run under its Bazel module. Focused standard-library suites
can also run directly, for example `python3 -B foundry/tools/tracker_lint_test.py`.
`verification.md` documents historical regression cases and real Bubblewrap
acceptance. GPU, ABI, workload, and release qualification use Foundry's gates.

`tools/agents/standing-verify.sh` composes the standing checks. Its offline
registry and pinned dependencies must already be present; a missing dependency
is a failed prerequisite, not a passed build. Keep tests and build experiments
on owned output bases, away from live estate caches and releases.
Set `VASO_ESTATE_ROOT`; select `VASO_CUDA_LINE` when needed (the standing
verifier defaults to cu129). It enters insula
before starting Bazel, including nested mechanics experiments. The tools come
from the rootfs; ambient host `BAZEL_BIN` settings do not cross that boundary.
`../foundry/README.md` documents the provisioning commands.

Coordination and publication tools are optional support. Their ownership and
authority contracts are in `agents/cross-agent-collaboration.md` and
`agents/publication.md`. No template or skill grants authority beyond the user
request.
