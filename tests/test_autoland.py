"""End-to-end checks for scripts/agents/autoland.sh on throwaway repos."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

AGENTS = Path(__file__).resolve().parents[1] / "scripts" / "agents"
SCRIPTS = (
    Path("scripts/agents/autoland.sh"),
    Path("scripts/agents/autoland-env.sh"),
    Path("scripts/agents/autoland-status.sh"),
    Path("scripts/agents/relay.py"),
    Path("scripts/insula/lease.py"),
)


def sh(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture()
def effort(tmp_path: Path) -> Iterator[dict]:
    main = tmp_path / "main"
    (main / "scripts" / "agents").mkdir(parents=True)
    (main / "scripts" / "insula").mkdir(parents=True)
    sh(main, "init", "-q", "-b", "target")
    sh(main, "config", "user.email", "t@example.com")
    sh(main, "config", "user.name", "t")
    root = AGENTS.parents[1]
    for rel in SCRIPTS:
        shutil.copy2(root / rel, main / rel)
    (main / ".scratch" / "tracker").mkdir(parents=True)
    (main / ".scratch" / "tracker" / "lead.md").write_text("tracker\n")
    sh(main, "add", ".")
    sh(main, "commit", "-q", "-m", "base")
    sh(main, "branch", "landing")
    lead = tmp_path / "lead"
    sh(main, "worktree", "add", "-q", "-b", "lead-wip", str(lead))
    estate = tmp_path / "estate"
    (estate / "agents" / "trae" / "logs").mkdir(parents=True)
    follower = subprocess.Popen(["sleep", "120"])
    env = {**os.environ, "VASO_ESTATE_ROOT": str(estate), "TARGET": "target", "LANDING": "landing",
           "TRACKER": ".scratch/tracker", "FOLLOWER_PID": str(follower.pid), "INTERVAL": "0.2"}
    yield {"main": main, "lead": lead, "estate": estate, "follower": follower, "env": env}
    stop(follower)


def stop(follower: subprocess.Popen) -> None:
    # Reap it too: an unreaped child is a zombie, and kill -0 still succeeds on it.
    follower.kill()
    follower.wait()


def follower_commit(main: Path) -> str:
    (main / "work.txt").write_text(f"{time.monotonic()}\n")
    sh(main, "add", "work.txt")
    sh(main, "commit", "-q", "-m", "follower slice")
    return sh(main, "rev-parse", "HEAD")


def lead_backlog(effort: dict, count: int) -> None:
    for index in range(count):
        path = effort["lead"] / f"lead-{index}.txt"
        path.write_text(f"lead backlog {index}\n")
        sh(effort["lead"], "add", path.name)
        sh(effort["lead"], "commit", "-q", "-m", f"lead backlog {index}")
    sh(effort["lead"], "branch", "-f", "landing", "HEAD")


def lead_payload(effort: dict) -> None:
    path = effort["lead"] / "lead-payload.txt"
    path.write_text("land me\n", encoding="utf-8")
    sh(effort["lead"], "add", path.name)
    sh(effort["lead"], "commit", "-q", "-m", "lead payload")


def old_empty_target_commit(repo: Path, minutes_ago: int) -> None:
    stamp = int(time.time()) - minutes_ago * 60
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": f"@{stamp}",
        "GIT_COMMITTER_DATE": f"@{stamp}",
    }
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-q", "-m", "old target marker"],
        env=env,
        check=True,
    )


def dirty_tracked_file(repo: Path, relative: str, minutes_ago: int) -> None:
    path = repo / relative
    path.write_text(f"dirty at {time.monotonic()}\n")
    stamp = int(time.time()) - minutes_ago * 60
    os.utime(path, (stamp, stamp))


def run_loop(e: dict, **env: str) -> subprocess.Popen:
    return subprocess.Popen(["bash", str(e["lead"] / "scripts" / "agents" / "autoland.sh")],
                            env={**e["env"], **env}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def stub_gate(path: Path, verdict: str = "failed", failing_stage: str = "workloads", sleep_seconds: str = "0") -> None:
    path.parent.mkdir(parents=True)
    script = f"""#!/usr/bin/env bash
