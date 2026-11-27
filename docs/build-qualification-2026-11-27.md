# Pascal build portability qualification — 2026-11-27

PyTorch and Triton completed full source-wheel builds inside Insula on a primary
Linux host and a CPU-only Colab instance. All four local cache repeats reused
their native action after the requested compiler concurrency changed. The two
distinct resulting wheel pairs each passed fresh CPU/compiler qualification and
the seven GPU suites on two Pascal GPUs.

The complete build/cache matrix below predates the final explicit CMake selector.
That distinction matters: the selector changes the native plan identity. Its
bounded validation is recorded separately and does not stand in for a new full
cold-build matrix.

## Scope and build contract

The tested profile is CUDA 12.6, SM61, CPython 3.12, PyTorch
`2.15.0+vaso.cu126.sm61`, Triton `3.8.0`, and Bazel `7.7.0`.
Both targets use the canonical `foundry/build.sh` entrypoint with targets
`cu126-sm61/torch` and `cu126-sm61/triton`, the same prepare/build/status
contract, and capsule-supplied native graphs.

Build hosts needed no GPU. Sources, package inputs, tools and toolchains came
from the selected immutable capsule; host installations supplied no replacement
compiler or dependency prefix. Bazel ran without its own sandbox because Insula
is the execution boundary. This preserves the existing shared host-network
contract; network isolation is not a qualification claim.

See [build portability](build-portability.md) for admission and transfer steps,
and the [Pascal native graph](../foundry/native/pascal/README.md) for native
inputs, provider boundaries, request receipts and action identity.

## Full source builds and local action reuse

All durations are the finite lifecycle owner's elapsed time, rounded to
milliseconds. They include launch and cleanup overhead. Concurrent workloads and
different resource budgets make these observations unsuitable as a host
performance comparison.

| Host | Target | Cold compiler jobs | Cold elapsed (s) | Repeat compiler jobs | Repeat elapsed (s) | Native executions, cold → repeat |
|---|---|---:|---:|---:|---:|---:|
| Primary Linux host | PyTorch | 3 | 16607.021 | 1 | 20.582 | 1 → 0 |
| Primary Linux host | Triton | 1 | 11216.513 | 2 | 50.896 | 1 → 0 |
| CPU Colab | PyTorch | 6 | 10253.708 | 3 | 7.393 | 1 → 0 |
| CPU Colab | Triton | 2 | 8771.658 | 1 | 10.267 | 1 → 0 |

Each cold receipt records one actual native wheel action and no cache hit.
Each repeat has a fresh request, an empty actual native execution log, a cache
hit, and the same wheel size and SHA256 as its corresponding cold build. Every
owner exited successfully and confirmed that all owned children had been
reaped.

These are native compiler job changes; the outer Bazel action concurrency stayed
fixed. They demonstrate that the runtime job request is outside native action
identity. The cache result is local reuse in each retained Bazel workspace.
It does not establish cache restoration into a fresh workspace or cross-host
cache sharing. One native action encapsulates a full wheel build; the count is
not a count of individual compiler processes.

## Exact wheel identities

The actual artifact files were hashed after production and after the Colab
return transfer. The two Triton files matched. The two PyTorch files differed in
both size and hash, so both exact pairs were qualified separately.

| Wheel | Bytes | SHA256 |
|---|---:|---|
| Primary PyTorch | 203106733 | `f67be3a84fc481a41f449c970825bfb3d5630d59a2c20711c6231a7a6d3edc1b` |
| Colab PyTorch | 203107654 | `e18b85dfc8e628bd273b43aaaf0dd14c1990421113cccafc83622bd7c30233c0` |
| Triton, both hosts | 312652656 | `138f9a656cbd7e3d4cd1de1070bc1f955e8c6b1873364e6e34bcf25af395d5a9` |

The filenames are
`torch-2.15.0+vaso.cu126.sm61-cp312-cp312-linux_x86_64.whl` and
`triton-3.8.0-cp312-cp312-linux_x86_64.whl`.
The observed CMake selection difference below is relevant provenance, but this
run does not establish that it caused the PyTorch byte difference or establish
general bitwise reproducibility.

## Fresh-wheel CPU and GPU results

Each pair's CPU check installed its exact new wheels into a fresh private
environment. GPU qualification reused that verified environment and checked
the imports of `torch`, `torch._C`, `triton`, and
`triton._C.libtriton` inside it for every suite.

| Exact pair | CPU/compiler on CPU Colab | CPU/compiler on primary host | GPU suites on primary host |
|---|---|---|---|
| Primary PyTorch + shared Triton | Not run for this PyTorch artifact | Passed, 52.693 s | All seven passed, 217.668 s |
| Colab PyTorch + shared Triton | Passed, 22.853 s | Passed, 14.819 s | All seven passed, 205.968 s |

CPU forward/backward and AdamW checks had zero maximum error against their
references. Loss decreased from 17.4375 to 16.469779413082467. Four SM61
compiler cases produced cubins: masked add, FP32 dot, signed INT8 dot, and inline
DP4A; the inline case also passed native DP4A disassembly inspection. These CPU
checks compiled GPU code without executing it. GPU execution was then tested
separately on the primary host for both pairs.

