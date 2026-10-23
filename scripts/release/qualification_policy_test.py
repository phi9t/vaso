"""Pinned qualification policy selects coverage and command recipes."""

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import candidate
from candidate_test import selection
import qualification_policy as policy


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
