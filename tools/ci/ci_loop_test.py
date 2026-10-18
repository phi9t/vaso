"""Behavioral tests for tools/ci/ci-loop.sh."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "ci" / "ci-loop.sh"


def write_executable(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def fake_verify(tmp_path: Path) -> Path:
    return write_executable(
        tmp_path / "fake-verify.sh",
        """#!/usr/bin/env bash
set -euo pipefail
python3 - "$CI_LOOP_EVENT_LOG" "${VASO_BAZEL_OB:-}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
with path.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({"event": "verify", "output_base": sys.argv[2]}, sort_keys=True) + "\\n")
PY
""",
    )


def fake_gate(tmp_path: Path, *, fail_name: str | None = None) -> Path:
    return write_executable(
        tmp_path / "fake-gate.sh",
        """#!/usr/bin/env bash
set -euo pipefail
profile=""
line=""
commit=""
acceptance_out=""
fail_name="__FAIL_NAME__"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --profile) profile="$2"; shift 2 ;;
    --line) line="$2"; shift 2 ;;
    --commit) commit="$2"; shift 2 ;;
    --acceptance-out) acceptance_out="$2"; shift 2 ;;
    *) shift ;;
  esac
done
name="$profile:$line"
python3 - "$CI_LOOP_EVENT_LOG" gate_start "$name" "${CUDA_VISIBLE_DEVICES:-}" "${VASO_BAZEL_OB:-}" <<'PY'
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
sleep "${CI_LOOP_FAKE_GATE_SLEEP:-0.25}"
mkdir -p "$(dirname "$acceptance_out")"
rc=0
verdict=passed
if [ "$name" = "$fail_name" ]; then
  rc=42
  verdict=failed
fi
python3 - "$acceptance_out" "$profile" "$line" "$commit" "$verdict" <<'PY'
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
doc = {
    "schema_version": 1,
    "profile": sys.argv[2],
    "line": sys.argv[3],
    "commit": sys.argv[4],
    "verdict": sys.argv[5],
    "sub_results": [
        {
            "name": "fake",
            "stage": "fake",
            "returncode": 0 if sys.argv[5] == "passed" else 42,
            "verdict": sys.argv[5],
        }
    ],
}
out.write_text(json.dumps(doc, sort_keys=True) + "\\n", encoding="utf-8")
PY
python3 - "$CI_LOOP_EVENT_LOG" gate_end "$name" "${CUDA_VISIBLE_DEVICES:-}" "${VASO_BAZEL_OB:-}" <<'PY'
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
""".replace("__FAIL_NAME__", fail_name or ""),
    )


def fake_gate_with_real_run_preamble(tmp_path: Path) -> Path:
    bazel = write_executable(
        tmp_path / "fake-bazel.sh",
        """#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then
  echo "bazel 7.7.0"
fi
exit 0
""",
    )
    return write_executable(
        tmp_path / "fake-gate-real-preamble.sh",
        f"""#!/usr/bin/env bash
set -euo pipefail
profile=""
line=""
commit=""
acceptance_out=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --profile) profile="$2"; shift 2 ;;
    --line) line="$2"; shift 2 ;;
    --commit) commit="$2"; shift 2 ;;
    --acceptance-out) acceptance_out="$2"; shift 2 ;;
    *) shift ;;
  esac
done
name="$profile:$line"
python3 - "$CI_LOOP_EVENT_LOG" gate_env "$name" "${{VASO_LEASES_HELD:-}}" "${{CUDA_VISIBLE_DEVICES:-}}" "${{VASO_GPU_SET:-}}" "${{VASO_GPUS:-}}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
with path.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({{
        "event": sys.argv[2],
        "name": sys.argv[3],
        "leases": sys.argv[4],
        "cuda_visible_devices": sys.argv[5],
        "vaso_gpu_set": sys.argv[6],
        "vaso_gpus": sys.argv[7],
    }}, sort_keys=True) + "\\n")
PY
VASO_CUDA_LINE="$line" \\
VASO_PROFILE="$profile" \\
VASO_REQUIRED_GIB=0 \\
BAZEL_BIN={str(bazel)!r} \\
VASO_LEASE_TIMEOUT="${{CI_LOOP_REAL_RUN_LEASE_TIMEOUT:-0.2}}" \\
  bash -c 'cd "$1" && exec ./run.sh --stub-stage-for-test ok' bash {str(ROOT / "foundry")!r}
