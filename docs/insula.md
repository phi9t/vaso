# Insula

Insula owns the rootfs, command execution, and estate directory projection.
Paths in the directory table are relative to `insula/`.
All tools, toolchains, and dependencies used by a process are under the
rootfs it sees. Host storage backs that filesystem and explicit data mounts.

| Directory | Responsibility |
|---|---|
| `rootfs/` | CUDA line rootfs construction, bundles, content identity, and validation |
| `estate/` | `VasoEstate`, storage placement, sandbox projections, and path translation |
| `execution/` | Bubblewrap argv, environment, readiness, leases, results, and operational acceptance |

Prepare a rootfs with `insula/rootfs/prepare.sh build --line cu129 --fetch` and
an explicit `VASO_ESTATE_ROOT`. Preparation stages the rootfs, tooling, and
manifests together; execution uses the prepared filesystem.

Run a generic command from the repository root without a Foundry profile or package lock:

```sh
VASO_ESTATE_ROOT=/path/to/estate insula/run.sh \
  --line cu129 --workspace /path/to/project -- /bin/sh -c 'pwd'
```

The command sees the project read-only at `/workspace/vaso`, writable estate
state at `/vaso`, a read-only rootfs, a cleared environment, and an isolated
network. `--rootfs DIR` selects an explicit rootfs instead of a CUDA line.
The workspace must be a readable, searchable directory. Empty, missing, and
file paths fail with exit 2 before estate materialization or child execution.
`--print-argv` renders the request without readiness checks or entering
Bubblewrap. It still materializes the selected estate layout. Fetch execution is explicit and
requires a SHA-256 pin and the network-fetch lease.

Readiness diagnostics go to stderr; stdout belongs to the command or the printed
JSON plan. The launcher returns the command status and uses Bubblewrap's
`--die-with-parent` to stop owned execution if the launcher terminates. It does
not automatically retry a workload or create a generic run ledger. Keep stdout,
stderr, and status with the calling workflow; repeated invocations reuse estate
state, so the workload owns safe recovery of its outputs. The
[CPU acceptance command](verification.md#real-insula-operational-acceptance)
retains separate reports and logs while removing only its disposable state.

For callers that prepare their own projections, source `execution/insula.sh`
and supply `ROOT_PLAN`, `INSULA_BIND_ARGS`, `INSULA_ENV_ARGS`, and optional
working directory/namespace settings before calling `insula -- COMMAND`.
The caller owns shared execution leases; the launcher exposes lease helpers.
`--print-argv` makes the resulting request inspectable. Preflight failure
and command exit status propagate to the caller.

Lease state lives under `$VASO_ESTATE_ROOT/agents/leases`. Malformed JSON,
unsupported schemas, or invalid holder metadata produce exit code 2 and remain
unchanged for diagnosis. Repair a record only after establishing holder ownership.
Valid expired leases and dead local holders are still reclaimed automatically.
Updates flush a staged file before replacing the state. Failed writes preserve
the previous record and remove staging files, so an acquisition can retry
without manual cleanup or retaining a reservation that was never granted.

GPU lease capacity comes from `--capacity`, then `VASO_GPU_COUNT`, or a
successful `nvidia-smi -L` query. A failed query never supplies an assumed device
count. Zero capacity permits no GPU leases. Invalid requests are rejected before
the lease directory is created or existing coordination state is changed.

Foundry's build/release context is in `foundry/tools/insula_context.sh`.
Its canonical entrypoint is `foundry/insula.sh`; pinned build tools are
installed by `rootfs/foundry_tools.py` before execution. See the [Foundry guide](foundry.md)
for acquisition and offline prerequisite checks.
Generic insula execution has no dependency on Foundry.

The Python package under `execution/vaso/` supplies the production readiness
checks, lease-state validation, and atomic file updates. The shell entrypoints
load these helpers directly from the checkout.

See [architecture](architecture.md) for identities and compatibility paths, and
[verification](verification.md) for real Bubblewrap acceptance.

For storage and recovery, see the [estate lifecycle deep dive](estate.md).

## Rootfs namespace

| Sandbox path | Purpose |
|---|---|
| `/workspace/<repo>` | Explicit source projections |
| `/vaso/cache` | Package, build, kernel, and tool caches |
| `/vaso/state` | Durable Vaso state |
| `/vaso/runs` | Plans, logs, results, and evidence |
| `/vaso/traces` | Derived trace indexes and exports |
| `/opt/vaso` | Prepared tools and runtime material |
| `/run/nvidia-driver` | Explicitly projected host driver libraries and tools |
| `/tmp` | Process-local temporary storage |

The prepared base is read-only during normal execution. Writable estate state
and home directories use explicit binds. The standalone launcher mounts the
project read-only; Foundry prepares its own source/output projections. The CUDA
compiler toolkit comes from the selected rootfs, separately from the projected
host driver. See [architecture](architecture.md) for caller-owned projections.
