# Vaso bwrap Rootfs Container Infrastructure Design

**Status:** Draft design record
**Date:** 2026-08-19
**Last updated:** 2026-09-04

## Purpose

Vaso is a local foundation-model development substrate built around a
Bubblewrap (`bwrap`) rootfs. Its job is to make complex CUDA, Bazel, and
coding-agent development run inside a controlled filesystem projection while
remaining usable as a normal multi-repo development environment.

The innermost insulation layer is always a `bwrap` rootfs. Docker is not a
runtime dependency for Vaso execution. Rootfs materialization may be automated
by scripts, but the runtime contract is a `bwrap` process over a materialized
root filesystem, with all host state projected explicitly.

## Reference and Downstream Implementations

Vaso owns the versioned rootfs contract, schemas, conformance behavior, and an
executable reference implementation. The Vaso CLI is the reference operator
surface for materialization, planning, execution, and evidence. A downstream
ML repository may expose its own repository-specific launcher while preserving
the observable Vaso contract.

Downstream implementations are self-contained. They pin a released Vaso
specification version and digest, implement and test the required behavior in
their own repository, and pass the public conformance suite. They do not import
the Vaso Python package, call a Vaso service, require a Vaso checkout, or depend
on another repository at build or runtime. Compatibility is behavioral rather
than a shared-code dependency.

The specification, digest, and conformance suite described here become public
interfaces only when v1 is frozen. Until then, this document and the current
implementation are a draft reference and must not be represented as a released
compatibility standard.

## Initial GPU Qualification

NVIDIA B200 is the first production GPU qualification target. Qualification
must cover the PyTorch/CUDA userspace stack, projected host driver, device
access, and applicable distributed communication behavior from inside the
selected rootfs. A sanitized qualification receipt binds the result to the
exact Vaso commit, rootfs identity, profile, and qualification inputs. Signing
and trust-root requirements belong to the release trust model and must be
defined before production qualification.

The validation tiers below remain a functional progression; B200 qualification
is a release gate over the applicable tiers, not a replacement name for any
tier. Other accelerators and GPU generations remain unqualified until their own
profiles and evidence are approved.

## Source-Grounded References

The bwrap contract in this document is based on upstream Bubblewrap source and
Codex's Linux sandbox implementation, inspected on 2026-08-19:

- Bubblewrap upstream commit `2f55bae38468d0c50cf5df87b1e481e882b63acb`.
  Relevant source: `bwrap.xml` and `bubblewrap.c`.
- Codex upstream commit `3b45c29062ff0e76e71c91b6753290400e7fa8da`.
  Relevant source:
  - `codex-rs/bwrap/`: Rust wrapper that vendors Bubblewrap C sources and
    builds a `bwrap` binary.
  - `codex-rs/linux-sandbox/`: Linux sandbox launcher that selects system or
    bundled bwrap and composes bwrap with additional sandboxing.
  - `codex-rs/sandboxing/src/bwrap.rs`: Bubblewrap argument construction for a
    read-only-by-default filesystem with explicit writable roots.
  - `codex-rs/rollout-trace/`: local trace-bundle design with raw events,
    payload references, and offline reduction.
- Astral `python-build-standalone` upstream commit
  `b25f9d7f7cdec4b63c46769ff40fe2829efd10ac`.
  Relevant source: `README.rst`, `docs/running.rst`,
  `docs/distributions.rst`, and `docs/quirks.rst`.
- Astral GPU wheel research captured in
  `docs/research/astral-gpu-wheels.md`.
Vaso should vendor/pin Bubblewrap source before implementing the runtime, using
Codex's `codex-rs/bwrap` pattern as the reference shape: prefer a usable system
`bwrap` when it satisfies capability probes, but keep a pinned bundled bwrap
available so the sandbox contract is not hostage to distro packaging. The
vendored source should live under `third_party/bubblewrap/` or an equivalent
clearly pinned subtree with the upstream commit recorded in a manifest.

Vaso should not vendor the full Codex repository. Instead, keep a small
reference manifest that records the Codex commit and the exact files inspected,
and copy only narrowly needed reference excerpts or tests if implementation
requires them. The Codex references are design inputs; Vaso's Linux container
module remains Bubblewrap-native.

Vaso should use `python-build-standalone` as its Python distribution source.
The selected release archive, target triple, Python version, sha256, extraction
path, and upstream commit should be recorded in Vaso's toolchain manifest.

## Core Requirements

- Run all substantive development commands inside the `bwrap` rootfs.
- Natively support host CUDA driver projection for GPU development.
- Provide a hermetic and comprehensive host directory structure with a matching
  sandbox-side projection.
- Support Bazel builds for CUDA, C++, Rust, Go, and Python.
- Integrate dependency and tool management for development workflows:
  `uv` for Python packages, `cargo` for Rust packages, and `mise` for general
  tool selection.
- Provide a pinned native ABI/toolchain layer for packages that should be
  built and shipped as native artifacts rather than resolved through Python,
  Rust, Go, or Bazel package managers.
- Support coding agents inside the sandbox, initially Codex and TraeCLI.
- Capture agentic execution traces and trajectories as first-class run
  artifacts.
- Make multi-repo development natural: repositories are mounted under
  `/workspace/<repo-name>` and commands run from declared repo paths.
- Provide a normal user home inside the rootfs for software that assumes
  `HOME`, `~/.cache`, `~/.local`, or tool-specific dot-directories.
- Emit a bwrap plan before execution and validate that the command actually
  matches that plan.

## Inner Filesystem Contract

The sandbox filesystem presents a small set of stable top-level namespaces:

```text
/home/kvothe
  .cache/                  user-level caches for tools that insist on HOME
  .config/                 user-level tool configuration
  .local/                  user-local binaries and data
  .cargo/                  Rust user cache/config when not overridden
  .npm/                    npm/node cache when needed
  .codex/                  Codex runtime/config/cache if required
  .trae/                   TraeCLI runtime/config/cache if required

/workspace
  vaso/                    Vaso repo when mounted
  monarch/                 optional mounted repo
  torchtitan/              optional mounted repo
  ferric_continuum/        optional mounted repo
  <repo-name>/             any other declared checkout

/vaso
  state/                   durable Vaso-owned generated state
  cache/                   explicit Vaso-managed caches
  runs/                    execution records and evidence
  traces/                  cross-run trajectory indexes and exports
  tools/                   Vaso-owned tool shims and helper materialization
  tmp/                     Vaso-owned scratch when /tmp is too broad

/opt/vaso
  rootfs-contract/         read-only runtime contract metadata
  profiles/                read-only declared profiles/manifests/schemas snapshot
  python/                  pinned python-build-standalone installation
  spack/                   pinned Spack checkout and immutable install snapshot
  view/                    immutable native ABI views assembled from Spack
  bin/                     read-only launcher helpers visible inside bwrap
  lib/                     read-only runtime libraries visible inside bwrap

/run
  nvidia-driver/           host CUDA driver DSOs and host nvidia-smi projection
  vaso/                    per-invocation sockets, pids, and locks

/tmp                         process temp, normally tmpfs
```

