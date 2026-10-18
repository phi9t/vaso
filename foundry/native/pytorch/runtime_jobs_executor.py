#!/usr/bin/env python3
"""PyTorch committed-plan launcher with an unkeyed runtime jobs source."""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any


MAX_JOBS_CAP = 96


def fail(message: str) -> None:
    raise SystemExit(message)


def read_runtime_jobs(path: Path) -> str:
    if path.is_file():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            if not value.isdecimal() or int(value) < 1:
                fail(f"{path}: PyTorch runtime jobs must be a positive integer, got {value!r}")
            if int(value) > MAX_JOBS_CAP:
                fail(f"{path}: PyTorch runtime jobs must be <= {MAX_JOBS_CAP}, got {value!r}")
            return value
    reserved = os.environ.get("VASO_ACTION_RESERVED_CPUS", "")
    if reserved:
        if not reserved.isdecimal() or int(reserved) < 1:
            fail(f"VASO_ACTION_RESERVED_CPUS must be a positive integer, got {reserved!r}")
        return str(min(int(reserved), MAX_JOBS_CAP))
    try:
        return str(min(len(os.sched_getaffinity(0)), MAX_JOBS_CAP))
    except AttributeError:
        return str(min(os.cpu_count() or 1, MAX_JOBS_CAP))


def _member_target(destination: Path, member: tarfile.TarInfo) -> Path:
    return destination / member.name


def _check_member(destination: Path, member: tarfile.TarInfo) -> None:
    target = _member_target(destination, member).resolve(strict=False)
    try:
        target.relative_to(destination.resolve(strict=False))
    except ValueError:
        fail(f"source archive member escapes extraction root: {member.name}")
    if member.issym() or member.islnk():
        link_target = Path(member.linkname)
        if link_target.is_absolute():
            fail(f"source archive link escapes extraction root: {member.name}")
        # Symlinks resolve from the member's directory; tar hard-link names are archive-relative.
        base = target.parent if member.issym() else destination
        resolved_link = (base / link_target).resolve(strict=False)
        try:
            resolved_link.relative_to(destination.resolve(strict=False))
        except ValueError:
            fail(f"source archive link escapes extraction root: {member.name}")


def safe_extract_with_zstd(archive: Path, destination: Path, zstd_prefix: Path, fallback: Any) -> Path:
    if archive.name.endswith(".tar.zst") or archive.name.endswith(".tzst"):
        zstd = zstd_prefix / "bin" / "zstd"
        if not zstd.is_file():
            fail(f"native zstd executable is missing: {zstd}")
        destination.mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen(
            [str(zstd), "-dc", str(archive)],
            stdout=subprocess.PIPE,
        )
        assert process.stdout is not None
        try:
            with tarfile.open(fileobj=process.stdout, mode="r|") as tar:
                for member in tar:
                    _check_member(destination, member)
                    tar.extract(member, destination)
        except tarfile.TarError as exc:
            process.kill()
            fail(f"failed to extract source archive {archive}: {exc}")
        finally:
            if process.stdout is not None:
                process.stdout.close()
        returncode = process.wait()
        if returncode != 0:
            fail(f"zstd failed to decompress source archive {archive}: exit code {returncode}")
        return destination
    return fallback(archive, destination)


def load_executor(path: Path):
    parent = str(path.parent.resolve())
    if parent not in sys.path:
        sys.path.insert(0, parent)
    spec = importlib.util.spec_from_file_location("vaso_plan_executor", path)
    if spec is None or spec.loader is None:
        fail(f"could not load plan executor: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executor", type=Path, required=True)
    parser.add_argument("--runtime-jobs-file", type=Path, required=True)
    parser.add_argument("--zstd-prefix", type=Path, required=True)
    parser.add_argument("executor_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.executor_args and args.executor_args[0] == "--":
        args.executor_args = args.executor_args[1:]
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    os.environ["VASO_ACTION_RESERVED_CPUS"] = read_runtime_jobs(args.runtime_jobs_file)
    executor = load_executor(args.executor)
    original_extract = executor.safe_extract_archive

    def patched_extract(archive: Path, destination: Path) -> Path:
        return safe_extract_with_zstd(archive, destination, args.zstd_prefix, original_extract)

    executor.safe_extract_archive = patched_extract
    return int(executor.main(args.executor_args))


if __name__ == "__main__":
    raise SystemExit(main())
