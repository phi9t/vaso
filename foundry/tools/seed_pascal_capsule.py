#!/usr/bin/env python3
"""Acquire optional public transfer seeds for an existing Pascal capsule.

This does not construct, validate, or admit a capsule. Reconcile the result with
the authoritative capsule using rsync --ignore-times before any execution.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import time
from typing import Any
import urllib.parse
import urllib.request
import zipfile

PREFIXES = {"cuda": "opt/vaso/cuda", "llvm": "opt/vaso/pascal/llvm"}
DEFAULT_LOCK = Path(__file__).resolve().parents[1] / "native/pascal/public-seed-lock.json"
DOWNLOAD_HOSTS = {
    "files.pythonhosted.org",
    "developer.download.nvidia.com",
    "oaitriton.blob.core.windows.net",
}


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe relative path: {value!r}")
    return path


def archives(lock: dict[str, Any]) -> list[dict[str, Any]]:
    result = list(lock["nvidia"]) + [lock["llvm"]]
    result += [
        dict(item, url=lock["cuda_debs"]["base"] + item["filename"])
        for item in lock["cuda_debs"]["packages"]
    ]
    seen = set()
    for item in result:
        name = item["filename"]
        if relative_path(name).name != name or name in seen:
            raise ValueError(f"invalid or duplicate archive filename: {name!r}")
        seen.add(name)
        parsed = urllib.parse.urlparse(item["url"])
        if parsed.scheme != "https" or parsed.hostname not in DOWNLOAD_HOSTS:
            raise ValueError(f"archive must use a supported public HTTPS source: {name}")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(f"archive URL must not contain credentials or query data: {name}")
        if not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
            raise ValueError(f"invalid archive SHA256: {name}")
        if int(item.get("size", item.get("bytes", 0))) <= 0:
            raise ValueError(f"invalid archive size: {name}")
    return result


def create_manifest(rootfs: Path, lock_path: Path, output: Path) -> dict[str, Any]:
    lock = json.loads(lock_path.read_text())
    archives(lock)
    allowed = {}
    for name, prefix in PREFIXES.items():
        source = rootfs / prefix
        if not source.is_dir():
            raise ValueError(f"capsule lacks the selected transfer prefix: {prefix}")
        # This bounded path walk creates the actual transfer mapping. It is not a
        # native build preflight and does not read or hash installed file payloads.
        allowed[name] = sorted(
            str((Path(directory) / filename).relative_to(source))
            for directory, _, filenames in os.walk(source)
            for filename in filenames
        )
    lock["allowed"] = allowed
    lock["purpose"] = "public transfer seeds; authoritative delta reconciliation required"
    atomic_json(output, lock)
    return {"manifest": str(output), "mapped_paths": {k: len(v) for k, v in allowed.items()}}


def acquire(item: dict[str, Any], cache: Path) -> dict[str, Any]:
    target = cache / item["filename"]
    expected_size = int(item.get("size", item.get("bytes", 0)))

    def digest(stream: Any, output: Any = None) -> tuple[str, int]:
        checksum = hashlib.sha256()
        size = 0
        while chunk := stream.read(1 << 20):
            checksum.update(chunk)
            size += len(chunk)
            if output is not None:
                output.write(chunk)
        return checksum.hexdigest(), size

    if target.exists():
        with target.open("rb") as stream:
            actual_sha, actual_size = digest(stream)
        if actual_sha != item["sha256"] or actual_size != expected_size:
            raise ValueError(f"cached archive does not match its pin: {target}")
    else:
        partial = target.with_name(target.name + ".partial")
        if partial.exists():
            raise ValueError(f"retained incomplete acquisition; choose a fresh cache or move {partial}")
        with urllib.request.urlopen(item["url"], timeout=90) as response, partial.open("xb") as output:
            actual_sha, actual_size = digest(response, output)
        if actual_sha != item["sha256"] or actual_size != expected_size:
            raise ValueError(f"downloaded archive does not match its pin: {partial}")
        partial.replace(target)
    return {"filename": target.name, "sha256": actual_sha, "bytes": actual_size, "url": item["url"]}


def seed(manifest: Path, rootfs: Path, cache: Path, receipt: Path, jobs: int) -> dict[str, Any]:
    lock = json.loads(manifest.read_text())
    inputs = archives(lock)
    if not 1 <= jobs <= 8:
        raise ValueError("acquisition jobs must be between 1 and 8")
    allowed = {}
    for name in PREFIXES:
        values = lock["allowed"][name]
        allowed[name] = {str(relative_path(value)) for value in values}
    if rootfs.exists() and any(rootfs.iterdir()):
        raise ValueError("transfer seed destination must be a fresh, empty rootfs directory")
    if cache.resolve().is_relative_to(rootfs.resolve()) or receipt.resolve().is_relative_to(rootfs.resolve()):
        raise ValueError("cache and receipt must be outside the incoming rootfs")
    if receipt.exists():
        raise ValueError("receipt already exists; preserve it and select a new attempt")
    rootfs.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    stage = cache / ("extracted-" + str(time.time_ns()))
    stage.mkdir()
    state: dict[str, Any] = {
        "schema": 1, "state": "acquiring", "started_at": time.time(),
        "archives": [], "seeded_file_writes": 0,
        "requires_authoritative_delta_reconciliation": True,
    }

    def write_receipt() -> None:
        atomic_json(receipt, state)

    def copy(source: Path, name: str, relative: str) -> None:
        target = rootfs / PREFIXES[name] / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target, follow_symlinks=True)
        state["seeded_file_writes"] += 1

    write_receipt()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            for result in pool.map(lambda item: acquire(item, cache), inputs):
                state["archives"].append(result)
                write_receipt()
        state["state"] = "seeding"
        write_receipt()
        debroot = stage / "cuda"
        for item in lock["cuda_debs"]["packages"]:
            subprocess.run(["dpkg-deb", "-x", str(cache / item["filename"]), str(debroot)], check=True)
        sdk = debroot / "usr/local/cuda-12.6"
        for relative in allowed["cuda"]:
            candidate = sdk / relative
            if candidate.is_file():
                copy(candidate, "cuda", relative)
        for item in lock["nvidia"]:
            wheelroot = stage / item["filename"]
            wheelroot.mkdir()
            with zipfile.ZipFile(cache / item["filename"]) as archive:
                for member in archive.infolist():
                    parts = relative_path(member.filename).parts
                    if len(parts) < 4 or parts[0] != "nvidia" or parts[2] not in ("lib", "include", "bin") or member.is_dir():
                        continue
                    destination = wheelroot.joinpath(*parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, destination.open("wb") as output:
                        shutil.copyfileobj(source, output)
            nvidia = wheelroot / "nvidia"
            for provider in nvidia.iterdir() if nvidia.is_dir() else []:
                for kind, subdir in (("lib", "lib64"), ("lib", "targets/x86_64-linux/lib"),
                                     ("include", "include"), ("include", "targets/x86_64-linux/include"),
                                     ("bin", "bin")):
                    source = provider / kind
                    if not source.is_dir():
                        continue
                    files = [path for path in source.iterdir() if path.is_file()]
                    for relative in allowed["cuda"]:
                        if not relative.startswith(subdir + "/"):
                            continue
                        rest = relative[len(subdir) + 1:]
                        candidate = source / rest
                        if not candidate.is_file() and kind == "lib" and ".so" in rest:
                            family = rest.split(".so", 1)[0] + ".so"
                            matches = [path for path in files if path.name == family or path.name.startswith(family + ".")]
                            if len(matches) == 1:
                                candidate = matches[0]
                        if candidate.is_file():
                            copy(candidate, "cuda", relative)
        llvm = stage / "llvm"
        llvm.mkdir()
        with tarfile.open(cache / lock["llvm"]["filename"], "r:gz") as archive:
            archive.extractall(llvm, filter="data")
        prefix = llvm / lock["llvm"]["filename"].removesuffix(".tar.gz")
        if not prefix.is_dir():
            raise ValueError("unexpected pinned LLVM archive prefix")
        for relative in allowed["llvm"]:
            candidate = prefix / relative
            if candidate.is_file():
                copy(candidate, "llvm", relative)
        state.update(state="transfer_seeds_ready", completed_at=time.time())
        write_receipt()
        return state
    except BaseException as error:
        state.update(state="failed", error=str(error), completed_at=time.time())
        write_receipt()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    manifest = commands.add_parser("manifest", allow_abbrev=False)
    manifest.add_argument("--rootfs", type=Path, required=True)
    manifest.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    manifest.add_argument("--output", type=Path, required=True)
    prepare = commands.add_parser("seed", allow_abbrev=False)
    prepare.add_argument("--manifest", type=Path, required=True)
    prepare.add_argument("--rootfs", type=Path, required=True)
    prepare.add_argument("--cache", type=Path, required=True)
    prepare.add_argument("--receipt", type=Path, required=True)
    prepare.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    try:
        if args.command == "manifest":
            result = create_manifest(args.rootfs, args.lock, args.output)
        else:
            result = seed(args.manifest, args.rootfs, args.cache, args.receipt, args.jobs)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"capsule transfer seed: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