`/workspace` is a joint root for mounted git repositories. There is no
`/workspace/current` alias and no generic `/workspace/mounts` subtree. Each
repo must be mounted at `/workspace/<repo-name>`, and each invocation declares
the repo and working directory it targets.

Commands may start in a declared mounted repo path such as `/workspace/vaso`;
they should not start directly in `/workspace`.

## Host Projection

The host-side generated layout is rooted under `.vaso/` by default:

```text
.vaso/
  rootfs/                  materialized bwrap rootfs
  home/kvothe/             backing store for /home/kvothe
  state/                   backing store for /vaso/state
  cache/                   backing store for /vaso/cache
  runs/                    backing store for /vaso/runs
  traces/                  backing store for /vaso/traces
  tmp/                     optional backing store for /vaso/tmp
```

Repository mounts are declared as a map. The sandbox path is always
`/workspace/<name>`. `mode` defaults to `ro` when omitted; writable repos must
say `mode: rw` explicitly.

```yaml
workspace:
  repos:
    vaso:
      host: repo://self
      sandbox: /workspace/vaso
      mode: rw
    monarch:
      host: /srv/vaso/workspaces/monarch
      sandbox: /workspace/monarch
      mode: rw
    cutlass:
      host: /srv/vaso/workspaces/cutlass
      sandbox: /workspace/cutlass
      mode: ro
```

`repo://self` resolves to the git toplevel that contains the Vaso checkout. If
the checkout is not inside a git worktree, it resolves to the directory that
contains `bin/vaso` after symlink resolution. `vaso doctor` must print the
resolved path and fail if `repo://self` would resolve outside the configured
host root.

`host_root` is an explicit absolute directory in the Vaso host configuration.
All repository hosts, including `repo://self` after expansion, must resolve
under `host_root` unless the profile declares an additional allowed host root.
For linked worktrees, `repo://self` resolves to the worktree's own toplevel, not
the common git directory. For submodules, it resolves to the submodule worktree
toplevel that contains the Vaso checkout. If neither a git toplevel nor
`bin/vaso` exists, `vaso doctor` fails instead of guessing from the current
working directory.

The declaration is part of the run contract and must be emitted into the bwrap
plan before execution.

## Runtime Environment

Vaso sets a normal user identity inside the sandbox:

```text
USER=kvothe
LOGNAME=kvothe
HOME=/home/kvothe
XDG_CACHE_HOME=/home/kvothe/.cache
XDG_CONFIG_HOME=/home/kvothe/.config
XDG_DATA_HOME=/home/kvothe/.local/share
```

Vaso-specific paths are explicit:

```text
VASO_PREFIX=/opt/vaso
VASO_HOME=/vaso
VASO_WORKSPACE_ROOT=/workspace
VASO_REPO_NAME=<target-repo-name>
VASO_REPO=/workspace/<target-repo-name>
VASO_RUNS=/vaso/runs
VASO_CACHE=/vaso/cache
VASO_STATE=/vaso/state
VASO_RUNTIME=/run/vaso
CUDA_DRIVER_ROOT=/run/nvidia-driver
VASO_PYTHON=/opt/vaso/python/bin/python3
UV_PYTHON=/opt/vaso/python/bin/python3
UV_CACHE_DIR=/vaso/cache/uv
CARGO_HOME=/vaso/cache/cargo/home
CARGO_TARGET_DIR=/vaso/cache/cargo/target/<target-repo-name>
GOMODCACHE=/vaso/cache/go/pkg/mod
GOCACHE=/vaso/cache/go/build
MISE_DATA_DIR=/vaso/state/mise
MISE_CACHE_DIR=/vaso/cache/mise
SPACK_ROOT=/opt/vaso/spack
SPACK_USER_CACHE_PATH=/vaso/cache/spack/user
SPACK_DISABLE_LOCAL_CONFIG=true
```

`HOME` is not a workspace path. It is `/home/kvothe` so Codex, TraeCLI,
Bazelisk, language package managers, and other development tools can use normal
home-directory conventions.

The environment is scrubbed by default. Vaso invokes bwrap with `--clearenv`,
then sets the Vaso environment above plus command-class-specific values. The
default inherited allowlist is intentionally small:

```text
TERM
COLORTERM
LANG
LC_ALL
TZ
CUDA_VISIBLE_DEVICES
NVIDIA_VISIBLE_DEVICES
HTTP_PROXY
HTTPS_PROXY
NO_PROXY
SSL_CERT_FILE
SSL_CERT_DIR
```

Secrets are not inherited by default. Agent wrappers that require credentials
must declare those inputs explicitly and record redacted names in
`environment.json`.

`SSH_AUTH_SOCK` is not part of the default inherited allowlist. A profile that
needs ssh-agent access must opt in by passing the environment variable and
binding the exact socket path into `/run/vaso/ssh-agent.sock` or another
declared runtime path. The plan must record the bind and redact only the secret
value, not the fact that ssh-agent access was granted.

## Rootfs and bwrap Rules

The rootfs is mounted read-only during normal execution. Writable surfaces are
explicit:

- `/home/kvothe`
- `/workspace/<repo-name>` for repos declared `rw`
- `/vaso/state`
- `/vaso/cache`
- `/vaso/runs`
- `/vaso/traces`
- `/vaso/tmp`
- `/run/vaso`
- `/tmp`

Each writable surface is an individual bind mount layered over the read-only
rootfs. `/vaso` itself is not made writable wholesale.

Rootfs bootstrap must pre-create mount targets that `bwrap` cannot create under
a read-only rootfs, including:

- `/home/kvothe`
- `/workspace`
- `/workspace/<repo-name>` for every repo mount declared in the selected
  profile, or a generated rootfs overlay that contains those leaf paths
- `/vaso`
- `/vaso/state`
- `/vaso/cache`
- `/vaso/runs`
- `/vaso/traces`
- `/vaso/tmp`
- `/opt/vaso`
- `/run/vaso`
- `/run/nvidia-driver`
- `/dev/nvidia-caps`
- `/dev/infiniband`

Mount targets are validated before bwrap is invoked. Parent directories are not
sufficient for bind destinations under a read-only rootfs; every leaf directory
or file path that receives a bind must already exist in the rootfs or in a
writable tmpfs created earlier in the bwrap argv.

### Canonical bwrap Invocation

Vaso's runtime module owns the bwrap interface. Callers do not pass raw bwrap
flags. A normal online run materializes this shape:

```text
bwrap
  --die-with-parent
  --unshare-user
  --uid <host-uid>
  --gid <host-gid>
  --unshare-ipc
  --unshare-pid
  --unshare-uts
  --unshare-cgroup-try
  --clearenv
  --ro-bind <rootfs> /
  --proc /proc
  --dev /dev
  --ro-bind-try /sys /sys
  --tmpfs /tmp
  --tmpfs /run
  --dir /run/vaso
  --dir /run/nvidia-driver
  <device binds>
  <driver DSO binds>
  <network file overlays>
  <workspace repo binds>
  <vaso writable binds>
  <opt/vaso read-only binds>
  --setenv ...
  --chdir /workspace/<target-repo>
  -- <command argv...>
```

