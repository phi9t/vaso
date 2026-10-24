"""Evidence writers preserve canonical bytes and publish only complete records."""

import hashlib
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import evidence


def test_new_evidence_keeps_its_canonical_identity_and_cannot_be_replaced(tmp_path):
    path = tmp_path / "candidate.json"
    doc = {"z": [1, 2], "a": "value"}
    expected = b'{\n  "a": "value",\n  "z": [\n    1,\n    2\n  ]\n}\n'
    checksum = evidence.write_new(path, doc)
    assert path.read_bytes() == expected
    assert checksum == hashlib.sha256(expected).hexdigest()
    with pytest.raises(FileExistsError):
        evidence.write_new(path, {"a": "different"})
    assert path.read_bytes() == expected
    assert list(tmp_path.iterdir()) == [path]


def test_incomplete_new_evidence_never_becomes_visible_and_can_retry(
    tmp_path, monkeypatch
):
    path = tmp_path / "candidate.json"

    def fail_flush(_fd):
        raise OSError("evidence flush failed")

    with monkeypatch.context() as scoped:
        scoped.setattr(os, "fsync", fail_flush)
        with pytest.raises(OSError, match="evidence flush failed"):
            evidence.write_new(path, {"a": 1})
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []
    evidence.write_new(path, {"a": 1})
    assert path.read_bytes() == evidence.canonical({"a": 1})


def test_failed_state_replacement_preserves_previous_state_and_can_retry(
    tmp_path, monkeypatch
):
    path = tmp_path / "state.json"
    previous = {"status": "running"}
    evidence.replace_json(path, previous)

    def fail_replace(source, destination):
        assert Path(source).read_bytes() == evidence.canonical({"status": "passed"})
        assert Path(destination).read_bytes() == evidence.canonical(previous)
        raise OSError("replacement failed")

    with monkeypatch.context() as scoped:
        scoped.setattr(os, "replace", fail_replace)
        with pytest.raises(OSError, match="replacement failed"):
            evidence.replace_json(path, {"status": "passed"})
    assert path.read_bytes() == evidence.canonical(previous)
    assert list(tmp_path.iterdir()) == [path]
    evidence.replace_json(path, {"status": "passed"})
    assert path.read_bytes() == evidence.canonical({"status": "passed"})


def test_concurrent_new_writer_keeps_the_first_record(tmp_path, monkeypatch):
    path = tmp_path / "candidate.json"
    original_link = os.link

    def race(source, destination):
        Path(destination).write_bytes(b"first writer\n")
        original_link(source, destination)

    monkeypatch.setattr(os, "link", race)
    with pytest.raises(FileExistsError):
        evidence.write_new(path, {"status": "second writer"})
    assert path.read_bytes() == b"first writer\n"
    assert list(tmp_path.iterdir()) == [path]


def test_new_evidence_does_not_follow_an_existing_symlink(tmp_path):
    original = tmp_path / "original.json"
    original.write_text("previous evidence\n")
    path = tmp_path / "candidate.json"
    path.symlink_to(original)
    with pytest.raises(FileExistsError):
        evidence.write_new(path, {"a": 1})
    assert path.is_symlink()
    assert original.read_text() == "previous evidence\n"
