#!/usr/bin/env python3
"""Prepare a complete immutable rootfs, or verify a matching existing selection.

Registry transport is separate. Restore accepts an available pinned archive;
--fetch authorizes tooling acquisition. Build requires --fetch because the
Docker constructor can acquire base images, apt packages and component sources.
"""
from __future__ import annotations

import argparse
import ctypes
import fcntl
import json
import shlex
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import uuid
import sys

import foundry_tools
import verify_cuda_ecosystem

HERE = Path(__file__).resolve().parent


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def check_tree(destination: Path) -> None:
    # CUDA roots legitimately contain absolute sandbox symlinks. The tooling
    # installer writes only this prefix, whose ancestors must be real owned dirs.
    for path in (destination / "rootfs", destination / "rootfs-bundle.json", destination / "rootfs/opt", destination / "rootfs/opt/vaso", destination / "rootfs-preparation.json"):
        if path.is_symlink():
            raise ValueError(f"unsafe rootfs preparation symlink: {path}")
    prefix = destination / "rootfs/opt/vaso"
    if prefix.exists():
        for parent, directories, files in os.walk(prefix):
            for name in (*directories, *files):
                if (Path(parent) / name).is_symlink():
                    raise ValueError(f"unsafe tooling prefix symlink: {Path(parent) / name}")


def verify(destination: Path, args, selection: dict, attempt: Path, *, receipt: bool) -> None:
    check_tree(destination)
    rootfs = destination / "rootfs"
    manifest = json.loads((destination / "rootfs-bundle.json").read_text())
    if not isinstance(manifest, dict) or not isinstance(manifest.get("lock"), dict):
        raise ValueError("invalid rootfs manifest: expected object with lock object")
    if manifest.get("line") != args.line:
        raise ValueError("rootfs manifest line mismatch")
    if manifest.get("lock", {}).get("sha256") != selection["ecosystem_sha256"]:
        raise ValueError("rootfs ecosystem lock mismatch")
    shell = verify_cuda_ecosystem.resolve_rootfs_path(rootfs, "/bin/sh")
    if not shell.is_file() or not os.access(shell, os.X_OK):
        raise ValueError("rootfs is missing or has no executable /bin/sh")
    foundry_tools.verify(rootfs, args.tools_lock)
    report = verify_cuda_ecosystem.build_report(rootfs, json.loads(args.lock.read_text()), args.line, gpu=False)
    write_json(attempt / "ecosystem-verification.json", report)
    if not report["ok"]:
        raise ValueError("rootfs ecosystem verification failed: " + json.dumps(report["issues"]))
    if receipt and json.loads((destination / "rootfs-preparation.json").read_text()) != selection:
        raise ValueError("rootfs preparation selection mismatch")


def publish(prepared: Path, destination: Path) -> None:
    # Linux renameat2 refuses even an empty directory or symlink created by a
    # writer that does not participate in our cooperative preparation lock.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(prepared), -100, os.fsencode(destination), 1):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))
    parent = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


class PreparationInterrupted(Exception):
    def __init__(self, signum: int):
        self.signum = signum
        super().__init__(f"interrupted by signal {signum}")


def interrupted(signum, _frame) -> None:
    raise PreparationInterrupted(signum)


def command(argv: list[str], attempt: Path, *, lock_fd: int, env=None) -> None:
    with (attempt / "commands.log").open("a") as log:
        log.write("+ " + shlex.join(argv) + "\n")
        log.flush()
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True, pass_fds=(lock_fd,))
        try:
            code = process.wait()
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            raise
        if code:
            raise subprocess.CalledProcessError(code if code > 0 else 128 - code, argv)


def remove_stage(staging: Path) -> None:
    # Extracted directories can be readonly; never chmod their files or follow
    # links into another tree while making our private staging removable.
    if staging.is_dir() and not staging.is_symlink():
        staging.chmod(staging.stat().st_mode | 0o700)
        for parent, directories, _ in os.walk(staging):
            for name in directories:
                directory = Path(parent) / name
                if not directory.is_symlink():
                    directory.chmod(directory.stat().st_mode | 0o700)
        shutil.rmtree(staging)