The default runtime identity maps the host uid/gid to the same numeric uid/gid
inside the user namespace and names that identity `kvothe` in the rootfs
`/etc/passwd` and `/etc/group`. This keeps host-backed writable binds usable
from both sides and avoids namespace-root ownership surprises for repos,
`/home/kvothe`, and Vaso run artifacts.

Namespace-root mode, using `--uid 0 --gid 0`, is reserved for explicit rootfs or
toolchain build profiles. Those profiles must record the resulting host
ownership behavior, refuse writable source repo binds unless requested, and
validate that generated artifacts remain modifiable by the host user.

`vaso doctor` must probe user namespace support with a command equivalent to:

```bash
bwrap --unshare-user --unshare-net --ro-bind / / /bin/true
```

If that probe fails, doctor distinguishes at least these cases: missing bwrap,
missing required bwrap flags, user namespaces disabled by sysctl or LSM policy,
and a bundled or setuid bwrap path that could still satisfy the runtime
contract.

Vaso uses `--as-pid-1` for command classes that launch process trees, including
Bazel and coding agents, so orphaned children are reaped inside the PID
namespace. If a command class disables it, that exception must be included in
`bwrap-plan.json`.

Online runs retain the host network namespace by omitting `--unshare-net`.
Offline runs add `--unshare-net`. Proxy-only mode is a separate future profile
and must not be silently treated as online mode.

For online runs, Vaso overlays host network files with read-only file data:

- `/etc/resolv.conf`
- `/etc/hosts`

CA certificates must be provided by the rootfs or projected read-only from the
host. `vaso doctor` must verify that HTTPS from inside the rootfs can validate
a certificate chain when online mode is enabled.

### Mount Table

| Sandbox path | bwrap operation | Host source | Notes |
|---|---|---|---|
| `/` | `--ro-bind` | `.vaso/rootfs` | immutable root filesystem |
| `/proc` | `--proc` | generated by bwrap | required for normal Linux tooling |
| `/dev` | `--dev` followed by device binds | generated + selected host devices | normal devices plus GPU device nodes |
| `/sys` | `--ro-bind-try` | `/sys` | read-only host sysfs |
| `/tmp` | `--tmpfs` | none | per-invocation temp |
| `/run` | `--tmpfs` | none | per-invocation runtime state |
| `/run/vaso` | `--dir` | inside `/run` tmpfs | sockets, pids, locks |
| `/run/nvidia-driver` | `--dir` plus `--ro-bind-try` files | host driver DSOs | driver libraries and host `nvidia-smi` |
| `/home/kvothe` | `--bind` | `.vaso/home/kvothe` | real user home |
| `/workspace/<repo>` | `--bind` or `--ro-bind` | declared repo host path | `rw` or `ro` per repo |
| `/vaso/state` | `--bind` | `.vaso/state` | durable state |
| `/vaso/cache` | `--bind` | `.vaso/cache` | shared caches |
| `/vaso/runs` | `--bind` | `.vaso/runs` | canonical run records |
| `/vaso/traces` | `--bind` | `.vaso/traces` | derived trace indexes and exports |
| `/vaso/tmp` | `--bind` or `--tmpfs` | `.vaso/tmp` or none | configurable scratch |
| `/opt/vaso` | `--ro-bind` | selected runtime/toolchain snapshot | read-only runtime material, Python, Spack, and views |

`/opt/vaso` is a read-only host bind of Vaso's runtime material, not mutable
state in the rootfs. The host launcher may live outside the sandbox, but any
helper invoked by the inner command must resolve under `/opt/vaso/bin` or
`/opt/vaso/lib`. Normal execution also consumes `/opt/vaso/python`,
`/opt/vaso/spack`, and `/opt/vaso/view/*` from this read-only snapshot.

## CUDA Driver Projection

Host NVIDIA driver libraries are runtime inputs, not Vaso state and not rootfs
contents. Vaso projects them under `/run/nvidia-driver` rather than into
read-only system library directories.

The projection includes:

- device nodes such as `/dev/nvidiactl`, `/dev/nvidia-uvm`,
  `/dev/nvidia-uvm-tools`, `/dev/nvidia-modeset`, and `/dev/nvidia[0-9]*`,
  bound to the same `/dev/...` paths with `--dev-bind-try`
- `/dev/nvidia-caps` bound to `/dev/nvidia-caps` with `--dev-bind-try` when
  present
- `/dev/infiniband` bound to `/dev/infiniband` with `--dev-bind-try` when
  present
- `libcuda.so.1`
- `libnvidia-ml.so.1`
- `libcudadebugger.so.1` when present
- resolved versioned targets for the above sonames
- host `nvidia-smi` exposed under `/run/nvidia-driver/bin/nvidia-smi`

Driver libraries use this sandbox layout:

```text
/run/nvidia-driver
  bin/nvidia-smi
  lib/libcuda.so.1
  lib/libcuda.so.<driver-version>
  lib/libnvidia-ml.so.1
  lib/libnvidia-ml.so.<driver-version>
  lib/libcudadebugger.so.1
  lib/<other explicitly selected driver DSOs>
```

For each soname, Vaso binds both the visible soname path and its resolved
versioned target into the same relative `lib/` directory when both exist. The
`--dir /run/nvidia-driver` and any needed child `--dir` operations must appear
before file binds. Device binds must appear after `--dev /dev`, because `--dev`
creates a fresh `/dev` tmpfs.

Library path precedence is profile-owned and deterministic:

```text
LD_LIBRARY_PATH=/run/nvidia-driver/lib:<selected-spack-cuda-lib-paths>:<selected-spack-native-lib-paths>:<profile-extra-lib-paths>
PATH=/run/nvidia-driver/bin:<selected-tool-paths>:/opt/vaso/python/bin:<rootfs-path>
```

The host driver path comes first so CUDA userspace resolves `libcuda.so.1` and
NVML from the host driver projection, not from a toolkit or Spack view. Spack
CUDA toolkit libraries are compile-time/toolkit inputs; they do not replace the
host kernel-driver userspace DSOs.

Device nodes do not live under `/run/nvidia-driver`; CUDA userspace opens them
from `/dev`. `/run/nvidia-driver` is only for host driver DSOs and host driver
tools.

`vaso doctor` must verify driver/toolkit compatibility before GPU work:

- host driver is visible through `nvidia-smi`
- `libcuda.so.1` and `libnvidia-ml.so.1` resolve inside the sandbox
- profile declares one CUDA channel such as `cu128` as the source of truth for
  compile-time toolkit, PyTorch wheels, Astral GPU wheels, and CUDA extension
  compatibility
- host driver supports at least the minimum driver floor for that CUDA channel
  and toolkit, accounting for CUDA forward-compat packages only when explicitly
  selected
- projected `libcuda.so.1` reports an API version compatible with the selected
  CUDA channel
- selected Spack CUDA/cuDNN/NCCL view, if present, matches the declared CUDA
  channel and architecture profile
