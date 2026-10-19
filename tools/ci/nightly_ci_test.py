"""Behavioral tests for tools/ci/nightly.sh."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "ci" / "nightly.sh"
SUMMARY_SCRIPT = ROOT / "tools" / "ci" / "nightly_summary.py"
OLD_TIMESTAMP = 1_600_000_000


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_executable(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def fake_gate(tmp_path: Path, *, fail_name: str | None = None, referenced_path: Path | None = None) -> Path:
    return write_executable(
        tmp_path / "fake-release-gate.sh",
        """#!/usr/bin/env bash
set -euo pipefail
profile=""
line=""
commit=""
acceptance_out=""
release=0
fail_name="__FAIL_NAME__"
referenced_path="__REFERENCED_PATH__"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --profile) profile="$2"; shift 2 ;;
    --line) line="$2"; shift 2 ;;
    --commit) commit="$2"; shift 2 ;;
    --acceptance-out) acceptance_out="$2"; shift 2 ;;
    --release) release=1; shift ;;
    *) shift ;;
  esac
done
name="$profile:$line"
if [ "$release" != "1" ]; then
  echo "missing --release" >&2
  exit 64
fi
python3 - "$NIGHTLY_EVENT_LOG" gate_start "$name" "${CUDA_VISIBLE_DEVICES:-}" "${VASO_BAZEL_OB:-}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
with path.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({
        "event": sys.argv[2],
        "name": sys.argv[3],
        "gpu": sys.argv[4],
        "output_base": sys.argv[5],
    }, sort_keys=True) + "\\n")
PY
mkdir -p "$(dirname "$acceptance_out")"
rc=0
verdict=passed
if [ "$name" = "$fail_name" ]; then
  rc=42
  verdict=failed
fi
python3 - "$acceptance_out" "$profile" "$line" "$commit" "$verdict" "$referenced_path" <<'PY'
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
refs = [sys.argv[6]] if sys.argv[6] else []
doc = {
    "schema_version": 1,
    "profile": sys.argv[2],
    "line": sys.argv[3],
    "commit": sys.argv[4],
    "verdict": sys.argv[5],
    "referenced_paths": refs,
}
out.write_text(json.dumps(doc, sort_keys=True) + "\\n", encoding="utf-8")
PY
python3 - "$NIGHTLY_EVENT_LOG" gate_end "$name" "${CUDA_VISIBLE_DEVICES:-}" "${VASO_BAZEL_OB:-}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
with path.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({
        "event": sys.argv[2],
        "name": sys.argv[3],
        "gpu": sys.argv[4],
        "output_base": sys.argv[5],
    }, sort_keys=True) + "\\n")
PY
exit "$rc"
""".replace("__FAIL_NAME__", fail_name or "").replace("__REFERENCED_PATH__", str(referenced_path or "")),
    )


def fake_determinism(tmp_path: Path) -> Path:
    return write_executable(
        tmp_path / "fake-determinism.sh",
        """#!/usr/bin/env bash
set -euo pipefail
profile=""
line=""
work_dir=""
out=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --profile) profile="$2"; shift 2 ;;
    --line) line="$2"; shift 2 ;;
    --work-dir) work_dir="$2"; shift 2 ;;
    --out) out="$2"; shift 2 ;;
    --git-sha) shift 2 ;;
    *) shift ;;
  esac
done
python3 - "$NIGHTLY_EVENT_LOG" determinism "$profile:$line" "$work_dir" "$out" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
with path.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({
        "event": sys.argv[2],
        "name": sys.argv[3],
        "work_dir": sys.argv[4],
        "out": sys.argv[5],
    }, sort_keys=True) + "\\n")
PY
mkdir -p "$work_dir" "$(dirname "$out")"
python3 - "$out" "$profile" "$line" <<'PY'
import json
import sys
from pathlib import Path

