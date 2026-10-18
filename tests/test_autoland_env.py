"""Behavioral checks for scripts/agents/autoland-env.sh."""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

AGENTS = Path(__file__).resolve().parents[1] / "scripts" / "agents"


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


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts" / "agents").mkdir(parents=True)
    sh(repo, "init", "-q", "-b", "main")
    sh(repo, "config", "user.email", "t@example.com")
    sh(repo, "config", "user.name", "t")
    shutil.copy2(AGENTS / "autoland-env.sh", repo / "scripts" / "agents" / "autoland-env.sh")
    write(repo / "README.md", "repo\n")
    sh(repo, "add", ".")
    sh(repo, "commit", "-q", "-m", "base")
    return repo


def write_fake_tmux(bin_dir: Path) -> Path:
    fake = bin_dir / "tmux"
    write(
        fake,
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            printf '%s\\n' "$*" >> "$FAKE_TMUX_LOG"
            if [ "$1" = "list-panes" ]; then
              printf '%%1 999999\\n'
              printf '%%9 %s\\n' "$FAKE_PANE_PID"
              exit 0
            fi
            exit 8
            """
        ),
    )
    fake.chmod(0o755)
    return fake


def source_env(repo: Path, env: dict[str, str], script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_autoland_env_discovers_follower_pane_from_follower_pid_ancestor(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tmux_log = tmp_path / "tmux.log"
    write_fake_tmux(bin_dir)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "VASO_ESTATE_ROOT": str(tmp_path / "estate"),
        "FAKE_TMUX_LOG": str(tmux_log),
    }

    result = source_env(
        repo,
        env,
        r"""
        sleep 30 &
        child=$!
        trap 'kill "$child" 2>/dev/null || true; wait "$child" 2>/dev/null || true' EXIT
        export FAKE_PANE_PID=$$
        FOLLOWER_PID=$child
        . scripts/agents/autoland-env.sh
        printf 'pane=%s\n' "$FOLLOWER_PANE"
        """,
    )

    assert result.returncode == 0, result.stderr
    assert "pane=%9" in result.stdout
    assert "list-panes -a -F #{pane_id} #{pane_pid}" in tmux_log.read_text(encoding="utf-8")


def test_autoland_env_respects_follower_pane_override_without_calling_tmux(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tmux_log = tmp_path / "tmux.log"
    write_fake_tmux(bin_dir)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "VASO_ESTATE_ROOT": str(tmp_path / "estate"),
        "FOLLOWER_PID": str(os.getpid()),
        "FOLLOWER_PANE": "%42",
        "FAKE_TMUX_LOG": str(tmux_log),
    }

    result = source_env(
        repo,
        env,
        ". scripts/agents/autoland-env.sh && printf 'pane=%s\\n' \"$FOLLOWER_PANE\"",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "pane=%42"
    assert not tmux_log.exists()


def test_autoland_env_exports_default_decision_specs(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    env = {
        **os.environ,
        "VASO_ESTATE_ROOT": str(tmp_path / "estate"),
        "FOLLOWER_PANE": "none",
    }

    result = source_env(
        repo,
        env,
        ". scripts/agents/autoland-env.sh && printf 'decision_specs=%s\\n' \"$DECISION_SPECS\"",
    )

    assert result.returncode == 0, result.stderr
    assert (
        result.stdout.strip()
        == "decision_specs=.scratch/pytorch-frontier-convergence/spec.md:.scratch/native-pytorch-build/spec.md"
    )


def test_autoland_env_exports_rollout_and_uncommitted_age_settings(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text("{}\n", encoding="utf-8")
    env = {
        **os.environ,
        "VASO_ESTATE_ROOT": str(tmp_path / "estate"),
        "FOLLOWER_PANE": "none",
        "FOLLOWER_ROLLOUT": str(rollout),
    }

    result = source_env(
        repo,
        env,
        ". scripts/agents/autoland-env.sh && printf 'rollout=%s\\nuncommitted=%s\\nrate=%s\\n' \"$FOLLOWER_ROLLOUT\" \"$UNCOMMITTED_MIN\" \"$UNCOMMITTED_RATE_LIMIT_MIN\"",
    )

    assert result.returncode == 0, result.stderr
    assert f"rollout={rollout}" in result.stdout
    assert "uncommitted=60" in result.stdout
    assert "rate=30" in result.stdout