- uv lockfile GPU wheel local-version tags match the declared CUDA channel and
  PyTorch version
- `cuInit` or an equivalent CUDA smoke succeeds inside the sandbox when GPUs
  are required

GPU visibility is explicit. By default Vaso exposes all detected NVIDIA device
nodes. A profile may restrict `CUDA_VISIBLE_DEVICES`, but that is an advisory
runtime filter unless Vaso also restricts the corresponding `/dev/nvidia*`
binds. Profiles that claim device isolation must bind only the requested GPU
device nodes and supporting control nodes.

MIG profiles must additionally validate `/dev/nvidia-caps` and any cgroup
device-access requirements. `--unshare-cgroup-try` is not sufficient evidence
that MIG capability nodes are usable.

## Bazel Support

Bazel is a first-class command mode, not an arbitrary shell convention. Bazel
commands run inside the rootfs with sandbox-relative cache paths:

```text
/vaso/cache/bazel/output-base
/vaso/cache/bazel/repository-cache
/vaso/cache/bazel/disk-cache
```

Related tool caches are also explicit:

```text
/vaso/cache/cargo
/vaso/cache/go
/vaso/cache/uv
/vaso/cache/pip
/vaso/cache/huggingface
/vaso/cache/ccache
/vaso/state/toolchains
```

The rootfs must include or project the system-level prerequisites needed to
build CUDA, C++, Rust, Go, and Python through Bazel. Vaso should keep Bazel
execution paths inside the sandbox and avoid host-absolute output bases or
repository caches.

Each Bazel invocation uses a per-repo output base by default:

```text
/vaso/cache/bazel/<repo-name>/output-base
/vaso/cache/bazel/<repo-name>/repository-cache
/vaso/cache/bazel/<repo-name>/disk-cache
```

Long-running concurrent builds for the same repo contend on Bazel's output-base
lock. Vaso should either serialize same-repo Bazel invocations with a
`/run/vaso/locks/bazel-<repo-name>.lock` lock file or allocate a unique
`output-base` per run when concurrency is requested. The chosen mode must be
recorded in `bwrap-plan.json`.

Bazel runs inside an already isolated bwrap environment. The default Vaso Bazel
profile treats bwrap as the outer sandbox and disables Bazel's `linux-sandbox`
strategy unless a repo profile proves nested sandboxing works on the host:

```text
--spawn_strategy=processwrapper-sandboxed
--genrule_strategy=processwrapper-sandboxed
--worker_sandboxing=false
```

Profiles that enable Bazel `linux-sandbox` must pass a doctor probe for nested
user and mount namespaces from inside the bwrap rootfs. The selected Bazel
sandbox strategy, persistent-worker policy, and `--as-pid-1` setting are part
of `bwrap-plan.json`. Bazel-owned repos still keep output bases and repository
caches under `/vaso/cache/bazel/<repo-name>/...`.

## Tool and Package Management

Vaso needs integrated package and tool management because coding agents and
foundation-model development workflows cannot rely only on the rootfs image.
The rule is: tools may be installed and cached inside the Vaso projection, but
runtime commands must still be reproducible from declared repo files, lockfiles,
and recorded Vaso state.

The default managers are:

- `mise` for general tool versions and tool activation.
- `uv` for Python virtual environments, dependency resolution, and lockfile
  updates.
- `cargo` for Rust package registry/git cache and build inputs.
- Bazel for repos that already use Bazel as their build and test executor.
- Spack for pinned native ABI/toolchain dependencies that must be built,
  cached, shipped, and validated as native artifacts.

Python itself is not taken from ambient rootfs `/usr/bin/python` for Vaso
development profiles. Vaso uses a pinned Astral `python-build-standalone`
distribution mounted at `/opt/vaso/python`, and `uv` creates environments from
that interpreter unless a profile explicitly selects another pinned Python.

The default cache/state layout is:

```text
/vaso/state/mise
/vaso/cache/mise
/vaso/cache/uv
/vaso/cache/pip
/vaso/cache/cargo/home
/vaso/cache/cargo/target/<repo-name>
/vaso/cache/go/pkg/mod
/vaso/cache/go/build
/vaso/cache/node
/vaso/cache/spack/source
/vaso/cache/spack/misc
/vaso/cache/spack/buildcache
/vaso/tmp/spack-stage
```

`/home/kvothe` remains a real home directory for tools that insist on home
conventions, but high-volume and reproducibility-sensitive caches should be
redirected to `/vaso/cache` and durable tool installs to `/vaso/state`.

### mise

Vaso should support repo-local `mise.toml` and `.mise.toml` files. The sandbox
environment sets:

```text
MISE_DATA_DIR=/vaso/state/mise
MISE_CACHE_DIR=/vaso/cache/mise
MISE_TRUSTED_CONFIG_PATHS=/workspace/<repo-name>
```

`mise install` and `mise exec` run inside the rootfs. `vaso doctor` must verify
that `mise` can resolve the target repo configuration without installing tools
outside `/vaso/state/mise` or `/vaso/cache/mise`.

Vaso should not silently trust arbitrary repo configs outside declared
`/workspace/<repo-name>` roots. Multi-repo runs must list every trusted repo
root explicitly.

### Spack Native ABI Layer

Spack is Vaso's native ABI foundation, not its general-purpose Python package
manager. It owns packages whose ABI, compiler, CUDA, C++ standard library,
Fortran runtime, BLAS/MPI/NCCL linkage, or system-level codec behavior must be
controlled independently of repo-local language package managers.

Vaso should borrow Draccus' shape rather than copy Draccus wholesale:

- declare named Spack environments such as `base-sys` and `native-abi`;
- keep `spack.yaml` and `spack.lock` under Vaso-managed manifests;
- use `concretizer.unify: true`, `reuse: true`, and minimal duplicate policy
  unless a profile justifies otherwise;
- record externals explicitly, especially host-provided compiler and CUDA
  toolkit entries;
- regenerate locks only through a dedicated build workflow, not during normal
  agent or development commands;
- validate each built view with explicit smoke scripts before it becomes a
  selectable runtime input.

The initial Draccus-derived package families to consider are:

- base system tools: `llvm`, `cmake`, `ninja`, `meson`, `pkgconf`, `gmake`,
  `patchelf`, `ccache`, `git`, `openssh`, `zsh`, `tmux`, `emacs`, `ripgrep`,
  `fd`, `jq`, and similar operator tools when mise is not the better owner;
- native math and media ABI: MKL or OpenBLAS, FFTW, Eigen, FFmpeg, compression
  libraries, and low-level codecs needed by ML data pipelines;
- GPU native stack components: CUDA toolkit, cuDNN, NCCL, and matching headers
  when they are needed for compilation, while the host kernel driver and driver
  DSOs still come from `/run/nvidia-driver`;
- compiler families: pinned GCC/LLVM toolchains and runtime libraries that
  Bazel, CMake, Cargo build scripts, and Python extension builds may consume.

