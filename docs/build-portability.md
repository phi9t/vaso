# Building a Pascal capsule on another Linux host

The CUDA 12.6 SM61 PyTorch and Triton source builds require CPU, memory and
disk, but do not require a GPU. GPU runtime qualification is a separate step.
Use the same immutable Insula capsule and committed Foundry entrypoint on each
host. The capsule supplies CUDA, LLVM, compilers, Python, Bazel and package inputs;
the host supplies the admitted Linux execution facilities and persistent Estate.

For a CPU Colab proof, use an existing authenticated managed Colab CLI. Create
one explicitly named CPU session, check its actual resources and namespace
support, and retain ownership until artifacts, logs and cleanup receipts have
been recovered. High-memory CPU allocation is useful for native compiler and
linker memory. Requesting it does not authorize purchasing a subscription.
Do not copy OAuth credentials into the VM or the capsule.

## Admit against the job's effective resources

Choose concurrency from the job's cgroup and its visible ancestors, together
with process affinity and the effective cpuset. `nproc` and `free` alone do not
establish the execution budget. Inspect the governing owner from the host
view where available; an unlimited leaf does not remove an ancestor's limit.
Record any ancestors hidden by the execution namespace as a visibility limit.

Use the read-only resource probe before selecting jobs:

~~~sh
uv run --offline --no-project --no-python-downloads --no-config -- \
  python -B foundry/tools/host_resources.py
# For an existing host lifecycle owner in the same mount namespace:
uv run --offline --no-project --no-python-downloads --no-config -- \
  python -B foundry/tools/host_resources.py --pid "$VASO_OWNER_PID"
~~~

The JSON contains visible resource estimates and per-ancestor observations for
cgroup v2 and legacy v1 controllers. `memory.high` is reported separately as an
advisory threshold. A partial or hidden hierarchy remains explicit; a valid
primary mount can still provide observations when another mount is unmappable.
The probe does not make an admission decision or claim complete outer limits.
It rejects parent traversal, symlink escapes and a different owner mount
namespace, and never changes cgroups or enters another namespace. Runtime
rejections still emit JSON and exit nonzero. Keep the output in private attempt
evidence rather than committing host telemetry.

| Resource | Admission check |
|---|---|
| CPU bandwidth | For every visible finite `cpu.max`, divide quota by period and take the minimum. Also cap concurrency by affinity and the CPUs in `cpuset.cpus.effective`. |
| Memory ceiling | Take the minimum visible finite `memory.max`. Treat each finite `memory.high` as a practical reclaim/throttling threshold, not a second hard limit. |
| Memory headroom | At each relevant ancestor, compare its limits with its own `memory.current`, including sibling workloads; use the smallest remaining budget and reserve space for the owner, Bazel/JVM, compiler and linker peaks. |

