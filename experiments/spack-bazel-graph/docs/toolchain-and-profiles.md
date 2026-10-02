# Toolchain and build profiles

Status: adopted (human, 2026-09-30 → 2026-10-02)
Scope: the native PyTorch / Triton / JAX builds in `experiments/spack-bazel-graph`

The toolchain is the most important foundation piece, after rootfs
hermeticity. This document records the layered decisions, the evidence behind
them, and the invariants that keep them true. It also says how each invariant
is enforced and how to revisit the decisions when versions move.

## 1. Layers

| Layer | Decision | Evidence |
|---|---|---|
| Rootfs | One hermetic Ubuntu 24.04 rootfs per CUDA line, built from `rootfs/cuda_ecosystem.lock.json` by `rootfs/build_rootfs.sh --line`. Toolchain components come only from the rootfs, and only Bazel sets toolchain settings. | `.scratch/cuda-ecosystem/spec.md` |
| CUDA lines | **cu129** (CUDA 12.9.1) and **cu130** (CUDA 13.0.3, PyTorch 2.14's stable line). Both carry cuDNN 9.24.0.43, NCCL 2.30.7 (built from the tag), cuSPARSELt 0.8.1.1, cuDSS 0.7.1.4 and NVSHMEM 3.4.5. Exactly one copy of each, in the rootfs. Spack treats them as `buildable: false` externals. | PyTorch v2.14.0 CI (`install_cuda.sh`); `rootfs/verify_cuda_ecosystem.py` |
| Python | `python@3.13.13` only. Spack config: `python: require: "@3.13.13"`. | FC-01; the 2026-10-01 3.14 drift incident |
| LLVM | **One llvm-project commit, `35901313`** (23.0.0git, plus XLA's two source patches). Built once with gcc 13.3 and installed at `/usr/lib/llvm-23` in both rootfs lines. It is the only LLVM: Triton links it, the JAX profile compiles with it, and XLA builds the same sources. | `.scratch/one-llvm/research-2026-09-30.md`; `scripts/triumvirate/llvm_alignment.py` |
| Profiles | **Torch profile** (ships first) and **JAX profile** (separate). Torch and JAX are never built or loaded together until the unified-toolchain study concludes. | Human, 2026-10-02 |
| Compiler family | **One compiler family per profile.** Torch profile: **gcc 13.3** everywhere (torch, torchvision, torchaudio, Triton, the jaxlib-deps natives; nvcc with gcc as its host compiler). JAX profile: **clang 23** (jaxlib requires clang; CUDA via clang or nvcc, whichever works). | `.scratch/one-llvm/compiler-family-2026-10-02.md` |

## 2. Why the profiles are split

From the compiler-family deep dive (2026-10-02):

- **All-gcc across torch, Triton and JAX is impossible.** jaxlib 0.10.2's
  `build/build.py` says "Clang is the only acceptable compiler", and no XLA
  build config uses gcc.
- **All-clang is blocked on torch's CUDA build.**
  - nvcc's host gate (`crt/host_config.h`) rejects clang ≥20 (CUDA 12.9) and
    ≥21 (13.0). Forcing it with `-allow-unsupported-compiler` crashed the
    compiler (an ICE in XLA's `cuda_dnn.cc`).
  - Clang as torch's CUDA compiler is unsupported: torch passes nvcc-only
    flags, and its only clang+CUDA CI job is build-only, on clang 20.
  - Clang-built torch would need libomp, which the rootfs doesn't ship.
    `-fopenmp=libgomp` silently disables OpenMP codegen.
- **Mixing families in one process is safe in principle** when no C++ API
  crosses the boundary. The official wheels do it today: gcc torch, clang
  Triton and clang jaxlib all link one system libstdc++ and libgcc_s, and talk
  only through C ABIs (the CUDA driver, the Python C API, DLPack). The human
  still chose one family per profile, and a unified toolchain later.
- **A hidden cross was found and removed:** the first Triton build used
  clang 23 while statically linking the gcc-built LLVM. The torch profile
  rebuilds Triton with gcc 13.3 (conda-forge practice), so Triton's compiler
  matches its LLVM's.

## 3. Torch-profile invariants

Each invariant is checked in the insula against every ELF in the torch,
torchvision, torchaudio and Triton prefixes ("ALL"). It is also checked
against `/proc/self/maps` after importing everything and running a CUDA op
and one `torch.compile` ("maps").

| # | Invariant | Mechanical check |
|---|---|---|
| I1 | One C++ standard library: the rootfs `libstdc++.so.6.0.33`; no libc++ | no `NEEDED libc++`/`libc++abi` in ALL; exactly one `libstdc++.so.6` in maps; max imported `GLIBCXX_` ≤ 3.4.33 |
| I2 | No static libstdc++ copies | only libstdc++ defines `__cxa_throw` / `__gxx_personality_v0` (`nm -D --defined-only`) |
| I3 | The C++11 string ABI everywhere | no imports of `_ZNSs`/`_ZNKSs`/`_ZNSbIw` |
| I4 | One unwinder (libgcc_s) | no `NEEDED libunwind`; only libgcc_s defines `_Unwind_RaiseException` |
| I5 | One OpenMP runtime (libgomp) | no libomp/libiomp5 in NEEDED or in maps; no `__kmpc_*` imports |
| I6 | Library internals stay hidden | `libtriton.so` exports only `PyInit_libtriton`; no symbol is exported by two DSOs (`nm -D --defined-only \| sort \| uniq -d`) |
| I7 | Each DSO's compiler matches its static deps | Triton and its LLVM/MLIR archives both show `GCC: (Ubuntu 13.3.0` in `.comment` |
| I8 | The torch profile is gcc-only | no `clang version` in any `.comment` in ALL; nvcc's host compiler is g++-13; `-allow-unsupported-compiler` never appears |
| I10 | Runtime compilers are pinned | `CC=/usr/bin/gcc` and `CXX=/usr/bin/g++` in the runtime env; Triton's `_find_compiler` and inductor's `get_cpp_compiler()` both resolve to gcc-13; never a global `CXX=clang++` |
| I11 | One CUDA driver | exactly one `libcuda.so.1` in maps; every `libcudart` is from the line's CUDA major |

The JAX profile adds **I9** (jaxlib is clang-only: `.comment` shows clang
23.0.0git at 35901313, and the CUDA path follows its policy entry), and
re-applies I1–I6 and I11 to its own prefixes.

## 4. Enforcement map

| What | Where |
|---|---|
| CUDA versions, single copy, one LLVM in the rootfs | `rootfs/verify_cuda_ecosystem.py` (static and `--gpu`) |
| One Python, one LLVM in every graph | `//tools` graph guards |
| Per-consumer compiler pathway (flags, paths, forbidden options) | `tools/compiler_pathways.json` + `//tools:compiler_pathway_guard` |
| Built-artifact producers (`.comment`) | `tools/compiler_provenance.py`, a final-gate stage |
| I1–I6, I10, I11 at runtime | the ABI-invariant stage of the final gate (work item B5) |
| Torch and JAX never together | the profile guard (work item B3) |
| Everything, per line | `scripts/agents/triumvirate-gate.sh --profile torch\|jax --line cu129\|cu130` → `acceptance-<profile>-<line>.json`; NP-17 refuses to flip without it |
| Commit hygiene | `scripts/agents/verified-commit.sh` → `standing-verify.sh` |

## 5. Revisiting the decisions

- **New torch, Triton or JAX versions:** use the
  `triumvirate-llvm-alignment` skill and `scripts/triumvirate/llvm_alignment.py`
  to find the one LLVM commit that can serve all of them. The commit and the
  JAX version change only with the human's decision.
- **Unified toolchain (phase 3):** re-run the compiler-family deep dive
  against the then-current versions. Candidates:
  - a clang that both nvcc lines accept as host (≤19 for CUDA 12.9), used
    with the one-LLVM library;
  - clang as torch's CUDA compiler, once upstream supports it;
  - all-clang with libomp shipped in the rootfs.

  Any choice must keep I1–I11 green.

### The planned next round: a unified nvcc+clang toolchain (human, 2026-10-02)

Find a clang version that forms a **valid nvcc+clang pair**, meaning nvcc
officially accepts it as the host compiler on the chosen CUDA line, and that
can build **torch, Triton and JAX together**. It would probably get its own
rootfs line (or lines) with that clang installed. The questions to answer:

1. **nvcc host window per CUDA line.** Read `crt/host_config.h` for each
   candidate CUDA version, e.g. 12.9 accepts clang ≤19, 13.0 ≤20, 13.2 ≤21.
   A newer CUDA line may widen it.
2. **jaxlib's clang floor.** JAX/XLA's minimum and tested clang (the
   hermetic clang is 18.1.8; some flags need ≥19).
3. **The one-LLVM tension.** Triton and XLA link LLVM *libraries* at their
   pinned commits, which are 23.0.0git for torch 2.14's Triton and JAX 0.10.2.
   A nvcc-accepted clang (≤20/21) cannot be that same commit. Options:
   - (a) relax the rule to one compiler plus one LLVM library, documented, with
     I7-style checks that each DSO matches its static LLVM's compiler;
   - (b) choose torch/Triton/JAX versions whose pinned LLVM is a release that
     nvcc accepts (likely older versions);
   - (c) wait for a CUDA release whose host window includes the LLVM major
     that Triton and XLA pin.
4. **Torch with a clang host plus nvcc.** Does PyTorch build and pass the
   gates with clang as host and nvcc for CUDA? OpenMP: libomp in that rootfs,
   or libgomp compatibility.
5. **Rootfs.** A dedicated lock entry and rootfs line with the chosen clang
   (packaged or built from the release tag), verified by the single-copy and
   I1–I11 checks.

Deliverable: a recommendation, with a working proof build on one line, for
the human to decide.

**Decided (human, 2026-10-02): option (a), one compiler plus one LLVM
library.**
- Exactly **one compiler** (one version, one family) compiles every target in
  a profile.
- Exactly **one LLVM library version** is linked by the targets that embed
  LLVM (Triton, XLA). That library may be newer than the compiler, and is
  itself built with the one compiler.
- A newer LLVM may also be present **as a tool** (for example
  `mlir-tblgen`/`llvm-config` from the library build). It is never used to
  compile our targets.
- Checks:
  - provenance (`.comment`) of every target DSO shows only the one compiler;
  - I7: each DSO's static LLVM/MLIR members were built by that same
    compiler;
  - no second LLVM library version in any graph or prefix.

Today's profiles already satisfy it:
- torch profile: compiler gcc 13.3; LLVM library 35901313, built by gcc 13.3;
- JAX profile: compiler clang 23 (35901313), and the same library.

The next round only has to pick the unified compiler, e.g. an nvcc-accepted
clang, and rebuild the one LLVM library with it.

## 6. Open items

- A gcc-built Triton 675c598 passing `-Werror` and `check-triton` (B1).
- For the JAX profile: clang-CUDA against libstdc++ 13 may need
  conda-forge-style patches; check how jaxlib links cudart.
- Running torch and JAX in one process is ecosystem practice but has no
  official CI. It is out of scope until phase 3.
