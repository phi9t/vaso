# Spack → Bazel build graph, inside a vaso insula

**Question.** Can a package-level, coarse-grained Spack build be consumed
*directly* by Bazel, where:

- the **dependency edges** come from the Spack concrete DAG (not hand-written), and
- the **package structure** follows each Spack package's hermetic install prefix
  (`lib/`, `include/`, `bin/`), which Bazel just wires up as consumption?

Everything runs inside a **vaso insula** (a `bwrap` rootfs) that presents the
canonical vaso sandbox namespace and persists Bazel's caches outside the
sandbox for incremental reuse.

## Milestones

1. **Working insula bundle that runs Bazel and persists output/cache.**
   `bootstrap_insula.sh` seats a host estate, materializes the canonical dir
   structure, projects host tools as shims, and runs a trivial Bazel build
   inside the insula against the persisted output base / disk cache / repository
   cache. Verified reproducible from a clean slate 3× (`--clean`).
2. **Multi-language capability.** `verify_languages.sh` builds and runs
   bash / C++ / Python / Rust / CUDA inside the insula (CUDA is a real B200
   kernel launch via host driver projection). All five pass.
3. **Spack → Bazel graph.** `run.sh` snapshots the Spack link-DAG into a lock,
   materializes one Bazel external repo per node, and links a synthetic C
   consumer purely through the generated graph — inside the insula, with a
   Bazel-owned hermetic Spack tool.
4. **Sealed insula.** `seal_insula.sh` captures a manifest + content digest of
   the read-only material, then runs the build from a sealed insula where only
   `/vaso/cache/bazel` and `/vaso/tmp` stay writable. Every sealed run
   re-verifies the digest and fails loudly on drift.
5. **Native migration (Spack as reference recipe).** A node's *provider* can
   flip from Spack to a native Bazel build while staying prefix/ABI identical,
   so unmigrated consumers `depends_on` it unchanged. zlib-ng is the landed
   leaf proof; PyTorch → Triton → JAX is the sequence, executed on two CUDA rootfs lines
   (cu130 and cu129) by `.scratch/execution-plan/spec.md`. See
   `docs/native-migration.md` for the design.
6. **Topological hillclimb + formal verification.** `tools/build_graph.py`
   emits the Spack build DAG in topological order with each node's build system
   (`build_graph.json`); `migration_ledger.json` tracks per-package migration
   status; `docs/recipes/<pkg>.md` captures each deep-dive (how Spack drives
   autotools/cmake/... underneath). The installed `SPACK_ROOT_PKG=python`
   graph is now migrated through the root CPython node, with ABI/prefix gates
   for Autotools-style configure/make packages, CMake, Makefile packages,
   generic data prefixes, executable/runtime prefixes, and the gated
   Python-wheel build skeleton. The next major frontier is native PyTorch,
   still gated behind its explicit build token.
   `formal/build_migration/` is the
   TLA+ contract for the migration transaction (topology-immutable, ABI-gated,
   native-subgraph-downward-closed), checked hermetically under Bazel;
   `formal/lean/` is the Lean4 artifact-obligation layer (`AbiEquiv` is an
   equivalence relation; `links_preserved` proves ABI-equivalence keeps
   consumers linking), built with `lake build`.

## Layout

```
experiments/spack-bazel-graph/
  bootstrap_insula.sh      milestone 1: seat estate + run bazel in insula (idempotent)
  verify_languages.sh      milestone 2: bash/c++/python/rust/cuda inside insula
  run.sh                   milestone 3: full Spack->Bazel graph in insula
  seal_insula.sh           milestone 4: seal the estate + run from sealed insula
  tools/
    estate.py              host dir-structure data model + placement discovery
    overlay_root.py        base-root bwrap bind plan (canonical namespace)
    spack_to_bazel.py      Spack concrete DAG -> spack_graph.lock.json (per-node build:spack|native)
    spack_repo.bzl         repository_rule + module_extension: DAG -> Bazel repos (spack|native dispatch)
    spack_dist.bzl         Bazel-owned hermetic Spack distribution
    abi_parity.py          ABI-parity gate: layout + soname/symbol + link-and-run
  native/
    zlib_ng/zlib_ng.bzl    native (Bazel-owned) zlib-ng build, Spack-prefix-identical (leaf proof)
    pytorch/               GATED native-PyTorch design skeleton (plan.py + pytorch_native.bzl)
  native_overrides.json    package -> native rule label; flips a node's provider without editing the lock
  docs/native-migration.md Spack-as-reference-recipe migration design (PyTorch -> Triton -> JAX)
  docs/rfcs/triton-jax-roadmap.md  Triton/JAX roadmap: decisions so far, open questions (to be specced)
  rootfs/
    build_rootfs.sh        export a CUDA/Ubuntu>=24.04 image into a bwrap rootfs bundle
  bootstrap/BUILD.bazel    tiny genrule touchstone target
  lang/                    bash/cpp/python/rust/cuda smoke sources + BUILD
  synthetic/               cc_test consuming @spack_zlib_ng//:lib + spack self-check + ABI-parity gate
  spack_graph.lock.json    generated snapshot (checked in as evidence)
```

