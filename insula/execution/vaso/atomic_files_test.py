"""Failed record publication preserves the destination and allows a clean retry."""

import pytest

from vaso import atomic_files


def test_parent_creation_remains_with_the_caller(tmp_path):
    parent = tmp_path / "missing"
    with pytest.raises(FileNotFoundError):
        atomic_files.atomic_text(parent / "result.json", "record\n")
    assert not parent.exists()


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", ["flush", "replace"])
def test_failed_record_write_preserves_destination_and_cleans_staging(
    tmp_path, monkeypatch, existing, failure
):
    path = tmp_path / "result.json"
    original = b'{"status":"previous"}\n'
    if existing:
        path.write_bytes(original)

    def fail(*args):
        raise OSError("record publication failed")

    with monkeypatch.context() as scoped:
        scoped.setattr(atomic_files.os, "fsync" if failure == "flush" else "replace", fail)
        with pytest.raises(OSError, match="record publication failed"):
            atomic_files.atomic_text(path, '{"status":"new"}\n')
    if existing:
        assert path.read_bytes() == original
    else:
        assert not path.exists()
    assert list(tmp_path.iterdir()) == ([path] if existing else [])

    atomic_files.atomic_text(path, '{"status":"new"}\n')
    assert path.read_text() == '{"status":"new"}\n'
    assert list(tmp_path.iterdir()) == [path]