Python application packages do not go into Spack by default. Vaso uses
`python-build-standalone` at `/opt/vaso/python`, `uv` for Python dependency
resolution, PyTorch/Astral CUDA wheel indexes for GPU Python packages, and
Bazel where a repo already has Bazel-owned Python execution. Spack may provide
native libraries that Python wheels link against, but it should not install a
parallel Spack Python or Spack-owned `py-torch` stack for normal Vaso profiles
unless the profile is explicitly an ABI-rebuild profile.

The read path for normal execution is immutable:

```text
/opt/vaso/spack                  pinned Spack checkout and config snapshot
/opt/vaso/spack/opt/spack        immutable install tree, or symlink to it
/opt/vaso/view/base-sys          operator/build-tool view
/opt/vaso/view/native-abi        ABI libraries and headers for builds
/opt/vaso/view/cuda-abi          optional CUDA/cuDNN/NCCL compile-time view
```

The write path exists only for toolchain build profiles:

```text
/vaso/cache/spack/source         downloaded source archives
/vaso/cache/spack/misc           Spack misc cache
/vaso/cache/spack/buildcache     binary cache mirror
/vaso/tmp/spack-stage            build stage, never part of runtime identity
/vaso/state/spack/locks          lock refresh records and build decisions
```

Normal `vaso run`, `vaso bazel`, `vaso uv`, `vaso cargo`, and agent commands
mount `/opt/vaso/spack` and `/opt/vaso/view/*` read-only. They do not receive a
writable Spack install tree and must fail if a command attempts to concretize,
install, or mutate Spack state. Mutable Spack work is a separate command class:

```bash
vaso spack concretize --env native-abi
vaso spack build --env native-abi
vaso spack validate --env native-abi
vaso spack promote --env native-abi --manifest <manifest>
```

Promotion is the only point where a newly built tree becomes runtime-visible.
It should be atomic: produce a manifest with the Spack commit, environment
name, `spack.yaml` hash, `spack.lock` hash, external package map, compiler map,
install-tree hash, view hashes, CUDA toolkit version, target architecture, and
validation results; then update a small selected-manifest pointer. Runtime
commands consume the selected immutable snapshot.

This is the deployment split:

- build: writable Spack checkout/install tree, writable stage, source cache,
  binary cache, network according to profile, and explicit lock refresh;
- ship: immutable install tree, immutable views, buildcache manifest, provenance
  manifest, and validation records;
- deploy/run: read-only selected snapshot plus writable Vaso run/cache/state
  surfaces unrelated to Spack mutation.

Vaso should support a local Spack buildcache because rebuilding ABI-heavy
packages during agentic development is too expensive and too hard to reason
about. The buildcache lives under `/vaso/cache/spack/buildcache` while being
populated, but promoted releases should be mirrored into a content-addressed
read-only host location and projected as part of `/opt/vaso/spack` or
`/opt/vaso/view/*`.

Environment defaults for runtime profiles:

```text
SPACK_ROOT=/opt/vaso/spack
SPACK_DISABLE_LOCAL_CONFIG=true
SPACK_USER_CACHE_PATH=/vaso/cache/spack/user
VASO_SPACK_VIEW_BASE_SYS=/opt/vaso/view/base-sys
VASO_SPACK_VIEW_NATIVE_ABI=/opt/vaso/view/native-abi
VASO_SPACK_VIEW_CUDA_ABI=/opt/vaso/view/cuda-abi
```

`PATH`, `LD_LIBRARY_PATH`, `CPATH`, `LIBRARY_PATH`, `PKG_CONFIG_PATH`, and
`CMAKE_PREFIX_PATH` should be composed from selected views by Vaso profiles, not
by sourcing arbitrary user shell files. A profile can expose `base-sys` tools
without exposing every ABI library globally; ABI views are best passed to Bazel,
CMake, or build scripts through explicit environment or toolchain config.

CUDA has a deliberate split:

- `/run/nvidia-driver` is the host driver projection and is required for
  runtime GPU access;
- a Spack CUDA toolkit, when selected, provides compile-time headers,
  libraries, `nvcc`, cuDNN, NCCL, and related development artifacts;
- `vaso doctor` must validate toolkit/driver compatibility, not just presence.

Draccus' `base-ml` includes Spack-built `py-torch` and `py-jax` variants. Vaso
should treat those as reference examples for ABI-heavy builds, not as the
default Python dependency strategy. The default foundation-model workflow is
`python-build-standalone` plus `uv` plus PyTorch/Astral wheels. A future
`spack-ml-rebuild` profile may deliberately build PyTorch, JAX, or custom CUDA
extension wheels from source, but it should be visibly separate from everyday
development profiles.

### uv

Python package management uses `uv` inside the rootfs, backed by the pinned
`python-build-standalone` interpreter at `/opt/vaso/python/bin/python3`.
Repo-local files such as `pyproject.toml`, `uv.lock`, and `.python-version`
remain source of truth for package metadata and project Python selection.

Default environment:

```text
VASO_PYTHON=/opt/vaso/python/bin/python3
UV_PYTHON=/opt/vaso/python/bin/python3
UV_CACHE_DIR=/vaso/cache/uv
PIP_CACHE_DIR=/vaso/cache/pip
VIRTUAL_ENV=/workspace/<repo-name>/.venv
```

Vaso should not set `PYTHONHOME` by default. `python-build-standalone`
distributions are intended to run from their extracted installation; setting
`PYTHONHOME` globally can break venv and subprocess behavior. `PATH` should
place `/opt/vaso/python/bin` before rootfs system Python for development
profiles.

For ordinary Python development, Vaso may create or update
`/workspace/<repo-name>/.venv` when the repo owns that convention. For Bazel
native Python repos, `uv` is an authoring tool for lockfiles and metadata while
Bazel remains the execution owner.

Python's default package index remains PyPI or an explicitly configured
mirror. Astral's wheel service is supplemental and must not be configured as
the global default index for ordinary packages. Index URLs are part of the Vaso
toolchain/dependency manifest and must be recorded in `bwrap-plan.json` for
mutating `uv` operations.

GPU Python packages should use CUDA-channel indexes as named uv sources, for
example `https://wheels.astral.sh/simple/cu128/` with `explicit = true` for
packages such as `flash-attn`, `deepspeed`, `causal-conv1d`, `mamba-ssm`, and
`transformer-engine-torch`. PyTorch itself should use the matching PyTorch CUDA
wheel index as a separate named source. Vaso should validate local-version tags
such as `+cu.12.8.torch.2.10` or package-specific equivalents against the
selected CUDA and PyTorch profile.

Default policy:

- online mode may resolve ordinary packages from the declared default index and
  GPU extension packages from named explicit indexes such as
  `https://wheels.astral.sh/simple/cu128/`;
- offline mode must use `/vaso/cache/uv`, `/vaso/cache/pip`, or a declared
  read-only wheelhouse and fail loudly on cache misses;
- lockfile updates must record the configured index URL and any extra indexes;
- Vaso should support prefetching a wheel closure into `/vaso/cache/uv` for
  later offline runs;