def reconcile(attempts: Path, destination: Path) -> None:
    # The destination lock is held, including by any surviving construction
    # subprocess. Only reclaim a dead owner's marked private staging directory.
    for path in attempts.glob("*/result.json"):
        previous = json.loads(path.read_text())
        if previous.get("destination") != str(destination) or previous.get("status") != "running":
            continue
        try:
            os.kill(previous["pid"], 0)
            continue
        except ProcessLookupError:
            pass
        staging = Path(previous["staging"]) if previous.get("staging") else None
        if staging and staging.parent == destination.parent and staging.name.startswith(f".{destination.name}.prepare-") and not staging.is_symlink():
            marker = staging / ".preparation-owner"
            if marker.is_file() and marker.read_text() == str(path.parent):
                remove_stage(staging)
        previous.update(status="interrupted", exit_code=137, error="preparation owner exited without a terminal outcome; owned staging reclaimed on retry")
        write_json(path, previous)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="insula/rootfs/prepare.sh", description=__doc__)
    parser.add_argument("mode", choices=("restore", "build"))
    parser.add_argument("--line", choices=("cu129", "cu130"), required=True)
    parser.add_argument("--estate-root", default=os.environ.get("VASO_ESTATE_ROOT") or None)
    parser.add_argument("--dest", type=Path, help="line directory (default: ESTATE/rootfs-lines/LINE)")
    parser.add_argument("--lock", type=Path, default=HERE / "cuda_ecosystem.lock.json")
    parser.add_argument("--tools-lock", type=Path, default=foundry_tools.LOCK)
    parser.add_argument("--tools-cache", type=Path, help="default: ESTATE/sources/foundry-tools")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--digest", help="mandatory archive SHA-256 for restore")
    parser.add_argument("--fetch", action="store_true", help="authorize acquisition; mandatory for Docker construction")
    args = parser.parse_args(argv)
    if not args.estate_root:
        parser.error("--estate-root or VASO_ESTATE_ROOT is required")
    if args.mode == "restore" and (args.bundle is None or not args.digest or len(args.digest) != 64 or any(c not in "0123456789abcdef" for c in args.digest)):
        parser.error("restore requires --bundle and an explicit 64-character --digest")
    if args.mode == "build" and (args.bundle is not None or args.digest is not None):
        parser.error("build does not accept --bundle or --digest")
    estate = Path(args.estate_root).resolve()
    destination = (args.dest or estate / "rootfs-lines" / args.line).absolute()
    args.tools_cache = args.tools_cache or estate / "sources/foundry-tools"
    attempts = estate / "agents/rootfs-prepare"
    attempts.mkdir(parents=True, exist_ok=True)
    attempt = attempts / uuid.uuid4().hex
    attempt.mkdir()
    result = {"mode": args.mode, "line": args.line, "destination": str(destination), "status": "running", "exit_code": None, "attempt": str(attempt), "pid": os.getpid()}
    write_json(attempt / "result.json", result)
    staging = None
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        if any(path.is_symlink() for path in (destination, *destination.parents)):
            raise ValueError(f"symlink destination or parent is unsafe: {destination}")
        if destination == estate or estate.is_relative_to(destination):
            raise ValueError("destination must not contain the estate or attempt records")
        selection = {"mode": args.mode, "line": args.line, "ecosystem_sha256": foundry_tools.digest(args.lock), "tooling_sha256": foundry_tools.digest(args.tools_lock)}
        if args.mode == "restore":
            selection["archive_sha256"] = args.digest
        result["selection"] = selection
        write_json(attempt / "result.json", result)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with (destination.parent / f".{destination.name}.prepare.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            reconcile(attempts, destination)
            if destination.exists():
                verify(destination, args, selection, attempt, receipt=True)
                result["status"] = "reused"
            else:
                staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.prepare-", dir=destination.parent))
                (staging / ".preparation-owner").write_text(str(attempt))
                result["staging"] = str(staging)
                write_json(attempt / "result.json", result)
                prepared = staging / "prepared"
                if args.mode == "restore":
                    command(["bash", str(HERE / "bundle.sh"), "unbundle", "--bundle", str(args.bundle), "--digest", args.digest, "--line", args.line, "--dest", str(prepared)], attempt, lock_fd=lock.fileno())
                else:
                    if not args.fetch:
                        raise ValueError("build requires --fetch: Docker construction acquires pinned images, apt packages and sources")
                    (estate / "rootfs-lines").mkdir(parents=True, exist_ok=True)
                    command(["bash", str(HERE / "build_rootfs.sh"), "--line", args.line, "--lock", str(args.lock), "--dest", str(prepared)], attempt, lock_fd=lock.fileno(), env={**os.environ, "VASO_ESTATE_ROOT": str(estate), "VASO_AGENT_IO_ROOT": str(attempt / "construction")})
                check_tree(prepared)
                prefix = prepared / "rootfs" / foundry_tools.PREFIX
                if prefix.exists() and any(prefix.iterdir()):
                    foundry_tools.verify(prepared / "rootfs", args.tools_lock)
                else:
                    foundry_tools.provision(prepared / "rootfs", args.tools_cache, lock=args.tools_lock, fetch=args.fetch)
                verify(prepared, args, selection, attempt, receipt=False)
                write_json(prepared / "rootfs-preparation.json", selection)
                publish(prepared, destination)
                result["status"] = "prepared"
        result["exit_code"] = 0
    except PreparationInterrupted as exc:
        result.update(status="interrupted", exit_code=128 + exc.signum, error=f"{exc}; retry the same command after interruption")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError, subprocess.CalledProcessError) as exc:
        result.update(status="failed", exit_code=exc.returncode if isinstance(exc, subprocess.CalledProcessError) else 2, error=f"{exc}; logs: {attempt}; use a fresh --dest for mismatched or damaged state; retry after correcting unavailable inputs")
        print(result["error"], file=sys.stderr)
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if staging:
            try:
                remove_stage(staging)
            except OSError as exc:
                result["cleanup_error"] = str(exc)
                if result["exit_code"] == 0:
                    result.update(status="failed", exit_code=2)
        write_json(attempt / "result.json", result)
    print(json.dumps(result, sort_keys=True))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
