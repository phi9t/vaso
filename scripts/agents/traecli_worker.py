#!/usr/bin/env python3
"""Briefed worker: a headless traecli worker, supervised by a lightweight monitor.

The lead (for example a Claude Code session) hands a bounded brief to a
non-interactive `traecli exec` worker running in its own git worktree, and a
cheap monitor agent (for example a Haiku subagent) blocks on `wait` and reports
events back to the lead. See docs/agents/cross-agent-collaboration.md
("Briefed worker").

Subcommands:

  command   print the canonical worker command line (no side effects).
  launch    start the worker detached in --worktree with the brief on stdin.
            Refuses a worktree that is dirty or already has a live worker.
            Disk I/O is pinned under <io-root>/agents/<agent>/ and every run
            gets <io-root>/agents/<agent>/runs/<stamp>/ holding brief.md,
            meta.json, events.jsonl (traecli --json), stderr.log,
            last-message.md and exit_code.
  status    one-shot summary of a run.
  wait      (monitor) block until something happens, print one line per event
            and exit 0; exit 3 with QUIET after --max-seconds without events.
            Events: WORKER_COMMITTED, WORKER_EXITED, WORKER_STALLED,
            WORKER_RESUMED, WORKER_ERROR, WORKER_DELETED_TRACKED and the
            relay I/O-health events for --io-health paths.
  digest    readable one-line-per-item view of the last events.jsonl items.
  score     objective metrics for a finished run (tokens, time, commands,
            rule violations, out-of-scope files, optional acceptance command)
            as one JSON line, for comparing worker settings.
  stop      (lead only) SIGTERM the worker's process group.

Only the Python standard library, git and traecli are used.
"""

from __future__ import annotations

import argparse
import calendar
import fnmatch
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import relay  # noqa: E402  (sibling module: git, agent_io_env, io_health)


# The canonical briefed-worker invocation. Keep the model, reasoning and
# isolation settings here so every lead launches workers the same way.
MODEL = "GPT-5.5"
REASONING = ("model_reasoning_effort=xhigh", "model_reasoning_summary=detailed")
# The brief is the worker's whole context: no cross-project memories, no
# user or plugin hooks injecting instructions, and no traecli-managed commits
# (the worker commits explicit paths itself, per the brief).
ISOLATION = (
    "features.memories=false",
    "features.hooks=false",
    "features.plugin_hooks=false",
    "features.codex_git_commit=false",
)
CONFIG = REASONING + ISOLATION
# The vaso-worker profile also turns off the workflow plugins (superpowers,
# mattpocock-skills, ...), which -c cannot do. Tuning round 2 (2026-09-29,
# .scratch/briefed-worker-tuning): same quality, about half the input tokens
# and about 27% less wall time than the same settings without it.
PROFILE = "vaso-worker"
PROFILE_SOURCE = Path(__file__).resolve().parent / f"{PROFILE}.traecli.toml"


def installed_profile(name: str) -> Path:
    return Path(os.environ.get("TRAE_HOME") or Path.home() / ".trae") / f"{name}.traecli.toml"


def check_profile(name: str | None) -> None:
    """The repo copy is the source of truth; refuse a missing or drifted install."""
    if name != PROFILE:
        return
    target = installed_profile(name)
    install = f"install it with: cp {PROFILE_SOURCE} {target}"
    if not target.is_file():
        raise relay.RelayError(f"traecli profile {name} is not installed at {target}; {install}")
    if target.read_bytes() != PROFILE_SOURCE.read_bytes():
        raise relay.RelayError(f"{target} differs from {PROFILE_SOURCE}; {install}")
SANDBOX = "workspace-write"
LOCK_NAME = "traecli-worker.json"
QUIET = 3


