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
rsync -aH --no-owner --no-group -z \
  -e "$VASO_COLAB_SSH" "$VASO_CAPSULE/" \
  root@colab:"$VASO_INCOMING/"
~~~

Keep the destination in a private incoming directory until transfer succeeds.
Do not modify an already admitted generation. Record the transfer exit code
and retain failed attempts. Use one active official Colab SSH bridge at a time;
run long remote work under an explicit durable owner so status access remains
available after the launching connection closes.

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
  -e "$VASO_COLAB_SSH" "$VASO_CAPSULE/" \
  root@colab:"$VASO_INCOMING/"
~~~

After successful reconciliation, move the incoming directory into the selected
private bundle store and run the ordinary Foundry admission and build commands.
The optimization does not qualify a capsule or replace source-wheel compilation.

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