## The estate: data model + placement

`tools/estate.py` is the **data model** for the host-side directory tree that
backs the insula (`VasoEstate`), plus a **placement tool** that discovers a disk
volume with enough free space and a writable per-user seat (`findmnt` + probe),
then materializes the canonical structure. The estate defines the canonical
sandbox namespace (`/home/kvothe`, `/workspace`, `/vaso/{state,cache,runs,
traces,tmp,tools/bin}`, `/opt/vaso`, `/run/vaso`) that the insula always
presents.

Seat resolution order: `VASO_ESTATE_ROOT` → preferred prefixes (the checkout's
volume) → roomiest suitable non-tmpfs volume with a writable per-user seat.

## The insula root

`tools/overlay_root.py` emits a bwrap **bind plan**: each base-root top-level
entry is `--ro-bind`'d from the base (the host `/` in fallback mode, or a CUDA
rootfs bundle), and every canonical sandbox dir is provided as a `--dir`/tmpfs
target that estate binds attach to. This avoids the self-referential symlink
loop you get from an overlay directory and keeps base paths resolving to the
real base root.

Bazel runs with `--batch` (no persistent server — the server does not survive
the insula PID-namespace teardown cleanly) and `--output_base` /
`--repository_cache` / `--disk_cache` pointed at the estate's `/vaso/cache`, so
caches persist on the host and are reused across runs.

## Run

```bash
experiments/spack-bazel-graph/bootstrap_insula.sh          # milestone 1
experiments/spack-bazel-graph/bootstrap_insula.sh --clean  # from clean slate
experiments/spack-bazel-graph/verify_languages.sh          # milestone 2
experiments/spack-bazel-graph/run.sh                       # milestone 3
experiments/spack-bazel-graph/seal_insula.sh seal          # milestone 4: seal
experiments/spack-bazel-graph/seal_insula.sh run           # build from sealed insula

# milestone 5: flip zlib-ng to a native Bazel build + run the ABI-parity gate
VASO_NATIVE=1 experiments/spack-bazel-graph/run.sh

# topology-only frontier capture; no Spack install, no native package build.
# Keep X.Org font resources lean with the replacement constraint below.
SPACK_ROOT_PKG='py-torch cuda_arch=80,90,100 ^openblas~fortran ^font-util fonts:=encodings' \
  VASO_GRAPH_ONLY=1 \
  VASO_GRAPH_NO_PREFIX=1 \
  VASO_SPACK_INSTALL=0 \
  VASO_BUILD_GRAPH_OUT=/workspace/experiment/py_torch_build_graph.json \
  experiments/spack-bazel-graph/run.sh
```

`run.sh` snapshots the lock by running the Bazel-vendored
`@spack_dist//:spack` tool inside the insula. That Spack release is pinned in
`MODULE.bazel` (currently GitHub's latest release, `v1.2.2`) and its store,
cache, and config live under `/vaso/cache/spack` and `/vaso/state/spack`.
Ambient host Spack and standalone Spack stores are not part of the workflow.
The CUDA rootfs includes `gfortran`, and the Bazel-owned Spack wrapper only
advertises Fortran compiler support when `/usr/bin/gfortran` exists inside the
insula; this is required for PyTorch-frontier BLAS/LAPACK concretization.

## What this proves / does not

- **Proves:** Spack link-DAG → Bazel `deps`, hermetic prefix → Bazel package,
  no per-package hand-authored `BUILD`; a working insula that runs Bazel + five
  language toolchains (incl. a real CUDA kernel) and persists caches.
- **Synthetic scope:** one small C root package (`zlib-ng`) and its link
  closure. `build`-only deps (compilers, gmake, glibc, gcc-runtime) are dropped
  from the Bazel graph — Bazel supplies its own C toolchain.
- **CUDA bundle only:** `run.sh` requires the CUDA/Ubuntu>=24.04 bundle at
  `<estate>/rootfs` and fails before any Spack-derived state is generated if it
  is missing. `VASO_ALLOW_HOST_ROOTFS=1` exists only for local development
  diagnostics and is not an accepted workflow input for migration evidence.
- **Not yet:** sealed/immutable insula image, Spack `run` deps as `data`,
  buildcache provenance, and drift-checking the lock against a fresh concretize.
