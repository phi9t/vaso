"""Pinned qualification policy selects coverage and command recipes."""

import json
import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import candidate
from candidate_test import selection
import qualification_policy as policy


@pytest.mark.parametrize("version", [1, 2])
def test_supported_policy_validation_needs_no_checkout_or_device(version):
    repo = Path(__file__).resolve().parents[2]
    doc = json.loads(
        (repo / f"docs/releases/preview-candidate-{version}.json").read_text()
    )
    before = copy.deepcopy(doc["qualification_policy"])
    policy.validate_policy(doc["qualification_policy"])
    assert doc["qualification_policy"] == before


@pytest.mark.parametrize(
    "problem",
    ["policy", "hardware", "gpu-count", "matrix", "row", "line", "diagnostics"],
)
def test_malformed_policy_is_rejected_before_recipe_reads(tmp_path, problem):
    repo = Path(__file__).resolve().parents[2]
    doc = json.loads((repo / "docs/releases/preview-candidate-2.json").read_text())
    value = doc["qualification_policy"]
    if problem == "policy":
        doc["qualification_policy"] = None
    elif problem == "hardware":
        value["hardware"] = None
    elif problem == "gpu-count":
        value["hardware"]["gpu_count"] = True
    elif problem == "matrix":
        value["matrix"] = None
    elif problem == "row":
        value["matrix"][0] = None
    elif problem == "line":
        value["matrix"][0]["line"] = []
    else:
        value["jax_diagnostics"] = None
    import hashlib

    doc["qualification_policy_sha256"] = hashlib.sha256(
        candidate.canonical(doc["qualification_policy"])
    ).hexdigest()
    before = copy.deepcopy(doc)
    with pytest.raises(ValueError, match="policy"):
        policy.make_plan(doc, tmp_path / "absent-checkout", 0)
    assert doc == before


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


def test_pinned_source_without_maintained_recipe_fails_clearly(tmp_path):
    doc = json.loads((Path(__file__).resolve().parents[2] / 'docs/releases/preview-candidate-1.json').read_text())
    with pytest.raises(ValueError, match='maintained qualification recipe.*pinned source'):
        policy.make_plan(doc, tmp_path)


def test_maintainer_recipe_change_controls_plan_commands_and_consumed_identity(tmp_path):
    import hashlib
    import shutil

    repo = Path(__file__).resolve().parents[2]
    for name in (policy.RECIPE_PATH, policy.RESOURCE_PATH):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / name, target)
    doc = json.loads((repo / 'docs/releases/preview-candidate-1.json').read_text())
    recipe = tmp_path / policy.RECIPE_PATH
    content = json.loads(recipe.read_text())
    content['matrix'][0]['workloads'].remove('W1b')
    recipe.write_text(json.dumps(content))
    identities = {}
    plan = policy.make_plan(doc, tmp_path, 7, input_identity=identities)
    torch = next(s for s in plan if s['line'] == 'cu129' and s['profile'] == 'torch')
    assert 'W1b' not in torch['workloads']
    assert 'W1b' not in policy.stage_command(torch, Path('/checkout'), Path('/release'))[-1]
    assert identities[policy.RECIPE_PATH] == hashlib.sha256(recipe.read_bytes()).hexdigest()
    assert identities[policy.RECIPE_PATH] != candidate.sha256(repo / policy.RECIPE_PATH)


@pytest.mark.parametrize('problem', ['duplicate', 'unknown-workload', 'gate', 'consumer', 'resources'])
def test_malformed_maintained_recipe_fails_before_execution(tmp_path, problem):
    import shutil

    repo = Path(__file__).resolve().parents[2]
    for name in (policy.RECIPE_PATH, policy.RESOURCE_PATH):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / name, target)
    doc = json.loads((repo / 'docs/releases/preview-candidate-1.json').read_text())
    recipe = tmp_path / policy.RECIPE_PATH
    content = json.loads(recipe.read_text())
    if problem == 'duplicate':
        content['matrix'][-1] = content['matrix'][0]
    elif problem == 'unknown-workload':
        content['matrix'][0]['workloads'].append('W999')
    elif problem == 'gate':
        content['matrix'][0]['gates'] = ['not a gate']
    elif problem == 'consumer':
        content['matrix'][0]['consumer'] = 'arbitrary.module'
    else:
        (tmp_path / policy.RESOURCE_PATH).write_text('raise RuntimeError("must not execute")')
    recipe.write_text(json.dumps(content))
    with pytest.raises(ValueError, match='invalid maintained qualification recipe'):
        policy.make_plan(doc, tmp_path, 7)


def test_recipe_identity_records_consumed_bytes_even_if_file_is_replaced(tmp_path, monkeypatch):
    import hashlib
    import shutil
    from candidate_test import replace_after_first_read

    repo = Path(__file__).resolve().parents[2]
    for name in (policy.RECIPE_PATH, policy.RESOURCE_PATH):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / name, target)
    doc = json.loads((repo / 'docs/releases/preview-candidate-1.json').read_text())
    recipe = tmp_path / policy.RECIPE_PATH
    original = recipe.read_bytes()
    changed = replace_after_first_read(monkeypatch, recipe, b'{}')
    identities = {}
    assert len(policy.make_plan(doc, tmp_path, input_identity=identities)) == 26
    assert changed['replaced']
    assert identities[policy.RECIPE_PATH] == hashlib.sha256(original).hexdigest()
    assert recipe.read_bytes() == b'{}'