set -u
line=""
out=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --line) line="$2"; shift 2 ;;
    --acceptance-out) out="$2"; shift 2 ;;
    *) shift ;;
  esac
done
[ -n "$line" ] || exit 2
[ -n "$out" ] || exit 2
mkdir -p "$(dirname "$out")"
printf '%s|%s|%s\\n' "$VASO_AGENT" "$CUDA_VISIBLE_DEVICES" "$VASO_GPU_SET" > "$out.env"
sleep {sleep_seconds}
cat > "$out" <<'JSON'
{{"verdict":"{verdict}","sub_results":[{{"stage":"rootfs","verdict":"passed","returncode":0}},{{"stage":"{failing_stage}","verdict":"{verdict}","returncode":1}}]}}
JSON
[ "{verdict}" = "passed" ]
"""
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


def wait_for_line(log: Path, word: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while not (log.exists() and f" {word} " in log.read_text()):
        assert time.monotonic() < deadline, f"no {word} in {log}"
        time.sleep(0.1)


def test_green_move_lands_and_follower_exit_ends_the_loop(effort: dict) -> None:
    tip = follower_commit(effort["main"])
    loop = run_loop(effort, VERIFY="true")
    log = effort["estate"] / "agents" / "claude" / "autoland.log"
    wait_for_line(log, "LANDED")
    assert sh(effort["main"], "merge-base", "--is-ancestor", tip, "landing") == ""
    # LANDED is logged before the iteration writes its heartbeat; wait for it.
    state = effort["estate"] / "agents" / "claude" / "autoland.state"
    deadline = time.monotonic() + 30
    while not state.exists():
        assert time.monotonic() < deadline, "no heartbeat state written"
        time.sleep(0.05)
    assert state.read_text().startswith("pid=")
    stop(effort["follower"])
    out, _ = loop.communicate(timeout=30)
    assert "GONE" in out and loop.returncode == 0


def test_red_verify_exits_and_leaves_landing_alone(effort: dict) -> None:
    before = sh(effort["main"], "rev-parse", "landing")
    follower_commit(effort["main"])
    loop = run_loop(effort, VERIFY="false")
    out, _ = loop.communicate(timeout=60)
    assert "RED" in out and "LANDED" not in out
    assert sh(effort["main"], "rev-parse", "landing") == before


def test_status_view_is_read_only_and_reports_the_loop(effort: dict) -> None:
    follower_commit(effort["main"])
    loop = run_loop(effort, VERIFY="true")
    wait_for_line(effort["estate"] / "agents" / "claude" / "autoland.log", "LANDED")
    out = subprocess.run(["bash", str(effort["lead"] / "scripts" / "agents" / "autoland-status.sh")],
                         env=effort["env"], capture_output=True, text=True, check=True).stdout
    assert "loop:      RUNNING" in out and "follower:  alive" in out and "(landed)" in out
    stop(effort["follower"])
    loop.communicate(timeout=30)


def test_backlog_event_logs_once_and_status_reports_it(effort: dict) -> None:
    lead_backlog(effort, 2)
    loop = run_loop(effort, VERIFY="true", BACKLOG_COMMITS="2", BACKLOG_MINUTES="999999")
    log = effort["estate"] / "agents" / "claude" / "autoland.log"
    wait_for_line(log, "BACKLOG", timeout=5)
    time.sleep(0.7)
    text = log.read_text()
    assert text.count(" BACKLOG ") == 1
    assert "BACKLOG 2 commits, oldest " in text
    out = subprocess.run(["bash", str(effort["lead"] / "scripts" / "agents" / "autoland-status.sh")],
                         env={**effort["env"], "BACKLOG_COMMITS": "2", "BACKLOG_MINUTES": "999999"},
                         capture_output=True, text=True, check=True).stdout
    assert "backlog:   2 commits, oldest " in out
    stop(effort["follower"])
    loop.communicate(timeout=30)


def test_uncommitted_age_logs_once_and_status_reports_severity(effort: dict) -> None:
    old_empty_target_commit(effort["main"], minutes_ago=240)
    dirty_tracked_file(effort["main"], ".scratch/tracker/lead.md", minutes_ago=190)
    loop = run_loop(
        effort,
        VERIFY="true",
        UNCOMMITTED_MIN="60",
        UNCOMMITTED_RATE_LIMIT_MIN="30",
    )
    log = effort["estate"] / "agents" / "claude" / "autoland.log"
    wait_for_line(log, "UNCOMMITTED", timeout=5)
    time.sleep(0.7)
    text = log.read_text()
    assert text.count(" UNCOMMITTED ") == 1
    assert "UNCOMMITTED 1 files, oldest " in text
    out = subprocess.run(
        ["bash", str(effort["lead"] / "scripts" / "agents" / "autoland-status.sh")],
        env={**effort["env"], "UNCOMMITTED_MIN": "60"},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "uncommitted:   1 files, oldest " in out
    assert "(RED)" in out
    stop(effort["follower"])
    loop.communicate(timeout=30)


def test_default_verify_delegates_to_standing_verify() -> None:
    text = (AGENTS / "autoland.sh").read_text()
    verify = next(line for line in text.splitlines() if line.startswith("DEFAULT_VERIFY="))
    assert "scripts/agents/standing-verify.sh" in verify
    assert ' --agent "$AGENT"' in verify
    assert "bazel-real" not in verify
    assert "-//tools:pytorch_recipe_provenance_test" not in verify


def test_gate_runs_after_autoland_step_and_writes_summary(effort: dict) -> None:
    lead_payload(effort)
    follower_commit(effort["main"])
    gate = effort["estate"] / "agents" / "stub" / "triumvirate-gate.sh"
    stub_gate(gate, verdict="failed", failing_stage="workloads")
    loop = run_loop(
        effort,
        VERIFY="true",
        AUTOLAND_LAND="1",
        AUTOLAND_GATE="1",
        AUTOLAND_GATE_LINES="cu130",
        AUTOLAND_GATE_CMD=str(gate),
        AUTOLAND_GATE_GPUS="2",
        AUTOLAND_GATE_TIMEOUT="5s",
    )
    log = effort["estate"] / "agents" / "claude" / "autoland.log"
    wait_for_line(log, "GATE", timeout=10)
    stop(effort["follower"])
    loop.communicate(timeout=30)

    landed = sh(effort["main"], "rev-parse", "landing")
    short = landed[:12]
    summary = json.loads((effort["estate"] / "agents" / "autoland" / "gate" / f"{short}-cu130.json").read_text())
    assert summary["commit"] == landed
    assert summary["line"] == "cu130"
    assert summary["verdict"] == "failed"
    assert summary["first_failing_stage"] == "workloads"
    assert summary["stage_summary"] == {"passed": ["rootfs"], "failed": ["workloads"]}
    assert (effort["estate"] / "agents" / "autoland" / "gate" / f"{short}-cu130.json.env").read_text() == (
        "autoland|0,1|0,1\n"
    )
    text = log.read_text(encoding="utf-8")
    assert f" GATE cu130 failed workloads" in text
    assert " RED " not in text


def test_gate_regression_wakes_lead(effort: dict) -> None:
    gate_dir = effort["estate"] / "agents" / "autoland" / "gate"
    gate_dir.mkdir(parents=True)
    (gate_dir / "latest-cu130.json").write_text('{"verdict":"passed"}\n', encoding="utf-8")
    lead_payload(effort)
    follower_commit(effort["main"])
    gate = effort["estate"] / "agents" / "stub" / "triumvirate-gate.sh"
    stub_gate(gate, verdict="failed", failing_stage="abi_invariants")
    loop = run_loop(
        effort,
        VERIFY="true",
        AUTOLAND_LAND="1",
        AUTOLAND_GATE="1",
        AUTOLAND_GATE_LINES="cu130",
        AUTOLAND_GATE_CMD=str(gate),
        AUTOLAND_GATE_TIMEOUT="5s",
    )
    log = effort["estate"] / "agents" / "claude" / "autoland.log"
    wait_for_line(log, "REGRESSION", timeout=10)
    stop(effort["follower"])
    loop.communicate(timeout=30)
    assert " REGRESSION GATE cu130 failed abi_invariants" in log.read_text(encoding="utf-8")
