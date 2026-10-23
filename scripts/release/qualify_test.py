"""Qualification attempts retain cache, composition, and evidence-reuse contracts."""

from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import qualify
import qualification_policy as policy
import candidate


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
    assert summary["runner_files_sha256"]["evidence.py"] == candidate.sha256(
        Path(qualify.__file__).with_name("evidence.py")
    )
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
