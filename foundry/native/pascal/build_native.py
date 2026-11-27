"""Packaged CPU-only Bazel build command for both Pascal native wheels."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid
import sys

from native_step import validate_wheel
from plans import WHEEL_NAMES, WORKSPACE

STATE_ROOT = Path("/workspace/native-build")
BAZEL = "/opt/vaso/pascal/bin/bazel"


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def execution_counts(path: Path) -> dict:
    """Read Bazel's actual spawn log; an absent native spawn is an action hit."""
    if not path.is_file():
        raise ValueError("Bazel execution log missing; cannot establish native execution or cache reuse")
    raw = path.read_text()
    decoder = json.JSONDecoder()
    index = 0
    entries = []
    while index < len(raw):
        while index < len(raw) and raw[index].isspace():
            index += 1
        if index == len(raw):
            break
        value, index = decoder.raw_decode(raw, index)
        entries.extend(value if isinstance(value, list) else [value])
    native = [entry for entry in entries if entry.get("mnemonic") == "PascalNativeWheel"]
    executed = sum(not entry.get("cacheHit", False) for entry in native)
    return {"native_execution": bool(executed), "native_execution_count": executed,
            "cache_hit": executed == 0, "spawn_records": len(native)}


def base_command(target: str) -> list[str]:
    return [
        BAZEL, "--batch", "--nosystem_rc", "--nohome_rc", "--noworkspace_rc",
        "--output_user_root=" + str(STATE_ROOT / "bazel-user"),
        "--output_base=" + str(STATE_ROOT / target / "bazel-output"),
    ]


def build_command(target: str, phase: str, attempt: Path) -> list[str]:
    command = base_command(target) + [
        "build", "--enable_bzlmod=false", "--enable_workspace=true",
        "--spawn_strategy=local", "--genrule_strategy=local", "--repository_disable_download",
        "--jobs=1", "--cpu=k8", "--disk_cache=" + str(STATE_ROOT / "action-cache"),
        "--symlink_prefix=" + str(STATE_ROOT / target / "links" / "bazel-"),
        "--repository_cache=" + str(STATE_ROOT / "repository-cache"),
        "--color=no", "--curses=no", "--show_timestamps",
        "--execution_log_json_file=" + str(attempt / "execution.json"),
        "--build_event_json_file=" + str(attempt / "build-events.json"),
    ]
    if phase == "prepare":
        command.append("--nobuild")
    command.append("//:" + target)
    return command


def wheel_output(target: str) -> Path:
    return STATE_ROOT / target / "bazel-output" / "execroot" / "vaso_pascal" / "bazel-out" / "k8-fastbuild" / "bin" / WHEEL_NAMES[target]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--target", required=True, choices=sorted(WHEEL_NAMES))
    parser.add_argument("--phase", required=True, choices=["prepare", "build", "status"])
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--request-id")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    target_root = STATE_ROOT / args.target
    status_path = target_root / "status.json"
    if args.phase == "status":
        result = json.loads(status_path.read_text()) if status_path.exists() else {
            "schema": "vaso.native-build-result.v1", "target": args.target, "state": "not_started",
        }
        print(json.dumps(result, indent=2))
        return 0
    cpu_count = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    if not 1 <= args.jobs <= cpu_count:
        raise ValueError("--jobs must be between 1 and admitted CPU count " + str(cpu_count))
    request_id = args.request_id or uuid.uuid4().hex
    if len(request_id) > 200 or not request_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for c in request_id):
        raise ValueError("--request-id must contain only letters, digits, dot, dash and underscore")
    target_root.mkdir(parents=True, exist_ok=True)
    (target_root / "links").mkdir(exist_ok=True)
    with (STATE_ROOT / ".native-owner.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        request_path = target_root / "requests" / (request_id + ".json")
        request_path.parent.mkdir(exist_ok=True)
        with request_path.open("x") as reserved:
            json.dump({"request_id": request_id, "target": args.target, "phase": args.phase, "state": "admitted"}, reserved)
        attempt_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
        attempt = target_root / "attempts" / attempt_id
        attempt.mkdir(parents=True)
        (target_root / "jobs.txt").write_text(str(args.jobs) + "\n")
        log = attempt / "build.log"
        receipt = {
            "schema": "vaso.native-build-result.v1", "request_id": request_id,
            "attempt_id": attempt_id, "target": args.target, "phase": args.phase,
            "state": "running", "started_at": stamp(), "backend": "bazel",
            "python_abi": "cp312", "cuda": "12.6", "gpu_arch": "sm_61",
            "jobs": args.jobs, "log": str(log), "attempt": str(attempt),
            "runtime_qualified": False, "artifacts": [],
            "native_execution": False, "cache_hit": False,
        }
        write_json(status_path, receipt)
        write_json(request_path, receipt)
        try:
            command = build_command(args.target, args.phase, attempt)
            write_json(attempt / "invocation.json", {"argv": command, "cwd": WORKSPACE})
            with log.open("w") as output:
                rc = subprocess.call(command, cwd=WORKSPACE, stdout=output, stderr=subprocess.STDOUT)
            receipt["backend_exit_code"] = rc
            receipt["exit_code"] = rc
            receipt["state"] = ("prepared" if args.phase == "prepare" else "built") if rc == 0 else "failed"
            if args.phase == "build":
                receipt.update(execution_counts(attempt / "execution.json"))
                if rc != 0:
                    receipt["cache_hit"] = False
            if rc == 0 and args.phase == "build":
                produced = wheel_output(args.target)
                info = validate_wheel(produced, args.target)
                artifact_dir = target_root / "artifacts" / attempt_id
                artifact_dir.mkdir(parents=True)
                artifact = artifact_dir / info["name"]
                shutil.copyfile(produced, artifact)
                digest = hashlib.sha256()
                with artifact.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(4 << 20), b""):
                        digest.update(chunk)
                receipt["artifacts"] = [dict(info, path=str(artifact), sha256=digest.hexdigest())]
        except BaseException as exc:
            receipt.update(state="failed", exit_code=2, error=str(exc))
            raise
        finally:
            receipt["finished_at"] = stamp()
            write_json(attempt / "receipt.json", receipt)
            write_json(request_path, receipt)
            write_json(status_path, receipt)
            if receipt["state"] == "built":
                write_json(target_root / "last-success.json", receipt)
            print(json.dumps(receipt, indent=2), flush=True)
    return int(receipt["exit_code"])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print("NATIVE_BUILD_ERROR: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
