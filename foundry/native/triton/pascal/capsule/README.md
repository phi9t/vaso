# Standalone Insula Pascal capsule

Vaso owns the build recipe and dependency closure; standalone Insula owns the
admitted runtime, GPU driver projection, and process supervision. The build
and qualification entrypoints are installed at `/opt/vaso/pascal/bin`.

The staged rootfs derives from the qualified CUDA 12.6 CPython 3.12 developer
rootfs. Add the pinned LLVM/MLIR prefix, JSON headers, explicit build Python
packages, uv binary, pinned Transformers source and runtime dependencies,
Vaso's Torch wheel, source archive/patch, and Triton wheel. Install wheels
inside the capsule with uv using offline local inputs. Include the
qualification fixtures under `/opt/vaso/pascal/qualification`. Supply the
unversioned zlib linker alias pointing at the same rootfs's versioned library.
Never change an already published bundle. Publish a new generation after
staging finishes.

The ordinary build needs no GPU allocation. Runtime JIT requires CUDA 12.6
ptxas, libdevice and headers; the native NVIDIA driver is an admitted host
projection, not part of the frozen rootfs. The final acceptance has no extra
dependency mounts; mutable sources, build caches, and receipts live in
the private Estate's /workspace.

Use a spec naming the exact immutable generation and its private Estate.
The caller owns physical GPU resource admission for qualification. Examples:

```sh
foundry/build.sh --pascal-capsule --insula /path/to/insula \
  --spec build.toml --state /path/to/estate/insula-state --plan-only build
foundry/build.sh --pascal-capsule --insula /path/to/insula \
  --spec build.toml --state /path/to/estate/insula-state --run-id build-sm61 build
```

On hosts without delegated cgroups, a separately authorized external
supervisor can consume the reviewed plan digest and invoke the standalone
runtime's `payload` interface. There is no fallback to an ambient host
compiler or to an unsupervised shell. Qualification of a specific published
generation is separate from source build completion.

This source-wheel path is additive and does not replace the existing
Blackwell Bazel release matrix.

## Repeated Inkling inference

The complete published capsule can run repeated FP32 text inference through
Vaso's unattended entrypoint:

```sh
foundry/build.sh --inkling-inference \
  --insula /path/to/standalone/insula \
  --supervisor /path/to/insula/workloads/acceptance/payload_owner.py \
  --bundle-store /path/to/bundle-store \
  --bundle-digest EXACT_GENERATION_KEY \
  --lease-cli /path/to/vaso/insula/execution/lease.py \
  --lease-estate /path/to/shared-estate \
  --output /path/to/new-private-run \
  --repeat 3
```

The entrypoint creates private Estates, snapshots the workload sources, acquires
the exact public tiny checkpoint inside a CPU-only capsule, and then runs three
independent processes of each suite. It needs both SM61 GPUs and uses the
existing shared GPU lease authority. Packages, Python, JIT tools, and the adapter
come from the immutable bundle; there are no extra dependency mounts. Model
weights, workload sources, JIT caches, and receipts reside in the private Estate.

The reduced random-weight suite varies model seeds, prompt length, batching,
chunked continuation, and logical loop caches. The published-checkpoint suite
uses the original tokenizer and model.generate, cached and uncached decoding,
short prompts and a 128-token window boundary. Every GPU result is compared
with an unpatched eager CPU reference. Each process repeats fresh requests to
check exact warm-run logits; the controller also checks generated token identity
across the independent processes. Tolerances are fixed and reported alongside
observed errors. The first process starts with a fresh JIT cache; later processes
reuse that suite's cache.

The published tiny checkpoint is untrained. This qualifies its checkpoint
conversion and FP32 text execution, not language quality, the full trained
checkpoint, BF16 execution, vision, MTP, padding, or packed sequences. The upstream full checkpoint conversion expands experts and does not fit this
Pascal host. The native packed expert mode described below avoids that expansion.

The external owner is an explicit finite execution mode. Each stage requires a
successful payload and complete child cleanup; GPU leases are released after
cleanup and an idle-device check. This is not production service supervision.
The default two-hour per-payload budget covers cold compilation; it is adjustable.
Failure retains the plan, logs, process identity, lifecycle receipt, and model
comparison receipt. Use a fresh output directory for a new attempt.

The final status.json and summary.json aggregate process counts, numerical
errors, token stability, cleanup, and lease release. This workflow does not
rebuild the already qualified native packages.

## Native packed weights with FP32 compute

The packed loader, bounded expert cache, Triton projections and text generation
entrypoint are described in [inkling_packed_inference.md](inkling_packed_inference.md).
Run the complete repeated suite through `foundry/build.sh --inkling-packed-inference`.
This route keeps NVFP4/MXFP8 expert storage and FP32 activations and arithmetic.
An optional fixed archive on a second admitted GPU serves reused packed weights;
`--archive-mib` enables its repeated qualification. The generation entrypoint
uses native final-message continuation and can require a completed final answer.

CUDA diagnostics use the pinned CUDA 12.6.77 cuobjdump and nvdisasm pair from the
12.6.3 redistributable. The CPU capsule probe assembles an SM61 DP4A fixture with
CUDA 12.6 ptxas and checks both disassemblers, so incompatible newer tools cannot
silently replace this diagnostic path.
