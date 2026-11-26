# Native packed Inkling inference on Pascal

This mode keeps Inkling expert weights in their checkpoint formats and performs
the model's arithmetic in FP32. NVFP4 weights remain E2M1 nibbles with per-row
16-value E4M3 blocks and a per-expert outer scale. MXFP8 weights remain E4M3 bytes
with the actual checkpoint's per-row 32-value UE8M0 scales. Activations and
ordinary model parameters are FP32. There is no activation quantization.

The Triton projection decodes values as it consumes them in registers and
accumulates in FP32. It does not allocate a dequantized expert matrix in GPU
memory. Native w13 rows alternate gate and up values; the adapter preserves
that layout and separates the projection outputs. NVFP4 outer scaling occurs
before each activation product, matching the reference weight reconstruction.

## Loading and residency

`packed_checkpoint.load_packed_checkpoint(path, cache_budget_bytes=...)` returns
the packed model and standard Transformers loading information. It replaces the
native expert modules while their parameters are still on meta and removes the
two expert dequantization converters. The pinned Transformers conversion path
continues to load every ordinary parameter in FP32.

For text generation, the optional argument
`text_device="cuda:0"` materializes ordinary text weights directly on that
device through the native loader and keeps vision on CPU. It avoids first
allocating the entire FP32 text model in CPU memory. This internal placement
does not install Accelerate dispatch hooks. The default API behavior remains
CPU loading when no text device is supplied.

Expert backing tensors remain in CPU safetensors storage, outside module
parameters and buffers. Moving the model to CUDA does not move or expand this
backing. A shared LRU admits only selected expert slices, keyed by physical model
layer, projection, expert and CUDA device. Logical loop iterations share the
same physical weights. The default retained-payload budget is 256 MiB per CUDA
device. A single expert larger than the budget is rejected before allocation.

The budget counts packed codes, scales and outer scales retained in the LRU.
It is separate from ordinary parameters, activations, CUDA allocator reservations
and in-flight tensors. Reports include actual peak allocated CUDA memory as well
as cache accounting. Cache consumers record their CUDA stream, and callers must
use `model.packed_weights.close()` when releasing all model-owned GPU storage.
`clear_cache()` clears the compute LRU; `clear_archive()` clears an optional fixed
archive. `close()` releases both.

`place_packed_text_model(model, device)` places the text model and output head on
the selected device and leaves the vision tower on CPU. This placement accepts
text only. It helps fit the trained model's ordinary FP32 text parameters on an
8 GiB GPU while retaining a bounded packed expert cache.

The initial adapter groups routed rows by expert and runs the packed projection
for each group. It is a correctness-oriented implementation for prefill as well
as decode. Expert transfers, Python dispatch and small-kernel launches can
dominate end-to-end latency. Operator speedups do not imply the same full-model
speedup, especially when the expert bank is larger than GPU memory.

## Optional fixed archive on a second GPU

A separate GPU can retain a fixed subset of the native expert bank. Enable this
explicitly with `--archive-device` and `--archive-mib`; the default remains off.
The archive and compute devices must be distinct, and both must be admitted and
leased for the complete model lifetime.

`model.packed_weights.prepare_archive(archive_device, compute_device=device,
budget_bytes=...)` preflights a deterministic selection of complete physical
layers. It prioritizes layers by their logical reuse in the pinned model. The
archive contains the original packed codes, scales and FP32 outer scales. On a
compute-cache miss, archived slices are copied through explicit synchronous
CPU staging; remaining slices come from CPU safetensors backing. All projection arithmetic stays on the
compute GPU. The fixed selection avoids repeatedly duplicating the same hot CPU
pages in another general LRU.

For the admitted trained configuration, the 12 core layers are visited three
times. Their packed storage is 6,879,719,424 bytes (6.407 GiB); adding one outer
NVFP4 layer uses 7,453,029,376 bytes (6.941 GiB). These are payload counts, excluding
CUDA contexts, allocator rounding and temporary storage. Availability is checked
at runtime; reserving an archive is not a guarantee that all remaining CPU pages
fit in RAM.

The archive preserves the CPU mappings without pinning or cloning the entire
bank. Archived ranges are not read again for CUDA inference, allowing their CPU
pages to become cold. Initial population still reads those weights once. The
report records peer capability for diagnosis only. Cross-GPU copies use
explicit host staging regardless of that capability. Archive payload, source accesses and transfer bytes are reported
separately from the compute LRU. Release both with `close()`.

## Repeated qualification through Vaso

Publish a new immutable capsule containing all files from this capsule directory
under `/opt/vaso/pascal/qualification`, and the wrapper under
`/opt/vaso/pascal/bin/inference-python`. The capsule also contains the pinned
Torch, Triton, Transformers, CUDA compiler, LLVM and Python dependencies.
Do not overwrite a published generation.

```sh
foundry/build.sh --inkling-packed-inference \
  --insula /path/to/standalone/insula \
  --supervisor /path/to/insula/workloads/acceptance/payload_owner.py \
  --bundle-store /path/to/bundle-store \
  --bundle-digest EXACT_GENERATION_KEY \
  --lease-cli /path/to/vaso/insula/execution/lease.py \
  --lease-estate /path/to/shared-estate \
  --gpus 1 \
  --output /path/to/new-private-run \
  --repeat 3 --new-tokens 8 --cache-mib 64
```

Physical GPU indices are explicit. Admission leases only that subset, waits for
its existing users to finish, and holds the lease through payload cleanup.
The capsule command selects those devices by their runtime UUIDs so namespace
subsets cannot silently choose a different card. Runtime identities and receipts
remain in the private Estate.

