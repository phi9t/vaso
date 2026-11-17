# Graph-owned Cuspy inputs: bounded patch design

This maps selected Torch2.15 source reads and already-installed smoke package
metadata to the existing native recipe style. It does not introduce guessed
source URLs/digests, download packages, run codegen, or modify the active build.
The inspected source is the existing pinned2.15 working tree with Pascal
patches, not a newly audited clean archive. The clean producer receipt remains
the source authority for the H20 build.

## Required graph edges

| Consumer | Input | Phase and proposed native ownership |
| --- | --- | --- |
| tools/gen_cupti_stubs.py | clang.cindex Python module plus matching libclang.so | Build-only. Add bindings from the existing declared LLVM23 source/bundle, paired with that same libclang. Do not automatically promote the smoke libclang18 wheel into the one-LLVM production graph. |
| tools/gen_cupti_stubs.py | cupti_activity.h and sibling cupti_version.h | Build-only headers from declared CUPTI13.3.75 SDK component. Set CUPTI_INCLUDE_DIR explicitly. Header API must be at least130300; smoke saw130301. |
| CMake configure | Python interpreter + codegen/module inputs | Keep source-built CPython3.13.13. Existing package metadata gives no requirement to upgrade to3.13.18. |
| torch.profiler._cuspy | cupti-python13.3.1 | Runtime, also present in qualification environment. New native recipe must preserve its declared dependencies below. Actual source release pin/recipe is not yet selected. |
| cupti-python13.3.1 | nvidia-cuda-cupti~=13.3 | Runtime vendor boundary. Select13.3.75 exactly, not an open~= constraint that permits13.5. One loaded libcupti.so.13 shared with Kineto; do not accidentally bundle a second runtime. |
| cupti-python13.3.1 | cuda-bindings>=13.0.2 | Runtime. Smoke13.3.1; native source/package provenance still needs selection. Do not enable its optional all-extra toolkit bundle implicitly. |
| cupti-python13.3.1 | cuda-pathfinder>=1.4.1, numpy>=1.23, pyelftools | Runtime. cuda-bindings13.3.1 raises pathfinder floor to1.4.2. Existing graph NumPy2.4.6 satisfies the minimum; smoke NumPy2.5.5 does not justify a source upgrade. |
| generated native encoder | third_party/perfetto/sdk and in-tree miniz | Source/build inputs already within the recursive Torch source. Retain them in the scoped archive; do not add a speculative separate zlib download based on an earlier stale CMake comment. |
| final wheel | torch/profiler/_cuspy/_cupti_stubs.py | Generated artifact explicitly installed by upstream CMake; inspect the actual wheel entry, not an editable source import. |
| final native module | torch._C._profiler._cuspy | Compile/runtime requirement, distinct from merely installing Python packages. |

CUDA13.3 is not one homogeneous package version: nvcc/compiler13.3.73,
CUDA runtime13.3.29 and CUPTI13.3.75 are separate component pins.

## Existing native pattern to reuse

native/py_scikit_build_core/py_scikit_build_core.bzl and its dev.MODULE.bazel
declaration demonstrate the existing source-installed prefix contract:
source archive attributes, explicit interpreter/venv/backend/build dependency
prefix labels, no-index install, exported prefix_path.txt/prefix_manifest.json,
and a consumption provider. New Cuspy Python nodes should follow the appropriate
package's actual source backend/dependencies, not copy this package's hatch
backend blindly. No new backend or source digest is inferred from a wheel.

Add side-by-side native nodes and corresponding source-graph edges for
cupti-python, cuda-bindings, cuda-pathfinder and pyelftools only where absent.
For libclang, first use the existing rootfs_llvm_23 boundary and its declared
source revision35901313: source-matched clang Python bindings plus a selected
libclang shared library. If the scoped LLVM bundle does not include these
artifacts, extend that bundle from its own source instead of importing the
legacy llvm20 node or silently mixing LLVM18 bindings and LLVM23 libraries.

Preserve the full selected Spack-derived graph and its variant semantics.
Keep no-sandbox actions inside Insula and shared host networking unchanged.

