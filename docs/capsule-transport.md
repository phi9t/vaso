# Layered Insula capsules

`tools/publish/capsule.py` packages a standard OCI image **as transport**, not
as a runnable Docker container. Its merged filesystem contains
`/capsule/rootfs`, `/capsule/bundles/*`, and `/capsule/descriptor.json`.
Insula, not the image config, decides what executes.

## Declare the closure

Supply a local packing specification:

```json
{
  "schema_version": 1,
  "rootfs_manifest": "/staging/rootfs-bundle.json",
  "layers": [
    {"name": "base", "source": "/staging/base", "path": "rootfs"},
    {"name": "graph", "source": "/staging/graph", "path": "bundles/graph", "guest": "/workspace/build-graph"},
    {"name": "sources", "source": "/staging/sources", "path": "bundles/sources", "guest": "/sources/pinned"},
    {"name": "sdk", "source": "/staging/cu133", "path": "bundles/sdk", "guest": "/vaso/toolchains/cu133"}
  ]
}
```

The local `source` and `rootfs_manifest` paths are never serialized into the
capsule descriptor. Supply the selected base rootfs's published manifest:
canonical compiler wrappers require it at `/run/vaso/rootfs-bundle.json`.
It is stored beside the inner rootfs, where normal Insula context binds it.
For an already packed layout, `add-rootfs-manifest --layout DIR --file FILE`
adds one small native OCI metadata layer without repacking existing blobs.
Each source must be a deliberately selected directory. Use distinct component
layers for the declared Bazel graph, pinned source archives, toolchains, and
optional installed-prefix/wheel artifacts. This is the adapted package graph,
not just a PyTorch wheel or a loose collection of host dependencies.

Preserve the native build's selected installed-prefix closure and exact guest
paths when relocation has not been qualified. Do not copy an entire Bazel
output_base, its symlink tree, or action cache. OCI blob caching and Bazel action
caching are different mechanisms. A runtime smoke does not prove an offline
source rebuild.

Do not put the output OCI layout inside a source directory. Do not package a
live, changing source tree. Existing output layouts are never replaced by pack.
The first implementation repacks selected layers when invoked; it does not
claim incremental source-change detection.

## Pack and restore

```sh
python3 tools/publish/capsule.py pack --spec capsule-local.json --layout /estate/capsule-oci
python3 tools/publish/capsule.py unpack --layout /estate/capsule-oci \
  --dest /estate/restored-capsule --umoci /tools/umoci
python3 tools/publish/capsule.py plan --bundle /estate/restored-capsule
```

Use official umoci 0.6.0 or newer. The implementation specifically uses
`umoci raw unpack --rootless`, not normal `umoci unpack`: normal unpack creates
a per-member mtree inventory that this workflow does not need. Raw unpack
handles OCI extraction semantics without that extra tree pass. Extraction is
staged beside the destination and published only on success; existing
destinations are not replaced. Run extraction unprivileged on a filesystem
with an appropriate disk quota. No untrusted image command is executed.

Packing uses semantic component boundaries, gzip level 1, normalized ownership
and timestamps. Components are further divided at tar-member boundaries using
a 2 GB uncompressed byte target (`--max-layer-bytes`). Each part is a complete,
independently compressed standard OCI tar layer, never a byte slice of one
compressed archive. A single member may exceed the target; a compressed part
at or above GHCR's 10 GB hard limit fails before publication. Rotation happens
during serialization with no preliminary member inventory. Only standard OCI compressed blob hashes and uncompressed
layer diff IDs are computed. There is no source/member hash manifest. Unchanged
layer bytes retain the same OCI digest even if another component changes.

## Insula projection

```sh
python3 tools/publish/capsule.py run \
  --bundle /estate/restored-capsule --repo /workspace/vaso \
  --estate /estate/fresh --workspace /workspace/vaso --line cu130 \
  --print-argv -- /bin/sh -c 'echo inspect-the-plan-first'
```

The wrapper prepares normal Insula context, rebuilds the overlay mountpoints,
then appends descriptor-selected read-only bundle projections. It never
executes commands supplied by the descriptor. The caller supplies the command.
The base may remain a CUDA13.0 rootfs with an explicitly bound CUDA13.3 SDK;
do not relabel the base to conceal this distinction.

The generic wrapper supports CPU/build commands. GPU qualification must use
the explicit host driver/device projection supported by the established
qualification launcher; the driver is **not** packed. Exported projection JSON
can be consumed by that launcher. A generic run is not a claim of GPU-ready
automatic setup.

## GHCR transport

Reuse the repository's pinned ORAS CLI and existing GHCR token lookup,
redaction and private-package checks before and after upload. A nonexistent
package may be created (GHCR defaults new packages to private); non-404 lookup
errors and existing nonprivate packages stop before any upload. Capsule authentication is temporary and
removed when the transfer ends.

```sh
python3 tools/publish/capsule.py push --layout /estate/capsule-oci \
  --remote ghcr.io/phi9t/vaso-capsule-torch-cu133:release-name
python3 tools/publish/capsule.py pull --layout /estate/capsule-cache \
  --remote ghcr.io/phi9t/vaso-capsule-torch-cu133@sha256:MANIFEST_DIGEST
```

These use `oras cp`, not `oras pull`: normal ORAS pull skips unnamed image
layers. Pull requires an immutable manifest digest. Reusing a local OCI layout
allows native blob reuse; a normal image transfer still retrieves every missing
layer, not automatic selective capsule fetching. The legacy chunked rootfs
artifact publication path remains unchanged.

Before publication, validate the build and restore the capsule into a fresh
estate with the original build output base unavailable. Run the actual
qualification there, and check the published digest/private visibility.
Package publication is not complete merely because local pack or GPU tests pass.

## Privacy and verification

Packing excludes common credential/history directories, Python bytecode/test-cache
directories (which can embed staging host paths), runtime directories,
logs, and selected host identity files. This is **not a general sanitizer**.
Prepare sanitized release inputs; review names, symlink targets, build metadata
and embedded strings as required by the existing publication policy. Never
include credentials, host-specific qualification logs, or raw estate/workspace
trees. No extra integrity scans are introduced.

Run `python3 tools/publish/capsule_test.py`. To include the real OCI roundtrip:
`CAPSULE_TEST_UMOCI=/tools/umoci python3 tools/publish/capsule_test.py`.
Record cold/warm upload/download, pack, unpack, smoke and first-iteration
timings separately. Small-fixture success is not full-capsule qualification.
