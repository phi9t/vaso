# Cross-framework decoder speedrun

This workload turns Vaso's built Torch, Triton, JAX, and jaxlib packages into a
matched training experiment. It is a workload-scale architecture lab, not a
checkpoint-compatible reproduction of the released models.

## What runs

Each arm trains the same deterministic token stream, model shape, optimizer,
and number of steps. Optimized arms run their own matched baseline first and
only record a speedup claim when the optimized steady-state throughput is
higher.

| Workload | Framework | Execution path |
|---|---|---|
| `W7a` | Torch | reference activations and explicit math SDPA |
| `W7b` | Torch | explicit Triton activation forward/backward and PyTorch FlashAttention SDPA |
| `W7c` | JAX | JAX activations and XLA attention |
| `W7d` | JAX | explicit Pallas GPU activation forward/backward and cuDNN attention |

The optimized arms require forward/backward numerical parity before a case can
pass. Pallas GPU kernels are retained as compiled-lowering evidence; Torch
records its Triton package identity and a FlashAttention microbenchmark. Model
results separately report matched end-to-end throughput, so a fast isolated
kernel cannot be mistaken for a faster trainer.

The four family substrates retain these graph-changing seams:

| Family | Retained structure |
|---|---|
| GPT | learned positions, LayerNorm, causal MHA, GELU, tied embeddings |
| Qwen3 | RoPE, RMSNorm, GQA, per-head QK norm, SwiGLU |
| mini-Kimi-K3 | 3:1 linear/full attention, latent Q/KV projections, SITU, top-2 MoE |
| mini-GLM-5.3-Flash | 3:1 linear/sparse attention, latent projections, ReLU2, dense prefix and top-2 MoE |

`spec.py` is the authoritative retained/omitted-element contract. In
particular, the mini families omit checkpoint naming and costly production
features such as KDA recurrence, mHC streams, the lightning indexer, and MTP.

## Run it

Prerequisites are a prepared estate line, built `output-base-torch` and
`output-base-jax` trees for that line, one available NVIDIA GPU, and an
unchanged checkout for the duration of the attempt.

Run a bounded smoke across all families, frameworks, and optimization arms:

```sh
foundry/workloads/nanogpt_speedrun/run.sh \
  --estate-root "$VASO_ESTATE_ROOT" \
  --line cu129 --mode smoke
```

Run the full benchmark:

```sh
foundry/workloads/nanogpt_speedrun/run.sh \
  --estate-root "$VASO_ESTATE_ROOT" \
  --line cu129 --mode benchmark --color always
```

`--color always` is useful when the command runs in tmux. The launcher emits
30-second elapsed-time heartbeats even when a compiler is quiet. Useful bounded
subsets include `--backend torch`, `--backend jax`, `--arm baseline`, and
`--families gpt,qwen3`.

Attempts are append-only under:

```text
<estate>/agents/<agent>/nanogpt-speedrun/<run-id>/
  state.json
  summary.json
  logs/{torch,jax}.log
  {torch,jax}/summary.json
  {torch,jax}/W7*.json
  {torch,jax}/W7*/progress.json
  history/{launcher,torch,jax}/...
```

Each stage records structured `nvidia-smi` snapshots before and after execution.
Snapshot failure is retained as diagnostic evidence but does not hide or replace
the workload result.

Inspect the latest attempt independently of its launching terminal:

```sh
foundry/workloads/nanogpt_speedrun/run.sh \
  --estate-root "$VASO_ESTATE_ROOT" --status
```

Resume an interrupted attempt without replacing its evidence:

```sh
foundry/workloads/nanogpt_speedrun/run.sh \
  --estate-root "$VASO_ESTATE_ROOT" \
  --line cu129 --mode benchmark --run-id <run-id> --resume
```

Resume refuses a different checkout identity. Passing stages are skipped;
failed or interrupted stages move their complete prior profile output and
summary under `history/`, retain their prior stage record, and append the retry
to the old launcher log. A resume also requires the same line, mode, backend,
arm, family list, and GPU count as the original invocation, so repeat any
nondefault selection arguments from the original command.

## Performance interpretation

Smoke proves imports, compilation, training, numerical parity, and durable
reporting. It is not a stable performance result. Use `benchmark` on an idle
GPU, keep power/clock conditions stable, and compare the recorded mean and
per-step timings. The first compiled steps are excluded by the configured
warmup.

PyTorch's in-tree FlashAttention is used because it is already a declared,
verified feature of Vaso's Torch build. An external `flash-attn` wheel is not
silently installed. New kernel libraries become experiment arms only after
their source, version, ABI, and package ownership are added to Foundry's pinned
closure.

## Workload attestation and CI

