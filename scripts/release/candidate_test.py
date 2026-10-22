"""Behavior checks for immutable candidate selection and restored payload proof."""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import candidate


def selection(tmp_path):
    components = []
    estate = tmp_path / "estate"
    for line in ("cu129", "cu130"):
        for profile in (None, "torch", "jax"):
            item = {
                "kind": "rootfs" if profile is None else "release",
                "line": line,
                "reference": "ghcr.io/example/test@sha256:" + "a" * 64,
                "files": {},
            }
            if profile:
                item["profile"] = profile
            directory = candidate.component_dir(estate, item)
            directory.mkdir(parents=True)
            name = "rootfs-bundle.json" if profile is None else "wheel.whl"
            (directory / name).write_text("{}")
            item["files"][name] = candidate.sha256(directory / name)
            if profile is None:
                item.update(
                    archive="rootfs.tar.zst", archive_sha256=item["files"][name]
                )
                (directory / "rootfs.tar.zst").write_text("{}")
                materialized = estate / "rootfs-lines" / line
                materialized.mkdir(parents=True)
                (materialized / name).write_text("{}")
            components.append(item)
    policy = {"rounds": 3}
    import hashlib

    doc = {
        "schema_version": 1,
        "kind": "vaso-release-candidate",
        "name": "preview.1",
        "source": {"commit": "a" * 40, "tree": "b" * 40},
        "components": components,
        "qualification_policy": policy,
        "qualification_policy_sha256": hashlib.sha256(
            candidate.canonical(policy)
        ).hexdigest(),
    }
    return doc, estate


def test_candidate_cannot_be_replaced(tmp_path):
    path = tmp_path / "candidate.json"
    candidate.write_new(path, {"a": 1})
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        candidate.write_new(path, {"a": 2})
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "tag", "traversal", "policy"]
)
def test_rejects_incomplete_or_mutable_selection(tmp_path, change):
    doc, _ = selection(tmp_path)
    if change == "missing":
        doc["components"].pop()
    elif change == "duplicate":
        doc["components"].append(copy.deepcopy(doc["components"][0]))
    elif change == "tag":
        doc["components"][0]["reference"] = "ghcr.io/example/test:latest"
    elif change == "traversal":
        doc["components"][0]["files"]["../secret"] = "a" * 64
    else:
        doc["qualification_policy"]["rounds"] = 1
    with pytest.raises(ValueError):
        candidate.validate(doc)


def test_corrupt_payload_fails_without_modifying_input(tmp_path):
    doc, estate = selection(tmp_path)
    assert candidate.verify_payloads(doc, estate)["status"] == "passed"
    wheel = estate / "downloads/release-torch-cu129/wheel.whl"
    wheel.write_text("corrupt")
    result = candidate.verify_payloads(doc, estate)
    assert result["status"] == "failed"
    assert result["errors"][0]["file"] == "wheel.whl"
    assert wheel.read_text() == "corrupt"


def test_reassembled_archive_replaces_missing_transport_parts_only(tmp_path):
    doc, estate = selection(tmp_path)
    item = doc["components"][0]
    item["files"]["rootfs/rootfs.tar.zst.part0000"] = "c" * 64
    assert candidate.verify_payloads(doc, estate)["status"] == "passed"
    (candidate.component_dir(estate, item) / item["archive"]).write_text("wrong")
    assert candidate.verify_payloads(doc, estate)["status"] == "failed"


def test_symlink_cannot_substitute_external_artifact(tmp_path):
    doc, estate = selection(tmp_path)
    wheel = estate / "downloads/release-torch-cu129/wheel.whl"
    wheel.unlink()
    external = tmp_path / "external"
    external.write_text("{}")
    wheel.symlink_to(external)
    assert candidate.verify_payloads(doc, estate)["status"] == "failed"
