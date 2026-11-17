# Cuspy runtime thread priority proposal

Pinned upstream source: PyTorch 2.15 commit `31a77fc9c0a7c6fff15a1ff58c743f53b1a9d457`.

This patch is reviewed but **not wired into the existing two-patch build contract or published wheel/capsule**. It changes Chrome JSON and pftrace exporters to prefer an explicit non-null CUPTI resource-to-OS-thread mapping. The existing same-process external-owner fallback remains only when that mapping is absent; raw normalized ID remains the final fallback. External-correlation kinds/IDs are not merged or rewritten by this patch.

Evidence: four-rank FSDP2×TP2, three gated MLP layers, two MSE/AdamW steps on B200 with source-built Torch 2.15/CUDA13.3/Python3.13.13. Default requested NCCL resolves to NCCL2, whose recorder is `_dump_fr_trace(backend="nccl2")`, not legacy `_dump_nccl_trace`. Unmodified exporter yielded 48 strict collective/kernel joins and 72 missing backward joins. A diagnostic-only wrapper preserved exported lanes and recorded raw IDs, all external-correlation kind/ID rows, and explicit resource mappings. Mapping-backed strict same-thread nesting plus exact CUDA correlation yielded 120/120 joins across 60 group operations. Numerical trajectory and selected-GPU cleanup passed.

The mismatch was explicit: autograd launch had a correct resource mapping, but numeric external-owner lookup selected the main thread. No cross-thread temporal-only matching was used. The published capsule remains unchanged; diagnostic evidence does not establish that the proposed source patch has passed a rebuilt GPU run.

Run the focused regression against patched upstream source:

```sh
python3 resource_thread_priority_test.py /path/to/patched/torch/profiler/_cuspy/trace.py -v
```

The test extracts the actual JSON helper and pftrace generator AST and checks conflicting mappings, mapped zero, absent/null mapping, wrong-process owner, other-process mapping and raw fallback. No GPU, source inventory or custom hashing is used.

Remaining limits: CUPTI UDR-disarm warning remains; no throughput, delay-localization, full compute-marker attribution, arbitrary relocation, or clean-rebuild claim. NCCL2 hook durations are not assumed equivalent to legacy CUDAEvent or kernel durations.