See the [kernel's cgroup v2 controller documentation](https://docs.kernel.org/admin-guide/cgroup-v2.html)
for these interfaces. Shared ancestor budgets are not dedicated reservations
for each build; split the available CPU and memory between concurrent jobs.

For example, a machine can report 4 CPUs and 31 GiB while the job's ancestor
allows 2.5 CPU cores, a 12 GiB `memory.max`, and a 10 GiB `memory.high`.
Schedule against the latter limits and current headroom. Fewer compiler jobs
can reduce concurrent memory demand, but do not guarantee that a large link
step fits.

Job counts, host placement and owner deadlines are runtime scheduling choices.
Record them per attempt while retaining the same immutable capsule, sources,
toolchains, native plans and compiler flags. Resource adjustment must not
silently change the native action identity.

Keep a finite owner budget sized for the admitted resources and a bounded
cancellation/cleanup period. Retain timeout or memory-failure receipts, logs
and the owner's terminal cleanup evidence before admitting a retry. Neither
an expired launcher nor an idle process proves that its descendants exited.

## Transfer the exact capsule

Copy only the selected immutable capsule generation, Insula executable, frozen
Foundry checkout and explicitly selected lifecycle owner. Preserve file modes,
symbolic links and hard links. Do not copy an entire Estate or home directory.

The Insula generation key is an opaque identity, not a content SHA256.
Its schema-2 metadata fingerprint does not prove installed payload bytes.
A completed, reconciled transfer is therefore needed even when admission
accepts the manifest.

A direct private transfer with rsync is sufficient:

~~~sh
rsync -aH --no-owner --no-group -z --partial \
  --partial-dir="$VASO_REMOTE_PARTIALS" \
  -e "$VASO_COLAB_SSH" "$VASO_CAPSULE/" \
  root@colab:"$VASO_INCOMING/"
~~~

Set VASO_REMOTE_PARTIALS to an absolute, task-owned directory on the receiver,
outside the incoming capsule. It retains incomplete large files for normal rsync
recovery after a transport interruption. Use bounded reconnect attempts and
retain each failure receipt.

Keep the destination in a private incoming directory until transfer succeeds.
Do not modify an already admitted generation. Record the transfer exit code
and retain failed attempts. Use one active official Colab SSH bridge at a time;
run long remote work under an explicit durable owner so status access remains
available after the launching connection closes. A detached shell can remain in
its launching service's cgroup, so a service restart may stop it. Record phase
completion explicitly; do not infer transfer success from an interrupted
launcher exit status.

## Optional public archive seeds

When the private bridge is slow, large unmodified CUDA and LLVM files can be
acquired directly from their pinned public archives at the receiving host.
This optimization does not change the selected capsule or its build inputs.

The checked-in public lock identifies historical NVIDIA wheel archives by exact
SHA256 and size, the CUDA 12.6 developer deb archives, and the matching upstream
LLVM archive. The helper reads only the selected CUDA and LLVM subtrees to
produce the actual transfer path mapping. It does not hash installed file trees
or act as a native build prerequisite.

On the source host:

~~~sh
uv run --offline --no-project --no-python-downloads --no-config -- \
  python -B foundry/tools/seed_pascal_capsule.py manifest \
  --rootfs "$VASO_CAPSULE/rootfs" \
  --output "$VASO_TRANSFER/seed-manifest.json"
~~~

Transfer the helper and generated manifest to the receiver. On that host, use
Python 3.12 or newer through uv, with HTTPS access and dpkg-deb available.
Select a fresh empty incoming rootfs, a cache outside it,
and a new receipt path:

~~~sh
uv run --offline --no-project --no-python-downloads --no-config -- \
  python -B seed_pascal_capsule.py seed \
  --manifest seed-manifest.json \
  --rootfs "$VASO_INCOMING/rootfs" \
  --cache "$VASO_TRANSFER/public-archives" \
  --receipt "$VASO_TRANSFER/public-seeds.json" \
  --jobs 4
~~~

The helper checks downloaded archive SHA256 and size, extracts only selected
paths, and retains a receipt. The result is **transfer seeds**, not a finished
capsule. Matching library names are only hints for reducing transfer volume.

Reconcile all incoming bytes and metadata against the authoritative capsule.
The --ignore-times flag is required here: matching size or timestamp must not
cause a seed file to be accepted without the rsync delta comparison.
The --delete flag is appropriate only for this private incoming copy.

~~~sh
rsync -aH --no-owner --no-group --no-whole-file --ignore-times --delete -z \
  --partial --partial-dir="$VASO_REMOTE_PARTIALS" \
  -e "$VASO_COLAB_SSH" "$VASO_CAPSULE/" \
  root@colab:"$VASO_INCOMING/"
~~~

After successful reconciliation, move the incoming directory into the selected
private bundle store and run the ordinary Foundry admission and build commands.
The optimization does not qualify a capsule or replace source-wheel compilation.

## Bootstrap the host launcher

Before the first prepare, select an explicit `host_bwrap` executable in the
Insula spec. When the Colab session runs as root, that executable and every
ancestor directory must be owned by root and must not be writable by group
or other users. A read-only executable beneath a writable ancestor still fails
admission.

Copy the exact bundled executable into a fresh task control directory beneath
ancestors that already satisfy this ownership boundary. Use root-owned `0755`
control directories and a `0555` executable; verify the selected copy against
the bundled original with `cmp` and SHA256, and retain that narrow bootstrap
evidence. Configure `host_bwrap` to select the verified copy. Scope ownership
and mode changes to the new control path rather than recursively changing a
transfer tree or relaxing the executable-path checks.

Keep the immutable capsule payload fixed. Retain any rejected pre-launch
attempts, then repeat canonical preparation with a new attempt and the finite
lifecycle owner. Preserve the terminal receipt confirming that all owned
children were reaped. Preparation and bootstrap success remain separate
from a completed native wheel build.

## Build and qualify

Use CPU-only specs with no GPU devices or extra dependency mounts. Select
cu126-sm61/torch and cu126-sm61/triton through foundry/build.sh --target,
with their own build receipts and logs. Use the same immutable native graph
on each machine. Host paths and job counts may differ; sources, package
versions, toolchains and native build flags must remain fixed.

A Colab VM may not expose a writable delegated cgroup. In that case select the
explicit external lifecycle owner; do not fall back to an unsupervised build.
Retain the reviewed plan, process identity, build result, wheel identity and
child-cleanup receipt. Read status through the normal entrypoint instead of
inferring success from an idle VM or a wheel filename.

For each target, evidence should distinguish:

- Real native source compilation from preparation or an action-cache hit.
- A produced wheel from an installed-wheel import and CPU functional test.
- CPU build portability from GPU runtime qualification.
- A successful payload from confirmed lifecycle cleanup.

Retrieve artifacts and receipts before terminating the exact owned Colab
session. Do not terminate unrelated sessions or leave a temporary allocation
running after the portability proof is complete.
