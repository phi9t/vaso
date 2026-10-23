"""Restoration refuses old estates and retains failures without advancing."""

import json
from pathlib import Path
import shutil
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import candidate
from candidate_test import selection
import restore


def fixture(tmp_path):
    doc, payload = selection(tmp_path)
    for item in doc["components"]:
        package = "-".join(
            [
                "vaso",
                item["kind"],
                *([item["profile"]] if item.get("profile") else []),
                item["line"],
            ]
        )
        item["reference"] = "ghcr.io/example/" + package + "@sha256:" + "a" * 64
    return doc, payload


def test_restore_plan_pulls_exact_digests_and_materializes_both_lines(tmp_path):
    doc, _ = fixture(tmp_path)
    doc["components"][0]["profile"] = None
    plan = restore.restoration_plan(doc, Path("/checkout"), tmp_path / "fresh")
    pulls = [stage for stage in plan if stage["name"].startswith("pull-")]
    assert len(pulls) == 6
    first_pull = pulls[0]["command"]
    assert first_pull[first_pull.index("--target-dir") + 1] == str(
        tmp_path / "fresh/downloads/rootfs-cu129"
    )
    assert all(
        "--tag" not in stage["command"] and "--digest" in stage["command"]
        for stage in pulls
    )
    assert [stage["name"] for stage in plan[-2:]] == [
        "materialize-cu129",
        "materialize-cu130",
    ]
    doc["components"][0]["reference"] = "ghcr.io/example/unrelated@sha256:" + "a" * 64
    with pytest.raises(ValueError, match="component reference"):
        restore.restoration_plan(doc, Path("/checkout"), tmp_path / "fresh")


def test_restore_never_replaces_an_existing_estate(tmp_path):
    rootfs = tmp_path / "rootfs-lines/cu129/rootfs"
    rootfs.mkdir(parents=True)
    marker = rootfs / "verified"
    marker.write_text("keep")
    with pytest.raises(ValueError, match="fresh estate required"):
        restore.prerequisites(tmp_path, 1)
    assert marker.read_text() == "keep"


def test_restore_checks_credentials_and_space_before_acquisition(tmp_path, monkeypatch):
    monkeypatch.delenv("GHCR_TOKEN", raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: "/bin/" + name)
    with pytest.raises(ValueError, match="GHCR_TOKEN"):
        restore.prerequisites(tmp_path, 1)
    monkeypatch.setenv("GHCR_TOKEN", "fixture-token")
    with pytest.raises(ValueError, match="insufficient restore space"):
        restore.prerequisites(tmp_path, 10**30)


@pytest.mark.parametrize("problem", [None, "pull", "corrupt", "launch"])
def test_restore_entrypoint_records_complete_execution_or_stops_before_probes(
    tmp_path, monkeypatch, problem
):
    doc, payload = fixture(tmp_path)
    frozen = tmp_path / "candidate.json"
    digest = candidate.write_new(frozen, doc)
    estate = tmp_path / "fresh"
    output = tmp_path / "evidence"
    calls = []
    monkeypatch.setattr(candidate, "verify_source", lambda doc, checkout: None)
    monkeypatch.setattr(restore, "prerequisites", lambda estate, size: None)

    def execute(command, env, log, timeout):
        calls.append(command)
        log.write_text("fixture stage\n")
        if "pull" in command:
            if problem == "pull":
                return {"status": "failed", "returncode": 17}
            destination = Path(command[command.index("--target-dir") + 1])
            shutil.copytree(payload / "downloads" / destination.name, destination)
            if problem == "corrupt" and destination.name == "release-torch-cu129":
                (destination / "wheel.whl").write_text("corrupt")
        elif "unbundle" in command:
            line = command[command.index("--line") + 1]
            shutil.copytree(
                payload / "rootfs-lines" / line, estate / "rootfs-lines" / line
            )
        return {"status": "passed", "returncode": 0}

    if problem == "launch":
        monkeypatch.setattr(
            restore,
            "restoration_plan",
            lambda *_: [
                {"name": "missing-tool", "command": [str(tmp_path / "missing-tool")]}
            ],
        )
    else:
        monkeypatch.setattr(restore, "run_command", execute)
    rc = restore.main(
        [
            "--candidate",
            str(frozen),
            "--candidate-sha256",
            digest,
            "--checkout",
            str(tmp_path),
            "--estate-root",
            str(estate),
            "--out",
            str(output),
        ]
    )
    summary = json.loads((output / "summary.json").read_text())
    entered = sum("insula.sh" == Path(command[0]).name for command in calls)
    if problem:
        assert rc != 0 and summary["status"] == "failed" and entered == 0
        assert summary["stages"][-1]["status"] == "failed"
    else:
        assert rc == 0 and summary["status"] == "restored-and-entered" and entered == 2
        assert len(summary["stages"]) == 12
    if problem == "launch":
        failed = summary["stages"][-1]
        assert failed["returncode"] is None
        assert "missing-tool" in failed["error"]
        assert failed["error"] in (output / "missing-tool.log").read_text()
        assert len(summary["stages"]) == 1
    assert summary["runner_files_sha256"]["evidence.py"] == candidate.sha256(
        Path(restore.__file__).with_name("evidence.py")
    )
    assert summary["qualification"] == "not performed"
    assert (output / "state.json").is_file()
