"""Registry updates publish complete records and preserve prior failures."""

import json
import subprocess
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import red_registry as registry


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", ["flush", "replace"])
def test_failed_update_preserves_record_cleans_staging_and_can_retry(
    tmp_path, monkeypatch, existing, failure
):
    root = tmp_path / "reds"
    attention = tmp_path / "attention.log"
    target = "//tools:fixture"
    path = root / registry.safe_name(target)
    first = [
        "record",
        str(root),
        str(attention),
        "worker-a",
        "revision",
        "bazel",
        target,
    ]
    if existing:
        assert registry.main(first) == 0
        original = path.read_bytes()
    else:
        original = None
    retry = [
        "record",
        str(root),
        str(attention),
        "worker-b",
        "revision",
        "bazel",
        target,
    ]

    def fail(*args):
        raise OSError("registry publication failed")

    with monkeypatch.context() as scoped:
        scoped.setattr(registry.os, "fsync" if failure == "flush" else "replace", fail)
        with pytest.raises(OSError, match="registry publication failed"):
            registry.main(retry)
    if existing:
        assert path.read_bytes() == original
    else:
        assert not path.exists()
    assert sorted(p.name for p in root.iterdir()) == sorted(
        [".lock", path.name] if existing else [".lock"]
    )
    assert not attention.exists()
    assert registry.main(retry) == 0
    updated = json.loads(path.read_text())
    assert updated["agents"] == (["worker-a", "worker-b"] if existing else ["worker-b"])
    if existing:
        assert updated["first_seen_epoch"] == json.loads(original)["first_seen_epoch"]
        assert updated["shared_escalated"]
        assert attention.read_text().count("SHARED_RED") == 1
    else:
        assert not updated["shared_escalated"]
        assert not attention.exists()


@pytest.mark.parametrize("action", ["record", "clear-stage"])
@pytest.mark.parametrize(
    "problem", ["json", "list", "schema", "agents", "epoch", "flag", "missing"]
)
def test_invalid_registry_is_preserved_before_any_update(tmp_path, action, problem):
    root = tmp_path / "reds"
    attention = tmp_path / "attention.log"
    target = "//tools:fixture"
    for name in ("//aaa:valid", target):
        assert (
            registry.main(
                [
                    "record",
                    str(root),
                    str(attention),
                    "worker-a",
                    "revision",
                    "bazel",
                    name,
                ]
            )
            == 0
        )
    path = root / registry.safe_name(target)
    doc = json.loads(path.read_text())
    if problem == "json":
        corrupted = b"{invalid JSON"
    elif problem == "list":
        corrupted = b"[]"
    else:
        if problem == "schema":
            doc["schema_version"] = 2
        elif problem == "agents":
            doc["agents"] = "worker-a"
        elif problem == "epoch":
            doc["first_seen_epoch"] = float("nan")
        elif problem == "flag":
            doc["shared_escalated"] = "false"
        else:
            del doc["stage"]
        corrupted = json.dumps(doc).encode()
    path.write_bytes(corrupted)
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    with pytest.raises(ValueError) as raised:
        registry.main(
            [action, str(root), str(attention), "worker-b", "revision", "bazel", target]
        )
    assert str(path) in str(raised.value)
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before
    assert not attention.exists()


def test_registry_cli_reports_corrupt_state_without_a_traceback(tmp_path):
    root = tmp_path / "reds"
    root.mkdir()
    path = root / registry.safe_name("//tools:fixture")
    path.write_bytes(b"{invalid JSON")
    result = subprocess.run(
        [
            sys.executable,
            str(Path(registry.__file__)),
            "clear-stage",
            str(root),
            str(tmp_path / "attention.log"),
            "worker",
            "revision",
            "bazel",
            "",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert str(path) in result.stderr
    assert "red registry:" in result.stderr
    assert "invalid JSON" in result.stderr
    assert "Traceback" not in result.stderr
    assert path.read_bytes() == b"{invalid JSON"
