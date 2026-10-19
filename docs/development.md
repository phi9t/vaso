# Development

Vaso has two product modules: insula and Foundry. Start with
`architecture.md` for ownership and `../GLOSSARY.md` for domain terms.
Use the nearest module reference and source implementation for current facts.

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

The host suite covers execution, support tooling, packaging, and fixtures.
Foundry suites run under its Bazel module. Focused standard-library suites
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
