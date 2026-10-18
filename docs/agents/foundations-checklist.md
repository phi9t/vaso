# Foundations checklist (gate before any build ticket)

Origin: the 2026-09-29 → 10-02 torch/Triton build-out
(`.scratch/process-improvement/spec.md`, failure modes F1a–F1d). Five
foundation decisions were made *after* work had been built on them. That cost
5 obsoleted provider commits, about 11 h of Triton proofs, 7 jaxlib commits
and at least 5 graph recaptures. Each foundation question took a research
agent 10–20 minutes to answer.

**Rule:** an effort's spec has a `## Foundations` section with every row
below answered, evidenced and **confirmed by the human** before the first
build ticket is briefed or claimed. Until then only research, spec and guard
tickets may run.

| # | Question | Evidence required | Typical source |
|---|---|---|---|
| 1 | Exact upstream versions and pins for each component, per platform line | file:line from the **matching** CI block (not the nearest one) | e.g. PyTorch `.ci/docker/common/install_cuda.sh` `install_<line>` |
| 2 | Platform lines (CUDA, OS, driver floor) | the host driver's max CUDA; the vendor's support matrix | `nvidia-smi`; release notes |
| 3 | Compiler family and version **per profile**; which compiler drives device code | each project's supported compilers; the nvcc host window (`crt/host_config.h`) | build scripts; upstream CI |
| 4 | The one LLVM library (if any target embeds LLVM) | `scripts/triumvirate/llvm_alignment.py` output | skill `triumvirate-llvm-alignment` |
| 5 | Language runtime pins (Python, etc.) | the pin in Spack config (`require`), not per spec | `tools/spack_dist.bzl` |
| 6 | Feature scope: each `USE_*` on/off, with the reason for each "off" | the upstream default and the human's decision | the effort spec's feature matrix |
| 7 | Profiles: what may be built or loaded together | an ABI or compiler argument | `docs/toolchain-and-profiles.md` |
| 8 | Silent defaults the stack would otherwise pick (Python, compiler, OpenMP runtime, runtime JIT compilers, downloads) | a list, each pinned or guarded | the defaults audit test |
| 9 | Acceptance: the exact gate command per line that means "done" | the command exists, runs, and is red with listed stages | `scripts/agents/triumvirate-gate.sh` |
| 10 | Resource parameters (jobs, memory, GPUs, budgets) | host facts cited (`nproc`, RAM, GPU count) | — |

## Process

1. **Research.** One research agent answers every row with file:line or
   command-output evidence.
2. **Cross-check.** A second agent independently re-derives rows 1, 3 and 4
   from the sources. Disagreements go to the lead.
3. **Decide.** The lead presents the rows to the human in **one batch**. The
   confirmed answers go into the spec with the date.
4. **Guard.** Each confirmed answer that can be checked mechanically gets a
   guard or a gate stage *before* builds start.
5. **Reversals.** Changing a confirmed row is allowed, but the change must:
   - list the landed work it obsoletes (commits, providers, proofs, graph
     captures);
   - estimate the rework;
   - be confirmed by the human.

   Record it in the spec's decision log.

## Anti-patterns this prevents

- Reading pins from the nearest CI line (12.6) instead of the target line
  (12.9/13.0).
- Building providers ahead of scope decisions (MAGMA, oneDNN).
- Letting a tool's default choose the runtime (Spack → Python 3.14) or the
  compiler (Triton's clang on a gcc-built LLVM).
- Numeric parameters chosen without host facts (`max_jobs=8` on 216 cores).
