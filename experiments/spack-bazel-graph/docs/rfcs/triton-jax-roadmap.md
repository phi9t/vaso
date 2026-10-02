# RFC: Beyond PyTorch — Triton, then JAX

Status: superseded (2026-09-30) by `.scratch/execution-plan/spec.md`, which
absorbs this RFC's facts and open questions (phase P3, decision D11).
Date: 2026-09-29

## Scope and priority

The migration's targets are **PyTorch → Triton → JAX**
(`docs/native-migration.md`, "Sequencing to the big three"). Beyond PyTorch,
only Triton and JAX matter. Nothing else in the Spack universe is a goal in
its own right; other packages matter only as dependencies of these three.

## Decisions already made that shape these phases

| Decision | Effect on Triton/JAX |
|---|---|
| The PyTorch closure is pruned with `^py-networkx~default` (ticket 14 in `.scratch/pytorch-frontier-convergence/`) | LLVM, numba, pandas, scipy and matplotlib leave PyTorch's path. Their existing native providers keep their status and are reused if a later phase needs them |
| LLVM moves to the Triton phase | Triton builds against **its own pinned LLVM commit**, not Spack's `llvm@20.1.8`, so LLVM work is scoped to what Triton needs |
| The Python line is `python@3.13.13` (ticket 01) | Triton and JAX wheels and extensions target cp313. Every Python-bound native must derive the ABI from the prefix (ticket 06 guard) |
| Frontier invariants (`native-migration.md`) | Spack owns the DAG shape; each provider flip is ABI-parity gated; guards cover version parity, dependency wiring, ODR, freeze and I/O hygiene |

## Phase 2: Triton (to be specced)

Known:
- Hermetic Spack v1.2.2 has `py-triton` (`PyTriton`), verified by
  `//tools:pytorch_recipe_provenance_test`.
- Triton "consumes the migrated torch", so it starts after the native
  PyTorch flip.

To settle when specced:
- The LLVM pin: Spack `py-triton`'s LLVM dependency versus upstream Triton's
  pinned llvm-project commit. Is a native LLVM built at Triton's pin, or taken
  from Spack's reference? What's the smallest LLVM configuration Triton needs
  (projects, targets NVPTX/AMDGPU, MLIR)?
- The CUDA toolchain surface: ptxas/nvdisasm, and the CUDA version Triton
  expects versus the rootfs CUDA 12.9.
- The ABI/behavior gate: wheel parity plus a Triton kernel launch in the
  sealed CUDA insula (B200), token-gated like PyTorch.

## Phase 3: JAX (to be specced)

Known:
- Hermetic Spack has `py-jax` and `py-jaxlib`. `py-jaxlib` carries an
  upstream Bazel/XLA build surface.
- JAX is "most naturally native": upstream already builds `jaxlib` with
  Bazel, so the likely path re-exports upstream XLA/`jaxlib` Bazel targets
  instead of replaying a Spack recipe.
- JAX's Python dependencies bring back numpy and **scipy** (dropped from
  PyTorch's closure by ticket 14), plus ml_dtypes and opt_einsum.

To settle when specced:
- Whether "Bazel-native upstream" fits the Spack-as-reference model (Spack
  still owns the DAG shape), or needs a new provider kind.
- CUDA/cuDNN/NCCL alignment with the PyTorch closure's native providers, and
  ODR risk if jaxlib vendors its own copies.
- The gate: jaxlib parity plus a CUDA JAX computation in the sealed insula,
  behind a token.

## Prerequisites carried from the PyTorch phase

- The native PyTorch flip (after tickets 08, 09 and 10, then the
  `build-native-pytorch` token).
- Execution hygiene in the insula (ticket 13: per-run `TMPDIR` on disk and a
  bounded private `/tmp`) before any LLVM- or XLA-sized build.
- The collaboration and relay machinery (`docs/agents/cross-agent-collaboration.md`)
  for lead/follower execution.
