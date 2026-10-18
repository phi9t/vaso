"""Behavioral tests for scripts/agents/traecli_worker.py with a fake traecli on PATH."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

AGENTS = Path(__file__).resolve().parents[1] / "scripts" / "agents"
sys.path.insert(0, str(AGENTS))
_spec = importlib.util.spec_from_file_location("traecli_worker", AGENTS / "traecli_worker.py")
assert _spec is not None and _spec.loader is not None
worker = importlib.util.module_from_spec(_spec)
sys.modules["traecli_worker"] = worker
_spec.loader.exec_module(worker)

# Emits traecli-style JSONL, records its argv/env/stdin, commits once, then
# waits for a release file so tests control when it exits (or exits once its
# log dir is gone, so an interrupted run leaves nothing behind).
FAKE_TRAECLI = r"""#!/bin/sh
printf '%s\n' "$@" > "$FAKE_LOG/argv"
cat > "$FAKE_LOG/stdin"
echo "$TMPDIR" > "$FAKE_LOG/tmpdir"
echo '{"type":"thread.started","thread_id":"t"}'
printf '%s\n' '{"type":"item.completed","item":{"type":"command_execution","command":"git status","aggregated_output":"clean\n","exit_code":0}}'
echo work > work.txt && git add work.txt && git commit -q -m "worker slice"
echo '{"type":"item.completed","item":{"type":"agent_message","text":"slice committed"}}'
while [ ! -e "$FAKE_LOG/release" ]; do [ -d "$FAKE_LOG" ] || exit 99; sleep 0.1; done
echo '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":2}}'
exit "$(cat "$FAKE_LOG/release")"
"""


def sh(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture()
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Path]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    sh(repo, "init", "-q", "-b", "main")
    sh(repo, "config", "user.email", "t@example.com")
    sh(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("a\n")
    sh(repo, "add", ".")
    sh(repo, "commit", "-q", "-m", "base")
    wt = tmp_path / "wt"
    sh(repo, "worktree", "add", "-q", "-b", "worker", str(wt))
    bindir, log = tmp_path / "bin", tmp_path / "fake-log"
    bindir.mkdir()
    log.mkdir()
    fake = bindir / "traecli"
    fake.write_text(FAKE_TRAECLI)
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(log))
    trae_home = tmp_path / "trae-home"
    trae_home.mkdir()
    (trae_home / "vaso-worker.traecli.toml").write_text((AGENTS / "vaso-worker.traecli.toml").read_text())
    monkeypatch.setenv("TRAE_HOME", str(trae_home))
    brief = tmp_path / "brief.md"
    brief.write_text("do ticket 02\n")
    io_root = tmp_path / "estate"
    io_root.mkdir()
    yield {"wt": wt, "log": log, "brief": brief, "io": io_root, "trae_home": trae_home}
    # A failing test never reaches its own release; free the fake here.
    release = log / "release"
    if not release.exists():
        release.write_text("0")


def launch(s: dict[str, Path]) -> Path:
    rc = worker.main(["launch", "--worktree", str(s["wt"]), "--agent", "w", "--io-root", str(s["io"]),
                      "--brief", str(s["brief"])])
    assert rc == 0
    (run,) = sorted((s["io"] / "agents" / "w" / "runs").iterdir())
    return run


def wait_for(path: Path) -> None:
    deadline = time.monotonic() + 20
    while not path.exists():
        assert time.monotonic() < deadline, f"timed out waiting for {path}"
        time.sleep(0.05)


def test_canonical_command_pins_model_and_reasoning(tmp_path: Path) -> None:
    argv = worker.worker_argv(tmp_path / "wt", tmp_path / "run", [tmp_path / "io"])
    assert argv[:8] == ["traecli", "exec", "-m", "GPT-5.5", "-c", "model_reasoning_effort=xhigh",
                        "-c", "model_reasoning_summary=detailed"]
    for flag in ("memories", "hooks", "plugin_hooks", "codex_git_commit"):
        assert f"features.{flag}=false" in argv
    assert argv[-1] == "-" and "--json" in argv and ["-s", "workspace-write"] == argv[argv.index("-s"):argv.index("-s") + 2]


def test_launch_runs_brief_with_pinned_io_and_records_run(setup: dict[str, Path]) -> None:
    run = launch(setup)
    wait_for(setup["log"] / "tmpdir")
    argv = (setup["log"] / "argv").read_text().splitlines()
    assert argv[:2] == ["exec", "-m"] and argv[-1] == "-"
    assert (setup["log"] / "stdin").read_text() == "do ticket 02\n"
    assert (setup["log"] / "tmpdir").read_text().strip() == str(setup["io"] / "agents" / "w" / "tmp")
    meta = json.loads((run / "meta.json").read_text())
    assert meta["branch"] == "worker" and meta["worktree"] == str(setup["wt"])
    (setup["log"] / "release").write_text("0")
    wait_for(run / "exit_code")


def test_second_launch_on_live_worktree_is_refused(setup: dict[str, Path]) -> None:
    run = launch(setup)
    rc = worker.main(["launch", "--worktree", str(setup["wt"]), "--agent", "w2", "--io-root", str(setup["io"]),
                      "--brief", str(setup["brief"]), "--allow-dirty"])
    assert rc == 2
    (setup["log"] / "release").write_text("0")
    wait_for(run / "exit_code")


def test_dirty_worktree_is_refused(setup: dict[str, Path]) -> None:
    (setup["wt"] / "a.txt").write_text("dirty\n")
    rc = worker.main(["launch", "--worktree", str(setup["wt"]), "--agent", "w", "--io-root", str(setup["io"]),
                      "--brief", str(setup["brief"])])
    assert rc == 2


def test_wait_reports_commit_then_exit_and_digest_is_readable(setup: dict[str, Path], capsys) -> None:
    run = launch(setup)
    wait_for(setup["log"] / "stdin")
    assert worker.main(["wait", "--run", str(run), "--interval", "0.1", "--max-seconds", "20"]) == 0
    assert "WORKER_COMMITTED" in capsys.readouterr().out
    (setup["log"] / "release").write_text("7")
    assert worker.main(["wait", "--run", str(run), "--interval", "0.1", "--max-seconds", "20"]) == 0
    assert "WORKER_EXITED rc=7" in capsys.readouterr().out
    worker.main(["digest", "--run", str(run)])
    out = capsys.readouterr().out
    assert "cmd rc=0: git status | clean" in out and "msg: slice committed" in out and "turn done" in out


def test_wait_is_quiet_and_then_flags_a_stall(setup: dict[str, Path], capsys) -> None:
    run = launch(setup)
    wait_for(setup["log"] / "stdin")
    worker.main(["wait", "--run", str(run), "--interval", "0.1", "--max-seconds", "20"])  # the commit
    capsys.readouterr()
    assert worker.main(["wait", "--run", str(run), "--interval", "0.1", "--max-seconds", "0.5",
                        "--stall-seconds", "60"]) == worker.QUIET
    assert worker.main(["wait", "--run", str(run), "--interval", "0.1", "--max-seconds", "5",
                        "--stall-seconds", "0.3"]) == 0
    assert "WORKER_STALLED" in capsys.readouterr().out
    (setup["log"] / "release").write_text("0")
    wait_for(run / "exit_code")


def test_error_events_are_reported() -> None:
    assert worker.digest_line({"type": "turn.failed", "error": {"message": "rate limited"}}).startswith("ERROR")
    assert worker.digest_line({"type": "turn.started"}) is None


def test_ignored_io_noise_does_not_wake_the_monitor(setup: dict[str, Path], tmp_path: Path, capsys) -> None:
    run = launch(setup)
    wait_for(setup["log"] / "stdin")
    worker.main(["wait", "--run", str(run), "--interval", "0.1", "--max-seconds", "20"])  # the commit
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    import threading
    threading.Timer(0.3, lambda: (scratch / "tmp.noise").write_text("x")).start()
    rc = worker.main(["wait", "--run", str(run), "--interval", "0.1", "--max-seconds", "1.5",
                      "--stall-seconds", "60", "--io-health", str(scratch),
                      "--ignore", f"IO_NEW_TMP_ENTRY {scratch}/tmp."])
    assert rc == worker.QUIET, capsys.readouterr().out
    (setup["log"] / "release").write_text("0")
    wait_for(run / "exit_code")


def _finished_run(tmp_path: Path, events: list[dict], repo: Path, base: str) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    (run / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    (run / "meta.json").write_text(json.dumps({"pid": 1, "worktree": str(repo), "base": base,
                                               "started_utc": "2026-09-29T07:00:00Z"}))
    (run / "exit_code").write_text("0\n")
    return run


def _cmd(command: str, rc: int = 0) -> dict:
    return {"type": "item.completed", "item": {"type": "command_execution", "command": command,
                                               "aggregated_output": "", "exit_code": rc}}


def test_score_counts_tokens_commands_violations_and_scope(setup: dict[str, Path], tmp_path: Path) -> None:
    wt = setup["wt"]
    base = sh(wt, "rev-parse", "HEAD")
    (wt / "ok").mkdir()
    (wt / "ok" / "a.py").write_text("x\n")
    (wt / "stray.txt").write_text("x\n")
    sh(wt, "add", "ok/a.py", "stray.txt")
    sh(wt, "commit", "-q", "-m", "slice")
    events = [
        _cmd("git status"),
        _cmd("bash -lc 'git add -A && git commit --amend'"),
        _cmd('GIT_INDEX_FILE="$PWD/x.index" git write-tree'),
        _cmd("cd x && bazel-real test //tools:all"),
        _cmd('bazel-real --output_base="$VASO_BAZEL_OB" test //tools:all'),
        _cmd("curl -sS https://example.com > /tmp/x", rc=6),
        {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 40,
                                             "output_tokens": 7, "reasoning_output_tokens": 3}},
        {"type": "turn.completed", "usage": {"input_tokens": 50, "output_tokens": 1}},
    ]
    run = _finished_run(tmp_path, events, wt, base)
    score = worker.score_run(run, allowed=["ok/*"])
    assert score["tokens"] == {"input": 150, "cached_input": 40, "output": 8, "reasoning": 3}
    assert score["commands"] == 6 and score["failed_commands"] == 1
    assert score["commits"] == 1 and score["exit_code"] == 0
    assert score["violations"] == {
        "git": ["bash -lc 'git add -A && git commit --amend'", 'GIT_INDEX_FILE="$PWD/x.index" git write-tree'],
        "io": ["cd x && bazel-real test //tools:all", "curl -sS https://example.com > /tmp/x"],
        "network": ["curl -sS https://example.com > /tmp/x"],
    }
    assert score["out_of_scope"] == ["stray.txt"]


def test_score_accept_command_runs_in_the_worktree(setup: dict[str, Path], tmp_path: Path) -> None:
    wt = setup["wt"]
    run = _finished_run(tmp_path, [], wt, sh(wt, "rev-parse", "HEAD"))
    assert worker.score_run(run, accept="test -f a.txt", io_root=setup["io"])["accept"] == "pass"
    assert worker.score_run(run, accept="test -f nope", io_root=setup["io"])["accept"] == "fail rc=1"
    assert worker.score_run(run, accept="touch dirt", io_root=setup["io"])["accept"] == "fail dirtied-tree"


def test_no_isolation_drops_only_the_isolation_flags(tmp_path: Path, capsys) -> None:
    common = ["--worktree", str(tmp_path), "--agent", "x", "--io-root", str(tmp_path)]
    worker.main(["command", *common])
    default = capsys.readouterr().out
    worker.main(["command", *common, "--no-isolation", "--config", "model_reasoning_effort=high"])
    bare = capsys.readouterr().out
    assert "features.memories=false" in default and "features." not in bare
    assert "model_reasoning_effort=xhigh" in bare and bare.index("effort=xhigh") < bare.index("effort=high ")


def test_worktree_git_dir_is_writable_for_the_worker(setup: dict[str, Path]) -> None:
    run = launch(setup)
    argv = json.loads((run / "meta.json").read_text())["argv"]
    add_dirs = {argv[i + 1] for i, a in enumerate(argv) if a == "--add-dir"}
    common = sh(setup["wt"], "rev-parse", "--path-format=absolute", "--git-common-dir")
    own = sh(setup["wt"], "rev-parse", "--absolute-git-dir")
    assert {common, own} <= add_dirs and own != common
    (setup["log"] / "release").write_text("0")
    wait_for(run / "exit_code")


def test_profile_is_passed_through(tmp_path: Path, capsys) -> None:
    worker.main(["command", "--worktree", str(tmp_path), "--agent", "x", "--io-root", str(tmp_path),
                 "--profile", "vaso-worker", "--model", "Seed-2.1-Turbo"])
    out = capsys.readouterr().out.split()
    assert out[:6] == ["traecli", "exec", "-m", "Seed-2.1-Turbo", "-p", "vaso-worker"]


def test_bazel_output_base_check_matches_invocations_not_paths() -> None:
    flagged = lambda c: bool(worker.BAZEL_CALL.search(c)) and "--output_base" not in c
    assert flagged("cd x && bazel-real test //tools:all")
    assert flagged('"$HOME/.vaso-estate/opt-vaso/bin/bazel-real" --batch build //:x')
    assert not flagged("sed -n '1,9p' /e/agents/w/bazel-ob/execroot/_main/bazel-out/testlogs/tools/t/test.log")
    assert not flagged('"$HOME/bin/bazel-real" --output_base="$VASO_BAZEL_OB" test //tools:all')


def test_default_worker_uses_the_vaso_worker_profile(setup: dict[str, Path]) -> None:
    run = launch(setup)
    argv = json.loads((run / "meta.json").read_text())["argv"]
    assert argv[:6] == ["traecli", "exec", "-m", "GPT-5.5", "-p", "vaso-worker"]
    (setup["log"] / "release").write_text("0")
    wait_for(run / "exit_code")


def test_launch_refuses_a_missing_or_drifted_profile(setup: dict[str, Path]) -> None:
    installed = setup["trae_home"] / "vaso-worker.traecli.toml"
    installed.write_text(installed.read_text() + "\n[features]\nmemories = true\n")
    args = ["launch", "--worktree", str(setup["wt"]), "--agent", "w", "--io-root", str(setup["io"]),
            "--brief", str(setup["brief"])]
    assert worker.main(args) == 2
    installed.unlink()
    assert worker.main(args) == 2
    assert worker.main(args + ["--no-profile"]) == 0
    (run,) = sorted((setup["io"] / "agents" / "w" / "runs").iterdir())
    assert "-p" not in json.loads((run / "meta.json").read_text())["argv"]
    (setup["log"] / "release").write_text("0")
    wait_for(run / "exit_code")


def test_launch_claims_ticket_and_releases_it_on_exit(setup: dict[str, Path]) -> None:
    rc = worker.main(["launch", "--worktree", str(setup["wt"]), "--agent", "w", "--io-root", str(setup["io"]),
                      "--brief", str(setup["brief"]), "--ticket", "C4a"])
    assert rc == 0
    (run,) = sorted((setup["io"] / "agents" / "w" / "runs").iterdir())
    wait_for(setup["log"] / "stdin")

    claim = setup["io"] / "agents" / "claims" / "C4a.json"
    assert json.loads(claim.read_text())["agent"] == "w"
    assert worker.relay.main(["claim", "C4a", "--agent", "other", "--estate-root", str(setup["io"])]) == 2

    (setup["log"] / "release").write_text("0")
    wait_for(run / "exit_code")
    deadline = time.monotonic() + 20
    while claim.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not claim.exists()


def test_launch_refuses_ticket_claimed_by_another_agent(setup: dict[str, Path]) -> None:
    assert worker.relay.main(["claim", "C4a", "--agent", "other", "--estate-root", str(setup["io"])]) == 0
    rc = worker.main(["launch", "--worktree", str(setup["wt"]), "--agent", "w", "--io-root", str(setup["io"]),
                      "--brief", str(setup["brief"]), "--ticket", "C4a"])
    assert rc == 2
    assert not (setup["io"] / "agents" / "w" / "runs").exists()


def test_stop_records_exit_code_and_last_message_for_killed_worker(setup: dict[str, Path]) -> None:
    run = launch(setup)
    wait_for(setup["log"] / "stdin")

    assert worker.main(["stop", "--run", str(run)]) == 0
    wait_for(run / "exit_code")

    assert (run / "exit_code").read_text().strip() == "143"
    assert (run / "last-message.md").read_text() == "terminated: SIGTERM\n"