- GPU extension package installation must not fall back from the selected
  Astral CUDA-channel index to another index unless the fallback is declared in
  the run profile.

The Vaso toolchain manifest records the standalone Python archive identity:

```json
{
  "python": {
    "provider": "astral-sh/python-build-standalone",
    "upstream_commit": "b25f9d7f7cdec4b63c46769ff40fe2829efd10ac",
    "version": "3.x.y",
    "target_triple": "x86_64-unknown-linux-gnu",
    "archive": "cpython-...-install_only.tar.gz",
    "sha256": "...",
    "sandbox_path": "/opt/vaso/python"
  },
  "python_wheels": {
    "default_index": "https://pypi.org/simple/",
    "cache_path": "/vaso/cache/uv",
    "fallback_indexes": [],
    "gpu_indexes": {
      "astral-cu128": "https://wheels.astral.sh/simple/cu128/",
      "pytorch-cu128": "https://download.pytorch.org/whl/cu128"
    }
  }
}
```

Vaso must record every mutating `uv` operation in the run artifact:

- command argv
- repo path
- changed lockfiles
- before/after package metadata when available
- whether the operation mutated `.venv`, lockfiles, or both

### cargo

Rust package management uses Cargo inside the rootfs with cache paths under
`/vaso/cache/cargo`:

```text
CARGO_HOME=/vaso/cache/cargo/home
CARGO_TARGET_DIR=/vaso/cache/cargo/target/<repo-name>
```

For Bazel-native Rust repos, Cargo may still be used for authoring,
`rust-analyzer` support, and lockfile maintenance, but Bazel remains the
build/test executor. Vaso must avoid host-absolute paths in Cargo config that
would leak outside the sandbox.

### Package Manager Policy

Conda, mamba, and micromamba are not runtime package managers for this
substrate. `vaso doctor` must fail if any of them are first-class tools on the
sandbox `PATH` for normal development profiles.

Package-manager network access follows the run network mode:

- online mode may use configured registries and proxies;
- offline mode must use existing caches and fail loudly on missing artifacts;
- proxy-only mode, when added, must route package-manager traffic through the
  declared proxy.

Mutating package/tool operations are normal Vaso runs and must produce
`bwrap-plan.json`, stdout/stderr, `result.json`, and any lockfile diff evidence.

## Agent Execution

Vaso should provide agent-specific wrappers rather than treating agents as
opaque commands:

```bash
vaso agent codex --repo vaso -- <codex args>
vaso agent traecli --repo monarch -- <traecli args>
```

Each agent run declares:

- target repo name
- target working directory under `/workspace/<repo-name>`
- mounted repo map
- command argv
- environment allowlist
- bwrap plan digest
- run id

Agent tools can use `/home/kvothe` naturally for their own home-directory
state. Vaso-owned traces and run evidence live under `/vaso/runs` and
`/vaso/traces`.

Codex should be run with local rollout tracing enabled when the installed
version supports it. Codex's inspected upstream trace design uses
`CODEX_ROLLOUT_TRACE_ROOT` to write local bundles containing `manifest.json`,
`trace.jsonl`, `payloads/*.json`, and an optional reduced `state.json`. Vaso
should set `CODEX_ROLLOUT_TRACE_ROOT=/vaso/runs/<run-id>/agent/raw/codex` for
Codex runs and preserve any resulting bundle.

TraeCLI support starts with a wrapper-owned capture contract rather than an
assumption about a stable upstream trace export. The wrapper must:

- set `TRAE_HOME=/home/kvothe/.trae` unless a profile declares another home;
- record TraeCLI version, provider/model, argv, cwd, and environment metadata;
- copy any session JSONL, log, or hook artifacts discovered under
  `$TRAE_HOME`, `$XDG_STATE_HOME`, or a profile-declared Trae session root into
  `/vaso/runs/<run-id>/agent/raw/traecli`;
- run the Vaso terminal/tool wrapper so command starts, exits, stdout/stderr
  paths, and file-diff snapshots are captured even when TraeCLI's internal log
  schema changes;
- fail trace normalization with an explicit `raw_trace_unavailable` result when
  no structured TraeCLI session artifact is found, while preserving terminal and
  git-diff evidence.

Agent wrappers must not assume raw trace schemas are stable across agent
versions. Vaso owns the normalized event schema and stores raw artifacts as
evidence.

## Run and Trajectory Artifacts

The primary run record lives under:

```text
/vaso/runs/<run-id>/
  command.json
  bwrap-plan.json
  environment.json
  stdout.log
  stderr.log
  result.json
  agent/
    raw/
    normalized-events.jsonl
    trajectory.json
    git-before.patch
    git-after.patch
    git-diff.patch
```

`/vaso/traces` is reserved for cross-run indexes and exports. The canonical
per-run trajectory stays under `/vaso/runs/<run-id>/agent/`. `/vaso/traces`
contains no canonical per-run data; it contains derived indexes, summaries, and
exports that can be regenerated from `/vaso/runs`.

The normalized event stream should preserve a stable Vaso schema even when
Codex and TraeCLI expose different raw trace formats. Initial event classes:

- `start`
- `tool_call`
- `tool_result`
- `file_change`
- `git_diff`
- `finish`
- `failure`

Each normalized event must include:

```json
{
  "schema_version": 1,
  "run_id": "20260819T210000Z-example",
  "seq": 1,
  "timestamp": "2026-08-19T21:00:00Z",
  "agent": "codex",
  "event_type": "tool_call",
  "target_repo": "vaso",
  "cwd": "/workspace/vaso",
  "raw_refs": [
    {
      "path": "agent/raw/codex/trace.jsonl",
      "kind": "codex_rollout_trace"
    }
  ],
  "payload": {}
}
```

`schema_version`, `run_id`, `seq`, `timestamp`, `agent`, `event_type`,
`target_repo`, `cwd`, `raw_refs`, and `payload` are required. `seq` is
monotonic within one run. `raw_refs` may be empty only when no raw artifact
exists for the event source.

Each run record should capture before/after git status and diffs for the target
repo. Multi-repo runs should record per-repo status and diffs for every
declared writable repo. Diff capture is bounded: record full patches up to a
configured byte limit per repo, then store a truncation marker and a status
summary. The default limit is 16 MiB per repo.

## bwrap Plan Schema

`bwrap-plan.json` is the pre-execution contract. The runtime computes the bwrap
argv from this plan, and execution records the digest of the exact argv used.

Required fields:

```json
{
  "schema_version": 1,
  "run_id": "20260819T210000Z-example",
  "target_repo": "vaso",
  "cwd": "/workspace/vaso",
  "network_mode": "online",
  "uid": 1018,
  "gid": 1018,
  "identity_mode": "host-user",
  "rootfs": {
    "host_path": ".vaso/rootfs",
    "sandbox_path": "/",
    "mode": "ro",
    "manifest_sha256": "..."
  },
  "mounts": [
    {
      "name": "workspace:vaso",
      "operation": "bind",
      "host_path": "/srv/vaso/workspaces/vaso",
      "sandbox_path": "/workspace/vaso",
      "mode": "rw"
    }
  ],
  "devices": [],
  "scratch": {
    "sandbox_path": "/vaso/tmp",
    "mode": "bind",
    "host_path": ".vaso/tmp"
  },
  "bazel": {
    "sandbox_strategy": "processwrapper-sandboxed",
    "worker_sandboxing": false,
    "output_base_mode": "per-repo"
  },
  "environment": {},
  "command_argv": ["bash", "-lc", "true"],
  "bwrap_argv_sha256": "..."
}
```