The supported acceptance path is `run.sh`. It runs the selected matrix through
Foundry's insula entrypoint and existing GPU/estate leases. It never rebuilds
native packages. Prepare the selected rootfs, Python/dependency prefixes,
native prefixes, provider metadata, and build plans before starting. JAX also
requires the pinned wheels in the estate distdir. A missing prerequisite fails
the attempt; it is not a successful skip.

Each attempt now finishes with an `attestation/` evidence bundle:

```text
attestation/
  evidence/          copied state, summary, workload reports, logs, input records
  verifier/          exact standard-library verifier code used for acceptance
  report.json        machine-readable acceptance, scope, errors, comparisons
  report.md          human-readable performance and qualification limits
  manifest.json      SHA-256 of every retained evidence/report file
```

Acceptance independently checks the selected workload/family/arm matrix,
source identity, canonical model/run specifications, complete finite losses,
positive step samples, recomputed warmup-excluded timing statistics, Vaso runtime
paths, component forward/backward numerical parity, and matched comparisons.
Recorded green verdicts and zero process exits alone are insufficient. An empty
summary, missing report/log, duplicated case, changed source, inflated numerical
tolerance, or inconsistent timing fails acceptance and returns nonzero.

A selected subset is labeled `selected-subset`; it cannot claim full matrix
coverage. A smoke attempt is labeled `smoke-only` and never supports a speedup
claim. Benchmark results are single-run observations. The report includes
median step times and total training wall time alongside mean throughput, so
startup costs and timing spikes remain visible. Correct optimized arms may be
slower: acceptance does not require a speedup, and regressions stay in the report.
Torch runs eagerly while JAX uses whole-step JIT; the backend comparison includes
that execution difference. Use repeated runs under controlled GPU conditions
before making a stable performance or regression claim.

New runs retain the selected rootfs bundle manifest and existing native provider
metadata/build plans under `inputs/`. Resume refuses changed retained inputs.
Provider `action_input_digest` values identify recorded build inputs. They do not
verify the installed prefix bytes or establish that the native action executed
cold during this attempt. GPU snapshots are diagnostics, not an idle-device or
lease-isolation proof.

Replay and package an existing attempt without a GPU or framework imports:

```sh
python3 -B foundry/workloads/nanogpt_speedrun/attest.py \
  --attempt-dir /absolute/path/to/completed-attempt \
  --out /absolute/path/to/new-attestation

python3 -B foundry/workloads/nanogpt_speedrun/attest.py \
  --verify-bundle /absolute/path/to/new-attestation
```

The packaging command requires full matrix coverage by default. For intentional
subsets, pass `--allow-subset`; the policy and narrower scope are retained.
Existing output directories are refused. Missing evidence creates a failed
report and returns nonzero. The bundle can be moved and verified offline:
verification checks file coverage, hashes, and the acceptance/report calculation.
The report binds the verifier code by digest. After the checkout's verifier
changes, use the retained copy to replay the original policy:

```sh
python3 -B /absolute/path/to/bundle/verifier/workloads/nanogpt_speedrun/attest.py \
  --verify-bundle /absolute/path/to/bundle
```

Retain the complete bundle in CI even when the command fails. Archive it in an
access-controlled artifact store and retain its manifest digest separately if
using it as a trusted record.

This is a **local unsigned workload attestation**. A hash manifest detects changes
relative to a trusted manifest; it does not authenticate an author or prevent
rewriting an entire bundle. Historical attempts can be evaluated, but absent
prepared-input records are explicitly labeled as legacy evidence. This verifier
complements the native build, ABI, release, and determinism gates. It does not
certify hermeticity, a fresh build, reproducibility, released-model equivalence,
or multi-GPU training. Public publication remains a separate authorized workflow.

## Failure recovery

`--timeout-seconds` bounds each backend stage (default 1,800 seconds), including
quiet children or partial output lines. Cancellation terminates the owned process
group; timeout returns stage exit 124, and cancellation returns 130. The launcher
returns nonzero and retains the stage outcome and logs. Missing build inputs
leave a failed `state.json`, summary, and attestation before GPU execution.

`--max-attempts` bounds retries, including the initial launch (default 3).
Repeat the original options when resuming. Each resume retains the prior summary,
attestation, stage record, and retried profile output under `history/`. An attempt
that reaches the limit requires a new run ID after correcting the failure.
Never edit a failed report to resume it.

Host verification needs a disk-backed `TMPDIR`:

```sh
TMPDIR=/absolute/path/to/disk-backed/tmp PYTHONPATH=foundry \
  PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest -q -p no:cacheprovider \
  foundry/workloads/criteria_test.py foundry/workloads/nanogpt_speedrun
```

The same suites are declared in `foundry/workloads/BUILD.bazel`. Host tests cover
portable replay, tampering, missing/duplicate evidence, numerical/timing
validation, subset/smoke claims, durable prerequisite failures, exit propagation,
cancellation, stage deadlines, and bounded recovery. They do not exercise CUDA.
