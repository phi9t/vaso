"""standing-verify.sh routes host-ineligible tests by tag."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "agents" / "standing-verify.sh"


def test_standing_verify_excludes_insula_only_tests_by_tag() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "--test_tag_filters=-insula-only" in text
    assert "-//tools:pytorch_recipe_provenance_test" not in text


def write_executable(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def run_verify(tmp_path: Path, agent: str, bazel: Path, *, pytest_cmd: str = "true", cargo_cmd: str = "true"):
    estate = tmp_path / "estate"
    env = {
        **os.environ,
        "VASO_ESTATE_ROOT": str(estate),
        "TMPDIR_VERIFY": str(estate / "agents" / agent / "tmp"),
        "BAZEL_BIN": str(bazel),
        "STANDING_VERIFY_PYTEST_CMD": pytest_cmd,
        "STANDING_VERIFY_CARGO_CMD": cargo_cmd,
        "ATTENTION_LOG": str(estate / "agents" / "claude" / "autoland.log"),
    }
    return subprocess.run(
        ["bash", str(SCRIPT), "--agent", agent],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def red_entries(estate: Path) -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((estate / "agents" / "reds").glob("*.json"))
    ]


def test_standing_verify_records_bazel_red_and_escalates_second_agent(tmp_path: Path) -> None:
    bazel = write_executable(
        tmp_path / "bazel",
        "#!/usr/bin/env bash\n"
        "echo 'FAILED: //tools:io_hygiene_guard_live_test'\n"
        "exit 7\n",
    )

    first = run_verify(tmp_path, "worker-a", bazel)
    second = run_verify(tmp_path, "worker-b", bazel)

    assert first.returncode == 7
    assert second.returncode == 7
    entries = red_entries(tmp_path / "estate")
    assert len(entries) == 1
    assert entries[0]["target"] == "//tools:io_hygiene_guard_live_test"
    assert entries[0]["stage"] == "bazel"
    assert entries[0]["agents"] == ["worker-a", "worker-b"]
    attention = (tmp_path / "estate" / "agents" / "claude" / "autoland.log").read_text(encoding="utf-8")
    assert " SHARED_RED //tools:io_hygiene_guard_live_test agents=worker-a,worker-b age=" in attention


def test_standing_verify_escalates_red_older_than_one_hour(tmp_path: Path) -> None:
    bazel = write_executable(
        tmp_path / "bazel",
        "#!/usr/bin/env bash\n"
        "echo 'FAILED: //tools:io_hygiene_guard_live_test'\n"
        "exit 7\n",
    )
    assert run_verify(tmp_path, "worker-a", bazel).returncode == 7
    red = red_entries(tmp_path / "estate")[0]
    red["first_seen_epoch"] -= 3700
    red["first_seen"] = "2026-10-02T00:00:00Z"
    red_path = next((tmp_path / "estate" / "agents" / "reds").glob("*.json"))
    red_path.write_text(json.dumps(red), encoding="utf-8")

    assert run_verify(tmp_path, "worker-a", bazel).returncode == 7

    attention = (tmp_path / "estate" / "agents" / "claude" / "autoland.log").read_text(encoding="utf-8")
    assert " SHARED_RED //tools:io_hygiene_guard_live_test agents=worker-a age=" in attention


def test_standing_verify_clears_red_entry_when_stage_passes(tmp_path: Path) -> None:
    failing_bazel = write_executable(
        tmp_path / "bazel-fail",
        "#!/usr/bin/env bash\n"
        "echo 'FAILED: //tools:io_hygiene_guard_live_test'\n"
        "exit 7\n",
    )
    passing_bazel = write_executable(
        tmp_path / "bazel-pass",
        "#!/usr/bin/env bash\n"
        "echo 'Executed 1 out of 1 test: 1 test passes.'\n",
    )

    assert run_verify(tmp_path, "worker-a", failing_bazel).returncode == 7
    assert red_entries(tmp_path / "estate")

    passed = run_verify(tmp_path, "worker-a", passing_bazel)

    assert passed.returncode == 0, passed.stdout + passed.stderr
    assert not red_entries(tmp_path / "estate")
