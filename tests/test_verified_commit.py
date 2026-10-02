"""verified-commit.sh refuses unless the verified tree is the committed tree."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "agents" / "verified-commit.sh"


def sh(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def make_repo(tmp_path: Path, verify_rc: int) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts" / "agents").mkdir(parents=True)
    shutil.copy(SCRIPT, repo / "scripts" / "agents" / "verified-commit.sh")
    stub = repo / "scripts" / "agents" / "standing-verify.sh"
    stub.write_text(f"#!/usr/bin/env bash\necho stub-verify\nexit {verify_rc}\n")
    stub.chmod(0o755)
    sh(tmp_path, "init", "-q", "-b", "target", str(repo))
    sh(repo, "config", "user.email", "t@example.com")
    sh(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("a\n")
    (repo / "b.txt").write_text("b\n")
    sh(repo, "add", ".")
    sh(repo, "commit", "-q", "-m", "base")
    return repo


def run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "TMPDIR": str(repo.parent)}
    return subprocess.run(["bash", str(repo / "scripts/agents/verified-commit.sh"), *args],
                          cwd=repo, capture_output=True, text=True, env=env)


def test_commits_only_named_paths_after_verify_passes(tmp_path):
    repo = make_repo(tmp_path, 0)
    (repo / "a.txt").write_text("a2\n")
    (repo / "junk.log").write_text("untracked\n")
    p = run(repo, "-m", "Change a", "--", "a.txt")
    assert p.returncode == 0, p.stderr
    assert "COMMITTED" in p.stdout
    assert sh(repo, "show", "--name-only", "--format=", "HEAD") == "a.txt"
    assert sh(repo, "status", "--porcelain", "--untracked-files=no") == ""


def test_refuses_when_tracked_files_outside_the_commit_are_dirty(tmp_path):
    repo = make_repo(tmp_path, 0)
    (repo / "a.txt").write_text("a2\n")
    (repo / "b.txt").write_text("b2\n")
    head = sh(repo, "rev-parse", "HEAD")
    p = run(repo, "-m", "Change a", "--", "a.txt")
    assert p.returncode == 3
    assert "b.txt" in p.stderr
    assert sh(repo, "rev-parse", "HEAD") == head


def test_refuses_when_standing_verify_fails(tmp_path):
    repo = make_repo(tmp_path, 1)
    (repo / "a.txt").write_text("a2\n")
    head = sh(repo, "rev-parse", "HEAD")
    p = run(repo, "-m", "Change a", "--", "a.txt")
    assert p.returncode == 4
    assert "standing verify failed" in p.stderr
    assert sh(repo, "rev-parse", "HEAD") == head
