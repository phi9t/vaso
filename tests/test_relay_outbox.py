"""Behavioral tests for scripts/agents/relay-outbox.sh."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "agents" / "relay-outbox.sh"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def base_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    estate = tmp_path / "estate"
    agent_io = estate / "agents" / "relay"
    return {
        **os.environ,
        "VASO_ESTATE_ROOT": str(estate),
        "VASO_AGENT_IO_ROOT": str(agent_io),
        "FOLLOWER_PANE": "%9",
        **extra,
    }


def run_outbox(tmp_path: Path, *args: str, text: str = "", env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=ROOT,
        env=env if env is not None else base_env(tmp_path),
        input=text,
        capture_output=True,
        text=True,
        check=False,
    )


def test_draft_list_and_drop_update_front_matter(tmp_path: Path) -> None:
    created = run_outbox(
        tmp_path,
        "draft",
        "--author",
        "claude",
        "--title",
        "Continue ticket 08",
        text="Please continue ticket 08.\n",
    )
    assert created.returncode == 0, created.stderr
    draft = Path(created.stdout.strip())
    assert draft.is_file()
    assert draft.parent == tmp_path / "estate" / "agents" / "relay" / "outbox"
    content = draft.read_text(encoding="utf-8")
    assert "author: claude\n" in content
    assert "target_pane: %9\n" in content
    assert "status: draft\n" in content
    assert content.endswith("Please continue ticket 08.\n")

    listed = run_outbox(tmp_path, "list")
    assert listed.returncode == 0, listed.stderr
    assert draft.name in listed.stdout
    assert "draft" in listed.stdout

    dropped = run_outbox(tmp_path, "drop", str(draft))
    assert dropped.returncode == 0, dropped.stderr
    assert "status: dropped\n" in draft.read_text(encoding="utf-8")
    assert "DROP" in (tmp_path / "estate" / "agents" / "relay" / "outbox.log").read_text(encoding="utf-8")


def write_fake_tmux(bin_dir: Path, capture: str, sent: Path, log: Path) -> None:
    write(
        bin_dir / "tmux",
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            printf '%s\\n' "$*" >> "$FAKE_TMUX_LOG"
            case "$1" in
              capture-pane)
                printf '%s' "$FAKE_CAPTURE"
                ;;
              load-buffer)
                case "$4" in
                  "$VASO_AGENT_IO_ROOT"/*) ;;
                  *) echo "buffer outside VASO_AGENT_IO_ROOT: $4" >&2; exit 7 ;;
                esac
                cp "$4" "$FAKE_SENT"
                ;;
              paste-buffer|send-keys)
                ;;
              *)
                exit 8
                ;;
            esac
            """
        ),
    )
    (bin_dir / "tmux").chmod(0o755)
    os.environ.pop("FAKE_CAPTURE", None)
    os.environ.pop("FAKE_SENT", None)
    os.environ.pop("FAKE_TMUX_LOG", None)
    sent.parent.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    assert capture