def worker_argv(worktree: Path, run_dir: Path, add_dirs: list[Path], model: str = MODEL,
                config: tuple[str, ...] = CONFIG, sandbox: str = SANDBOX,
                shell_timeout: str | None = None, profile: str | None = None) -> list[str]:
    argv = ["traecli", "exec", "-m", model] + (["-p", profile] if profile else [])
    for item in config:
        argv += ["-c", item]
    argv += ["-C", str(worktree), "-s", sandbox, "--json", "-o", str(run_dir / "last-message.md")]
    for path in add_dirs:
        argv += ["--add-dir", str(path)]
    if shell_timeout:
        argv += ["--shell-tool-timeout", shell_timeout]
    return argv + ["-"]  # the brief arrives on stdin


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def lock_path(worktree: Path) -> Path:
    git_dir = Path(relay.git(worktree, "rev-parse", "--absolute-git-dir"))
    return git_dir / LOCK_NAME


def read_meta(run_dir: Path) -> dict:
    return json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))


def exit_code(run_dir: Path) -> int | None:
    try:
        return int((run_dir / "exit_code").read_text().strip())
    except (OSError, ValueError):
        return None


def default_add_dirs(worktree: Path, agent_root: Path) -> list[Path]:
    """Writable dirs besides the worktree: agent I/O, git metadata, Bazel's user root.

    The sandbox keeps the worktree's own git dir (.git/worktrees/<name>, where
    index.lock and HEAD.lock live) read-only unless it is named explicitly;
    adding only the common .git is not enough (measured 2026-09-29).
    """
    common = Path(relay.git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    own = Path(relay.git(worktree, "rev-parse", "--absolute-git-dir"))
    dirs = [agent_root, common] + ([own] if own != common else [])
    bazel_user_root = Path.home() / ".cache" / "bazel"
    if bazel_user_root.is_dir():
        dirs.append(bazel_user_root)
    return dirs


def config_of(args: argparse.Namespace) -> tuple[str, ...]:
    return (REASONING if args.no_isolation else CONFIG) + tuple(args.config)


def cmd_command(args: argparse.Namespace) -> int:
    run_dir = args.io_root / "agents" / args.agent / "runs" / "<stamp>"
    add_dirs = [args.io_root / "agents" / args.agent] + list(args.add_dir)
    print(" ".join(worker_argv(args.worktree, run_dir, add_dirs, args.model, config_of(args), args.sandbox, None, args.profile)))
    return 0


def cmd_launch(args: argparse.Namespace) -> int:
    worktree = args.worktree
    lock = lock_path(worktree)
    if lock.exists():
        held = json.loads(lock.read_text(encoding="utf-8"))
        if pid_alive(held["pid"]) and exit_code(Path(held["run_dir"])) is None:
            raise relay.RelayError(f"{worktree} already has a live worker (pid {held['pid']}, run {held['run_dir']})")
    check_profile(args.profile)
    dirty = relay.dirty_entries(worktree)
    if dirty and not args.allow_dirty:
        raise relay.RelayError(f"{worktree} is dirty ({len(dirty)} entries); a worker starts from a clean tree")
    if args.ticket:
        relay.acquire_claim(args.io_root, args.ticket, args.agent, args.lease_minutes)
    env = dict(os.environ)
    env.update(relay.agent_io_env(args.io_root, args.agent))
    agent_root = Path(env["VASO_AGENT_IO_ROOT"])
    run_dir = agent_root / "runs" / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    try:
        run_dir.mkdir(parents=True)
        brief = run_dir / "brief.md"
        brief.write_text(args.brief.read_text(encoding="utf-8"), encoding="utf-8")
        add_dirs = default_add_dirs(worktree, agent_root) + list(args.add_dir)
        argv = worker_argv(worktree, run_dir, add_dirs, args.model, config_of(args), args.sandbox,
                           args.shell_timeout, args.profile)
        wrapper = r'''
exit_file=$0
ticket=$1
relay=$2
agent=$3
estate=$4
last_message=$5
brief_path=$6
shift 6
child=

finish() {
  rc=$1
  sig=$2
  if [ -n "$sig" ]; then
    printf 'terminated: SIG%s\n' "$sig" > "$last_message"
  fi
  if [ -n "$ticket" ]; then
    "$PYTHON" "$relay" release "$ticket" --agent "$agent" --estate-root "$estate" >/dev/null 2>&1 || true
  fi
  printf '%s\n' "$rc" > "$exit_file"
  exit "$rc"
}

terminate() {
  sig=$1
  case "$sig" in
    HUP) rc=129 ;;
    INT) rc=130 ;;
    QUIT) rc=131 ;;
    TERM) rc=143 ;;
    *) rc=143 ;;
  esac
  trap - HUP INT QUIT TERM
  if [ -n "$child" ]; then
    kill -"$sig" "$child" 2>/dev/null || true
    wait "$child" 2>/dev/null || true
  fi
  finish "$rc" "$sig"
}

trap 'terminate HUP' HUP
trap 'terminate INT' INT
trap 'terminate QUIT' QUIT
trap 'terminate TERM' TERM

"$@" < "$brief_path" &
child=$!
wait "$child"
rc=$?
case "$rc" in
  129) finish "$rc" HUP ;;
  130) finish "$rc" INT ;;
  131) finish "$rc" QUIT ;;
  143) finish "$rc" TERM ;;
  *) finish "$rc" "" ;;
esac
'''.strip()
        with (run_dir / "events.jsonl").open("wb") as out, (run_dir / "stderr.log").open("wb") as err:
            proc = subprocess.Popen(
                ["sh", "-c", wrapper, str(run_dir / "exit_code"), args.ticket or "",
                 str(Path(__file__).resolve().parent / "relay.py"), args.agent, str(args.io_root),
                 str(run_dir / "last-message.md"), str(brief), *argv],
                cwd=worktree, env={**env, "PYTHON": sys.executable}, stdout=out, stderr=err,
                start_new_session=True,
            )
    except Exception:
        if args.ticket:
            relay.release_claim(args.io_root, args.ticket, args.agent)
        raise
    meta = {
        "pid": proc.pid,
        "agent": args.agent,
        "ticket": args.ticket,
        "worktree": str(worktree),
        "branch": relay.git(worktree, "rev-parse", "--abbrev-ref", "HEAD"),
        "base": relay.git(worktree, "rev-parse", "HEAD"),
        "argv": argv,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "env": {k: env[k] for k in ("TMPDIR", "VASO_BAZEL_OB", "VASO_AGENT_IO_ROOT")},
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    lock.write_text(json.dumps({"pid": proc.pid, "run_dir": str(run_dir)}) + "\n", encoding="utf-8")
    print(f"LAUNCHED pid={proc.pid} run={run_dir}")
    return 0


def _commits(worktree: Path, base: str) -> list[str]:
    out = relay.git(worktree, "log", "--reverse", "--format=%h %s", f"{base}..HEAD", check=False)
    return [line for line in out.splitlines() if line]


def _events_size(run_dir: Path) -> int:
    try:
        return (run_dir / "events.jsonl").stat().st_size
    except OSError:
        return 0


def _new_errors(run_dir: Path, offset: int) -> tuple[list[str], int]:
    """Error/failed-turn items appended to events.jsonl since offset."""
    path = run_dir / "events.jsonl"
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read()
    except OSError:
        return [], offset
    complete, _, _ = chunk.rpartition(b"\n")
    errors = []
    for raw in complete.splitlines():
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if event.get("type") in ("error", "turn.failed"):
            errors.append(f"WORKER_ERROR {json.dumps(event)[:300]}")
    return errors, offset + len(complete) + (1 if complete else 0)


def cmd_wait(args: argparse.Namespace) -> int:
    run_dir = args.run
    meta = read_meta(run_dir)
    worktree = Path(meta["worktree"])
    # Commits are reported once per run across successive `wait` calls.
    cursor = run_dir / "reported-commits"
    seen = set(cursor.read_text(encoding="utf-8").splitlines()) if cursor.exists() else set()
    deleted = set(relay.tracked_deletions(worktree))
    io_state = relay.io_health(args.io_health) if args.io_health else {}
    size, last_growth, stalled = _events_size(run_dir), time.monotonic(), False
    _, offset = _new_errors(run_dir, 0)
    rc = exit_code(run_dir)
    if rc is not None:
        print(f"WORKER_EXITED rc={rc} (already exited)")
        return 0
    deadline = time.monotonic() + args.max_seconds
    while time.monotonic() < deadline:
        time.sleep(args.interval)
        events = []
        commits = _commits(worktree, meta["base"])
        events += [f"WORKER_COMMITTED {c}" for c in commits if c not in seen]
        seen.update(commits)
        cursor.write_text("".join(f"{c}\n" for c in commits), encoding="utf-8")
        now_deleted = set(relay.tracked_deletions(worktree))
        events += [f"WORKER_DELETED_TRACKED {p}" for p in sorted(now_deleted - deleted)]
        deleted = now_deleted
        errors, offset = _new_errors(run_dir, offset)
        events += errors
        new_size = _events_size(run_dir)
        if new_size != size:
            size, last_growth = new_size, time.monotonic()
            if stalled:
                stalled = False
                events.append("WORKER_RESUMED")
        elif not stalled and time.monotonic() - last_growth >= args.stall_seconds:
            stalled = True
            events.append(f"WORKER_STALLED no events for {int(time.monotonic() - last_growth)}s")
        if args.io_health:
            new_io = relay.io_health(args.io_health)
            events += [e for e in relay.diff_io_health(io_state, new_io)
                       if not any(e.startswith(prefix) for prefix in args.ignore)]
            io_state = new_io
        rc = exit_code(run_dir)
        if rc is None and not pid_alive(meta["pid"]):
            rc = -1
        if rc is not None:
            events.append(f"WORKER_EXITED rc={rc}")
        if events:
            stamp = time.strftime("%H:%M:%S", time.gmtime())
            for event in events:
                print(f"{event} [{stamp}Z]", flush=True)
            return 0
    print(f"QUIET {int(args.max_seconds)}s events.jsonl={size}B commits={len(seen)}", flush=True)
    return QUIET


def digest_line(event: dict, width: int = 240) -> str | None:
    kind = event.get("type", "?")
    item = event.get("item") or {}
    if kind == "item.completed":
        itype = item.get("type")
        if itype == "command_execution":
            out = (item.get("aggregated_output") or "").strip().splitlines()
            tail = f" | {out[-1][:120]}" if out else ""
            return f"cmd rc={item.get('exit_code')}: {item.get('command', '')[:width]}{tail}"
        if itype == "agent_message":
            return f"msg: {' '.join((item.get('text') or '').split())[:width * 2]}"
        if itype == "reasoning":
            return f"think: {' '.join((item.get('text') or '').split())[:width]}"
        if itype == "file_change":
            changes = item.get("changes") or []
            return "edit: " + ", ".join(f"{c.get('kind', '?')} {c.get('path', '?')}" for c in changes)[:width]
        return f"{itype}: {json.dumps(item)[:width]}"
    if kind == "turn.completed":
        usage = event.get("usage") or {}
        return f"turn done: in={usage.get('input_tokens')} out={usage.get('output_tokens')}"
    if kind in ("error", "turn.failed"):
        return f"ERROR: {json.dumps(event)[:width]}"
    return None


def cmd_digest(args: argparse.Namespace) -> int:
    lines = []
    try:
        raw_lines = (args.run / "events.jsonl").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        raw_lines = []
    for raw in raw_lines:
        try:
            line = digest_line(json.loads(raw))
        except json.JSONDecodeError:
            continue
        if line:
            lines.append(line)
    for line in lines[-args.last:]:
        print(line)
    return 0


VIOLATIONS = {
    # Plumbing (a private index, commit-tree, update-ref) is how workers route
    # around a blocked index or ref; a blocked worker must stop instead.
    "git": re.compile(r"git\s+add\s+(-A|--all|\.(\s|$))|--amend|git\s+push|git\s+rebase|reset\s+--hard"
                      r"|GIT_INDEX_FILE=|git\s+commit-tree|git\s+update-ref"),
    "io": re.compile(r"(^|[\s'\"=>])/(var/)?tmp/"),
    "network": re.compile(r"\b(curl|wget)\b|pip3?\s+download|git\s+(fetch|clone|pull)\b"),
}
# A bazel invocation (command token, optional startup flags, then a command),
# not any path that happens to contain "bazel" (e.g. <io>/bazel-ob/.../test.log).
BAZEL_CALL = re.compile(r"(^|[\s;&|(])[\"']?(\S*/)?bazel(-real)?[\"']?(\s+--\S+)*\s+(build|test|run|query|cquery|aquery)\b")


def _utc_seconds(stamp: str) -> float:
    return calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))