| GPU suite | Verified coverage for each exact pair |
|---|---|
| Compiler | 16 cases |
| Operators | 26 cases |
| Model smoke checks | 2 cases |
| Transport | Both GPU roles; 124 byte checks per role; allocations released |
| Packed dense | 64 runs, 4 forced-eviction generations, 8 CPU references; final cache/store closure |
| Archive, first role assignment | 8 generations; zero resident bytes after close; rollback released |
| Archive, reversed role assignment | 8 generations; zero resident bytes after close; rollback released |

The packed-dense completed generation count was 68, including forced evictions.
All CPU/GPU owners exited successfully and retained complete child-reaping
evidence. These checks qualify the exercised compiler, numerical, transport and
memory-lifecycle paths. They do not qualify trained-model quality or broad
model-parallel performance and scaling.

## CMake discovery finding and final selector

The primary PyTorch build selected bundled CMake `3.31.10` at
`/opt/vaso/cmake/bin/cmake`. The Colab build selected bundled CMake `4.4.5`
from the Python dependency tree. The primary raw log records a CMake probe
timing out immediately before selection of 3.31.10. The warning does not name
the candidate. Combined with the backend's module-first search logic, fallback
from the Python candidate is the explanation inferred from this evidence.
The reason for the timeout was not established.

The Torch plan now explicitly sets
`CMAKE_EXECUTABLE=/opt/vaso/cmake/bin/cmake`, matching its declared CMake
provider. The bundled backend treats this as its sole candidate and fails if
it is unusable, avoiding discovery fallback. A targeted comparison found this
environment entry to be the only generated Torch-plan change. The generator
was updated accordingly, while the checked Triton plan, profiles, provider
contracts and execution helpers were unchanged. The eight-test native-builder
suite passed after the pin.

The new immutable capsule was published, and the real bundled backend was
tested in a fresh CPU-only Insula against the exact generated plan. The valid
case built and loaded a small C wheel whose function returned 42, while the
alternative bundled 4.4.5 remained present. Both failure cases exercised the
real native build wrapper and produced no wheel.

| Post-pin check | Result |
|---|---|
| Explicit declared CMake provider | Selected 3.31.10; small configure/build and compiled C result passed |
| Missing explicit candidate | Failed with no fallback and no wheel |
| Explicit candidate exceeding the real 5-second probe timeout | Failed with no fallback and no wheel |
| Canonical PyTorch prepare | Passed in 70.843 s; zero native build executions |
| Canonical Triton prepare | Passed in 70.815 s; zero native build executions |

The selector regression owner took 22.823 s. It and both prepare owners exited
successfully with complete child-reaping evidence.

The full primary PyTorch build and its exact-wheel CPU/GPU tests already used
3.31.10. The bounded post-pin probe validates the selector and preparation,
not a second multi-hour full build. The four cold builds and four cache
repeats above therefore remain evidence for the preceding plan. No full
post-pin Colab PyTorch wheel build is claimed.

## Resource admission, lifecycle and retention

The resource review recorded process affinity, effective cpusets, visible
ancestor cgroup limits and current memory headroom. The initial primary-host
job choices used host-level observations; later review found the tighter
ancestor budget and scheduling was adjusted. The resource probe is strictly
observational and does not grant admission or claim complete outer limits.

| Resource observation | Primary Linux host | CPU Colab |
|---|---|---|
| CPU | Minimum visible ancestor quota: 2.5 cores | 8 affinity CPUs; no finite CPU quota visible |
| Memory | 12 GiB `memory.max`; 10 GiB advisory `memory.high` | About 51 GiB physical memory; visible `memory.max` and `memory.high` unlimited |
| Visibility | Visible hierarchy only; hidden ancestors unknown | Visible hierarchy only; hidden ancestors unknown |

These are observations, not dedicated reservations. Compiler jobs, affinity,
niceness and deadlines were runtime scheduling choices, separate from immutable
native inputs.

The builds used explicit external Linux child-subreaper owners. Cold deadlines
were 12 hours for primary PyTorch, 4 hours for primary Triton, and 8 hours per
Colab target. Owner receipts bind the admitted plan and command, terminal exit,
and `waitpid(-1, WNOHANG): ECHILD` cleanup proof. This exercises the external
owner path; it does not qualify a delegated-cgroup native lifecycle on these
hosts.

The CPU Colab launcher required a trusted explicit `host_bwrap` path. A
byte-identical bundled executable was placed beneath root-owned directories
with appropriate permissions; the capsule payload stayed fixed. Rejected
pre-launch attempts were retained, and both canonical prepares then passed.

Colab wheels, full cold logs, native requests, execution records, qualification
results and cleanup receipts were retrieved before termination. Only the owned
VM was stopped; a subsequent status confirmed it was absent. The bridge close
request succeeded, its original master later exited 255, and both the master
PID and socket were confirmed absent. Temporary task credentials were removed.

## Verification boundary

The preserved code checkpoints have 99 focused integration tests, 20 resource
probe tests, and a matching 893-test collection baseline. After the selector
pin, the eight-test native-builder suite passed, including the new selector
assertion. These are scoped checks; collection agreement does not mean all
893 tests were executed.

The source checkpoints use sanitized publication with reviewed public ancestry.
Runtime source provenance is checked through finite Git-blob comparisons.
Capsule generation identifiers are opaque immutable identities, not content
SHA256 digests. The full-build and post-pin selector receipts remain distinct,
so the final preparation result cannot be mistaken for another full wheel build.

Other Foundry profiles, trained-model quality, cross-host cache sharing and
general deterministic wheel output are outside this qualification.
