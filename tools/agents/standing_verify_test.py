"""standing-verify.sh routes host-ineligible tests by tag."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "agents" / "standing-verify.sh"


def test_standing_verify_excludes_insula_only_tests_by_tag() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "--test_tag_filters=-insula-only" in text
    assert "-//tools:pytorch_recipe_provenance_test" not in text


def test_standing_verify_runs_m2b_native_toolchain_tests() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    required_labels = {
        "//native/common:native_action_toolchain_test",
        "//native/common:plan_executor_test",
        "//native/zlib_ng:action_prefix_test",
        "//native/pytorch:plan_test",
        "//native/torchvision:plan_test",
        "//native/torchvision:action_driver_test",
        "//native/torchaudio:plan_test",
        "//native/torchaudio:action_driver_test",
        "//native/triton:plan_test",
        "//native/triton:action_driver_test",
        "//native/triton:action_prefix_test",
        "//native/jaxlib:plan_test",
        "//native/jaxlib:action_driver_test",
    }
    pytorch_build = ROOT / "foundry" / "native" / "pytorch" / "BUILD.bazel"
    if 'name = "action_driver_test"' in pytorch_build.read_text(encoding="utf-8"):
        required_labels.add("//native/pytorch:action_driver_test")

    missing = sorted(label for label in required_labels if label not in text)

    assert missing == []


def test_standing_verify_runs_bazel_lab_fast_subset() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'BAZEL_BIN="$BAZEL" tools/bazel-lab/run_experiments.sh --subset fast' in text
    assert "--source tools/bazel-lab/run_experiments.sh" in text
    assert "--source tools/bazel-lab/tools/lab_runner.py" in text
    assert "verify-bazel-lab.log" in text
    assert "stage=bazel-lab" in text
    assert "tools/bazel-lab:fast" in text


def test_standing_verify_runs_layout_guard_before_bazel() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "python3 foundry/tools/layout_guard.py --repo-root" in text
    assert "verify-layout-guard.log" in text
    assert "stage=layout-guard" in text
    assert text.index("stage=layout-guard") < text.index("verify-bazel-lab.log")


def write_executable(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_default_standing_routes_to_insula_without_running_host_bazel(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "foundry/tools").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "foundry/tools/bcr_mirror.py").write_text("raise SystemExit(0)\n")
    args = tmp_path / "insula-args"
    write_executable(repo / "foundry/insula.sh", f'#!/bin/bash\nprintf "%s\\n" "$@" > {str(args)!r}\nexit 37\n')
    marker = tmp_path / "host-bazel-ran"
    host_bazel = write_executable(tmp_path / "bazel", f'#!/bin/bash\ntouch {str(marker)!r}\n')
    env = {**os.environ, "VASO_ESTATE_ROOT": str(tmp_path / "estate"),
           "VASO_CUDA_LINE": "cu129", "BAZEL_BIN": str(host_bazel)}
    env.pop("VASO_IN_INSULA", None)
    result = subprocess.run(["bash", str(SCRIPT), "--agent", "routing-fixture"],
                            cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode == 37, result.stdout + result.stderr
    assert not marker.exists()
    forwarded = args.read_text().splitlines()
    assert forwarded[:2] == ["--line", "cu129"]
    assert "routing-fixture" in forwarded
    assert "tools/agents/standing-verify.sh" in " ".join(forwarded)


def run_verify(tmp_path: Path, agent: str, bazel: Path, *, pytest_cmd: str = "true"):
    estate = tmp_path / "estate"
    env = {
        **os.environ,
        "VASO_ESTATE_ROOT": str(estate),
        "VASO_IN_INSULA": "1",  # Fixture runs fake tools, never a host Bazel.
        "TMPDIR_VERIFY": str(estate / "agents" / agent / "tmp"),
        "BAZEL_BIN": str(bazel),
        "STANDING_VERIFY_BAZEL_LAB_CMD": "true",
        "STANDING_VERIFY_PYTEST_CMD": pytest_cmd,
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


@pytest.mark.parametrize("action", ["clear-stage", "record"])
def test_registry_failure_is_reported_without_hiding_stage_status(tmp_path, monkeypatch, action):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable(
        bin_dir / "python3",
        '#!/bin/bash\n'
        'if [[ "${1:-}" == */red_registry.py && "$2" == "$FAIL_REGISTRY_ACTION" && "$7" == bazel ]]; then\n'
        '  echo "registry unavailable" >&2; exit 37\nfi\n'
        f'exec {str(sys.executable)!r} "$@"\n',
    )
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAIL_REGISTRY_ACTION", action)
    bazel = write_executable(
        tmp_path / "bazel",
        '#!/bin/bash\n'
        + ("echo 'FAILED: //tools:fixture'; exit 7\n" if action == "record"
           else "echo 'Executed fixture successfully.'; exit 0\n"),
    )
    result = run_verify(tmp_path, "registry-fixture", bazel)
    assert result.returncode == (7 if action == "record" else 37), result.stdout + result.stderr
    assert "STANDING_VERIFY_FAILED stage=red-registry" in result.stderr
    assert "related_stage=bazel" in result.stderr
    assert "action=" + action in result.stderr
    assert "registry unavailable" in result.stderr
    assert "STANDING_VERIFY_PASSED" not in result.stdout
    if action == "record":
        assert "STANDING_VERIFY_FAILED stage=bazel rc=7" in result.stdout