def score_run(run_dir: Path, allowed: list[str] | None = None, accept: str | None = None,
              io_root: Path | None = None) -> dict:
    meta = read_meta(run_dir)
    worktree, base = Path(meta["worktree"]), meta["base"]
    tokens = {"input": 0, "cached_input": 0, "output": 0, "reasoning": 0}
    commands, failed = [], 0
    for raw in (run_dir / "events.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "turn.completed":
            usage = event.get("usage") or {}
            for key, field in (("input", "input_tokens"), ("cached_input", "cached_input_tokens"),
                               ("output", "output_tokens"), ("reasoning", "reasoning_output_tokens")):
                tokens[key] += usage.get(field) or 0
        item = event.get("item") or {}
        if event.get("type") == "item.completed" and item.get("type") == "command_execution":
            commands.append(item.get("command") or "")
            failed += item.get("exit_code") not in (0, None)
    violations: dict[str, list[str]] = {}
    for command in commands:
        for kind, pattern in VIOLATIONS.items():
            if pattern.search(command):
                violations.setdefault(kind, []).append(command)
        if BAZEL_CALL.search(command) and "--output_base" not in command:
            violations.setdefault("io", []).append(command)
    for kind in violations:
        violations[kind] = list(dict.fromkeys(violations[kind]))
    violations = {kind: violations[kind] for kind in sorted(violations)}

    changed = set(relay.git(worktree, "diff", "--name-only", f"{base}..HEAD", check=False).splitlines())
    changed |= {path for _status, path in relay.dirty_entries(worktree)}
    out_of_scope = sorted(p for p in changed if p and allowed is not None
                          and not any(fnmatch.fnmatch(p, glob) for glob in allowed))
    commit_times = relay.git(worktree, "log", "--reverse", "--format=%ct", f"{base}..HEAD", check=False).split()
    started = _utc_seconds(meta["started_utc"])
    rc = exit_code(run_dir)
    ended = (run_dir / "exit_code").stat().st_mtime if rc is not None else None
    result = {
        "run": run_dir.name,
        "exit_code": rc,
        "wall_seconds": round(ended - started) if ended else None,
        "tokens": tokens,
        "commands": len(commands),
        "failed_commands": failed,
        "commits": len(commit_times),
        "first_commit_seconds": int(commit_times[0]) - int(started) if commit_times else None,
        "violations": violations,
        "out_of_scope": out_of_scope,
    }
    if accept:
        env = dict(os.environ)
        if io_root is not None:
            env.update(relay.agent_io_env(io_root, "score"))
        before = relay.dirty_entries(worktree)
        accept_rc = subprocess.run(accept, shell=True, cwd=worktree, env=env).returncode
        if sorted(relay.dirty_entries(worktree)) != sorted(before):
            result["accept"] = "fail dirtied-tree"
        else:
            result["accept"] = "pass" if accept_rc == 0 else f"fail rc={accept_rc}"
    return result


def cmd_score(args: argparse.Namespace) -> int:
    result = score_run(args.run, args.allowed, args.accept, args.io_root)
    result.update({"label": args.label} if args.label else {})
    line = json.dumps(result, sort_keys=True)
    print(line)
    if args.append:
        with args.append.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    meta = read_meta(args.run)
    worktree = Path(meta["worktree"])
    rc = exit_code(args.run)
    state = f"exited rc={rc}" if rc is not None else ("running" if pid_alive(meta["pid"]) else "gone")
    commits = _commits(worktree, meta["base"])
    print(f"run={args.run.name} pid={meta['pid']} {state} started={meta['started_utc']} "
          f"events={_events_size(args.run)}B commits={len(commits)} dirty={len(relay.dirty_entries(worktree))}")
    for commit in commits:
        print(f"  {commit}")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    meta = read_meta(args.run)
    if exit_code(args.run) is not None or not pid_alive(meta["pid"]):
        print("NOT_RUNNING")
        return 0
    os.killpg(meta["pid"], signal.SIGTERM)
    print(f"SIGTERM sent to process group {meta['pid']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def worker_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--worktree", type=Path, required=True, help="the worker's own git worktree")
        p.add_argument("--agent", required=True, help="worker name; I/O goes under <io-root>/agents/<agent>/")
        p.add_argument("--io-root", type=Path, required=True, help="disk-backed estate root")
        p.add_argument("--model", default=MODEL)
        p.add_argument("--config", action="append", default=[], help="extra -c key=value (after the defaults)")
        p.add_argument("--no-isolation", action="store_true",
                       help="drop the isolation flags (memories/hooks/git-commit); for tuning experiments only")
        p.add_argument("--sandbox", default=SANDBOX)
        p.add_argument("--profile", default=PROFILE,
                       help=f"traecli profile (-p); default {PROFILE} (scripts/agents/{PROFILE}.traecli.toml)")
        p.add_argument("--no-profile", dest="profile", action="store_const", const=None,
                       help="run without a profile (tuning experiments only)")
        p.add_argument("--add-dir", type=Path, action="append", default=[])

    command = sub.add_parser("command", help="print the canonical worker command")
    worker_options(command)
    command.set_defaults(func=cmd_command)

    launch = sub.add_parser("launch", help="start a detached worker on a brief")
    worker_options(launch)
    launch.add_argument("--brief", type=Path, required=True)
    launch.add_argument("--ticket", help="ticket id to claim before launch and release when the worker exits")
    launch.add_argument("--lease-minutes", type=float, default=240)
    launch.add_argument("--shell-timeout", default="3h", help="hard timeout per worker shell command")
    launch.add_argument("--allow-dirty", action="store_true")
    launch.set_defaults(func=cmd_launch)

    for name, func, text in (("status", cmd_status, "one-shot run summary"),
                             ("stop", cmd_stop, "(lead only) terminate the worker")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--run", type=Path, required=True)
        p.set_defaults(func=func)

    wait = sub.add_parser("wait", help="(monitor) block until a worker event")
    wait.add_argument("--run", type=Path, required=True)
    wait.add_argument("--interval", type=float, default=15.0)
    wait.add_argument("--max-seconds", type=float, default=540.0)
    wait.add_argument("--stall-seconds", type=float, default=1200.0)
    wait.add_argument("--io-health", type=Path, nargs="*", default=[])
    wait.add_argument("--ignore", action="append", default=[],
                      help="drop I/O events starting with this prefix (known noise from other sessions), "
                           "e.g. 'IO_NEW_TMP_ENTRY /tmp/tmp.'")
    wait.set_defaults(func=cmd_wait)

    digest = sub.add_parser("digest", help="readable tail of the worker's events")
    digest.add_argument("--run", type=Path, required=True)
    digest.add_argument("--last", type=int, default=25)
    digest.set_defaults(func=cmd_digest)

    score = sub.add_parser("score", help="objective metrics for a finished run, as one JSON line")
    score.add_argument("--run", type=Path, required=True)
    score.add_argument("--allowed", action="append", help="glob of paths the brief allows (repeatable)")
    score.add_argument("--accept", help="acceptance command run in the worktree (pinned I/O, must not dirty it)")
    score.add_argument("--io-root", type=Path, help="disk-backed estate root for the acceptance command's I/O")
    score.add_argument("--label", help="cell/bench label stored with the result")
    score.add_argument("--append", type=Path, help="also append the JSON line to this results file")
    score.set_defaults(func=cmd_score)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for name in ("worktree", "run", "io_root"):
        if getattr(args, name, None) is not None:
            setattr(args, name, getattr(args, name).resolve())
    try:
        return args.func(args)
    except relay.RelayError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
