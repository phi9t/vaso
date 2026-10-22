# Candidate qualification

A candidate pins source, two CUDA rootfs bundles, four profile wheel closures,
and an acceptance policy. Freezing those identities does not certify a release.
[Status and evidence](status.md) records the remaining release gates.

## Freeze and verify

Freeze from an explicit source revision and the artifact inventory. The output
must be new; the command refuses to overwrite an existing candidate.

```sh
python3 -B scripts/release/candidate.py freeze \
  --source <source-commit> --name <new-candidate-name> \
  --out "$VASO_ESTATE_ROOT/candidates/candidate.json"
```

Retain the printed SHA-256 separately. Restore the six immutable references with
[the operator guide](operator-guide.md#restore-a-rootfs-and-wheels), using these
estate directories: `downloads/rootfs-{cu129,cu130}` and
`downloads/release-{torch,jax}-{cu129,cu130}`. Materialize each rootfs under
`rootfs-lines/<line>/rootfs` with its bundle metadata alongside it.
Create a clean detached checkout at the candidate's source commit.

For a new estate, `restore.py` composes the existing acquisition, bundle, and
generic insula entrypoints. Provision the GHCR credential first as
`$VASO_ESTATE_ROOT/publish/ghcr-token` with mode `600`, or supply `GHCR_TOKEN`
through the environment. The host needs Bash, Python, Git, curl, tar, zstd,
sha256sum, and Bubblewrap. The runner installs the pinned ORAS tool, pulls all
six digests, verifies and unpacks both rootfs archives, checks the candidate
payloads, and runs a generic command inside each line:

```sh
python3 -B scripts/release/restore.py \
  --candidate "$CANDIDATE" --candidate-sha256 "$CANDIDATE_SHA256" \
  --checkout "$CANDIDATE_CHECKOUT" --estate-root "$VASO_ESTATE_ROOT" \
  --out "$VASO_ESTATE_ROOT/restore/attempt-1"
```

It refuses an estate with existing downloads, rootfs lines, or runtime state.
It requires 100 GiB free by default; `--min-free-gib` declares another storage
budget, and `--stage-timeout` bounds each command (default: one hour). Logs,
`state.json`, and `summary.json` survive failure. Retain a failed estate and
retry in a new one. A `restored-and-entered` result covers acquisition and
generic execution; it does not qualify GPUs or native builds. The new runner's
failure paths have host-test coverage; a complete live registry restoration
through this entrypoint remains unverified.

For an already restored estate, verify its candidate inputs separately:

```sh
python3 -B scripts/release/candidate.py verify \
  --candidate "$CANDIDATE" --candidate-sha256 "$CANDIDATE_SHA256" \
  --checkout "$CANDIDATE_CHECKOUT" --estate-root "$VASO_ESTATE_ROOT" \
  --out "$VASO_ESTATE_ROOT/candidate-verification.json"
```

Verification hashes restored wheels, metadata, and complete rootfs archives and
checks the source checkout. Transport chunks may have been removed after
reassembly; the complete archive checksum is still mandatory. This verifies
archive identity and materialized rootfs metadata, not every unpacked rootfs
file or successful runtime execution.

## Run unattended

The single-GPU preview policy requires three rounds on one selected B200:
fresh runtime caches, then two rounds whose cache files match the previous
round's recorded hashes. Each round covers both CUDA lines, both profiles,
consumers, and co-residence. The two-process CPU Gloo check remains included.
Launched MPI is outside support.

Set `GPU_INDEX` to the selected physical device index:

```sh
python3 -B scripts/release/qualify.py \
  --candidate "$CANDIDATE" --candidate-sha256 "$CANDIDATE_SHA256" \
  --checkout "$CANDIDATE_CHECKOUT" --estate-root "$VASO_ESTATE_ROOT" \
  --gpu-index "$GPU_INDEX" \
  --out "$VASO_ESTATE_ROOT/qualification/attempt-1" --execute
```

Omit `--execute` for a resource preflight and recorded plan. Every invocation
needs a new output directory. Missing prerequisites return a nonzero status.
The default stage deadline is three hours; set `--stage-timeout` in seconds
before starting if the execution contract requires another bound.

The plan explicitly lists omitted multi-GPU workloads and gates. These features
remain unqualified. The historical eight-GPU candidate retains a separate
protocol: three matrix rounds plus three cold W5b attempts, three reused-cache
W5b attempts, and a cold/warm complete CUDA 13 JAX profile pair. That protocol
uses `--reservation "$GPU_RESERVATION"` instead of `--gpu-index`. Its private
JSON reservation needs `reference`, `exclusive: true`, `gpu_count: 8`, and
timezone-aware `starts_at` and `ends_at`. Idle devices alone do not establish
that reservation. Single-GPU evidence does not resolve its collective timeout.

The runner acquires an estate-local lease for the exact selected device set and
samples physical occupancy during execution. Foreign compute on a selected
device stops its own process group and fails the attempt. Other devices may
remain occupied for single-GPU runs. A cooperative lease does not prevent an
unrelated host process from starting; monitoring makes detected interference
an explicit failure. Private logs include host-local execution details and must
be sanitized before publication.

The runner uses the frozen checkout's insula entrypoint, installs hashed wheels
offline into a new venv for each stage, and preserves owned runtime caches for
reuse rounds. It never rebuilds native packages. Co-residence composes the two
pinned closures with the JAX NumPy wheel, which carries its own OpenBLAS
dependency; versions must agree and other overlapping wheels must have identical hashes.

Inspect `state.json` while running, `events.jsonl` for completed stages, and
`summary.json` for the final outcome. Each stage retains its log, GPU observations,
cache inventories, and workload/gate JSON. Logs and results survive failure;
a retry gets a separate attempt directory. Cache inventory equality proves
retention of those files, not that every kernel used a cache hit.

## Reuse after a policy revision

A scope change gets a new candidate record. If its source, component identities,
selection hashes, and complete executed plan are identical, verify the retained
attempt against the new policy without changing the original evidence:

```sh
python3 -B scripts/release/qualify.py \
  --candidate "$CANDIDATE" --candidate-sha256 "$CANDIDATE_SHA256" \
  --checkout "$CANDIDATE_CHECKOUT" --estate-root "$VASO_ESTATE_ROOT" \
  --reuse-attempt "$PREVIOUS_ATTEMPT" \
  --evidence-candidate "$PREVIOUS_CANDIDATE" \
  --out "$VASO_ESTATE_ROOT/qualification/policy-reuse-1"
```

Only a completed passing attempt can satisfy this check. Missing stages, changed
inputs or criteria, altered stage records, and unverified cache reuse fail.
The new receipt hashes the retained evidence and links both candidate identities;
it does not erase earlier failed attempts or grant promotion.

## Promotion

`passed-attempt` records successful execution of that attempt's scope. Promotion
also requires native clean-build and reproducibility proof, artifact provenance,
license/SBOM metadata, fresh-estate restoration, and disposition of earlier
unexplained failures in the supported scope. The retained eight-GPU W5b timeout
remains a prerequisite for adding multi-GPU support. Later passing attempts do
not erase failed ones. A changed candidate or relevant verification input requires reassessing reusable evidence.

## Maintaining the workflow

| File in `scripts/release/` | Responsibility |
|---|---|
| `candidate.py` | Candidate schema, immutable selection, source and payload identity |
| `restore.py` | Fresh-estate acquisition and generic execution using existing entrypoints |
| `qualify.py` | Attempt lifecycle, cache retention, wheel composition, evidence reuse, and final attempt status |
| `qualification_policy.py` | Supported coverage and command recipes derived from the pinned candidate/source |
| `gpu_monitor.py` | Physical-device preflight, exact leases, process ownership, and interference observations |
| `process_runner.py` | Shared noninteractive execution, deadlines, logs, and cleanup of owned process groups |
| `preview-policy.json` | Current policy used when freezing a new candidate |

Candidate JSON files embed their policy; changing the default policy never
rewrites an existing candidate. Runners record hashes of their implementation
files. Keep raw attempt evidence on the estate, and publish only reviewed,
sanitized summaries. Restore and qualification do not invoke the publisher.
The existing native determinism and release-cut commands remain separate:
runtime success cannot substitute for build proof or grant promotion.
