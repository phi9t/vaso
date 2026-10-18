#!/usr/bin/env python3
"""Wait for host GPUs to be free enough for nightly release gates."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


EX_UNAVAILABLE = 75
DEFAULT_MAX_MIB = 8192
DEFAULT_TIMEOUT_SECONDS = 4 * 60 * 60
DEFAULT_POLL_INTERVAL_SECONDS = 60.0


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer, got {raw!r}") from exc


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be a number, got {raw!r}") from exc


def write_json(path: str | None, value: dict[str, Any]) -> None:
    if not path:
        return
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(out)


def run_nvidia_smi(nvidia_smi: str, query: str) -> str:
    cmd = [
        nvidia_smi,
        f"--query-{query}",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"nvidia-smi not found: {nvidia_smi}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"nvidia-smi timed out for query {query}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"nvidia-smi query {query} failed: {detail}")
    return result.stdout


def parse_mib(raw: str) -> int:
    value = raw.strip()
    if not value or value.upper() in {"N/A", "[N/A]", "NOT SUPPORTED", "[NOT SUPPORTED]"}:
        return 0
    value = value.replace("MiB", "").strip()
    try:
        return int(float(value))
    except ValueError as exc:
        raise RuntimeError(f"cannot parse memory value {raw!r}") from exc


def parse_gpu_memory(stdout: str) -> list[dict[str, int]]:
    rows: list[dict[str, int]] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = [part.strip() for part in stripped.split(",")]
        if len(parts) != 2:
            raise RuntimeError(f"unexpected gpu memory row: {line!r}")
        try:
            index = int(parts[0])
        except ValueError as exc:
            raise RuntimeError(f"cannot parse gpu index in row: {line!r}") from exc
        rows.append({"index": index, "used_mib": parse_mib(parts[1])})
    return sorted(rows, key=lambda row: row["index"])


def parse_compute_apps(stdout: str) -> list[dict[str, Any]]:
    apps: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = [part.strip() for part in stripped.split(",")]
        if len(parts) != 3:
            raise RuntimeError(f"unexpected compute app row: {line!r}")
        try:
            pid = int(parts[0])
        except ValueError:
            pid = 0
        apps.append(
            {
                "pid": pid,
                "gpu_uuid": parts[1],
                "used_memory_mib": parse_mib(parts[2]),
            }
        )
    return apps


def sample(nvidia_smi: str, max_mib: int) -> dict[str, Any]:
    gpu_rows = parse_gpu_memory(run_nvidia_smi(nvidia_smi, "gpu=index,memory.used"))
    compute_apps = parse_compute_apps(
        run_nvidia_smi(nvidia_smi, "compute-apps=pid,gpu_uuid,used_memory")
    )
    gpus = []
    blocked = []
    for row in gpu_rows:
        used_mib = row["used_mib"]
        gpu = {
            "index": row["index"],
            "used_mib": used_mib,
            "foreign_mib": used_mib,
            "blocked": used_mib > max_mib,
        }
        gpus.append(gpu)
        if gpu["blocked"]:
            blocked.append(gpu)
    max_used = max((gpu["foreign_mib"] for gpu in gpus), default=0)
    return {
        "schema_version": 1,
        "threshold_mib": max_mib,
        "max_foreign_mib": max_used,
        "gpus": gpus,
        "compute_apps": compute_apps,
        "blocked_gpus": blocked,
        "verdict": "passed" if not blocked else "blocked",
        "message": blocked_message(blocked, max_mib) if blocked else "GPU memory is below threshold.",
    }


def blocked_message(blocked_gpus: list[dict[str, Any]], max_mib: int) -> str:
    if not blocked_gpus:
        return "GPU memory is below threshold."
    details = ", ".join(
        f"gpu {gpu['index']} foreign memory {gpu['foreign_mib']} MiB > {max_mib} MiB"
        for gpu in blocked_gpus
    )
    return f"GPU memory above threshold: {details}"


def add_timing(report: dict[str, Any], started: float, waited: bool) -> dict[str, Any]:
    out = dict(report)
    out["elapsed_seconds"] = round(time.monotonic() - started, 3)
    out["waited"] = waited
    return out


def log_line(args: argparse.Namespace, message: str) -> None:
    print(f"{args.log_prefix}: {message}", flush=True)


def cmd_wait(args: argparse.Namespace) -> int:
    started = time.monotonic()
    deadline = started + max(0.0, args.timeout)
    waited = False
    logged_waiting = False

    while True:
        try:
            report = sample(args.nvidia_smi, args.max_mib)
        except RuntimeError as exc:
            error_report = {
                "schema_version": 1,
                "verdict": "failed",
                "message": str(exc),
                "threshold_mib": args.max_mib,
                "gpus": [],
                "compute_apps": [],
                "blocked_gpus": [],
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "waited": waited,
            }
            write_json(args.json_out, error_report)
            print(f"{args.log_prefix}: error: {exc}", file=sys.stderr, flush=True)
            return 2

        if report["verdict"] == "passed":
            final = add_timing(report, started, waited)
            write_json(args.json_out, final)
            if waited:
                log_line(
                    args,
                    "ready "
                    f"max_foreign_mib={final['max_foreign_mib']} "
                    f"threshold_mib={args.max_mib} "
                    f"elapsed_seconds={final['elapsed_seconds']}",
                )
            return 0

        waited = True
        now = time.monotonic()
        if not logged_waiting:
            log_line(
                args,
                "waiting "
                f"condition={report['message']} "
                f"timeout_seconds={args.timeout} "
                f"poll_interval_seconds={args.poll_interval}",
            )
            logged_waiting = True
        if now >= deadline:
            final = add_timing(report, started, waited)
            write_json(args.json_out, final)
            log_line(
                args,
                "blocked "
                f"condition={final['message']} "
                f"elapsed_seconds={final['elapsed_seconds']}",
            )
            return EX_UNAVAILABLE
        sleep_for = min(max(0.0, args.poll_interval), max(0.0, deadline - now))
        if sleep_for > 0:
            time.sleep(sleep_for)


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(description=__doc__)
    sub = top.add_subparsers(dest="cmd", required=True)

    wait = sub.add_parser("wait")
    wait.add_argument(
        "--max-mib",
        type=int,
        default=env_int("NIGHTLY_GPU_FOREIGN_MAX_MIB", DEFAULT_MAX_MIB),
    )
    wait.add_argument(
        "--timeout",
        type=float,
        default=env_float("NIGHTLY_GPU_WAIT_TIMEOUT", float(DEFAULT_TIMEOUT_SECONDS)),
    )
    wait.add_argument(
        "--poll-interval",
        type=float,
        default=env_float("NIGHTLY_GPU_WAIT_POLL_INTERVAL", DEFAULT_POLL_INTERVAL_SECONDS),
    )
    wait.add_argument("--json-out")
    wait.add_argument("--log-prefix", default="nightly gpu wait")
    wait.add_argument("--nvidia-smi", default=os.environ.get("NIGHTLY_NVIDIA_SMI", "nvidia-smi"))
    wait.set_defaults(func=cmd_wait)

    return top


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
