"""Behavior checks for immutable candidate selection and restored payload proof."""

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import candidate


def replace_after_first_read(monkeypatch, target, replacement_bytes):
    open_file = Path.open
    state = {"replaced": False}

    class ReplaceAfterRead:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self.stream

        def __exit__(self, *error):
            self.stream.close()
            if not error[0]:
                state["replaced"] = True
                with open_file(target, "wb") as stream:
                    stream.write(replacement_bytes)

    def changing_file(path, *args, **kwargs):
        stream = open_file(path, *args, **kwargs)
        return (
            ReplaceAfterRead(stream)
            if path == target and not state["replaced"]
            else stream
        )

    monkeypatch.setattr(Path, "open", changing_file)
    return state


def test_freeze_records_the_inventory_bytes_it_actually_selected(tmp_path, monkeypatch):
    import hashlib

    doc, _ = selection(tmp_path)
    inventory = tmp_path / "inventory.json"
    original = json.dumps({"artifacts": doc["components"]}).encode()
    inventory.write_bytes(original)
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(doc["qualification_policy"]))
    state = replace_after_first_read(monkeypatch, inventory, b'{"artifacts":[]}')
    frozen = candidate.freeze(candidate.REPO, "HEAD", inventory, policy, "preview.1")
    assert state["replaced"]
    assert frozen["components"] == doc["components"]
    assert {
        "tools/release/determinism_allowlist.json",
        "tools/release/determinism_compare.py",
        "tools/release/qualification_recipe.json",
    } <= frozen["selection_sha256"].keys()
    assert "scripts/release/determinism_allowlist.json" not in frozen["selection_sha256"]
    assert frozen["inventory_sha256"] == hashlib.sha256(original).hexdigest()


def test_verification_uses_the_candidate_bytes_that_matched_the_pin(
    tmp_path, monkeypatch
):
    doc, estate = selection(tmp_path)
    frozen = tmp_path / "candidate.json"
    frozen.write_text(json.dumps(doc))
    digest = candidate.sha256(frozen)
    replacement = {**doc, "name": "replacement-candidate"}
    replacement_bytes = json.dumps(replacement).encode()
    state = replace_after_first_read(monkeypatch, frozen, replacement_bytes)
    observed = []
    monkeypatch.setattr(
        candidate, "verify_source", lambda value, _: observed.append(value["name"])
    )
    assert (
        candidate.main(
            [
                "verify",
                "--candidate",
                str(frozen),
                "--candidate-sha256",
                digest,
                "--checkout",
                str(tmp_path),
                "--estate-root",
                str(estate),
                "--out",
                str(tmp_path / "verification.json"),
            ]
        )
        == 0
    )
    assert state["replaced"]
    assert frozen.read_bytes() == replacement_bytes
    assert observed == [doc["name"]]


@pytest.mark.parametrize(
    "path, value, diagnostic",
    [
        ((), None, "expected a candidate object"),
        ((), [], "expected a candidate object"),
        (("schema_version",), True, "unsupported candidate schema"),
        (("name",), [], "invalid candidate name"),
        (("source",), None, "source"),
        (("source", "commit"), [], "source commit"),
        (("components",), None, "components"),
        (("components", 0), None, "component 0"),
        (("components", 0, "kind"), [], "component 0"),
        (("components", 0, "profile"), [], "component 0"),
        (("components", 0, "reference"), None, "immutable GHCR digest"),
        (("components", 0, "files"), [], "file hashes"),
        (("components", 0, "files", "rootfs-bundle.json"), None, "file hash"),
        (("components", 0, "archive"), None, "artifact member"),
        (("components", 0, "archive_sha256"), 7, "archive hash"),
    ],
)
def test_invalid_candidate_shapes_have_cli_diagnostics_and_no_verification_output(
    tmp_path, capsys, path, value, diagnostic
):
    doc, estate = selection(tmp_path)
    if path:
        parent = doc
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = value
    else:
        doc = value
    source = tmp_path / "candidate.json"
    source.write_text(json.dumps(doc))
    original = source.read_bytes()
    output = tmp_path / "verification.json"
    assert (
        candidate.main(
            [
                "verify",
                "--candidate",
                str(source),
                "--candidate-sha256",
                candidate.sha256(source),
                "--checkout",
                str(tmp_path / "absent-checkout"),
                "--estate-root",
                str(estate),
                "--out",
                str(output),
            ]
        )
        == 2
    )
    assert diagnostic in capsys.readouterr().err
    assert source.read_bytes() == original
    assert not output.exists()


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


@pytest.mark.parametrize("value", [None, []])
def test_freeze_rejects_nonobject_policy_without_publishing_a_candidate(
    tmp_path, capsys, value
):
    doc, _ = selection(tmp_path)
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({"artifacts": doc["components"]}))
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(value))
    output = tmp_path / "candidate.json"
    assert (
        candidate.main(
            [
                "freeze",
                "--source",
                "HEAD",
                "--name",
                "preview.1",
                "--inventory",
                str(inventory),
                "--policy",
                str(policy),
                "--out",
                str(output),
            ]
        )
        == 2
    )
    assert "qualification policy: expected an object" in capsys.readouterr().err
    assert not output.exists()


def test_candidate_cannot_be_replaced(tmp_path):
    path = tmp_path / "candidate.json"
    candidate.write_new(path, {"a": 1})
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        candidate.write_new(path, {"a": 2})
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "tag", "traversal", "directory-member", "nul-member", "policy"]
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
    elif change == "directory-member":
        doc["components"][0]["files"]["."] = "a" * 64
    elif change == "nul-member":
        doc["components"][0]["files"]["bad\x00name"] = "a" * 64
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


@pytest.mark.parametrize("problem", ["missing-metadata", "conflicting-archive"])
def test_rootfs_identity_must_be_complete_and_consistent(tmp_path, capsys, problem):
    doc, _ = selection(tmp_path)
    rootfs = doc["components"][0]
    if problem == "missing-metadata":
        rootfs["files"] = {rootfs["archive"]: rootfs["archive_sha256"]}
    else:
        rootfs["files"][rootfs["archive"]] = "f" * 64
    with pytest.raises(ValueError, match="rootfs-bundle.json|conflicting.*archive"):
        candidate.validate(doc)
    path = tmp_path / "invalid-candidate.json"
    digest = candidate.write_new(path, doc)
    output = tmp_path / "verification.json"
    estate = tmp_path / "fresh"
    assert (
        candidate.main(
            [
                "verify",
                "--candidate",
                str(path),
                "--candidate-sha256",
                digest,
                "--checkout",
                str(tmp_path / "missing-checkout"),
                "--estate-root",
                str(estate),
                "--out",
                str(output),
            ]
        )
        == 2
    )
    error = capsys.readouterr().err
    assert "rootfs-bundle.json" in error or "conflicting rootfs archive" in error
    assert not output.exists() and not estate.exists()


def test_matching_archive_pin_and_null_rootfs_profile_preserve_component_identity(
    tmp_path,
):
    doc, estate = selection(tmp_path)
    for item in doc["components"]:
        if item["kind"] == "rootfs":
            item["profile"] = None
            item["files"][item["archive"]] = item["archive_sha256"]
            assert candidate.component_dir(estate, item) == estate / "downloads" / (
                "rootfs-" + item["line"]
            )
    candidate.validate(doc)
    assert candidate.verify_payloads(doc, estate)["status"] == "passed"
