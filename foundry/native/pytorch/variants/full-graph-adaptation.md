# Torch 2.15 / CUDA 13.3 H20: full graph reconciliation

Status: preparation, not a working full native build or GPU qualification.

## Contract and rollback

The final target must preserve the Spack-derived graph with explicitly scoped
source, tool and dependency inputs supplied by the Insula capsule. All Bazel
actions use no-sandbox intentionally; host networking remains shared. This is
not a claim of network isolation. OCI is only the proposed bundle transport,
not a runnable container or an implemented transport in this change.

The uncommitted seven-prefix production switch was removed. The canonical
PyTorch action and H20 plan helper again retain all 36 direct dependency
prefixes (35 build-phase arguments plus zstd for archive extraction).
No default production dependency or feature was removed. The unfinished
reduced site workspace generator was also removed rather than presented as
a final H20 build target.

## Source-built versus vendor/tool smoke inputs

- MODULE.bazel loads the native graph extension; dev.MODULE.bazel declares
  native source rules for CPython, CMake, CPU libraries and Python packages.
- CUDA is explicitly a vendor SDK/rootfs boundary in native/cuda/cuda.bzl,
  not a CUDA source compilation. cuDNN, NCCL, cuSPARSELt, cuDSS and NVSHMEM
  similarly use declared SDK/rootfs boundaries. Preserve those real boundaries,
  including CUDA's declared installer-tool dependencies.
- Staged CUDA13.3/NCCL/cuDNN packages, portable Python3.13.18 and installed
  wheels were used for functional compiler/import/link smoke checks. They
  have not replaced the graph's source-built CPython/Python dependency nodes.
- Bazel7.7 starts inside the builder Insula. GCC12.2, CMake4.4.5 and Ninja
  executables were checked there. This does not prove full graph analysis.
- No H20 native build, full source configure or GPU Cuspy trace was run by this
  reconciliation. The original live Argonaut compiler job was not modified.

## Concrete remaining adaptations

1. Authoritative graph input is present:
   graphs/py_torch_214_full_build_graph.json contains144 nodes rooted at
   py-torch. The full_cu129 and full_cu130 variants each contain140 nodes.
   foundry/BUILD.bazel exports the graph files through filegroup aliases;
   a Bazel label here is not a same-named root filesystem path.
   locks/torch.lock.json is a separate62-package native frontier rooted at
   vaso_py_requests. Do not confuse that frontier with the full source graph.
   Adapt the existing full_cu130 graph side by side for2.15/cu133; do not
   recover/reinvent a graph that already exists.
2. Source and version binding:
   native/pytorch/BUILD.bazel still instantiates 2.14 source/archive targets.
   Bind the existing clean 2.15 producer receipt and archive under a separate
   concrete target, with the approved post-extraction Cuspy patch. Reuse the
   existing receipt without another archive or source-file scan.
3. Release/toolchain selection:
   native/common/build_settings.bzl, plans/BUILD.bazel and normal generation
   selectors remain cu129/cu130. toolchains/BUILD.bazel's registered CUDA
   provider is 10.0+PTX; canonical Python is 3.13.13 and GCC is 13.3.
   The committed H20 explicit-provider route records SM90,
   experimental GCC12.2 and Python3.13.18 without relabeling them, but it is
   not an automatically resolved full cu133 release/toolchain closure.
4. Dependency closure:
   retain every graph edge and adapt CUDA-family SDK boundaries to13.3.
   Existing source-built Python3.13.13 nodes and their descendants cannot be
   silently represented by the portable3.13.18 smoke environment. Select the
   intended source-built3.13.x variant explicitly and adapt its dependency
   labels. Cupti-python/libclang/NumPy build/runtime inputs need graph-owned
   declarations, not only an installed requirements file.
5. Capsule:
   supply the chosen compiler, source archives, build dependencies and SDK
   roots through declared Insula projections. Do not substitute ambient host
   prefixes or add a Bazel sandbox. Proposed OCI bundle layout is separate
   design work, not a prerequisite to proving these local scoped bindings.
6. Build validation:
   review2.15 configure/source-normalization assumptions, then analyze the
   complete concrete target in Insula without triggering competing native
   repository builds. Only after owning safe capacity run configure/build,
   native Cuspy import and GPU tracing. No duplicate compile on Argonaut.

## Safe next step

Use graphs/py_torch_214_full_cu130_build_graph.json to make a
side-by-side2.15/cu133 variant mapping, preserving existing2.14 declarations.
Bind scoped source/toolchain labels to that mapping before real analysis.
Do not procure zstd merely to continue the superseded seven-prefix smoke
target. Zstd remains a legitimate existing full-graph source dependency.

Focused regression checks cover full36-prefix preservation, the H20 plan
schema/phase resolver and declared toolchain bindings. They do not establish
complete MODULE graph analysis, package builds or GPU qualification.

## Offline side-by-side mapping from the existing cu130 graph

This is an adaptation plan, not a generated/concretized2.15 graph. Do not edit
existing Spack node identities or claim unchanged identities describe new pins.

| Area | Existing cu130 source graph | H20 adaptation |
| --- | --- | --- |
| Torch |2.14.0, commit2b3ec348, CUDA arch100|2.15.0, existing clean31a77fc9 receipt, arch90, reviewed Cuspy patch|
| CUDA |13.0.3 vendor SDK node|CUDA13.3 family: nvcc/compiler13.3.73, runtime13.3.29, CUPTI13.3.75; declare each component and scoped bundle|
| NCCL |2.30.7-1, makefile source recipe, CUDA arch100, verbs|Retain recipe semantics and source identity; adapt arch90/CUDA13.3. Current native provider is a binary SDK boundary, so explicitly document and validate that existing adaptation rather than silently inventing a source build.|
| cuDNN |9.24.0.43-13 vendor component|Keep CUDA13-compatible component version; bind actual headers/libs and declared bundle|
| Python |Source-built3.13.13|Preserve3.13.13 unless deliberately selecting a side-by-side source-built3.13.18 variant; portable3.13.18 is only smoke evidence|
| Python build frontend |scikit-build-core1.0.0, packaging26.2, typing-extensions4.15.0|These meet known2.15 minimum versions; do not blindly replace with all smoke-environment versions|
| NumPy |2.4.6 source recipe/patch|Keep graph-owned source dependency unless2.15 compatibility proves an update necessary|
| Cuspy extras |No separately named cupti-python/libclang nodes observed|Add explicit build/runtime recipe inputs for header generation and CUPTI Python support, with matching13.3 API/runtime|
| CPU/distributed graph |OpenBLAS, protobuf, MPI, Gloo, NUMA and full transitive closures|Preserve edges according to selected Torch variants; no blanket seven-prefix removal|

The complete graph is the authority for selected variant dependencies, not a
requirement that every optional feature be enabled in every future variant.
Any intentional pruning must follow that variant's dependency semantics and be
visible in the generated source/build graph. The existing144-to140 line
variants already differ; preserve these distinctions rather than flattening
them into the current36 direct action-prefix arguments.
