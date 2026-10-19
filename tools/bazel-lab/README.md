# Bazel Lab

`tools/bazel-lab` is a small standalone Bazel module for proving build-key
mechanics before those mechanics reach native packages. It runs on the host,
offline, with the same Bazel binary as the main repo:

```sh
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT/.vaso-estate \
  tools/bazel-lab/run_experiments.sh
```

The runner copies this directory into
`$VASO_ESTATE_ROOT/agents/<agent>/bazel-lab/workspace`, then mutates only that
copy. Output bases, logs and disk caches are also under the same agent
directory. It always passes `--lockfile_mode=off` and
`--registry=file:///nonexistent`.

## Experiments

| Experiment | Fact proved | Build-stability rule |
|---|---|---|
| E1 | A comment edit in a driver or planner file leaves the `aquery` action definition key unchanged but still re-executes when that file is a declared input; the committed-plan action does not re-execute. | Planner and driver refactors must not reach native action inputs. |
| E2 | `use_default_shell_env=True` lets `--action_env=FOO=...` change the key; an explicit `env` allowlist keeps the key stable. | Native actions must not use the default shell environment. |
| E3 | A string build setting passed to argv changes the key for every value; a seed derived from committed plan bytes is stable. | Per-commit seeds belong in committed plan content, not ambient settings. |
| E4 | A dependency read through an undeclared path string can change without a rebuild, producing stale output; a declared dependency has the same `aquery` action definition key but rebuilds from the changed input digest. | Dependency contents, not prefix paths, must be declared action inputs. |
| E5 | A repository rule re-runs after a `.bzl` edit, after an `environ` change, and in a new output base even with the same disk cache. | Native dependencies should be ordinary actions when cacheability matters. |
| E6 | Two output bases share disk-cache hits when argv/env have no absolute output-base path; embedding the output-base path in argv misses; computing path maps at runtime hits again. | Prefix maps and CUDA homes must be computed inside the action, not passed as host paths. |
| E7 | A persistent work directory outside the action output tree can reuse an object compiled with old flags after the key changes; a fresh action-owned work directory rebuilds correctly. | Native work is either a cache hit or a clean build from the plan. |
| E8 | A job count in argv changes the key even when output is identical; reading a job count from an undeclared runtime source leaves the key and output stable. | Job/resource counts must not be native action key inputs. |
| E9 | A committed plan, drift test, Merkle key snapshot and aquery env allowlist all pass for the miniature N1 pattern, and the same tests reject tampered/bad variants. | N1 guardrails make rebuild causes visible in review. |
| E10 | A nested Bazel module with a local override and `--lockfile_mode=off` works with the registry disabled; removing the override fails offline. | Nested Bazel tests must be offline and use local module overrides. |
| E11 | A repository-rule output used as a declared action input keeps the same downstream `actionKey` when only its contents change, but the downstream action still reruns; unchanged contents hit the shared cache. | Repository-generated native manifests must be deterministic, and input digests must be compared alongside `actionKey`. |

The fast standing-verify subset is E2, E3, E4 and E6:

```sh
tools/bazel-lab/run_experiments.sh --subset fast
```
