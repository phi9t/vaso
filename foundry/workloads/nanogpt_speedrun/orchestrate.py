#!/usr/bin/env python3
"""Unattended host orchestrator for the cross-framework decoder speedrun."""

from __future__ import annotations

import argparse
import codecs
import math
import re
import shutil
import csv
import fcntl
import hashlib
import io
import json
import os
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
FOUNDRY_RUN = REPO_ROOT / "foundry" / "run.sh"
sys.path.insert(0, str(SCRIPT_DIR.parents[1]))
from workloads.nanogpt_speedrun import attest

PROFILES = ("torch", "jax")
PROFILE_WORKLOADS = {
    "torch": ("W7a", "W7b"),
    "jax": ("W7c", "W7d"),
}
GPU_QUERY_FIELDS = (
    "timestamp",
    "index",
    "uuid",
    "name",
    "pci.bus_id",
    "driver_version",
    "pstate",
    "memory.total",
    "memory.used",
    "utilization.gpu",
    "temperature.gpu",
    "power.draw",
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _git_output(*args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        check=True,
        capture_output=True,
    ).stdout


def _checkout_identity() -> dict[str, str]:
    commit = _git_output("rev-parse", "HEAD").decode().strip()
    paths = _git_output("ls-files", "-c", "-o", "--exclude-standard", "-z")
    digest = hashlib.sha256()
    for raw_path in sorted(item for item in paths.split(b"\0") if item):
        relative = os.fsdecode(raw_path)
        path = REPO_ROOT / relative
        digest.update(raw_path)
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(b"symlink\0")
            digest.update(os.readlink(path).encode())
        elif path.is_file():
            digest.update(b"file\0")
            digest.update(path.read_bytes())
            digest.update(b"\0executable=" + str(os.access(path, os.X_OK)).encode())
        else:
            digest.update(b"missing\0")
        digest.update(b"\0")
    return {"git_commit": commit, "worktree_sha256": digest.hexdigest()}


def _gpu_snapshot() -> dict[str, Any]:
    command = [
        "nvidia-smi",
        f"--query-gpu={','.join(GPU_QUERY_FIELDS)}",
        "--format=csv,noheader,nounits",
    ]
    snapshot: dict[str, Any] = {
        "captured_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "command": command,
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
    }
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        snapshot.update({"available": False, "error": str(exc)})
        return snapshot
    snapshot["returncode"] = completed.returncode
    if completed.returncode != 0:
        snapshot.update(
            {
                "available": False,
                "error": (completed.stderr or completed.stdout).strip(),
            }
        )
        return snapshot
    rows = csv.reader(io.StringIO(completed.stdout), skipinitialspace=True)
    try:
        gpus = [
            dict(zip(GPU_QUERY_FIELDS, (value.strip() for value in row), strict=True))
            for row in rows
            if row
        ]
    except (ValueError, csv.Error) as exc:
        snapshot.update(
            {
                "available": False,
                "error": f"cannot parse nvidia-smi output: {exc}",
                "stdout": completed.stdout,
            }
        )
        return snapshot
    snapshot.update(
        {
            "available": True,
            "gpus": gpus,
        }
    )
    return snapshot


def _run_configuration(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "line": args.line,
        "mode": args.mode,
        "backend": args.backend,
        "arm": args.arm,
        "families": [
            family.strip() for family in args.families.split(",") if family.strip()
        ],
        "gpus": args.gpus,
        "timeout_seconds": getattr(args, "timeout_seconds", 1800.0),
        "max_attempts": getattr(args, "max_attempts", 3),
    }


def _resume_argument_mismatches(
    state: dict[str, Any], requested: dict[str, Any]
) -> list[str]:
    recorded = state.get("configuration")
    if not isinstance(recorded, dict):
        recorded = {key: state.get(key) for key in requested}
    return [
        f"{key}: recorded={recorded.get(key)!r} requested={value!r}"
        for key, value in requested.items()
        if recorded.get(key) != value
    ]


def _archive_previous_summary(
    attempt_dir: Path, state: dict[str, Any]
) -> dict[str, Any]:
    history = state.setdefault("run_history", [])
    record = {
        "state": state.get("state"),
        "failure": state.get("failure"),
        "finished_utc": state.get("finished_utc"),
    }
    summary_path = attempt_dir / "summary.json"
    if summary_path.is_file():
        archive_path = (
            attempt_dir
            / "history"
            / "launcher"
            / f"summary-{len(history) + 1:02d}.json"
        )
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(summary_path, archive_path)
        record["summary"] = str(archive_path)
    attestation = attempt_dir / "attestation"
    if attestation.exists():
        destination = attempt_dir / "history" / "launcher" / f"attestation-{len(history) + 1:02d}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(attestation, destination)
        record["attestation"] = str(destination)
    history.append(record)
    return record


def _archive_profile_output(
    attempt_dir: Path, *, profile: str, retry_number: int
) -> Path | None:
    profile_dir = attempt_dir / profile
    if not profile_dir.exists():
        return None
    archive_dir = attempt_dir / "history" / profile / f"attempt-{retry_number:02d}"
    archive_dir.parent.mkdir(parents=True, exist_ok=True)
    if archive_dir.exists():
        raise ValueError(f"retry archive already exists: {archive_dir}")
    os.replace(profile_dir, archive_dir)
    return archive_dir


def _color_enabled(choice: str) -> bool:
    return choice == "always" or (choice == "auto" and sys.stdout.isatty())


def _paint(text: str, code: str, *, enabled: bool) -> str:
    return f"\033[{code}m{text}\033[0m" if enabled else text


def _format_duration(seconds: float) -> str:
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _stream_command(
    command: list[str],
    *,
    environment: dict[str, str],
    log_path: Path,
    status_interval: float,
    label: str,
    color: bool,
    timeout_seconds: float = 1800.0,
    cancelled: Any = lambda: False,
) -> int:
    """Drain partial lines without blocking deadlines; terminate the owned process group."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        command, cwd=REPO_ROOT, env=environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
    )
    assert process.stdout is not None
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    started = time.monotonic()
    next_heartbeat = started + status_interval
    last_line = "starting"
    forced_exit: int | None = None
    termination_started: float | None = None
    stream_open = True
    with selectors.DefaultSelector() as selector, log_path.open("a", encoding="utf-8") as log:
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            while stream_open or process.poll() is None:
                now = time.monotonic()
                if forced_exit is None and (cancelled() or now - started >= timeout_seconds):
                    forced_exit = 130 if cancelled() else 124
                    termination_started = now
                    message = f"{label}: {'cancelled' if forced_exit == 130 else 'stage timeout'}\n"
                    log.write(message)
                    log.flush()
                    print(message, end="", flush=True)
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                if termination_started is not None and now - termination_started >= 5:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    if process.poll() is not None:
                        break
                for key, _ in selector.select(0.1):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        stream_open = False
                        continue
                    text = decoder.decode(chunk)
                    log.write(text)
                    log.flush()
                    last_line = text.strip()[-120:] or last_line
                    print(text, end="", flush=True)
                if now >= next_heartbeat:
                    print(_paint(f"  {label} elapsed={_format_duration(now - started)} last={last_line}",
                                 "36", enabled=color), flush=True)
                    next_heartbeat = now + status_interval
        finally:
            # Also covers host exceptions while a child is live.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            process.stdout.close()
    return forced_exit if forced_exit is not None else int(process.returncode)


def _profile_command(
    args: argparse.Namespace,
    *,
    profile: str,
    sandbox_out: str,
    git_commit: str,
) -> list[str]:
    workloads = PROFILE_WORKLOADS[profile]
    if args.arm == "baseline":
        workloads = workloads[:1]
    elif args.arm == "optimized":
        workloads = workloads[1:]
    return [
        str(FOUNDRY_RUN),
        "--insula-cmd",
        "env",
        f"VASO_NANOGPT_MODE={args.mode}",
        f"VASO_NANOGPT_FAMILIES={args.families}",
        f"VASO_SOURCE_GIT_COMMIT={git_commit}",
        "bash",
        "workloads/nanogpt_speedrun/run_inside.sh",
        "--profile",
        profile,
        "--out",
        sandbox_out,
        "--only",
        ",".join(workloads),
    ]


def _profile_environment(
    args: argparse.Namespace,
    *,
    profile: str,
) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "VASO_ESTATE_ROOT": str(args.estate_root),
            "VASO_CUDA_LINE": args.line,
            "VASO_PROFILE": profile,
            "VASO_GPUS": str(args.gpus),
            "VASO_AGENT": args.agent,
            "VASO_BAZEL_OB": str(
                args.estate_root
                / "vaso"
                / "lines"
                / args.line
                / "cache"
                / "bazel"
                / f"output-base-{profile}"
            ),
            "VASO_NANOGPT_MODE": args.mode,
            "VASO_NANOGPT_FAMILIES": args.families,
        }
    )
    return environment


def _preflight(args: argparse.Namespace, profiles: tuple[str, ...]) -> None:
    if not args.estate_root.is_absolute():
        raise ValueError("--estate-root must be absolute")
    manifest = args.estate_root / "rootfs-lines" / args.line / "rootfs-bundle.json"
    if not manifest.is_file():
        raise ValueError(
            f"missing rootfs bundle manifest: {manifest}; prepare the selected estate line"
        )
    for profile in profiles:
        output_base = (
            args.estate_root
            / "vaso"
            / "lines"
            / args.line
            / "cache"
            / "bazel"
            / f"output-base-{profile}"
        )
        if not output_base.is_dir():
            raise ValueError(
                f"missing {profile} output base: {output_base}; build that profile first"
            )


def _capture_build_inputs(args: argparse.Namespace, profiles: tuple[str, ...], destination: Path) -> None:
    """Retain existing provider/plan identities without rebuilding or touching caches."""
    for profile in profiles:
        packages = ("pytorch", "triton") if profile == "torch" else ("jaxlib",)
        output_base = args.estate_root / "vaso" / "lines" / args.line / "cache" / "bazel" / f"output-base-{profile}"
        for package in packages:
            matches = list(output_base.glob(
                f"execroot/_main/bazel-out/*/bin/native/{package}/{package}_action_provider_metadata.json"))
            if len(matches) != 1:
                raise ValueError(f"expected one {package} provider, found {len(matches)}; build/materialize the selected profile")
            provider = matches[0]
            if not (provider.parent / f"{package}_action_prefix").is_dir():
                raise ValueError(f"missing {package} prefix beside {provider}; materialize the native target")
            metadata = attest.read_json(provider)
            digest = metadata.get("action_input_digest", "")
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError(f"missing native input identity: {provider}")
            for source in (provider, provider.parent / f"{package}_action_build_plan.json"):
                target = destination / profile / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                content = source.read_bytes()
                if target.exists() and target.read_bytes() != content:
                    raise ValueError(f"native input differs from the original attempt: {source}")
                target.write_bytes(content)


def _load_workload_results(
    attempt_dir: Path, profiles: tuple[str, ...], arm: str = "all"
) -> tuple[list[dict[str, Any]], list[str]]:
    results: list[dict[str, Any]] = []
    failures: list[str] = []
    for profile in profiles:
        for workload_id in PROFILE_WORKLOADS[profile]:
            if arm == "baseline" and workload_id in ("W7b", "W7d"):
                continue
            if arm == "optimized" and workload_id in ("W7a", "W7c"):
                continue
            path = attempt_dir / profile / f"{workload_id}.json"
            if not path.is_file():
                failures.append(workload_id)
                continue
            try:
                result = attest.read_json(path)
            except (OSError, ValueError) as exc:
                failures.append(workload_id)
                results.append({"id": workload_id, "verdict": "failed", "error": str(exc)})
                continue
            results.append(result)
            if result.get("verdict") != "passed":
                failures.append(workload_id)
    return results, failures


def _print_result_table(results: list[dict[str, Any]], *, color: bool) -> None:
    rows: list[tuple[str, str, str, str, str]] = []
    for result in results:
        for comparison in result.get("comparisons", []):
            if not isinstance(comparison, dict):
                continue
            speedup = comparison.get("speedup")
            rows.append(
                (
                    str(result.get("backend", "?")),
                    str(comparison.get("family", "?")),
                    f"{float(comparison.get('baseline_tokens_per_second', 0.0)):,.0f}",
                    f"{float(comparison.get('optimized_tokens_per_second', 0.0)):,.0f}",
                    f"{float(speedup):.3f}x"
                    if isinstance(speedup, (int, float))
                    else "-",
                )
            )
    if not rows:
        return
    headings = ("BACKEND", "FAMILY", "BASE TOK/S", "OPT TOK/S", "SPEEDUP")
    widths = [
        max(len(headings[index]), *(len(row[index]) for row in rows))
        for index in range(len(headings))
    ]
    print(_paint("\nOptimization results", "1;36", enabled=color))
    print(
        "  "
        + "  ".join(value.ljust(widths[index]) for index, value in enumerate(headings))
    )
    print("  " + "  ".join("-" * width for width in widths))
    for row in rows:
        print(
            "  "
            + "  ".join(value.ljust(widths[index]) for index, value in enumerate(row))
        )


def _show_status(args: argparse.Namespace) -> int:
    root = args.estate_root / "agents" / args.agent / "nanogpt-speedrun"
    if args.run_id:
        attempt_dir = root / args.run_id
    else:
        candidates = sorted(path for path in root.glob("*") if path.is_dir())
        if not candidates:
            print(f"no speedrun attempts under {root}", file=sys.stderr)
            return 1
        attempt_dir = candidates[-1]
    state_path = attempt_dir / "state.json"
    if not state_path.is_file():
        print(f"missing state: {state_path}", file=sys.stderr)
        return 1
    state = _read_json(state_path)
    print(
        f"run={state.get('run_id')} state={state.get('state')} "
        f"line={state.get('line')} mode={state.get('mode')} out={attempt_dir}"
    )
    for profile, stage in state.get("stages", {}).items():
        print(
            f"  {profile:<5} {stage.get('state', '?'):<10} "
            f"exit={stage.get('exit_code', '-')} log={stage.get('log', '-')}"
        )
    results, _ = _load_workload_results(attempt_dir, PROFILES)
    _print_result_table(results, color=_color_enabled(args.color))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--estate-root", required=True, type=Path)
    parser.add_argument("--line", choices=("cu129", "cu130"), default="cu129")
    parser.add_argument("--mode", choices=("smoke", "benchmark"), default="smoke")
    parser.add_argument("--backend", choices=("all", "torch", "jax"), default="all")
    parser.add_argument(
        "--arm", choices=("all", "baseline", "optimized"), default="all"
    )
    parser.add_argument(
        "--families", default="gpt,qwen3,mini_kimi_k3,mini_glm_53_flash"
    )
    parser.add_argument("--gpus", type=int, default=1)
    parser.add_argument("--agent", default=os.environ.get("VASO_AGENT", "verifier"))
    parser.add_argument("--run-id")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--status-interval", type=float, default=30.0)
    parser.add_argument("--timeout-seconds", type=float, default=1800.0, help="deadline per backend stage")
    parser.add_argument("--max-attempts", type=int, default=3, help="total attempts including resumes")
    parser.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    args.estate_root = args.estate_root.expanduser().resolve()
    if args.resume and not args.run_id:
        print("--resume requires the original --run-id", file=sys.stderr)
        return 2
    if args.gpus != 1:
        print("this workload currently requires --gpus 1", file=sys.stderr)
        return 2
    if not all(math.isfinite(x) and x > 0 for x in (args.status_interval, args.timeout_seconds)) or args.max_attempts < 1:
        print("interval/deadline must be finite and positive; --max-attempts must be positive", file=sys.stderr)
        return 2
    for name, value in (("agent", args.agent), ("run-id", args.run_id)):
        if value is not None and (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) or value in (".", "..")):
            print(f"--{name} must be a single safe path component", file=sys.stderr)
            return 2
    families = [item.strip() for item in args.families.split(",") if item.strip()]
    if not families or len(families) != len(set(families)) or not set(families) <= set(attest.FAMILIES):
        print("--families must be a nonempty, unique subset of the supported families", file=sys.stderr)
        return 2
    args.families = ",".join(families)
    if args.status:
        return _show_status(args)
    profiles = PROFILES if args.backend == "all" else (args.backend,)
    color = _color_enabled(args.color)
    try:
        checkout = _checkout_identity()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"cannot identify checkout: {exc}; run from an available Git checkout", file=sys.stderr)
        return 2
    configuration = _run_configuration(args)
    run_id = args.run_id or (
        time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        + "-"
        + checkout["worktree_sha256"][:8]
    )
    attempt_root = args.estate_root / "agents" / args.agent / "nanogpt-speedrun"
    attempt_dir = attempt_root / run_id
    state_path = attempt_dir / "state.json"
    if attempt_dir.exists() and not args.resume:
        print(
            f"attempt already exists: {attempt_dir}; pass --resume with the same --run-id",
            file=sys.stderr,
        )
        return 2
    attempt_dir.mkdir(parents=True, exist_ok=True)
    lock_path = attempt_root / "launcher.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"another speedrun launcher owns {lock_path}", file=sys.stderr)
        return 2

    if state_path.is_file():
        state = _read_json(state_path)
        if state.get("checkout") != checkout:
            print(
                "checkout identity differs from the recorded attempt; resume from the original checkout",
                file=sys.stderr,
            )
            return 2
        mismatches = _resume_argument_mismatches(state, configuration)
        if mismatches:
            print(
                "resume arguments differ from the recorded attempt:\n  "
                + "\n  ".join(mismatches),
                file=sys.stderr,
            )
            return 2
        if state.get("attempt_count", 1) >= args.max_attempts:
            print("attempt limit reached; start a new run-id after correcting the failure", file=sys.stderr)
            return 2
        if (attempt_dir / "summary.json").exists() or (attempt_dir / "attestation").exists():
            _archive_previous_summary(attempt_dir, state)
        state["attempt_count"] = state.get("attempt_count", 1) + 1
        state["state"] = "running"
        state.pop("failure", None)
        state.pop("verification_errors", None)
        state.pop("finished_utc", None)
        state["resumed_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _write_json(state_path, state)
    else:
        state = {
            "schema_version": 2,
            "attempt_count": 1,
            "run_id": run_id,
            "state": "running",
            "configuration": configuration,
            "line": args.line,
            "mode": args.mode,
            "backend": args.backend,
            "arm": args.arm,
            "families": args.families.split(","),
            "gpus": args.gpus,
            "checkout": checkout,
            "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "stages": {},
        }
        _write_json(state_path, state)

    print(
        _paint(
            f"NanoGPT speedrun {run_id} · {args.line} · {args.mode}",
            "1;36",
            enabled=color,
        )
    )
    print(f"  durable state: {state_path}")
    interrupted = False

    def handle_signal(signum: int, frame: object) -> None:
        del signum, frame
        nonlocal interrupted
        interrupted = True

    previous_handlers = {
        signum: signal.signal(signum, handle_signal)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        try:
            _preflight(args, profiles)
            inputs = attempt_dir / "inputs"
            inputs.mkdir(exist_ok=True)
            manifest = args.estate_root / "rootfs-lines" / args.line / "rootfs-bundle.json"
            manifest_copy = inputs / "rootfs-bundle.json"
            if manifest_copy.exists() and manifest_copy.read_bytes() != manifest.read_bytes():
                raise ValueError("rootfs manifest differs from the original attempt")
            shutil.copyfile(manifest, manifest_copy)
            _capture_build_inputs(args, profiles, inputs)
        except (OSError, ValueError) as exc:
            state["failure"] = f"preflight failed: {exc}"
            print(state["failure"], file=sys.stderr)
        for index, profile in enumerate(profiles, start=1):
            if state.get("failure"):
                break
            if interrupted:
                break
            existing = state["stages"].get(profile, {})
            if args.resume and existing.get("state") == "passed":
                print(f"[{index}/{len(profiles)}] {profile}: already passed; skipping")
                continue
            if args.resume and existing:
                profile_history = state.setdefault("stage_history", {}).setdefault(
                    profile, []
                )
                archived_stage = dict(existing)
                archived_output = _archive_profile_output(
                    attempt_dir,
                    profile=profile,
                    retry_number=len(profile_history) + 1,
                )
                if archived_output is not None:
                    archived_stage["archived_output"] = str(archived_output)
                profile_history.append(archived_stage)
            current_checkout = _checkout_identity()
            if current_checkout != checkout:
                raise ValueError("checkout changed during execution")
            sandbox_out = (
                f"/vaso/agents/{args.agent}/nanogpt-speedrun/{run_id}/{profile}"
            )
            command = _profile_command(
                args,
                profile=profile,
                sandbox_out=sandbox_out,
                git_commit=checkout["git_commit"],
            )
            log_path = attempt_dir / "logs" / f"{profile}.log"
            stage = {
                "state": "running",
                "command": command,
                "log": str(log_path),
                "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "gpu_before": _gpu_snapshot(),
            }
            state["stages"][profile] = stage
            _write_json(state_path, state)
            heading = f"[{index}/{len(profiles)}] {profile.upper()}"
            print(_paint(f"\n{heading} ▶", "1;34", enabled=color), flush=True)
            returncode = _stream_command(
                command,
                environment=_profile_environment(args, profile=profile),
                log_path=log_path,
                status_interval=args.status_interval,
                label=profile,
                color=color,
                timeout_seconds=args.timeout_seconds,
                cancelled=lambda: interrupted,
            )
            stage.update(
                {
                    "state": "passed" if returncode == 0 else "failed",
                    "exit_code": returncode,
                    "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "gpu_after": _gpu_snapshot(),
                }
            )
            _write_json(state_path, state)
            symbol = "✓" if returncode == 0 else "✗"
            code = "1;32" if returncode == 0 else "1;31"
            print(_paint(f"{heading} {symbol}", code, enabled=color), flush=True)
            if returncode != 0:
                break
        final_checkout = _checkout_identity()
        if state.get("failure"):
            state["state"] = "failed"
        elif final_checkout != checkout:
            state["state"] = "failed"
            state["failure"] = "checkout changed during execution"
        elif interrupted:
            state["state"] = "interrupted"
            state["failure"] = "received SIGINT or SIGTERM"
        elif all(
            state["stages"].get(profile, {}).get("state") == "passed"
            for profile in profiles
        ):
            state["state"] = "passed"
        else:
            state["state"] = "failed"
        state["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        results, failures = _load_workload_results(attempt_dir, profiles, args.arm)
        if failures:
            state["state"] = "failed"
        summary = {
            "schema_version": 1,
            "run_id": run_id,
            "state": state["state"],
            "failed_workloads": failures,
            "results": results,
            "state_path": str(state_path),
        }
        _write_json(attempt_dir / "summary.json", summary)
        _write_json(state_path, state)
        validation = attest.evaluate(attempt_dir, require_full=False)
        if validation["verdict"] != "passed":
            state["state"] = summary["state"] = "failed"
            state["verification_errors"] = validation["errors"]
            for profile in profiles:
                if state["stages"].get(profile, {}).get("state") == "passed":
                    state["stages"][profile]["state"] = "invalid-evidence"
            _write_json(state_path, state)
            _write_json(attempt_dir / "summary.json", summary)
        attest.create_bundle(attempt_dir, attempt_dir / "attestation", require_full=False)
        print(f"  attestation: {attempt_dir / 'attestation/report.md'}")
        _print_result_table(results, color=color)
        final_code = "1;32" if state["state"] == "passed" else "1;31"
        print(
            _paint(
                f"\nFinal: {state['state']} · results={attempt_dir / 'summary.json'}",
                final_code,
                enabled=color,
            )
        )
        return 0 if state["state"] == "passed" and not failures else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        state["state"] = "failed"
        state["failure"] = f"launcher error: {exc}"
        state["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for stage in state["stages"].values():
            if stage.get("state") == "running":
                stage.update(state="failed", failure=state["failure"], finished_utc=state["finished_utc"])
        _write_json(state_path, state)
        _write_json(attempt_dir / "summary.json", {"schema_version": 1, "run_id": run_id,
                    "state": "failed", "failure": state["failure"], "results": [],
                    "failed_workloads": list(attest.expected_workloads(configuration))})
        if not (attempt_dir / "attestation").exists():
            attest.create_bundle(attempt_dir, attempt_dir / "attestation", require_full=False)
        print(state["failure"], file=sys.stderr)
        return 1
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        lock_file.close()


if __name__ == "__main__":
    raise SystemExit(main())