mkdir -p "$(dirname "$acceptance_out")"
python3 - "$acceptance_out" "$profile" "$line" "$commit" <<'PY'
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
doc = {{
    "schema_version": 1,
    "profile": sys.argv[2],
    "line": sys.argv[3],
    "commit": sys.argv[4],
    "verdict": "passed",
    "sub_results": [
        {{
            "name": "real-run-preamble",
            "stage": "real-run-preamble",
            "returncode": 0,
            "verdict": "passed",
        }}
    ],
}}
out.write_text(json.dumps(doc, sort_keys=True) + "\\n", encoding="utf-8")
PY
""",
    )


def run_loop(
    tmp_path: Path,
    verify: Path,
    gate: Path,
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    estate = tmp_path / "estate"
    event_log = tmp_path / "events.jsonl"
    env = {
        **os.environ,
        "VASO_ESTATE_ROOT": str(estate),
        "VASO_AGENT": "pb-ci-test",
        "TMPDIR": str(estate / "agents" / "pb-ci-test" / "tmp"),
        "VASO_BAZEL_OB": str(estate / "agents" / "pb-ci-test" / "bazel-ob"),
        "VASO_GPU_COUNT": "4",
        "CI_LOOP_GPUS": "1",
        "CI_LOOP_EVENT_LOG": str(event_log),
        "CI_LOOP_FAKE_GATE_SLEEP": "0.35",
        "CI_LOOP_VERIFY_CMD": str(verify),
        "CI_LOOP_GATE_CMD": str(gate),
        "CI_LOOP_LEASE_TIMEOUT": "5",
        "CI_LOOP_LEASE_TTL": "30",
        "CI_LOOP_GATE_TIMEOUT": "10s",
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


def test_ci_loop_runs_changed_tip_gates_in_parallel_with_disjoint_leases_and_skips_same_tip(tmp_path: Path) -> None:
    verify = fake_verify(tmp_path)
    gate = fake_gate(tmp_path)

    first = run_loop(tmp_path, verify, gate)

    assert first.returncode == 0, first.stdout + first.stderr
    summaries = sorted((tmp_path / "estate" / "ci").glob("*/summary.json"))
    assert len(summaries) == 1
    summary = json.loads(summaries[0].read_text(encoding="utf-8"))
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert summary["schema_version"] == 1
    assert summary["commit"] == sha
    assert summary["verdict"] == "passed"
    assert summary["host_facts"]["nproc"] >= 1
    assert summary["host_facts"]["ram_kib"] > 0
    assert summary["host_facts"]["gpu_count"] == 4
    assert {(stage["profile"], stage["line"]) for stage in summary["stages"] if stage["kind"] == "gate"} == {
        ("torch", "cu129"),
        ("torch", "cu130"),
        ("jax", "cu129"),
        ("jax", "cu130"),
    }
    assert all(stage["duration_seconds"] >= 0 for stage in summary["stages"])

    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event"] for event in events].count("verify") == 1
    starts = [event for event in events if event["event"] == "gate_start"]
    assert len(starts) == 4
    assert len({event["output_base"] for event in starts}) == 4

    active: dict[str, str] = {}
    max_active = 0
    for event in events:
        if event["event"] == "gate_start":
            assert event["gpu"]
            assert event["gpu"] not in active.values()
            active[event["name"]] = event["gpu"]
            max_active = max(max_active, len(active))
        elif event["event"] == "gate_end":
            active.pop(event["name"])
    assert max_active >= 2

    second = run_loop(tmp_path, verify, gate)

    assert second.returncode == 0, second.stdout + second.stderr
    assert "CI_LOOP_SKIPPED same tip" in second.stdout
    assert len(sorted((tmp_path / "estate" / "ci").glob("*/summary.json"))) == 1
    events_after_skip = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(events_after_skip) == len(events)


def test_ci_loop_exports_held_leases_to_real_run_preamble(tmp_path: Path) -> None:
    verify = fake_verify(tmp_path)
    gate = fake_gate_with_real_run_preamble(tmp_path)

    result = run_loop(
        tmp_path,
        verify,
        gate,
        extra_env={"VASO_GPU_COUNT": "1", "CI_LOOP_REAL_RUN_LEASE_TIMEOUT": "0.2", "CI_LOOP_LEASE_TIMEOUT": "30"},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    gate_envs = [event for event in events if event["event"] == "gate_env"]
    assert len(gate_envs) == 4
    for event in gate_envs:
        profile, line = event["name"].split(":")
        assert profile in {"torch", "jax"}
        assert line in {"cu129", "cu130"}
        assert event["leases"].count(f"insula:{line}:") == 1
        assert event["leases"].count("gpu:") == 1
        assert event["cuda_visible_devices"] == event["vaso_gpu_set"]
        assert event["vaso_gpu_set"] == "0"
        assert event["vaso_gpus"] == "1"


def test_ci_loop_appends_attention_line_on_red_gate(tmp_path: Path) -> None:
    verify = fake_verify(tmp_path)
    gate = fake_gate(tmp_path, fail_name="jax:cu130")

    result = run_loop(tmp_path, verify, gate)

    assert result.returncode == 1, result.stdout + result.stderr
    summary_path = next((tmp_path / "estate" / "ci").glob("*/summary.json"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["verdict"] == "failed"
    failed = [stage for stage in summary["stages"] if stage["verdict"] == "failed"]
    assert [(stage["profile"], stage["line"], stage["exit_code"]) for stage in failed] == [
        ("jax", "cu130", 42)
    ]
    attention = (tmp_path / "estate" / "agents" / "claude" / "autoland.log").read_text(
        encoding="utf-8"
    )
    assert f" CI_LOOP_RED summary={summary_path}" in attention


def test_ci_loop_leases_all_eight_gpus_by_default() -> None:
    # The gate's workload stage runs with --gpus 8.
    assert 'gpu_amount="${CI_LOOP_GPUS:-8}"' in (ROOT / "tools" / "ci" / "ci-loop.sh").read_text()