Path(sys.argv[1]).write_text(json.dumps({
    "schema_version": 1,
    "profile": sys.argv[2],
    "line": sys.argv[3],
    "identical": True,
    "differing_files": [],
}, sort_keys=True) + "\\n", encoding="utf-8")
PY
""",
    )


def fake_nvidia_smi(tmp_path: Path, scenario: str, *, log_events: bool = True) -> Path:
    bin_dir = tmp_path / f"fake-nvidia-smi-{scenario}" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    state_path = tmp_path / f"fake-nvidia-smi-{scenario}" / "state.json"
    script = bin_dir / "nvidia-smi"
    return write_executable(
        script,
        f"""#!/usr/bin/python3
import json
import os
import sys
from pathlib import Path

SCENARIO = {scenario!r}
STATE_PATH = Path({str(state_path)!r})
GPU_COUNT = 8
LOG_EVENTS = {log_events!r}


def load_state():
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {{}}


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, sort_keys=True) + "\\n", encoding="utf-8")


def active_gpu_lease_count():
    estate = os.environ.get("VASO_ESTATE_ROOT")
    if not estate:
        return 0
    try:
        doc = json.loads((Path(estate) / "agents" / "leases" / "gpu.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return 0
    leases = doc.get("leases", [])
    return len(leases) if isinstance(leases, list) else 0


def write_event(record):
    if not LOG_EVENTS:
        return
    event_log = os.environ.get("NIGHTLY_EVENT_LOG")
    if not event_log:
        return
    record["active_gpu_leases"] = active_gpu_lease_count()
    path = Path(event_log)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\\n")


args = sys.argv[1:]
if args == ["-L"]:
    for index in range(GPU_COUNT):
        print(f"GPU {{index}}: Fake B200 (UUID: GPU-fake-{{index}})")
    raise SystemExit(0)

if any(arg.startswith("--query-gpu=index,memory.used") for arg in args):
    state = load_state()
    query_count = int(state.get("gpu_queries", 0)) + 1
    state["gpu_queries"] = query_count
    busy = SCENARIO == "always-busy" or (SCENARIO == "busy-then-free" and query_count == 1)
    state["last_busy"] = busy
    save_state(state)
    used = [9000] + [0] * (GPU_COUNT - 1) if busy else [700] + [0] * (GPU_COUNT - 1)
    write_event(
        {{
            "event": "nvidia_smi_gpu",
            "query": query_count,
            "used_mib": used,
            "max_used_mib": max(used),
        }}
    )
    for index, memory in enumerate(used):
        print(f"{{index}}, {{memory}}")
    raise SystemExit(0)

if any(arg.startswith("--query-compute-apps=pid,gpu_uuid,used_memory") for arg in args):
    state = load_state()
    query_count = int(state.get("compute_queries", 0)) + 1
    state["compute_queries"] = query_count
    save_state(state)
    busy = bool(state.get("last_busy"))
    write_event(
        {{
            "event": "nvidia_smi_compute_apps",
            "query": query_count,
            "used_mib": 9000 if busy else 0,
        }}
    )
    if busy:
        print("4242, GPU-fake-0, 9000")
    raise SystemExit(0)

print("unsupported fake nvidia-smi args: " + " ".join(args), file=sys.stderr)
raise SystemExit(99)
""",
    )


def write_summary(path: Path, run_id: str, verdict: str, finished: str, refs: list[Path] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": run_id,
                "verdict": verdict,
                "finished_utc": finished,
                "referenced_paths": [str(path) for path in refs or []],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def make_work_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "payload.txt").write_text("work\n", encoding="utf-8")
    mark_old(path)


def mark_old(path: Path) -> None:
    if path.is_symlink():
        os.utime(path, (OLD_TIMESTAMP, OLD_TIMESTAMP), follow_symlinks=False)
        return
    if path.is_dir():
        for child in path.rglob("*"):
            os.utime(child, (OLD_TIMESTAMP, OLD_TIMESTAMP), follow_symlinks=False)
    os.utime(path, (OLD_TIMESTAMP, OLD_TIMESTAMP), follow_symlinks=False)


def run_gc(
    estate: Path,
    current_run_dir: Path,
    out: Path,
    *,
    apply: bool = False,
    min_age_days: str = "7",
) -> subprocess.CompletedProcess[str]:
    args = [
        "python3",
        str(SUMMARY_SCRIPT),
        "gc",
        "--estate-root",
        str(estate),
        "--current-run-dir",
        str(current_run_dir),
        "--min-age-days",
        min_age_days,
        "--out",
        str(out),
    ]
    if apply:
        args.append("--apply")
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=False)


def write_release_result(run_dir: Path, profile: str, line: str, *, exit_code: int = 0) -> None:
    result_path = run_dir / "results" / f"release-{profile}-{line}.result.json"
    log_path = run_dir / "logs" / f"release-{profile}-{line}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")
    write_json(
        result_path,
        {
            "schema_version": 1,
            "mode": "nightly-release-gate",
            "line": line,
            "commit": "abcdef1234567890abcdef1234567890abcdef12",
            "exit_code": exit_code,
            "verdict": "passed" if exit_code == 0 else "failed",
            "duration_seconds": 1.0,
            "stages": [
                {
                    "name": f"release-{profile}-{line}",
                    "exit_code": exit_code,
                    "duration_seconds": 1.0,
                    "log_path": str(log_path),
                }
            ],
        },
    )


def run_summary(run_dir: Path, out: Path) -> subprocess.CompletedProcess[str]:
    host_facts = run_dir / "host-facts.json"
    disk_headroom = run_dir / "results" / "disk-headroom.json"
    write_json(host_facts, {"schema_version": 1})
    write_json(disk_headroom, {"schema_version": 1, "verdict": "passed"})
    return subprocess.run(
        [
            "python3",
            str(SUMMARY_SCRIPT),
            "summary",
            "--run-dir",
            str(run_dir),
            "--run-id",
            "20261003T010203Z-test",
            "--commit",
            "abcdef1234567890abcdef1234567890abcdef12",
            "--target",
            "HEAD",
            "--started-utc",
            "2026-10-03T01:01:00Z",
            "--finished-utc",
            "2026-10-03T01:02:03Z",
            "--host-facts",
            str(host_facts),
            "--disk-headroom",
            str(disk_headroom),
            "--out",
            str(out),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def run_nightly(
    tmp_path: Path,
    gate: Path,
    determinism: Path,
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    estate = tmp_path / "estate"
    event_log = tmp_path / "events.jsonl"
    nvidia_smi = fake_nvidia_smi(tmp_path, "always-free", log_events=False)
    env = {
        **os.environ,
        "VASO_ESTATE_ROOT": str(estate),
        "VASO_AGENT": "pb-nightly-test",
        "TMPDIR": str(estate / "agents" / "pb-nightly-test" / "tmp"),
        "VASO_GPU_COUNT": "8",
        "NIGHTLY_GATE_CMD": str(gate),
        "NIGHTLY_DETERMINISM_CMD": str(determinism),
        "NIGHTLY_EVENT_LOG": str(event_log),
        "NIGHTLY_LEASE_TIMEOUT": "5",
        "NIGHTLY_LEASE_TTL": "30",
        "NIGHTLY_GATE_TIMEOUT": "10s",
        "NIGHTLY_DETERMINISM_TIMEOUT": "10s",
        "NIGHTLY_RUN_ID": "20261003T010203Z-test",
        "NIGHTLY_FAKE_ESTATE_FREE_GIB": "900",
        "PATH": f"{nvidia_smi.parent}:{os.environ['PATH']}",
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_nightly_summary_fails_when_release_line_is_missing_for_profile(tmp_path: Path) -> None:
    run_dir = tmp_path / "estate" / "ci" / "nightly" / "20261003T010203Z-test"
    write_release_result(run_dir, "torch", "cu130")
    write_release_result(run_dir, "jax", "cu130")
    write_release_result(run_dir, "jax", "cu129")
    out = run_dir / "summary.json"

    result = run_summary(run_dir, out)

    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(out.read_text(encoding="utf-8"))
    assert summary["verdict"] == "failed"
    assert {
        "profile": "torch",
        "line": "cu129",
        "stage": "release_gate",
        "status": "missing",
    } in summary["release_matrix"]["required"]


def test_nightly_runs_release_gates_determinism_and_reports_gc_by_default(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    keep_from_gate = estate / "vaso" / "lines" / "cu130" / "work" / "torch" / "keep-gate"
    keep_from_release = estate / "vaso" / "lines" / "cu130" / "work" / "torch" / "keep-release"
    drop_work = estate / "vaso" / "lines" / "cu130" / "work" / "torch" / "drop-old"
    for path in (keep_from_gate, keep_from_release, drop_work):
        make_work_dir(path)
    release_ref = estate / "releases" / "torch" / "cu130" / "1" / "manifest.json"
    release_ref.parent.mkdir(parents=True, exist_ok=True)
    release_ref.write_text(json.dumps({"work": str(keep_from_release)}) + "\n", encoding="utf-8")

    write_summary(
        estate / "ci" / "nightly" / "20260101-old-green" / "summary.json",
        "20260101-old-green",
        "passed",
        "2026-01-01T00:00:00Z",
    )
    write_summary(
        estate / "ci" / "nightly" / "20260102-keep-green" / "summary.json",
        "20260102-keep-green",
        "passed",
        "2026-01-02T00:00:00Z",
    )
    write_summary(
        estate / "ci" / "nightly" / "20260103-keep-green" / "summary.json",
        "20260103-keep-green",
        "passed",
        "2026-01-03T00:00:00Z",
        refs=[keep_from_gate],
    )
    write_summary(
        estate / "ci" / "old-fast" / "summary.json",
        "old-fast",
        "passed",
        "2026-01-01T00:00:00Z",
    )
    gate = fake_gate(tmp_path, referenced_path=keep_from_gate)
    determinism = fake_determinism(tmp_path)

    result = run_nightly(tmp_path, gate, determinism)

    assert result.returncode == 0, result.stdout + result.stderr
    summary_path = estate / "ci" / "nightly" / "20261003T010203Z-test" / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["verdict"] == "passed"
    release_gates = [stage for stage in summary["stages"] if stage["kind"] == "release_gate"]
    assert [(stage["profile"], stage["line"], stage["release"]) for stage in release_gates] == [
        ("torch", "cu130", True),
        ("torch", "cu129", True),
        ("jax", "cu130", True),
        ("jax", "cu129", True),
    ]
    assert all("acceptance_path" in stage for stage in release_gates)
    assert all(Path(stage["acceptance_path"]).is_file() for stage in release_gates)
    determinism_stages = [stage for stage in summary["stages"] if stage["kind"] == "determinism"]
    assert [(stage["profile"], stage["line"]) for stage in determinism_stages] == [
        ("torch", "cu130"),
        ("torch", "cu129"),
        ("jax", "cu130"),
        ("jax", "cu129"),
    ]
    assert len({stage["name"] for stage in determinism_stages}) == 4
    assert len({stage["result_path"] for stage in determinism_stages}) == 4
    assert len({stage["work_dir"] for stage in determinism_stages}) == 4
    assert len({stage["output_base"] for stage in release_gates}) == 4
    assert all(str(estate / "ci" / "nightly" / "20261003T010203Z-test") in stage["output_base"] for stage in release_gates)
    assert {
        (record["name"], record["limit_seconds"], record["within_slo"])
        for record in summary["build_slos"]
    } == {
        ("torch", 2700, True),
        ("jaxlib", 7200, True),
    }

    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event"] for event in events[:8]] == [
        "gate_start",
        "gate_end",
        "gate_start",
        "gate_end",
        "gate_start",
        "gate_end",
        "gate_start",
        "gate_end",
    ]
    assert [event["name"] for event in events if event["event"] == "determinism"] == [
        "torch:cu130",
        "torch:cu129",
        "jax:cu130",
        "jax:cu129",
    ]
    assert (estate / "ci" / "nightly" / "20260101-old-green").exists()
    assert (estate / "ci" / "nightly" / "20260102-keep-green").exists()
    assert (estate / "ci" / "old-fast").exists()
    assert drop_work.exists()
    assert keep_from_gate.exists()
    assert keep_from_release.exists()
    assert summary["disk_headroom"]["verdict"] == "passed"
    assert summary["gc"]["apply"] is False
    assert summary["gc"]["removed_paths"] == []
    assert str(drop_work) in summary["gc"]["candidate_paths"]
    assert summary["gc"]["candidate_bytes"] > 0


def test_nightly_waits_for_external_gpu_memory_before_release_gate(tmp_path: Path) -> None:
    gate = fake_gate(tmp_path)
    determinism = fake_determinism(tmp_path)
    nvidia_smi = fake_nvidia_smi(tmp_path, "busy-then-free")

    result = run_nightly(
        tmp_path,
        gate,
        determinism,
        extra_env={
            "PATH": f"{nvidia_smi.parent}:{os.environ['PATH']}",
            "NIGHTLY_GPU_FOREIGN_MAX_MIB": "8192",
            "NIGHTLY_GPU_WAIT_TIMEOUT": "5",
            "NIGHTLY_GPU_WAIT_POLL_INTERVAL": "0",
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    run_dir = tmp_path / "estate" / "ci" / "nightly" / "20261003T010203Z-test"
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["verdict"] == "passed"
    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    first_gate_index = next(index for index, event in enumerate(events) if event["event"] == "gate_start")
    gpu_samples_before_gate = [
        event for event in events[:first_gate_index] if event["event"] == "nvidia_smi_gpu"
    ]
    assert [sample["max_used_mib"] for sample in gpu_samples_before_gate] == [9000, 700]
    assert all(sample["active_gpu_leases"] == 0 for sample in gpu_samples_before_gate)
    release_log = (run_dir / "logs" / "release-torch-cu130.log").read_text(encoding="utf-8")
    assert "nightly gpu wait: waiting" in release_log
    assert "nightly gpu wait: ready" in release_log


def test_nightly_records_external_gpu_memory_timeout_as_blocked(tmp_path: Path) -> None:
    gate = fake_gate(tmp_path)
    determinism = fake_determinism(tmp_path)
    nvidia_smi = fake_nvidia_smi(tmp_path, "always-busy")

    result = run_nightly(
        tmp_path,
        gate,
        determinism,
        extra_env={
            "PATH": f"{nvidia_smi.parent}:{os.environ['PATH']}",
            "NIGHTLY_GPU_FOREIGN_MAX_MIB": "8192",
            "NIGHTLY_GPU_WAIT_TIMEOUT": "0",
            "NIGHTLY_GPU_WAIT_POLL_INTERVAL": "0",
        },
    )

    assert result.returncode == 1, result.stdout + result.stderr
    run_dir = tmp_path / "estate" / "ci" / "nightly" / "20261003T010203Z-test"
    release_result = json.loads(
        (run_dir / "results" / "release-torch-cu130.result.json").read_text(encoding="utf-8")
    )
    assert release_result["exit_code"] == 75
    assert release_result["verdict"] == "blocked"
    assert "GPU memory" in release_result["blocked_reason"]
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["verdict"] == "failed"
    release_gates = [stage for stage in summary["stages"] if stage["kind"] == "release_gate"]
    assert len(release_gates) == 4
    assert {stage["verdict"] for stage in release_gates} == {"blocked"}
    assert all(stage["exit_code"] == 75 for stage in release_gates)
    assert all("GPU memory" in stage["blocked_reason"] for stage in release_gates)
    assert {item["status"] for item in summary["release_matrix"]["required"]} == {"blocked"}
    failed_stages = [stage for stage in summary["stages"] if stage["verdict"] == "failed"]
    assert failed_stages == []
    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event"] for event in events if event["event"] == "gate_start"] == []
    assert all(
        event["active_gpu_leases"] == 0
        for event in events
        if event["event"] == "nvidia_smi_gpu"
    )


def test_gc_apply_refuses_without_green_nightly_and_keeps_everything(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    stale_work = estate / "lines" / "cu130" / "work" / "torch" / "stale-digest"
    stale_ci = estate / "ci" / "nightly" / "20260101-red"
    make_work_dir(stale_work)
    write_summary(stale_ci / "summary.json", "20260101-red", "failed", "2026-01-01T00:00:00Z")
    mark_old(stale_ci)

    result = run_gc(estate, estate / "ci" / "nightly" / "current", tmp_path / "gc.json", apply=True)

    assert result.returncode == 1
    report = json.loads((tmp_path / "gc.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "refused"
    assert "no green nightly" in report["message"]
    assert stale_work.exists()
    assert stale_ci.exists()
    assert report["removed_paths"] == []


def test_gc_report_only_removes_nothing_but_reports_candidates(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    stale_work = estate / "lines" / "cu130" / "work" / "torch" / "stale-digest"
    stale_ci = estate / "ci" / "old-fast"
    make_work_dir(stale_work)
    write_summary(stale_ci / "summary.json", "old-fast", "passed", "2026-01-01T00:00:00Z")
    mark_old(stale_ci)
    write_summary(
        estate / "ci" / "nightly" / "20260102-green" / "summary.json",
        "20260102-green",
        "passed",
        "2026-01-02T00:00:00Z",
    )

    result = run_gc(estate, estate / "ci" / "nightly" / "current", tmp_path / "gc.json")

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "gc.json").read_text(encoding="utf-8"))
    assert report["apply"] is False
    assert report["removed_paths"] == []
    assert str(stale_work) in report["candidate_paths"]
    assert str(stale_ci) in report["candidate_paths"]
    assert report["candidate_bytes"] > 0
    assert stale_work.exists()
    assert stale_ci.exists()


def test_gc_apply_removes_only_old_unreferenced_dirs(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    old_drop = estate / "lines" / "cu130" / "work" / "torch" / "old-drop"
    old_keep_green = estate / "lines" / "cu130" / "work" / "torch" / "old-keep-green"
    old_keep_release = estate / "lines" / "cu130" / "work" / "torch" / "old-keep-release"
    recent_keep = estate / "lines" / "cu130" / "work" / "torch" / "recent-keep"
    result_keep = estate / "lines" / "cu130" / "work" / "torch" / "digest-from-result"
    active_keep = estate / "lines" / "cu130" / "work" / "torch" / "active-keep"
    old_ci_drop = estate / "ci" / "old-fast"
    for path in (old_drop, old_keep_green, old_keep_release, result_keep, active_keep):
        make_work_dir(path)
    recent_keep.mkdir(parents=True, exist_ok=True)
    (recent_keep / "payload.txt").write_text("recent\n", encoding="utf-8")
    write_summary(old_ci_drop / "summary.json", "old-fast", "passed", "2026-01-01T00:00:00Z")
    mark_old(old_ci_drop)
    write_summary(
        estate / "ci" / "nightly" / "20260102-green" / "summary.json",
        "20260102-green",
        "passed",
        "2026-01-02T00:00:00Z",
        refs=[old_keep_green],
    )
    release_ref = estate / "releases" / "torch" / "cu130" / "1" / "manifest.json"
    release_ref.parent.mkdir(parents=True, exist_ok=True)
    release_ref.write_text(json.dumps({"work": str(old_keep_release)}) + "\n", encoding="utf-8")
    recent_result = estate / "agents" / "worker" / "results" / "recent.json"
    recent_result.parent.mkdir(parents=True, exist_ok=True)
    recent_result.write_text(json.dumps({"input_digest": result_keep.name}) + "\n", encoding="utf-8")
    active_lease = estate / "agents" / "leases" / "gpu.json"
    active_lease.parent.mkdir(parents=True, exist_ok=True)
    active_lease.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "resource": "gpu",
                "leases": [
                    {
                        "id": "gpu-test",
                        "resource": "gpu",
                        "holder": "active-worker",
                        "pid": os.getpid(),
                        "host": "",
                        "expiry_time": 9_999_999_999,
                        "paths": [str(active_keep)],
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_gc(estate, estate / "ci" / "nightly" / "current", tmp_path / "gc.json", apply=True)

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "gc.json").read_text(encoding="utf-8"))
    assert report["apply"] is True
    assert report["verdict"] == "passed"
    assert set(report["removed_paths"]) == {str(old_drop), str(old_ci_drop)}
    assert report["reclaimed_bytes"] > 0
    assert not old_drop.exists()
    assert not old_ci_drop.exists()
    assert old_keep_green.exists()
    assert old_keep_release.exists()
    assert recent_keep.exists()
    assert result_keep.exists()
    assert active_keep.exists()


def test_gc_refuses_symlink_that_escapes_estate(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "payload.txt").write_text("outside\n", encoding="utf-8")
    escaping = estate / "lines" / "cu130" / "work" / "torch" / "aaa-escape"
    escaping.parent.mkdir(parents=True, exist_ok=True)
    escaping.symlink_to(outside, target_is_directory=True)
    mark_old(escaping)
    other_old = estate / "lines" / "cu130" / "work" / "torch" / "zzz-old"
    make_work_dir(other_old)
    write_summary(
        estate / "ci" / "nightly" / "20260102-green" / "summary.json",
        "20260102-green",
        "passed",
        "2026-01-02T00:00:00Z",
    )

    result = run_gc(estate, estate / "ci" / "nightly" / "current", tmp_path / "gc.json", apply=True)

    assert result.returncode == 1
    report = json.loads((tmp_path / "gc.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "refused"
    assert str(escaping) in {entry["path"] for entry in report["refused_paths"]}
    assert escaping.is_symlink()
    assert (outside / "payload.txt").exists()
    assert other_old.exists()


def test_nightly_appends_attention_line_on_red_release_gate(tmp_path: Path) -> None:
    gate = fake_gate(tmp_path, fail_name="jax:cu129")
    determinism = fake_determinism(tmp_path)

    result = run_nightly(tmp_path, gate, determinism)

    assert result.returncode == 1, result.stdout + result.stderr
    summary_path = tmp_path / "estate" / "ci" / "nightly" / "20261003T010203Z-test" / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["verdict"] == "failed"
    failed = [stage for stage in summary["stages"] if stage["verdict"] == "failed"]
    assert [(stage["kind"], stage["profile"], stage["line"], stage["exit_code"]) for stage in failed] == [
        ("release_gate", "jax", "cu129", 42)
    ]
    attention = (tmp_path / "estate" / "agents" / "claude" / "autoland.log").read_text(
        encoding="utf-8"
    )
    assert f" NIGHTLY_RED summary={summary_path}" in attention


def test_nightly_fails_below_disk_headroom_threshold(tmp_path: Path) -> None:
    gate = fake_gate(tmp_path)
    determinism = fake_determinism(tmp_path)

    result = run_nightly(
        tmp_path,
        gate,
        determinism,
        extra_env={"NIGHTLY_FAKE_ESTATE_FREE_GIB": "41.9"},
    )

    assert result.returncode == 1, result.stdout + result.stderr
    summary_path = tmp_path / "estate" / "ci" / "nightly" / "20261003T010203Z-test" / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["verdict"] == "failed"
    assert summary["disk_headroom"] == {
        "free_gib": 41.9,
        "required_free_gib": 42.0,
        "verdict": "failed",
    }
    assert not (tmp_path / "events.jsonl").exists()


def load_nightly_summary():
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "tools" / "ci" / "nightly_summary.py"
    spec = importlib.util.spec_from_file_location("nightly_summary_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_build_slo_checks_each_gate_not_the_sum() -> None:
    summary = load_nightly_summary()

    def gate(profile: str, seconds: float) -> dict:
        return {"kind": "release_gate", "profile": profile, "duration_seconds": seconds}

    rows = {row["name"]: row for row in summary.build_slos([gate("torch", 1962.4), gate("torch", 2090.0), gate("jax", 1419.0), gate("jax", 1403.5)])}
    assert rows["torch"] == {"name": "torch", "duration_seconds": 2090.0, "limit_seconds": 2700, "within_slo": True}
    assert rows["jaxlib"]["duration_seconds"] == 1419.0
    assert rows["jaxlib"]["within_slo"] is True

    slow = {row["name"]: row for row in summary.build_slos([gate("torch", 1000.0), gate("torch", 2700.5)])}
    assert slow["torch"]["within_slo"] is False
