# Candidate qualification

A candidate pins source, two CUDA rootfs bundles, four profile wheel closures,
and an acceptance policy. Freezing those identities does not certify a release.
[Status and evidence](status.md) records the remaining release gates.
Each rootfs must pin `rootfs-bundle.json` and its whole archive. If the archive
also appears in the file inventory, both hashes must agree. Validation rejects
incomplete or contradictory selections before restoration starts.

## Candidate and attempt state

The candidate fixes which inputs and policy an attempt uses. Restore and
qualification each create a new attempt directory; several attempts can refer
to the same candidate without editing it. A changed selection or policy gets
a new candidate and checksum.

| Record or state | Location | Meaning and lifetime |
|---|---|---|
| Candidate | The JSON path supplied to `--candidate` | Immutable selection, checked against the separately retained `--candidate-sha256` |
| Attempt | New directory supplied to restore or qualification `--out` | Evidence for one invocation, including failed prerequisites; never reused for a retry |
| Progress | `<attempt>/state.json` | Replaceable live status; final qualification status points to the summary |
| Plan | `<attempt>/plan.json`, when planning is reached | Selected acquisition or execution recipe; recording it does not establish execution |
| Final result | `<attempt>/summary.json` | Terminal outcome and retained stage records for that attempt |
| Payload verification | `<attempt>/payload-verification.json`, when verification is reached | Result of checking restored files against candidate pins |
| Runtime caches | `<estate>/run/<group-id>/` during qualification | Writable execution state shared by the intended reuse rounds; inventories are retained with stage evidence |

An early failure may leave a summary without a plan or payload-verification
record. An attempt can finish as `blocked-prerequisite` or `preflight-passed`.
`passed-attempt` records successful runtime execution within the attempt's scope.
Keep the complete attempt directory for diagnosis and reuse checks. Read the
[estate lifecycle](estate.md) for backing-state persistence and restore choices.

## Freeze and verify

Freeze from an explicit source revision and the artifact inventory. The output
must be new; the command refuses to overwrite an existing candidate.

```sh
python3 -B tools/release/candidate.py freeze \
  --source <source-commit> --name <new-candidate-name> \
  --out "$VASO_ESTATE_ROOT/candidates/candidate.json"
```

