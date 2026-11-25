# Repeated Inkling inference in the Pascal capsule

`inkling_repeated_inference.py` exercises the real pinned Transformers
`Inkling2LoopForCausalLM` text graph with reduced, randomly initialized FP32
weights. It compares the Pascal Triton acceptance adapter with the unpatched
eager CPU implementation. A passed result does not qualify trained checkpoints,
packed NVFP4/MXFP8 loading, image input, MTP, padding, or production throughput.

The capsule must supply Torch, the source-built Pascal Triton wheel, the pinned
Transformers implementation, and the acceptance adapter plus its kernel module.
The launcher supplies both SM61 GPUs under the shared GPU lease and standalone
Insula process owner. Python packages and tools must come from the capsule;
the writable estate holds only the declared test source, caches, and receipts.

From inside that capsule, with its qualification environment configured:

```sh
python3 /workspace/inkling_repeated_inference.py \
  --output /workspace/receipts/inkling-repeat-01.json
```

Defaults exercise three model seeds (101, 202, 303), six input scenarios, and
both GPUs: 36 model cases. Prefill lengths 1, 7, 8, 9, and 17 cover convolution
and sliding-window boundaries; a separate batch-of-two case tests row indexing.
Each case checks full logits, mixed one-token and two-token cached continuation,
attention/convolution cache values, and two fresh-cache greedy generation runs.
The generated tokens must match the reference exactly; logits and cache values
must meet the recorded numerical tolerances. Warm repeated logits and tokens
must match exactly. Every case must invoke all twelve adapter operation classes.

Generation is a fixed four-token greedy loop through the forward/cache API.
EOS stopping is disabled so every run exercises decoding. GenerationMixin's
sampling/stopping interface and meaningful natural-language quality need
separate checkpoint tests. The eager reference also checks cached outputs
against an uncached full-sequence pass, including the generated trajectory.

For diagnosis, `--seeds`, `--cases`, `--new-tokens`, and `--warm-repeats` can
select an explicit subset. The program always requires both visible SM61 GPUs,
at least two generated tokens, and at least two warm repeats; an empty GPU loop
cannot produce a pass. `--cases short,below_window` is a smaller useful first
run. The complete default matrix remains the acceptance target.

Use fresh output paths for separate process launches. A cold process/cache run
and later launches reusing its disk compiler cache are different evidence and
should retain separate runtime receipts. This program has no internal wall-time
deadline. Its lifecycle owner handles cancellation and records complete cleanup
before releasing GPU leases.

Each reference start, case start, case pass, and final outcome emits one compact
JSON event. The result file is replaced atomically with progress and per-case
evidence; a failure retains the failing seed, input case, GPU, exception, and
traceback and exits nonzero. Source paths and package versions identify the
loaded implementation. The caller records the admitted capsule and runtime
plan digest alongside this result.

The reusable `AdapterPatches` context saves original model methods before
installing the adapter and restores them after each GPU section. Construct it
before any other code calls `backend.install()`. All reference computation runs
with the originals restored; callers must not run model inference concurrently
while these global methods switch. `generate_greedy` is reusable for a separate
checkpoint test, with unpadded token IDs and an already configured model.
