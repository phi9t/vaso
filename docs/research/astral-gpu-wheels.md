# Astral GPU Wheels

**Status:** Research note
**Date:** 2026-08-19

This note records how Vaso should consume Astral's GPU wheel indexes for
foundation-model development inside the bwrap rootfs. The scope is Python GPU
packages that are expensive or fragile to build locally, such as FlashAttention,
DeepSpeed, Transformer Engine, causal-conv1d, Mamba, and related PyTorch CUDA
extensions.

## Primary Sources

- Astral wheel indexes: `https://wheels.astral.sh/simple/`
- Astral CUDA channel example: `https://wheels.astral.sh/simple/cu128/`
- Astral index builder: `https://github.com/astral-sh-build/_build-index`
- FlashAttention builder:
  `https://github.com/astral-sh-build/build-flash-attention`
- FlashAttention 3 builder:
  `https://github.com/astral-sh-build/build-flash-attention-3`
- DeepSpeed builder: `https://github.com/astral-sh-build/build-deepspeed`
- causal-conv1d builder:
  `https://github.com/astral-sh-build/build-causal-conv1d`
- Mamba builder: `https://github.com/astral-sh-build/build-mamba`
- Transformer Engine builders:
  `https://github.com/astral-sh-build/build-transformer-engine`,
  `https://github.com/astral-sh-build/build-transformer-engine-torch`,
  `https://github.com/astral-sh-build/build-transformer-engine-cu12`, and
  `https://github.com/astral-sh-build/build-transformer-engine-cu13`
- vLLM builder: `https://github.com/astral-sh-build/build-vllm`
- uv index documentation: `https://github.com/astral-sh/uv/blob/main/docs/concepts/indexes.md`
- uv cache documentation: `https://github.com/astral-sh/uv/blob/main/docs/concepts/cache.md`
- uv Python version documentation:
  `https://github.com/astral-sh/uv/blob/main/docs/concepts/python-versions.md`
- python-build-standalone:
  `https://github.com/astral-sh/python-build-standalone`

## Index Shape

Astral publishes supplemental Python package indexes at:

```text
https://wheels.astral.sh/simple/<channel>/
```

The index builder repository declares CUDA and CPU channels in
`config/index.toml`. At the time of inspection, the channel family included
`cpu`, `cu118`, `cu121`, `cu124`, `cu126`, `cu128`, `cu129`, `cu130`, and
`cu132`.

Project pages follow the normal Python Simple API shape:

```text
https://wheels.astral.sh/simple/cu128/flash-attn/
https://wheels.astral.sh/simple/cu128/deepspeed/
https://wheels.astral.sh/simple/cu128/transformer-engine-torch/
https://wheels.astral.sh/simple/cpu/vllm/
```

Live `wheels.astral.sh` checks showed that CUDA channel roots can serve PEP 691
JSON. The generated project JSON records wheel filenames, artifact URLs, SHA-256
hashes, core metadata hashes, `requires-python`, size, and upload time. Artifact
URLs are immutable-looking paths of the form:

```text
https://wheels.astral.sh/artifacts/<sha256>/<wheel-filename>.whl
```

Reproducible endpoint check from 2026-08-19:

```bash
curl -L --fail --silent --show-error -A 'Mozilla/5.0' \
  -w '%{http_code} %{content_type} %{url_effective}\n' \
  https://wheels.astral.sh/simple/cu128/
```

Observed result:

```text
200 application/vnd.pypi.simple.v1+json https://wheels.astral.sh/simple/cu128/
```

The returned project list included `causal-conv1d`, `deepspeed`,
`flash-attn`, `flash-attn-3`, `mamba-ssm`, `transformer-engine`,
`transformer-engine-cu12`, and `vllm`. A separate `GET` to
`https://wheels.astral.sh/simple/` returned HTML containing the `cu128` channel.
Vaso validation should record the same HTTP status, content type, response hash,
and required project names rather than treating the endpoint as assumed.

These indexes are supplemental. They do not mirror PyPI, PyTorch, or every
transitive dependency. Vaso should therefore treat them as explicit named
sources for selected packages, not as a global replacement for every Python
index.

## Build Matrix

The open build repos show why these wheels are useful. A single package often
needs a matrix over:

- upstream package version;
- PyTorch version;
- Python version;
- CUDA version;
- CPU architecture;
- manylinux/glibc floor;
- C++11 ABI setting;
- package-specific switches.

Representative examples:

- FlashAttention 2 is built across Python, PyTorch, CUDA, architecture, and ABI
  variants in `build-flash-attention`.
- FlashAttention 3 publishes `flash-attn-3` ABI3 wheels and has Hopper-specific
  constraints documented in `build-flash-attention-3`.
- DeepSpeed includes PyTorch, Python, CUDA, architecture, and `deepcompile`
  variants in `build-deepspeed`.
- causal-conv1d and mamba-ssm follow the FlashAttention-style CUDA/PyTorch/Python
  matrix in their build repos.
- Transformer Engine is split between a metapackage, CUDA core packages, and a
  PyTorch extension. The CUDA core package can be independent of PyTorch/Python,
  while `transformer-engine-torch` is built per PyTorch/Python/CUDA variant.
- Astral's `build-vllm` is CPU-focused, while `_build-index` can import CUDA vLLM
  artifacts from upstream release inventories.

The practical implication for Vaso is that local source builds should be the
exception. For normal development profiles, Vaso should prefer matching
pre-built wheels when they exist, then use Spack or source builds only for
profiles whose purpose is ABI rebuild work.

## Local Version Tags

Astral follows the PyTorch convention of one index per CUDA channel plus local
version tags that encode compatibility. README examples and live wheel filenames
show variants such as:

```text
flash-attn==2.8.3+cu.12.8.torch.2.10
flash-attn-3==3.0.0b1+cu.12.8.torch.2.10
deepspeed==0.18.9+cu.12.8.torch.2.10
transformer-engine-torch==2.16.0+cu.12.8.torch.2.11
transformer-engine-cu12==2.16.0+cu.12.8
causal-conv1d==1.6.2.post1+cu.12.8.torch.2.11
mamba-ssm==2.3.2.post1+cu.12.8.torch.2.11
vllm==0.10.2+cpu
```

Older or package-specific filenames can use a denser spelling, for example:

```text
deepspeed-0.17.5+cu128torch2.7-...
causal_conv1d-1.5.4+cu12.8torch2.7.1cxx11abiTRUE-...
mamba_ssm-2.2.6.post3+cu12.8torch2.7.1cxx11abiTRUE-...
```

The Vaso resolver policy should verify the lockfile against expected local
version tokens, not just package names. Installing `flash-attn` from a CUDA
channel is not enough if the selected wheel was built against the wrong PyTorch
line.

One caveat from `_build-index`: selected older DeepSpeed artifacts preserve
upstream metadata/version inconsistencies. The index builder documents that pip
may reject those inconsistent local versions while uv can resolve them. Vaso
should prefer uv for these packages and should record the exact resolved wheel
filename and hash in run evidence.

## uv Integration

Vaso should model Astral sources as named explicit uv indexes selected by CUDA
channel. A repo-local `pyproject.toml` can pin only selected packages to Astral:

```toml
[tool.uv.sources]
flash-attn = { index = "astral-cu128" }
deepspeed = { index = "astral-cu128" }
causal-conv1d = { index = "astral-cu128" }
mamba-ssm = { index = "astral-cu128" }
transformer-engine = { index = "astral-cu128" }
transformer-engine-cu12 = { index = "astral-cu128" }
transformer-engine-torch = { index = "astral-cu128" }

[[tool.uv.index]]
name = "astral-cu128"
url = "https://wheels.astral.sh/simple/cu128/"
explicit = true
```

PyTorch itself should use the matching PyTorch CUDA index as a separate named
source, for example the PyTorch `cu128` wheel index when the Astral channel is
`cu128`. Astral build workflows install PyTorch from PyTorch CUDA wheel indexes
before building extension wheels, so the Vaso profile should keep those channels
aligned.

Inside bwrap, uv runs with Vaso's Python contract:

```text
VASO_PYTHON=/opt/vaso/python/bin/python3
UV_PYTHON=/opt/vaso/python/bin/python3
UV_CACHE_DIR=/vaso/cache/uv
PIP_CACHE_DIR=/vaso/cache/pip
```

Vaso should avoid letting uv silently download a different managed Python unless
the selected profile explicitly permits that. The default interpreter is the
pinned `python-build-standalone` installation at `/opt/vaso/python`.

## Offline And Cache Policy

Astral indexes are online population sources. Offline Vaso runs should consume a
pre-populated uv cache or a declared read-only wheelhouse. A valid offline
closure must include:

- selected Astral wheels;
- core metadata needed by uv;
- PyPI dependencies not hosted by Astral;
- matching PyTorch CUDA wheels;
- lockfile hashes;
- the pinned standalone Python archive identity.

The cache path is `/vaso/cache/uv` for mutable population and execution-time
cache hits. Promoted wheelhouses, when added, should be content-addressed and
mounted read-only under `/opt/vaso` or another declared read-only root.

Offline mode must fail loudly on a missing wheel or metadata entry. It must not
silently fall back to PyPI, PyTorch, Astral, or a host cache outside the bwrap
projection.

## Vaso Doctor Checks

`vaso doctor` should validate:

- the requested accelerator channel maps to a known Astral channel;
- the configured Astral URL exactly matches
  `https://wheels.astral.sh/simple/<channel>/` or an approved local mirror;
- GPU extension packages use `[tool.uv.sources]` and a named explicit Astral
  index;
- the PyTorch source channel matches the Astral CUDA channel;
- locked versions include expected local-version tokens for CUDA, PyTorch, and
  ABI compatibility;
- Python tags and `Requires-Python` are compatible with `/opt/vaso/python`;
- platform tags match the rootfs architecture and glibc floor;
- `UV_PYTHON` resolves inside the bwrap rootfs to
  `/opt/vaso/python/bin/python3`;
- `UV_CACHE_DIR` resolves inside the rootfs to `/vaso/cache/uv` and is writable
  during dependency sync/install;
- offline mode has all required Astral, PyPI, and PyTorch artifacts before
  network isolation is applied;
- runtime GPU projection is visible inside bwrap through `libcuda.so.1`, NVIDIA
  device nodes, and `nvidia-smi` or an equivalent driver probe;
- installed wheels import under the selected rootfs Python and report expected
  CUDA/Torch compatibility when the package exposes that information.

## Vaso Design Consequences

Use Astral GPU wheels for the normal Python ML stack, but keep the ABI
boundaries explicit:

- Python interpreter: `python-build-standalone` under `/opt/vaso/python`.
- Python package manager and lock owner: uv.
- Python GPU wheel source: Astral and PyTorch named indexes.
- Native ABI/toolchain source: Spack views under `/opt/vaso/view/*`.
- Host GPU runtime: NVIDIA driver projection under `/run/nvidia-driver`.
- Build/test executor for Bazel-native repos: Bazel, with uv only updating
  checked-in metadata and lockfiles.

This split avoids Docker as the runtime, avoids conda as a hidden package
manager, and keeps expensive native ABI rebuilds in a separate Spack workflow
instead of mixing them into ordinary agent runs.
