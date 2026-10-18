"""Behavioral tests for scripts/agents/review-batch.sh."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "agents" / "review-batch.sh"


def sh(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    sh(repo, "init", "-q", "-b", "target")
    sh(repo, "config", "user.email", "t@example.com")
    sh(repo, "config", "user.name", "t")
    write(repo / "base.txt", "base\n")
    sh(repo, "add", "base.txt")
    sh(repo, "commit", "-q", "-m", "base")
    base = sh(repo, "rev-parse", "HEAD")
    write(repo / "pkg" / "one.txt", "one\n")
    sh(repo, "add", "pkg/one.txt")
    sh(repo, "commit", "-q", "-m", "Re-seat pkg-one")
    first = sh(repo, "rev-parse", "HEAD")
    write(repo / "pkg" / "two.txt", "two\n")
    sh(repo, "add", "pkg/two.txt")
    sh(repo, "commit", "-q", "-m", "Re-seat pkg-two")
    return repo, base, first


def env_for(tmp_path: Path) -> dict[str, str]:
    estate = tmp_path / "estate"
    return {
        **os.environ,
        "VASO_ESTATE_ROOT": str(estate),
        "AGENT": "claude",
        "FOLLOWER_AGENT": "trae",
        "FOLLOWER_PANE": "none",
        "FOLLOWER_PID": "",
    }


def run_review(tmp_path: Path, repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), "--repo", str(repo), "--target", "target", *args],
        cwd=ROOT,
        env=env_for(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )


def test_review_batch_prints_commits_files_and_proof_verdicts_since_mark(tmp_path: Path) -> None:
    repo, base, _first = make_repo(tmp_path)
    estate = tmp_path / "estate"
    write(estate / "agents" / "claude" / "review.mark", base + "\n")
    logs = estate / "agents" / "trae" / "logs"
    write(
        logs / "pkg-one-proof.log",
        "ok=true\nPASSED pkg-one proof\nparity counts: headers=12 libs=3\n",
    )
    write(logs / "pkg-two-proof.log", "ok=true\nPASSED pkg-two proof\n")

    result = run_review(tmp_path, repo)

    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "Re-seat pkg-one" in out
    assert "pkg/one.txt" in out
    assert "ok=true" in out
    assert "parity counts: headers=12 libs=3" in out
    assert "Re-seat pkg-two" in out
    assert "pkg/two.txt" in out
    assert (estate / "agents" / "claude" / "review.mark").read_text(encoding="utf-8").strip() == base


def test_review_batch_mark_advances_to_target_tip_only_with_mark_flag(tmp_path: Path) -> None:
    repo, base, _first = make_repo(tmp_path)
    estate = tmp_path / "estate"
    mark = estate / "agents" / "claude" / "review.mark"
    write(mark, base + "\n")

    result = run_review(tmp_path, repo, "--mark")

    assert result.returncode == 0, result.stderr
    assert mark.read_text(encoding="utf-8").strip() == sh(repo, "rev-parse", "target")
    assert "MARK" in result.stdout


def test_review_batch_defaults_to_previous_commit_without_mark_file(tmp_path: Path) -> None:
    repo, _base, first = make_repo(tmp_path)

    result = run_review(tmp_path, repo)

    assert result.returncode == 0, result.stderr
    assert "Re-seat pkg-two" in result.stdout
    assert "Re-seat pkg-one" not in result.stdout
    assert first[:12] in result.stdout