Retain the printed SHA-256 separately. Restore the six immutable references with
[the operator guide](operator-guide.md#restore-a-rootfs-and-wheels), using these
estate directories: `downloads/rootfs-{cu129,cu130}` and
`downloads/release-{torch,jax}-{cu129,cu130}`. Materialize each rootfs under
`rootfs-lines/<line>/rootfs` with its bundle metadata alongside it.
Create a clean detached checkout at the candidate's source commit.

For a new estate, `restore.py` composes acquisition,
`insula/rootfs/prepare.sh restore`, and `insula/run.sh`. Preparation installs the
matching bundle manifests and verifies rootfs tooling before generic execution. Provision the GHCR credential first as
`$VASO_ESTATE_ROOT/publish/ghcr-token` with mode `600`, or supply `GHCR_TOKEN`
through the environment. The host needs Bash, Python, Git, curl, tar, zstd,
sha256sum, and Bubblewrap. The runner installs the pinned ORAS tool, pulls all
six digests, verifies and unpacks both rootfs archives, checks the candidate
payloads, and runs a generic command inside each line:

```sh
python3 -B tools/release/restore.py \
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
python3 -B tools/release/candidate.py verify \
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
python3 -B tools/release/qualify.py \
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
python3 -B tools/release/qualify.py \
  --candidate "$CANDIDATE" --candidate-sha256 "$CANDIDATE_SHA256" \
  --checkout "$CANDIDATE_CHECKOUT" --estate-root "$VASO_ESTATE_ROOT" \
  --reuse-attempt "$PREVIOUS_ATTEMPT" \
  --evidence-candidate "$PREVIOUS_CANDIDATE" \
  --out "$VASO_ESTATE_ROOT/qualification/policy-reuse-1"
```

Only a completed passing attempt can satisfy this check. The consumed recipe and
Foundry resource bytes and the runner closure must also match. Missing stages,
changed inputs or criteria, altered stage records, and unverified cache reuse fail.
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

| File in `tools/release/` | Responsibility |
|---|---|
| `candidate.py` | Candidate schema and input-shape validation, canonical component names, immutable selection, source and payload identity |
| `evidence.py` | Canonical JSON bytes, atomic creation of immutable evidence, and replacement of progress state |
| `nightly_selection.py` | Pure selection of complete matching nightly summaries, including required gates on both CUDA lines |
| `cut.py` | Read nightly summaries, validate artifacts, and assemble a release directory |
| `restore.py` | Fresh-estate acquisition and generic execution using existing entrypoints |
| `qualify.py` | Attempt lifecycle, cache retention, wheel composition, evidence reuse, and final attempt status |
| `qualification_policy.py` | Validate supported policy data and select ordered coverage and commands from the pinned source recipe |
| `qualification_recipe.json` | Maintained workload IDs, gates, and consumer modules for each line/profile |
| `gpu_monitor.py` | Physical-device preflight, exact leases, process ownership, and interference observations |
| `process_runner.py` | Scoped attempt interruption, noninteractive execution, deadlines, logs, and cleanup of owned process groups |
| `preview-policy.json` | Current policy used when freezing a new candidate |

`qualification_recipe.json` is read from the exact candidate source checkout;
new freezes pin its bytes together with the comparator and allowlist under
`tools/release/`. Resource needs remain declared once in Foundry's `WORKLOADS`;
its passive `resource_metadata.py` reader inspects literal declarations without
executing the selected source. Plans and summaries retain the actual consumed
recipe/resource hashes, while summaries retain runner file hashes. Edit the
recipe to change coverage, and verify the ordered plan and commands with the
adjacent policy tests. A changed recipe, resources, runner, or executed plan
requires new evidence rather than policy-only admission.

Old immutable candidates, source checkouts, and attempt records keep their
original identities and runners. A pinned checkout predating the maintained
recipe fails clearly before GPU discovery; this runner never reconstructs live
policy from a historical observation report. Use its original pinned runner to
interpret historical evidence, or freeze a new candidate from maintained source.
The retained historical reports remain evidence, not current coverage policy.
An explicit device still selects single-device coverage even with an eight-GPU
policy; this preserves the existing branch and does not qualify multi-GPU use.

Tests sit beside these owners: `gpu_monitor_test.py` covers observation parsing,
physical-device readiness, and process ownership; `qualification_policy_test.py`
covers matrix coverage and command recipes; `qualify_test.py` covers composition,
cache inventory, and evidence reuse. `nightly_selection_test.py` exercises
selection on summary data; `release_cut_test.py` covers the cut entrypoint and
its failure paths. These host tests use synthetic observations
and owned child processes; they require no GPU or reservation.

Candidate loading hashes and parses one byte snapshot through
`candidate.load_verified`. Verify, restore, qualification, and evidence reuse
share it. Freezing likewise derives component selections and the recorded
inventory hash from the same bytes, so file replacement cannot mix identities.

GPU observation parsing belongs to `gpu_monitor.parse_snapshot`. Acquisition
adds the observation time; policy separately decides which devices are supported
and free. Invalid query rows or inconsistent device/process observations stop
qualification with a query-and-row diagnostic that omits device UUIDs.

The restore and qualification CLIs validate arguments in `main`; `_run_attempt`
owns each attempt's evidence and execution. The shared `interrupt_on_sigterm`
scope restores the caller's signal handler when the attempt exits. An existing
output directory is rejected with exit code 2 and remains unchanged.

Candidate JSON files embed their policy; changing the default policy never
rewrites an existing candidate. Runners record hashes of their implementation
files, including the shared evidence writer. Evidence creation refuses an
existing name and publishes only a complete, flushed JSON file. Progress updates
replace the previous state atomically. A failed write preserves previous records
and removes its partial file; it does not reinterpret a failed attempt.
Keep raw attempt evidence on the estate, and publish only reviewed,
sanitized summaries. Restore and qualification do not invoke the publisher.
The existing native determinism and release-cut commands remain separate:
runtime success cannot substitute for build proof or grant promotion.

Release cutting stages the complete directory beside its final destination and
renames it into place after preparation succeeds. Each copied file is hashed in
staging: wheels and SBOMs retain their selected pins; unpinned metadata must match
the source hash captured immediately before copying. Validation failures and Python
interruptions remove that staging directory, so the same process can retry.
The upstream nightly results and acceptance evidence remain available for diagnosis.
