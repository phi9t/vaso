#!/usr/bin/env python3
"""Provision and verify pinned Foundry tools in an immutable rootfs.

Acquisition is explicit (--fetch). Installation only accepts a fresh tools
prefix. --base makes a fresh rootfs with hard-linked, untouched base files;
only new files are installed, so an existing shared rootfs remains unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile


LOCK = Path(__file__).with_name("foundry_tools.lock.json")
PREFIX = "opt/vaso"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def relative(value: str) -> Path:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe tool path: {value}")
    return Path(*path.parts)


def verify(rootfs: Path, lock: Path = LOCK) -> dict:
    manifest = rootfs / PREFIX / "foundry-tools.json"
    if not manifest.is_file():
        raise ValueError(f"missing rootfs tooling manifest: {manifest}; provision with foundry_tools.py")
    report = json.loads(manifest.read_text())
    if report["lock_sha256"] != digest(lock):
        raise ValueError(f"rootfs tooling lock mismatch: {manifest}")
    if not report.get("files"):
        raise ValueError(f"empty rootfs tooling manifest: {manifest}")
    for name, sha in report["files"].items():
        path = rootfs / relative(name)
        if not path.is_file() or path.is_symlink() or digest(path) != sha:
            raise ValueError(f"missing or mismatched rootfs tool: {path}")
    # Tool manifest cannot silently omit an executable selected by the lock.
    for tool in json.loads(lock.read_text())["tools"]:
        if tool["kind"] != "wheel" and tool["path"] not in report["files"]:
            raise ValueError(f"unrecorded rootfs tool: {tool['path']}")
    return report


def provision(rootfs: Path, cache: Path, *, lock: Path = LOCK, fetch: bool = False) -> dict:
    tools = json.loads(lock.read_text())["tools"]
    # Validate every acquired input before modifying the rootfs.
    for tool in tools:
        archive = cache / tool["sha256"]
        if not archive.exists() and fetch:
            cache.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=cache, delete=False) as stream:
                partial = Path(stream.name)
                try:
                    with urllib.request.urlopen(tool["url"], timeout=120) as response:
                        shutil.copyfileobj(response, stream)
                    stream.close()
                    if digest(partial) != tool["sha256"]:
                        raise ValueError(f"download sha256 mismatch: {tool['name']}")
                    partial.replace(archive)
                finally:
                    partial.unlink(missing_ok=True)
        if not archive.is_file() or digest(archive) != tool["sha256"]:
            raise ValueError(f"missing or mismatched pinned input: {tool['name']}: {archive}")
    prefix = rootfs / PREFIX
    if prefix.exists() and any(prefix.iterdir()):
        return verify(rootfs, lock)
    prefix.mkdir(parents=True, exist_ok=True)
    for tool in tools:
        archive = cache / tool["sha256"]
        target = rootfs / relative(tool["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        if tool["kind"] == "binary":
            shutil.copyfile(archive, target)
            target.chmod(0o555)
        elif tool["kind"] == "deb-file":
            with tempfile.TemporaryDirectory(dir=cache) as temp:
                subprocess.run(["dpkg-deb", "-x", str(archive), temp], check=True)
                shutil.copyfile(Path(temp) / relative(tool["member"]), target)
                target.chmod(0o555)
        elif tool["kind"] == "wheel":
            with zipfile.ZipFile(archive) as wheel:
                for name in wheel.namelist():
                    relative(name)
                wheel.extractall(target)
        else:
            raise ValueError(f"unsupported tool kind: {tool['kind']}")
    compat = prefix / "compat-bin"
    compat.mkdir()
    tool_paths = {t["path"] for t in tools}
    for name, executable in (("bazel", "/opt/vaso/bin/bazel-real"), ("bwrap", "/opt/vaso/bin/bwrap"), ("zstd", "/opt/vaso/bin/zstd")):
        if executable.lstrip("/") not in tool_paths:
            continue
        path = compat / name
        path.write_text(f'#!/bin/bash\nexec {executable} "$@"\n')
        path.chmod(0o555)
    report = {
        "schema_version": 1, "lock_sha256": digest(lock),
        "tools": [{"name": t["name"], "version": t["version"], "sha256": t["sha256"]} for t in tools],
        "files": {p.relative_to(rootfs).as_posix(): digest(p)
                  for p in sorted(prefix.rglob("*")) if p.is_file()},
    }
    (prefix / "foundry-tools.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return verify(rootfs, lock)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rootfs", required=True, type=Path)
    parser.add_argument("--base", type=Path, help="clone an existing immutable rootfs to a new --rootfs")
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--lock", type=Path, default=LOCK)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    try:
        if args.base:
            if args.verify:
                parser.error("--base cannot be used with --verify")
            shutil.copytree(args.base, args.rootfs, symlinks=True, copy_function=os.link)
        if args.verify:
            report = verify(args.rootfs, args.lock)
        else:
            if args.cache is None:
                parser.error("--cache is required to provision tooling")
            report = provision(args.rootfs, args.cache, lock=args.lock, fetch=args.fetch)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        parser.exit(2, f"rootfs tooling: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