## Exact integration points

1. CMake's caffe2/CMakeLists.txt runs tools/gen_cupti_stubs.py at configure time
   when USE_CUDA and BUILD_PYTHON are true. Failure is only a warning:
   an otherwise successful Torch wheel may still lack usable Cuspy.
2. tools/setup_helpers/cupti.py checks CUPTI_INCLUDE_DIR first, then toolkit
   locations, staged /usr/local headers and a wheel fallback. Bind the first
   explicitly from the declared CUPTI component to avoid ambient selection.
3. tools/gen_cupti_stubs.py accepts LIBCLANG_PATH as a full library FILE,
   calling Config.set_library_file, not a directory. Supply declared bindings
   on build PYTHONPATH and that exact shared library path.
4. Apply only the approved no-header-hash patch before codegen. Do not run
   the unpatched active-source generator: it still contains the header hash.
5. In the variant plan/provider, add explicit codegen input labels and expose
   CUPTI_INCLUDE_DIR and LIBCLANG_PATH before configure. Retain the current
   full dependency arguments; do not replace them with the portable smoke venv.
6. Package runtime dependencies through the graph/release environment and
   verify generated stubs, native module and the actual libcupti loaded from
   one declared runtime location.

## Bounded tests before a native build

- Unit-test that declared header/library paths resolve before source chdir.
- Run patched generator with the graph-owned CPython3.13.13, matching LLVM
  bindings/library and CUPTI headers into an isolated output, checking
  BUILD_CUPTI_API_VERSION and representative generated enums without hashes.
- Check configure reports CUPTI_STUBS_GENERATED rather than accepting only a
  successful overall configure exit. Upstream intentionally degrades on error.
- After a real build, inspect the wheel's single generated stub entry and
  import torch._C._profiler._cuspy and torch.profiler._cuspy from its install.
  GPU tracing remains a separate final qualification.

## What remains unknown

No official source artifact pins/backends for the new NVIDIA Python recipes
were acquired in this offline pass. Installed wheel METADATA establishes
dependency edges and Python compatibility, not source-build reproducibility.
The existing scoped LLVM23 bundle's clang Python bindings/libclang availability
has not been established. Therefore declarations with invented archive pins
or claims of completed Cuspy source closure would be misleading.

## Implemented opt-in declaration increment

The H20 tool provider now accepts enable_cuspy_codegen=True with five declared
file labels: cupti_header, cupti_version_header, libclang, clang_cindex and
clang_init, plus clang_source_identity. All five are included in action inputs.
Supplying only part of the set, or supplying inputs without enabling codegen,
fails analysis. Existing callers with no opt-in remain unchanged.

The executor resolves these files before phase chdir, requires named sibling
CUPTI headers and sibling clang package bindings, sets CUPTI_INCLUDE_DIR and
the full-file LIBCLANG_PATH, and prepends only the declared bindings site to
the already-declared Python dependency site. An ambient Python/CUPTI/libclang
path is not used as a fallback for an incomplete opted-in contract. Both
source Python3.13.13 and the separate experimental3.13.18 identity remain valid.

clang_source_identity is the producer's common-source assertion for the
bindings/library pair, not proof of ABI compatibility. No installed artifacts
have been fabricated or selected by this change. The actual matched-generator
smoke and package closure are still required. Python unit tests cover path
resolution, missing inputs, library-directory rejection, sibling coherence
and3.13.13; actual Bazel7.7 load/analysis inside Insula passed with a harmless-file fixture (8 packages,72 targets,0 actions). This is not production graph or ABI qualification.


The five codegen files are selected anchors, not a complete Python/native
runtime closure. The inspected smoke cindex.py imports clang.enumerations.
The provider now requires nonempty runtime_files when codegen is enabled;
callers must declare the complete bindings package and libclang transitive
shared libraries there. This nonempty check cannot prove closure completeness.
Aquery of the opt-in fixture confirmed all five anchors, enumerations.py and
an additional shared-library fixture reach the canonical action, whose
no-sandbox requirement remains intact. No real generator was executed and
the fixture does not establish matching LLVM23 ABI.
