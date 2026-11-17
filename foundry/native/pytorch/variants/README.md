# Torch 2.15 / CUDA 13.3 / SM90 Cuspy preparation

This variant is **not build-ready or GPU-qualified**. The opt-in canonical
extension is deliberately not instantiated as a target with invented source or
rootfs pins.

## Implemented integration

- `tools/gen_plans.py:pytorch_h20_cuspy_plan` prepares the same configure,
  build and install phases used by the canonical PyTorch action. It takes an
  explicit recursive-source archive descriptor, source receipt, and complete
  rootfs identity. Python remains the canonical CPython 3.13 ABI.
- `pytorch_action.bzl:pytorch_variant_action` binds the archive, manifest, plan
  and rootfs explicitly and reuses the canonical action implementation. The opt-in
  path uses its declared toolchain instead of the legacy rootfs transition. The manifest path is no longer hard-coded to 2.14.
- Small declared metadata uses exact UTF-8 `expected_text` comparison, not
  per-file SHA256. The recursive source archive retains its pinned SHA256.
- Existing 2.14/cu129/cu130 targets do not opt into the new source or arch.

The producer receipt must contain `source_commit` equal to
`31a77fc9c0a7c6fff15a1ff58c743f53b1a9d457`,
`clean_recursive_source: true`, and `archive.sha256` matching the archive
descriptor. The descriptor must bind `name: main` and
`archive: "{source_archive}"`. The clean receipt describes the archive before
patching. Exactly one post-extraction patch is required:
`native/pytorch/variants/torch215-cuspy-no-header-hash.patch`, with
`expected_text` exactly matching the committed patch. Other patches, altered
content and additional digest fields are rejected. The canonical action binds
this file through its `patches` label map and passes it to the executor. These are producer assertions:
the generator does **not** prove archive cleanliness. A Pascal-patched tarball
must not be relabeled to satisfy the receipt.

## Remaining prerequisites

1. Bind the existing `torch215-source-receipt.json` producer receipt and its
   archive as declared Bazel source inputs; no repeated source audit is needed.
2. Register the immutable cu133 release line across rootfs locks, platform,
   dependency repositories and plan selectors. The plan schema admits cu133,
   but the default release-line registry remains cu129/cu130. The explicit
   variant route does not silently change those defaults.
3. Supply a compatible CUDA13.3/CUPTI13.3 closure, full recursive NCCL headers,
   cupti-python and NumPy. Verify native Cuspy and generated `_cupti_stubs`.
4. Instantiate the variant macro with those real labels and publish a separate
   release-wheel action. Do not overwrite the 2.14 release.
5. Admit an owned H20 host with compatible driver, run the real native build,
   and verify CUDA-event and Cuspy GPU traces. No wheel currently exists from
   this preparation work.

Tests: `python3 foundry/tools/pytorch_variant_plan_test.py` (synthetic receipts,
no GPU build or qualification).

## Generator patch

The torch215-cuspy-no-header-hash.patch removes the upstream header digest and
retains only the header basename and CUPTI API version comments. The H20 API
requires exactly this patch, and the canonical action forwards it to the executor.
Application to a temporary pinned source copy and Python syntax checks passed.
This is not a full native build or generated-stub runtime test.

## Minimal compiler setup

The provision_cuda133_compiler.py helper stages official NVIDIA packages selected from the existing apt metadata into an isolated prefix. It refuses an existing prefix and does not install system packages or hash installed files. Downloads use temporary files and are promoted only after successful transfer and expected-size checks. An optional package cache reuses already downloaded archives. Run it with --root PATH and optionally --package-cache PATH. It needs curl, dpkg-deb and populated NVIDIA apt metadata.

This minimal set has compiled an SM90 CUDA object inside the existing Vaso builder Insula using nvcc13.3.73 and GCC12.2; its CUPTI headers report130301. This is compiler readiness only, not GPU execution or a Torch wheel. Full Torch CUDA libraries, Python/Cuspy dependencies, canonical cu133 rootfs registration and H20 access still remain.

## Explicit opt-in build-tool model

`//toolchains:h20_build_toolchain.bzl` defines `h20_build_toolchain`.
Callers provide declared labels for cc, cxx, nvcc, Python, CUDA/Python prefix
markers and runtime file closures. No private estate directory is baked into
the committed graph. The provider records Linux/x86_64 host, CUDA13.3/SM90
device target, cp313 ABI, and the actual compiler/Python patch identities.

`pytorch_variant_action(..., variant_toolchain=":h20_tools")` consumes these
declared files, executes the declared Python, and passes the provider contract
to the canonical plan executor. The executor binds compiler paths into the
build environment and rejects a mismatched plan identity/architecture. The
variant does not apply the legacy gcc13.3/Python3.13.13 platform transition;
ordinary `pytorch_action_prefix` remains unchanged.

The experimental GCC12.2/Python3.13.18 choice is represented as
`experimental:gcc@12.2.0` and `python_3_13_18_variant`; it is not relabeled
as the canonical GCC13.3/Python3.13.13 toolchain. Preparation's optional
`compiler_identity` and `python_version` arguments make that selection explicit.

Verified: actual Bazel7.7 load/analysis of the provider and canonical action in
a harmless-file harness, with no compile actions executed; focused Python
binding tests. Not yet verified: a full production MODULE graph, real compile,
or GPU Cuspy trace. The concrete cu133 package/release integration and real
dependency closures remain prerequisites, not silently fabricated here.

Use --torch-libraries to include CUDA math libraries, NCCL2.30.7 for CUDA13.3, and cuDNN9.24. The staged full set compiled SM90 code including all math-library, NCCL-device and cuDNN headers inside Insula. The accompanying python313-cuspy-requirements.txt was installed in an isolated CPython3.13.18 environment; numpy/libclang/CUPTI imports and actual patched Torch2.15 stub generation passed with API130301. This experimental interpreter differs from the existing canonical3.13.13 pin and must be selected explicitly in the Bazel toolchain. These checks do not constitute a Torch build or GPU qualification.


Declared Python dependency metadata is
`//native/pytorch:variants/python313-cuspy-requirements.txt`; bind it through
the toolchain's mandatory `python_requirements` label. Runtime package files
must also be declared via `runtime_files` and the selected Python prefix.
The compiler contract supplies CC, CXX, CUDAHOSTCXX, CUDACXX,
CMAKE_CUDA_COMPILER and PYTHON_EXECUTABLE. CUDA/Python dependency prefixes
come from the same declared toolchain markers, overriding legacy manifest
prefix inference for those two dependencies.

The portable interpreter and installed Python wheels are separate declared
inputs. `python_site_packages_file` is a mandatory label containing the selected
wheel site-packages directory. The executor resolves it before phase chdir and
sets PYTHONPATH; the H20 configure script preserves that binding ahead of the
legacy per-package prefixes. This is not inferred from an ambient venv or host
site-packages. Include those wheel artifacts in `runtime_files`.
NCCL and cuDNN markers point at their declared usr roots; the H20 path uses
NCCL include, cuDNN include/x86_64-linux-gnu, and lib/x86_64-linux-gnu for both.

This first increment uses an explicit typed-provider label, not automatic Bazel
toolchain resolution. ToolchainInfo/type exposure does not imply a registered
production resolver or complete hermetic package acquisition graph.