The digest covers the exact argv passed to `bwrap`, including mount order,
environment, network mode, uid/gid settings, working directory, and inner
command argv. `result.json` must record the same digest and the bwrap binary
identity used for execution.

Plan generation uses deterministic ordering. Mounts are emitted in this order:
rootfs, kernel pseudo-filesystems, tmpfs roots, runtime dirs, device binds,
driver DSO binds, network overlays, workspace repos sorted by sandbox path,
Vaso writable surfaces sorted by sandbox path, and `/opt/vaso` read-only
snapshot. Environment entries are sorted by variable name before argv
construction. The digest is over the final argv, not a reserialized JSON object.

## Tiered Validation and Verification

Vaso validation is tiered. Lower tiers prove the rootfs contract and simple
developer workflows before higher tiers spend time on GPU-heavy package stacks,
Spack ABI builds, or agent trajectory capture. Every tier is a normal Vaso run
and writes `bwrap-plan.json`, `stdout.log`, `stderr.log`, `result.json`, and a
small machine-readable validation report under `/vaso/runs/<run-id>/validation/`.

The first implementation should include a fixture repo, mounted at
`/workspace/vaso-fixtures` or generated under `/vaso/tmp/fixtures`, containing
small C++, Rust, Go, and Python sources plus Bazel targets. Fixture sources
should be boring and deterministic: compile one function, run one binary, and
emit a known string or JSON value. The goal is to verify the substrate, not the
languages.

### Tier 0: Rootfs and bwrap Contract

Tier 0 validates that the sandbox can start and that the filesystem projection
matches the plan:

- bwrap binary identity and required flags;
- user namespace support and selected identity mode;
- pre-created bind targets for `/home/kvothe`, `/workspace/<repo>`,
  `/vaso/{state,cache,runs,traces,tmp}`, `/opt/vaso`, `/run/vaso`, and
  `/run/nvidia-driver`;
- deterministic mount ordering and matching `bwrap_argv_sha256`;
- read-only rootfs enforcement, verified by a negative write probe to `/usr` or
  another read-only rootfs path;
- writable surfaces, verified by write/read/delete probes in `/home/kvothe`,
  writable `/workspace/<repo>`, `/vaso/cache`, `/vaso/runs`, and `/vaso/tmp`;
- network mode: online HTTPS with CA validation, or offline namespace with no
  reachable external network.

Tier 0 acceptance is a JSON report with each probe result and the exact bwrap
argv digest. It is the prerequisite for every other tier.

### Tier 1: Simple Build and Run

Tier 1 validates ordinary development commands without GPU dependencies.

Bazel fixture targets:

```text
//cpp:hello_cpp          cc_binary plus cc_test
//rust:hello_rust        rust_binary plus rust_test
//go:hello_go            go_binary plus go_test
//python:hello_python    py_binary plus py_test
//integration:all        runs all language binaries and checks expected output
```

Required commands:

```bash
vaso bazel --repo vaso-fixtures -- build //cpp:hello_cpp //rust:hello_rust //go:hello_go //python:hello_python
vaso bazel --repo vaso-fixtures -- test //cpp:hello_cpp_test //rust:hello_rust_test //go:hello_go_test //python:hello_python_test
vaso bazel --repo vaso-fixtures -- run //integration:all
```

Tier 1 records:

- Bazel version and Bazelisk identity when used;
- Bazel sandbox strategy and whether nested `linux-sandbox` was disabled or
  explicitly probed;
- output base, repository cache, and disk cache paths under
  `/vaso/cache/bazel/<repo-name>/...`;
- compiler/toolchain identities for C++, Rust, Go, and Python;
- proof that no host-absolute output base or repository cache was used.

### Tier 2: Python, uv, PyTorch CUDA, and Astral Wheels

Tier 2 validates the default Python ML dependency path. It uses
`python-build-standalone` plus uv, not Spack Python and not conda.

The fixture project declares:

- default Python index: PyPI or a configured package mirror;
- named PyTorch CUDA index, for example `pytorch-cu128`;
- named explicit Astral CUDA index, for example `astral-cu128`;
- dependencies for `torch`, `flash-attn`, and `vllm` where supported by the
  selected Python, PyTorch, CUDA, platform, and wheel availability matrix.

Example uv source shape:

```toml
[tool.uv.sources]
torch = { index = "pytorch-cu128" }
flash-attn = { index = "astral-cu128" }
vllm = { index = "astral-cu128" }

[[tool.uv.index]]
name = "pytorch-cu128"
url = "https://download.pytorch.org/whl/cu128"
explicit = true

[[tool.uv.index]]
name = "astral-cu128"
url = "https://wheels.astral.sh/simple/cu128/"
explicit = true
```

Before install, the validation checks the live or mirrored index endpoints:

```bash
curl -L --fail -A 'Mozilla/5.0' https://wheels.astral.sh/simple/cu128/
curl -L --fail -A 'Mozilla/5.0' https://wheels.astral.sh/simple/cu128/flash-attn/
curl -L --fail -A 'Mozilla/5.0' https://wheels.astral.sh/simple/cu128/vllm/
```

The index probe records HTTP status, content type, response hash, and whether
the project list contains required projects. A 403 from a `HEAD` request is not
fatal if `GET` with a normal user agent succeeds; the validation should use the
same method uv will effectively rely on.

Required online commands:

```bash
vaso uv --repo vaso-fixtures -- sync --locked
vaso run --repo vaso-fixtures -- python - <<'PY'
import json
import torch

result = {
    "torch": torch.__version__,
    "cuda_runtime": torch.version.cuda,
    "cuda_available": torch.cuda.is_available(),
    "device_count": torch.cuda.device_count(),
}
if torch.cuda.is_available():
    x = torch.ones((2, 2), device="cuda")
    result["cuda_sum"] = float((x @ x).sum().item())
print(json.dumps(result, sort_keys=True))
PY
vaso run --repo vaso-fixtures -- python - <<'PY'
import importlib.util
import json

mods = ["flash_attn", "vllm"]
print(json.dumps({m: importlib.util.find_spec(m) is not None for m in mods}, sort_keys=True))
PY
```

If `flash-attn` or `vllm` has no compatible wheel for the selected Python,
PyTorch, CUDA, architecture, or glibc floor, Tier 2 fails as
`unsupported_wheel_matrix` with the exact missing compatibility tuple. It does
not silently build from source in the normal profile. Source builds belong to a
separate ABI-rebuild tier.

Tier 2 records:

- `/opt/vaso/python/bin/python3 --version` and standalone Python manifest;
- `uv --version`, `uv.lock` hash, and selected indexes;
- PyTorch version, `torch.version.cuda`, and local wheel tag;
- Astral wheel filenames, hashes, and local-version compatibility tokens;
- CUDA smoke result from PyTorch;
- offline replay status when run with a pre-populated cache.

### Tier 3: Native ABI and Spack Views

Tier 3 validates selected Spack snapshots and views without rebuilding them:

- selected Spack manifest and lock hashes;
- read-only mount status of `/opt/vaso/spack` and `/opt/vaso/view/*`;
- `base-sys` tools on `PATH` only when selected by profile;
- `native-abi` headers and libraries visible through explicit toolchain env;
- CUDA/cuDNN/NCCL compile-time view compatibility with the declared CUDA channel
  and host driver projection.

A separate build-profile validation covers `vaso spack concretize`, `build`,
`validate`, and `promote`. That profile is allowed to use writable Spack stage
and cache paths, and normal runtime profiles must prove those writable surfaces
are absent.

### Tier 4: Agent Execution and Trajectory Capture

Tier 4 validates Codex and TraeCLI wrappers against a small writable fixture
repo:

- run each agent with a prompt that edits one known file and runs one harmless
  command;
- capture raw artifacts under `/vaso/runs/<run-id>/agent/raw/<agent>`;
- capture terminal/tool wrapper events even when the agent's internal trace
  format is unavailable;
- produce `normalized-events.jsonl`, `trajectory.json`, and before/after git
  diffs;
- verify redaction and declared credential binds, including ssh-agent opt-in.

Codex acceptance requires a rollout trace bundle when the installed Codex build
supports `CODEX_ROLLOUT_TRACE_ROOT`. TraeCLI acceptance initially requires
wrapper-level terminal/tool/file-diff capture and either discovered structured
session artifacts or an explicit `raw_trace_unavailable` marker.

## Doctor Checklist

`vaso doctor` is the operator-facing validation command. It must check:

- `bwrap` exists or a bundled bwrap is available.
- bwrap supports the flags Vaso requires, including `--die-with-parent`,
  `--clearenv`, `--ro-bind`, `--bind`, `--dev-bind`, `--proc`, `--dev`, and
  `--unshare-user`.
- user namespaces work on this host, or doctor reports the exact sysctl, LSM,
  setuid-bwrap, or bundled-bwrap condition that blocks them.
- the rootfs exists and contains `/bin/sh` or `/bin/bash`.
- required leaf mount targets exist in the rootfs, not only their parents.
- `.vaso/home/kvothe`, `.vaso/state`, `.vaso/cache`, `.vaso/runs`, and
  `.vaso/traces` are writable from the host.
- default runtime identity maps to the host uid/gid and the rootfs names that
  identity as `kvothe`.
- `repo://self` resolves to the expected Vaso checkout.
- each declared repo resolves to an absolute host path and a
  `/workspace/<repo-name>` sandbox path.
- every repo path resolves under `host_root` or a declared additional host root.
- online mode has usable DNS and CA certificates.
- offline mode actually uses a separate network namespace.
- ssh-agent access is absent by default and, when enabled, has a matching socket
  bind recorded in the plan.
- GPU-required profiles can see host NVIDIA devices and driver DSOs under the
  declared `/run/nvidia-driver` layout.
- declared CUDA channel is coherent across host driver, projected `libcuda`,
  Spack CUDA view, PyTorch wheels, Astral wheels, and lockfile local-version
  tags.
- pinned `python-build-standalone` exists at `/opt/vaso/python`, matches the
  toolchain manifest, and can run `import ssl, sqlite3, venv`.
- selected Spack snapshot records its Spack commit, environment names,
  `spack.yaml` hashes, `spack.lock` hashes, externals, compiler map, and view
  hashes.
- normal runtime profiles mount Spack install trees and views read-only.
- Spack build profiles have writable stage/cache paths and no writable Spack
  surfaces leak into normal `run`, `bazel`, `uv`, `cargo`, or agent profiles.
- selected Spack CUDA/cuDNN/NCCL compile-time view, when enabled, is compatible
  with the host driver projection under `/run/nvidia-driver`.
- Spack-provided Python, `py-torch`, or `py-jax` are absent from normal
  development profiles unless an explicit ABI-rebuild profile selects them.
- ordinary Python package default index is PyPI or a configured package mirror,
  not the supplemental Astral index.
- configured Astral GPU package sources use the selected CUDA-channel URL such
  as `https://wheels.astral.sh/simple/cu128/` and are named explicit uv indexes.
- Astral CUDA-channel endpoint probes succeed or fail with recorded HTTP status,
  content type, and response hash.
- locked GPU Python wheels carry local-version compatibility tokens matching
  the selected CUDA and PyTorch profile.
- the selected PyTorch wheel source matches the Astral CUDA channel.
- offline Python package operations fail on missing cached wheels instead of
  silently reaching an undeclared index.
- Bazel cache/output-base paths are sandbox-relative.
- Bazel sandbox strategy is declared, and nested `linux-sandbox` is enabled
  only after an in-rootfs probe succeeds.
- `mise`, `uv`, and `cargo` either exist in the rootfs or are installed under
  declared Vaso tool roots.
- tool-manager caches resolve under `/vaso/cache` and durable tool installs
  resolve under `/vaso/state`.
- normal development profiles do not put conda, mamba, or micromamba on `PATH`.
- Codex and TraeCLI agent wrappers can create a run directory and write trace
  artifacts.
- Tier 0 and Tier 1 validation fixtures pass before GPU or agent tiers run.

## CLI Shape

The initial public command surface is:

```bash
vaso doctor
vaso shell [--repo <name>]
vaso run --repo <name> -- <cmd>
vaso bazel --repo <name> -- <bazel args>
vaso uv --repo <name> -- <uv args>
vaso cargo --repo <name> -- <cargo args>
vaso mise --repo <name> -- <mise args>
vaso spack concretize --env <name>
vaso spack build --env <name>
vaso spack validate --env <name>
vaso spack promote --env <name> --manifest <manifest>
vaso agent codex --repo <name> -- <codex args>
vaso agent traecli --repo <name> -- <traecli args>
vaso trace show <run-id>
```

The interface should keep bwrap mount details behind the Vaso runtime module.
Callers choose the command class and target repo; Vaso creates the layout,
validates the projection, emits the plan, executes the command, and records the
evidence.

## Design Principles

- One stable sandbox namespace beats many top-level special cases.
- `/workspace` behaves like a multi-repo development root.
- `/home/kvothe` behaves like a normal user home.
- `/vaso` contains durable Vaso-owned generated state.
- `/opt/vaso` contains read-only Vaso runtime/config material.
- `/run` contains runtime projections and short-lived coordination.
- Host CUDA driver projection is explicit and auditable.
- Bazel paths are sandbox-relative and high-volume caches are explicit.
- Spack is for native ABI/toolchain artifacts; Python packages stay with
  `python-build-standalone`, `uv`, and declared wheel indexes by default.
- Agent trajectories are run evidence, not terminal scrollback.
