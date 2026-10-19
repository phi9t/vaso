"""verified-commit.sh refuses unsafe native rebuilds before committing."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "agents" / "verified-commit.sh"
REBUILD_IMPACT = ROOT / "foundry" / "tools" / "rebuild_impact.py"


def sh(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "tools" / "agents").mkdir(parents=True)
    (repo / "foundry" / "tools").mkdir(parents=True)
    shutil.copy(SCRIPT, repo / "tools" / "agents" / "verified-commit.sh")
    shutil.copy(REBUILD_IMPACT, repo / "foundry" / "tools" / "rebuild_impact.py")
    stub = repo / "tools" / "agents" / "standing-verify.sh"
    stub.write_text("#!/usr/bin/env bash\necho stub-verify\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    write(repo / "foundry/plans/native-inputs.json", json.dumps({
        "schema_version": 1,
        "packages": {
            "zlib-ng": {
                "mnemonic": "ZlibNgActionPrefix",
                "inputs": [
                    {"path": "native/zlib_ng/source.sha256", "cause": "source"},
                    {"path": "native/zlib_ng/zlib_ng_action.bzl", "cause": "other"},
                ],
            }
        },
    }, indent=2) + "\n")
    write(repo / "foundry/native/zlib_ng/source.sha256", "old\n")
    write(repo / "foundry/native/zlib_ng/zlib_ng_action.bzl", "old\n")
    sh(tmp_path, "init", "-q", "-b", "target", str(repo))
    sh(repo, "config", "user.email", "t@example.com")
    sh(repo, "config", "user.name", "t")
    sh(repo, "add", ".")
    sh(repo, "commit", "-q", "-m", "base")
    return repo


def run(repo: Path, *args: str, estate: Path) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "TMPDIR": str(repo.parent),
        "VASO_ESTATE_ROOT": str(estate),
        "VASO_AGENT": "commit-test",
    }
    return subprocess.run(
        ["bash", str(repo / "tools/agents/verified-commit.sh"), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )


def test_verified_commit_refuses_native_rebuild_without_message_reason(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    write(repo / "foundry/native/zlib_ng/source.sha256", "new\n")
    head = sh(repo, "rev-parse", "HEAD")

    proc = run(
        repo,
        "-m",
        "Update zlib source",
        "--",
        "foundry/native/zlib_ng/source.sha256",
        estate=tmp_path / "estate",
    )

    assert proc.returncode == 6
    assert "Native rebuild:" in proc.stderr
    assert "--allow-native-rebuild-other" not in proc.stderr
    assert sh(repo, "rev-parse", "HEAD") == head


def test_verified_commit_refuses_other_native_cause_without_override(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    write(repo / "foundry/native/zlib_ng/zlib_ng_action.bzl", "driver refactor\n")
    head = sh(repo, "rev-parse", "HEAD")

    proc = run(
        repo,
        "-m",
        "Refactor zlib action",
        "-m",
        "Native rebuild: zlib-ng; reason: source: fixture",
        "--",
        "foundry/native/zlib_ng/zlib_ng_action.bzl",
        estate=tmp_path / "estate",
    )

    assert proc.returncode == 7
    assert "--allow-native-rebuild-other" in proc.stderr
    assert "other" in proc.stderr
    assert sh(repo, "rev-parse", "HEAD") == head


def test_verified_commit_records_other_override_ledger(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    estate = tmp_path / "estate"
    write(repo / "foundry/native/zlib_ng/zlib_ng_action.bzl", "driver refactor\n")

    proc = run(
        repo,
        "-m",
        "Refactor zlib action",
        "--allow-native-rebuild-other",
        "fixture exception",
        "--",
        "foundry/native/zlib_ng/zlib_ng_action.bzl",
        estate=estate,
    )

    assert proc.returncode == 0, proc.stderr
    ledger = estate / "agents" / "commit-test" / "native-rebuild-overrides.jsonl"
    entries = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert entries[-1]["justification"] == "fixture exception"
    assert entries[-1]["paths"] == ["foundry/native/zlib_ng/zlib_ng_action.bzl"]
