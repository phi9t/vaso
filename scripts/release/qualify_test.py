"""Qualification must stop on missing ownership, contamination, or changed policy."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import qualify
import gpu_monitor as gpu
import qualification_policy as policy
from process_runner import run_command
import candidate
from candidate_test import selection


def idle():
    return {
        "devices": [
            {"name": "NVIDIA B200", "utilization": 0, "index": i, "memory_mib": 0}
            for i in range(8)
        ],
        "compute_pids": [],
    }


def reservation():
    now = datetime.now(timezone.utc)
    return {
        "exclusive": True,
        "gpu_count": 8,
        "reference": "test-reservation",
        "starts_at": (now - timedelta(minutes=1)).isoformat(),
        "ends_at": (now + timedelta(minutes=30)).isoformat(),
    }


def test_idle_devices_do_not_establish_reservation():
    assert gpu.resource_blockers(idle(), None)
    assert not gpu.resource_blockers(idle(), reservation())


def test_occupied_expired_and_wrong_hardware_block():
    snapshot = idle()
    snapshot["compute_pids"] = [123]
    assert gpu.resource_blockers(snapshot, reservation())
    snapshot = idle()
    snapshot["devices"][0]["name"] = "other"
    assert gpu.resource_blockers(snapshot, reservation())
    r = reservation()
    r["ends_at"] = r["starts_at"]
    assert gpu.resource_blockers(idle(), r)


def test_complete_plan_retains_diagnostics_consumers_and_coresidence(tmp_path):
    import hashlib

    doc, _ = selection(tmp_path)
    doc["qualification_policy"] = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "docs/releases/preview-candidate-1.json"
        ).read_text()
    )["qualification_policy"]
    doc["qualification_policy_sha256"] = hashlib.sha256(
        candidate.canonical(doc["qualification_policy"])
    ).hexdigest()
    plan = policy.make_plan(doc, Path(__file__).resolve().parents[2])
    assert len(plan) == 26
    assert len({s["name"] for s in plan}) == 26
    full = [s for s in plan if s["name"].startswith("round-")]
    assert sum(s["profile"] == "coresidence" for s in full) == 6
    assert sum(bool(s["consumer"]) for s in full) == 12
    assert all(s["reuse"] for s in full if s["name"].startswith(("round-2", "round-3")))
    assert plan[3]["cache_group"] == plan[0]["cache_group"]


def test_foreign_gpu_process_stops_owned_stage_only(tmp_path, monkeypatch):
    foreign = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        monkeypatch.setattr(
            gpu, "gpu_snapshot", lambda: {**idle(), "compute_pids": [foreign.pid]}
        )
        r = run_command(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            dict(os.environ),
            tmp_path / "stage.log",
            60,
            monitor=gpu.Monitor(
                tmp_path / "gpu-observations.jsonl", reservation(), None
            ),
        )
        assert r["status"] == "failed" and "contaminated" in r["error"]
        assert foreign.poll() is None
        assert (tmp_path / "stage.log").exists()
    finally:
        foreign.terminate()
        foreign.wait()


def test_cache_inventory_excludes_venv_and_retains_content_identity(tmp_path):
    cache = tmp_path / "workloads/cache/kernel"
    cache.parent.mkdir(parents=True)
    cache.write_text("one")
    venv = tmp_path / "venv/__pycache__/test.pyc"
    venv.parent.mkdir(parents=True)
    venv.write_text("ignored")
    before = qualify.cache_inventory(tmp_path)
    cache.write_text("two")
    assert len(before) == 1 and before != qualify.cache_inventory(tmp_path)


def test_single_gpu_ignores_other_devices_but_rejects_selected_occupancy():
    snapshot = {
        **idle(),
        "processes": [{"gpu_index": 0, "pid": 123}],
        "compute_pids": [123],
    }
    snapshot["devices"][0]["memory_mib"] = 100
    assert not gpu.resource_blockers(snapshot, None, 7)
    snapshot["processes"].append({"gpu_index": 7, "pid": 456})
    assert gpu.resource_blockers(snapshot, None, 7)
    assert gpu.resource_blockers(snapshot, None, 9)


def test_single_gpu_plan_explicitly_omits_multi_gpu_cases(tmp_path):
    import hashlib

    doc, _ = selection(tmp_path)
    doc["qualification_policy"] = json.loads(
        Path(__file__).with_name("preview-policy.json").read_text()
    )
    doc["qualification_policy_sha256"] = hashlib.sha256(
        candidate.canonical(doc["qualification_policy"])
    ).hexdigest()
    plan = policy.make_plan(doc, Path(__file__).resolve().parents[2], 7)
    assert len(plan) == 18
    assert all(stage["gpu_count"] == 1 for stage in plan)
    torch = next(stage for stage in plan if stage["profile"] == "torch")
    jax = next(stage for stage in plan if stage["profile"] == "jax")
    assert set(torch["omitted_workloads"]) == {"W1c", "W1d", "W6c"}
    assert set(jax["omitted_workloads"]) == {"W3b", "W5a", "W5b", "W5f", "W5g"}
    assert torch["omitted_gates"] == ["nccl_distributed"]
    assert "gloo_distributed" in torch["gates"]
    command = policy.stage_command(torch, Path("/checkout"), Path("/release"))
    assert command[command.index("--gpus") + 1] == "1"
    assert "W1c" not in command[-1]


def test_composition_selects_self_contained_numpy_and_rejects_other_conflicts(tmp_path):
    import hashlib

    estate = tmp_path / "estate"
    doc = {"components": []}
    for profile in ("torch", "jax"):
        root = estate / f"downloads/release-{profile}-cu129/wheelhouse"
        root.mkdir(parents=True)
        numpy = root / "numpy-2.4.6-cp313-cp313-linux_x86_64.whl"
        numpy.write_bytes(profile.encode())
        doc["components"].append(
            {
                "kind": "release",
                "line": "cu129",
                "profile": profile,
                "files": {
                    "wheelhouse/" + numpy.name: hashlib.sha256(
                        numpy.read_bytes()
                    ).hexdigest()
                },
            }
        )
    composed = qualify.compose(doc, estate, tmp_path / "combined", "cu129")
    assert next((composed / "wheels").glob("numpy-*")).read_bytes() == b"jax"
    assert " --hash=sha256:" in (composed / "requirements.txt").read_text()
    for item in doc["components"]:
        root = qualify.candidate.component_dir(estate, item)
        wheel = root / "wheelhouse/other-1-py3-none-any.whl"
        wheel.write_bytes(item["profile"].encode())
        item["files"]["wheelhouse/" + wheel.name] = hashlib.sha256(
            wheel.read_bytes()
        ).hexdigest()
    with pytest.raises(ValueError, match="conflict"):
        qualify.compose(doc, estate, tmp_path / "conflict", "cu129")


def test_single_gpu_policy_requires_explicit_device(tmp_path):
    import hashlib

    doc, _ = selection(tmp_path)
    doc["qualification_policy"] = json.loads(
        Path(__file__).with_name("preview-policy.json").read_text()
    )
    doc["qualification_policy_sha256"] = hashlib.sha256(
        candidate.canonical(doc["qualification_policy"])
    ).hexdigest()
    with pytest.raises(ValueError, match="explicit --gpu-index"):
        policy.make_plan(doc, Path(__file__).resolve().parents[2])


def reuse_fixture(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    previous = repo / "docs/releases/preview-candidate-1.json"
    doc = json.loads((repo / "docs/releases/preview-candidate-2.json").read_text())
    plan = policy.make_plan(doc, repo, 7)
    candidate.write_new(tmp_path / "plan.json", plan)
    candidate.write_new(tmp_path / "payload-verification.json", {"status": "passed"})
    summary = {
        "status": "passed-attempt",
        "candidate_sha256": candidate.sha256(previous),
        "gpu_index": 7,
        "scope": "single-gpu",
        "stages": [],
    }
    for stage in plan:
        directory = tmp_path / stage["name"]
        directory.mkdir()
        result = {"stage": stage, "status": "passed", "returncode": 0}
        summary["stages"].append(result)
        candidate.write_new(directory / "result.json", result)
        candidate.write_new(
            directory / "cache-before.json",
            {"cache/a": "same"} if stage["reuse"] else {},
        )
        candidate.write_new(directory / "cache-after.json", {"cache/a": "same"})
    candidate.write_new(tmp_path / "summary.json", summary)
    return doc, previous, repo


def test_policy_revision_reuses_identical_execution_without_rewriting_attempt(tmp_path):
    doc, previous, repo = reuse_fixture(tmp_path)
    before = candidate.sha256(tmp_path / "summary.json")
    report = qualify.verify_reuse(doc, previous, tmp_path, repo)
    assert report["status"] == "compatible-runtime-evidence"
    assert report["stages"] == 18 and report["promotion"] == "not evaluated"
    assert candidate.sha256(tmp_path / "summary.json") == before


def test_reuse_cli_preserves_executed_scope_without_a_new_device_selection(
    tmp_path, monkeypatch
):
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    doc, previous, repo = reuse_fixture(attempt)
    revised = tmp_path / "candidate.json"
    digest = candidate.write_new(revised, doc)
    monkeypatch.setattr(candidate, "verify_source", lambda doc, checkout: None)
    output = tmp_path / "receipt"
    assert (
        qualify.main(
            [
                "--candidate",
                str(revised),
                "--candidate-sha256",
                digest,
                "--checkout",
                str(repo),
                "--estate-root",
                str(tmp_path / "estate"),
                "--reuse-attempt",
                str(attempt),
                "--evidence-candidate",
                str(previous),
                "--out",
                str(output),
            ]
        )
        == 0
    )
    summary = json.loads((output / "summary.json").read_text())
    assert summary["scope"] == "single-gpu" and summary["gpu_index"] == 7
    assert summary["promotion"] == "not evaluated"


@pytest.mark.parametrize("problem", ["artifact", "failed", "missing", "cache", "stage"])
def test_reuse_rejects_changed_or_incomplete_evidence(tmp_path, problem):
    doc, previous, repo = reuse_fixture(tmp_path)
    summary_path = tmp_path / "summary.json"
    summary = json.loads(summary_path.read_text())
    if problem == "artifact":
        doc["components"][0]["reference"] = "ghcr.io/example/changed@sha256:" + "f" * 64
    elif problem == "failed":
        summary["status"] = "failed"
    elif problem == "missing":
        summary["stages"].pop()
    elif problem == "cache":
        (tmp_path / "round-2-cu129-torch/cache-before.json").write_text("{}")
    else:
        (tmp_path / "round-1-cu129-torch/result.json").write_text("{}")
    summary_path.write_text(json.dumps(summary))
    with pytest.raises(ValueError):
        qualify.verify_reuse(doc, previous, tmp_path, repo)


def test_exited_owned_gpu_pid_is_not_foreign_and_reused_pid_is_not_owned(monkeypatch):
    known = {}
    monkeypatch.setattr(gpu, "process_start", lambda pid: 100)
    monkeypatch.setattr(gpu, "descendant", lambda pid, ancestor: True)
    assert gpu.attribute_gpu_processes([123], 1, known) == {123: "owned"}
    monkeypatch.setattr(gpu, "process_start", lambda pid: None)
    monkeypatch.setattr(gpu, "descendant", lambda pid, ancestor: False)
    assert gpu.attribute_gpu_processes([123, 456], 1, known) == {
        123: "exited-owned",
        456: "unattributed-exited",
    }
    monkeypatch.setattr(gpu, "process_start", lambda pid: 200)
    assert gpu.attribute_gpu_processes([123], 1, known) == {123: "foreign"}