def fake_tmux_env(tmp_path: Path, capture: str) -> tuple[dict[str, str], Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sent = tmp_path / "sent.txt"
    log = tmp_path / "tmux.log"
    write_fake_tmux(bin_dir, capture, sent, log)
    env = base_env(
        tmp_path,
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        FAKE_CAPTURE=capture,
        FAKE_SENT=str(sent),
        FAKE_TMUX_LOG=str(log),
    )
    return env, sent, log


def draft(tmp_path: Path, body: str, env: dict[str, str] | None = None) -> Path:
    result = run_outbox(
        tmp_path,
        "draft",
        "--author",
        "claude",
        "--title",
        "Two line note",
        text=body,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    return Path(result.stdout.strip())


def test_send_uses_idle_capture_load_buffer_paste_and_enter(tmp_path: Path) -> None:
    env, sent, log = fake_tmux_env(tmp_path, "previous output\n❯ Explain this codebase\n")
    path = draft(tmp_path, "line one\nline two\n", env)

    result = run_outbox(tmp_path, "send", str(path), env=env)

    assert result.returncode == 0, result.stderr
    assert sent.read_text(encoding="utf-8") == "line one\nline two\n"
    assert "status: sent\n" in path.read_text(encoding="utf-8")
    calls = log.read_text(encoding="utf-8")
    assert "capture-pane -p -e -J -S -20 -t %9" in calls
    assert "load-buffer -b relay-outbox " in calls
    assert "paste-buffer -p -d -b relay-outbox -t %9" in calls
    assert "send-keys -t %9 Enter" in calls
    assert "SEND" in (tmp_path / "estate" / "agents" / "relay" / "outbox.log").read_text(encoding="utf-8")


def test_send_refuses_when_capture_is_busy(tmp_path: Path) -> None:
    env, sent, log = fake_tmux_env(tmp_path, "work\nesc to interrupt\n❯ Explain this codebase\n")
    path = draft(tmp_path, "line one\nline two\n", env)

    result = run_outbox(tmp_path, "send", str(path), env=env)

    assert result.returncode == 2
    assert "not idle" in result.stderr
    assert not sent.exists()
    assert "status: draft\n" in path.read_text(encoding="utf-8")
    assert "paste-buffer" not in log.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")
def test_private_tmux_socket_send_delivers_exactly_two_lines_and_refuses_busy_pane(tmp_path: Path) -> None:
    agent_io = Path(os.environ.get("VASO_AGENT_IO_ROOT", tmp_path / "agent-io"))
    agent_io.mkdir(parents=True, exist_ok=True)
    # Unix socket paths are capped near 108 bytes and pytest's tmp_path can be
    # longer, so keep the socket in a short dir on the pinned TMPDIR (never /tmp).
    sock_dir = Path(tempfile.mkdtemp(prefix="tx", dir=os.environ.get("TMPDIR") or agent_io))
    socket = sock_dir / "s"
    received = tmp_path / "received.txt"
    env = base_env(tmp_path, VASO_TMUX_SOCKET=str(socket))
    server = ["tmux", "-S", str(socket)]
    subprocess.run(
        [
            *server,
            "new-session",
            "-d",
            "-s",
            "outbox",
            "sh",
            "-c",
            f"printf '❯ '; head -n 2 > {received}; sleep 60",
        ],
        check=True,
    )
    try:
        pane = subprocess.run(
            [*server, "display-message", "-p", "-t", "outbox:0.0", "#{pane_id}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        env["FOLLOWER_PANE"] = pane
        path = draft(tmp_path, "line one\nline two\n", env)
        sent = run_outbox(tmp_path, "send", str(path), env=env)
        assert sent.returncode == 0, sent.stderr

        deadline = time.monotonic() + 10
        while not received.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert received.read_text(encoding="utf-8") == "line one\nline two\n"

        subprocess.run([*server, "new-session", "-d", "-s", "busy", "sh", "-c", "printf 'esc to interrupt\\n❯ '; sleep 60"], check=True)
        busy = subprocess.run(
            [*server, "display-message", "-p", "-t", "busy:0.0", "#{pane_id}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        deadline = time.monotonic() + 10
        capture = ""
        while "esc to interrupt" not in capture and time.monotonic() < deadline:
            capture = subprocess.run(
                [*server, "capture-pane", "-p", "-J", "-S", "-20", "-t", busy],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            time.sleep(0.1)
        assert "esc to interrupt" in capture
        env["FOLLOWER_PANE"] = busy
        busy_path = draft(tmp_path, "should not send\n", env)
        refused = run_outbox(tmp_path, "send", str(busy_path), env=env)
        assert refused.returncode == 2
        assert "not idle" in refused.stderr
        assert "status: draft\n" in busy_path.read_text(encoding="utf-8")
    finally:
        subprocess.run([*server, "kill-server"], check=False)
        shutil.rmtree(sock_dir, ignore_errors=True)


FIXTURES = Path(__file__).resolve().parents[1] / "crates" / "autoland-tui" / "tests" / "fixtures"


def test_send_refuses_a_real_busy_traecli_pane(tmp_path: Path) -> None:
    # In a real traecli pane the busy marker sits above the to-do list and the
    # prompt box, about 10 lines from the bottom; a short tail window misses it.
    env, sent, _ = fake_tmux_env(tmp_path, (FIXTURES / "trae-pane-busy.txt").read_text(encoding="utf-8"))
    path = draft(tmp_path, "nudge\n", env)
    result = run_outbox(tmp_path, "send", str(path), env=env)
    assert result.returncode == 2 and "not idle" in result.stderr
    assert not sent.exists()


def test_send_accepts_a_real_idle_traecli_pane(tmp_path: Path) -> None:
    env, sent, _ = fake_tmux_env(tmp_path, (FIXTURES / "trae-pane-idle.txt").read_text(encoding="utf-8"))
    path = draft(tmp_path, "nudge\n", env)
    result = run_outbox(tmp_path, "send", str(path), env=env)
    assert result.returncode == 0, result.stderr
    assert sent.read_text(encoding="utf-8") == "nudge\n"


def test_send_refuses_a_turn_timer_without_the_interrupt_hint(tmp_path: Path) -> None:
    capture = "◆ Update docs… (3h 58m 25s • ↓ 515K tokens • \n─────\n❯ Explain this codebase\n─────\n"
    env, sent, _ = fake_tmux_env(tmp_path, capture)
    path = draft(tmp_path, "nudge\n", env)
    result = run_outbox(tmp_path, "send", str(path), env=env)
    assert result.returncode == 2 and "not idle" in result.stderr
    assert not sent.exists()


def test_send_refuses_when_a_message_is_already_queued(tmp_path: Path) -> None:
    capture = "  Send after tool call · esc to edit\n  ❯ earlier note\n─────\n❯ Explain this codebase\n"
    env, sent, _ = fake_tmux_env(tmp_path, capture)
    path = draft(tmp_path, "nudge\n", env)
    assert run_outbox(tmp_path, "send", str(path), env=env).returncode == 2
    assert not sent.exists()