An existing exact tiny checkpoint can be supplied with `--checkpoint`; otherwise
the controller acquires it inside a CPU-only capsule. Checkpoint files are data
in the Estate. Packages, runtime helpers and JIT tools come from the capsule,
with no extra dependency projections.

The controller checks capsule closure, both packed operators and production
projection shapes, three independent model processes, forced cache eviction,
and the standalone text-generation entrypoint. Each model process compares
packed execution with independent eager CPU and dense FP32 GPU references,
checks cached and uncached generation, uses distinct batched prompts, crosses
the sliding-window boundary, and repeats fresh requests. Warm repeats require
identical logits, and independent processes must generate identical token IDs.

With two admitted GPUs, `--archive-mib 16` exercises the optional fixed archive
on the peer of each compute device. It preserves the same independent CPU and
dense-GPU comparisons, additionally checks archive and CPU source coverage, and
requires both archive and compute-cache storage to be released.

The qualification checkpoint is
`hf-internal-testing/tiny-inkling2-mini@ee242a48af34ff0729518f4ac28cfcb847e470ab`.
It is untrained. Passing this suite establishes loader and execution correctness
for these covered paths; it does not establish trained-model language quality.

## Text generation

For the full public trained checkpoint, use the bundled acquisition entrypoint
inside a networked CPU-only capsule:

~~~sh
/opt/vaso/pascal/bin/inference-python \
  /opt/vaso/pascal/qualification/inkling_acquire_production.py \
  --output /workspace/checkpoints/inkling2-mini
~~~

It pins
thinkingmachines/Inkling-2-Mini@c9d028302cd0f035fdf8e7e91f089a4ce94d2195,
resumes partial downloads, and validates completed LFS assets against their
published SHA256. It never supplies credentials. A completed acquisition receipt
allows reuse without repeatedly hashing the complete model shard.

Inside an admitted, supervised GPU capsule, run:

```sh
/opt/vaso/pascal/bin/inference-python \
  /opt/vaso/pascal/qualification/inkling_packed_generate.py \
  --checkpoint /workspace/checkpoints/inkling2-mini \
  --prompt "What is two plus two? Answer with just the number." \
  --max-new-tokens 16 --repeat 2 --cache-mib 256 \
  --result /workspace/receipts/packed-generation.json
```

The default `--response-mode final` uses the supported continuation API with an
empty assistant message, so the native prompt ends in the model's final-text
channel. Setting reasoning effort to none alone does not force that channel in
this checkpoint. `--response-mode model` retains the model's channel choice;
`--raw-prompt` preserves caller-supplied raw text. The checkpoint template itself
is not modified.

Repeat `--prompt` to exercise several prompts in one loaded model. Each repeat
is a fresh request with its own generation cache; expert residency may remain
warm. Comparisons are scoped to each prompt. Reports separate final text from
the presence of reasoning, identify token-budget truncation and turn completion,
and retain finite-logit, exact-repeat, format-dispatch, time, memory and cleanup
evidence. Use `--require-final-answer` for trained acceptance that must produce a
completed final answer rather than only finite truncated tokens.

For text inference with a fixed archive, admit and lease both cards, expose them
in the desired order, and run the same entrypoint with, for example:

```sh
--device cuda:1 --archive-device cuda:0 --archive-mib 7168 --cache-mib 512 \
  --prompt "List the numbers one through five, separated by commas." \
  --prompt "What is 2 + 2? Respond with only the number." \
  --repeat 2 --max-new-tokens 32 --require-final-answer
```

Actual allocated/reserved GPU memory includes costs outside both payload budgets.
The generation-phase peaks in request rows exclude earlier checkpoint and
archive-loading phases, which are recorded separately when available. The dedicated
generation entrypoint defaults HF_DEACTIVATE_ASYNC_LOAD to 1 to bound concurrent
CPU staging during direct device loading; an explicitly supplied environment
setting is retained and recorded. The qualification suite checks every ordinary
parameter and buffer against the independent CPU model after direct GPU loading.

The source checkpoint remains the serialization format. The packed model
intentionally rejects `save_pretrained`, since its external expert backing
would otherwise be omitted from the saved state.

## Scope

This is the SM61 inference path in the pinned Vaso capsule. It does not enable
native FP4/FP8 arithmetic on Pascal. Larger-batch grouped GEMM, training,
vision input and distributed model execution have separate qualification needs.
Finite external-supervisor runs are qualified independently of production
systemd service supervision.

### Integrity regressions

The pinned production acquisition hashes every current asset before admitting
existing bytes; a prior acquisition receipt cannot substitute for verification.
Suspect final or complete partial assets are quarantined rather than reused.
Small-file SHA256 pins were cross-checked against independent downloads of the
same immutable revision; the LFS assets use their published SHA256. A freshly
downloaded digest mismatch fails closed, and the next invocation starts fresh.
No per-token hashing is required. Acquisition assumes exclusive ownership of
its destination; it does not protect against a concurrent non-cooperating
writer after admission.

Cross-GPU archive refills stage the selected tensor slice in host memory
and synchronize before cache admission. No direct peer mode is exposed.
Peer-access capability in receipts is diagnostic, not an integrity guarantee.
Run the targeted offline regressions with `uv run --offline --no-project --
python p0_integrity_test.py`. Run `p0_transport_test.py` only with both GPUs
admitted and leased, using the pinned runtime, and retain its JSON receipt.
