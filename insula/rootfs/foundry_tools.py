#!/usr/bin/env python3
"""Provision and verify pinned Foundry tools in an immutable rootfs.

Acquisition is explicit (--fetch). Installation only accepts a fresh tools
prefix. --base makes a fresh rootfs with hard-linked, untouched base files;
only new files are installed, so an existing shared rootfs remains unchanged.
The derived rootfs is published after tooling verification; failed attempts
remove their private staging trees and leave the destination available to retry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
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


def tool_relative(value: str) -> Path:
    path = relative(value)
    if path == Path(PREFIX) or not path.is_relative_to(PREFIX):
        raise ValueError(f"tool path must be under {PREFIX}: {value}")
    return path


def verify(rootfs: Path, lock: Path = LOCK) -> dict:
    manifest = rootfs / PREFIX / "foundry-tools.json"
    if not manifest.is_file():
        raise ValueError(
            f"missing rootfs tooling manifest: {manifest}; provision with foundry_tools.py"
        )
    report = json.loads(manifest.read_text())
    if report["lock_sha256"] != digest(lock):
        raise ValueError(f"rootfs tooling lock mismatch: {manifest}")
    if not report.get("files"):
        raise ValueError(f"empty rootfs tooling manifest: {manifest}")
    for name, sha in report["files"].items():
        path = rootfs / tool_relative(name)
        if not path.is_file() or path.is_symlink() or digest(path) != sha:
            raise ValueError(f"missing or mismatched rootfs tool: {path}")
    # Tool manifest cannot silently omit an executable selected by the lock.
    for tool in json.loads(lock.read_text())["tools"]:
        tool_relative(tool["path"])
        if tool["kind"] != "wheel" and tool["path"] not in report["files"]:
            raise ValueError(f"unrecorded rootfs tool: {tool['path']}")
    return report


def _acquire_tools(tools: list[dict], cache: Path, *, fetch: bool) -> None:
    # Validate every acquired input before modifying the rootfs.
    for tool in tools:
        tool_relative(tool["path"])
        if tool["kind"] not in {"binary", "deb-file", "wheel"}:
            raise ValueError(f"unsupported tool kind: {tool['kind']}")
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
            raise ValueError(
                f"missing or mismatched pinned input: {tool['name']}: {archive}"
            )


def provision(
    rootfs: Path, cache: Path, *, lock: Path = LOCK, fetch: bool = False
) -> dict:
    tools = json.loads(lock.read_text())["tools"]
    _acquire_tools(tools, cache, fetch=fetch)
    prefix = rootfs / PREFIX
    if prefix.exists() and any(prefix.iterdir()):
        return verify(rootfs, lock)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=prefix.parent, prefix=".foundry-tools-"
    ) as directory:
        staged_root = Path(directory)
        _install_tools(staged_root, cache, tools, lock)
        # A nonempty prefix is immutable. Directory replacement also refuses
        # a concurrently installed prefix instead of merging into its files.
        os.replace(staged_root / PREFIX, prefix)
    return verify(rootfs, lock)


def _install_tools(rootfs: Path, cache: Path, tools: list[dict], lock: Path) -> dict:
    """Install and verify within a private staging root; never alter the base."""
    prefix = rootfs / PREFIX
    prefix.mkdir(parents=True, exist_ok=True)
    for tool in tools:
        archive = cache / tool["sha256"]
        target = rootfs / tool_relative(tool["path"])
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
            try:
                with zipfile.ZipFile(archive) as wheel:
                    for name in wheel.namelist():
                        relative(name)
                    wheel.extractall(target)
            except zipfile.BadZipFile as exc:
                raise ValueError(
                    f"invalid pinned wheel archive {tool['name']}: {exc}"
                ) from exc
        else:
            raise ValueError(f"unsupported tool kind: {tool['kind']}")
    compat = prefix / "compat-bin"
    compat.mkdir()
    tool_paths = {t["path"] for t in tools}
    for name, executable in (
        ("bazel", "/opt/vaso/bin/bazel-real"),
        ("bwrap", "/opt/vaso/bin/bwrap"),
        ("zstd", "/opt/vaso/bin/zstd"),
    ):
        if executable.lstrip("/") not in tool_paths:
            continue
        path = compat / name
        path.write_text(f'#!/bin/bash\nexec {executable} "$@"\n')
        path.chmod(0o555)
    report = {
        "schema_version": 1,
        "lock_sha256": digest(lock),
        "tools": [
            {"name": t["name"], "version": t["version"], "sha256": t["sha256"]}
            for t in tools
        ],
        "files": {
            p.relative_to(rootfs).as_posix(): digest(p)
            for p in sorted(prefix.rglob("*"))
            if p.is_file()
        },
    }
    (prefix / "foundry-tools.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    return verify(rootfs, lock)


def _make_cloned_directories_writable(root: Path) -> None:
    """Prepare owned staging directories for cleanup without chmod on hard links."""
    if not root.is_dir():
        return
    root.chmod(root.stat().st_mode | stat.S_IRWXU)
    for parent, directories, _ in os.walk(root):
        for name in directories:
            directory = Path(parent) / name
            if not directory.is_symlink():
                directory.chmod(directory.stat().st_mode | stat.S_IRWXU)


def _clone_and_provision(
    base: Path, rootfs: Path, cache: Path, lock: Path, fetch: bool
) -> dict:
    if rootfs.exists() or rootfs.is_symlink():
        raise FileExistsError(f"rootfs destination already exists: {rootfs}")
    # Missing pins must fail before the potentially large base tree is cloned.
    _acquire_tools(json.loads(lock.read_text())["tools"], cache, fetch=fetch)
    rootfs.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=rootfs.parent, prefix=f".{rootfs.name}-provision-"
    ) as directory:
        staged_root = Path(directory) / "rootfs"
        try:
            shutil.copytree(base, staged_root, symlinks=True, copy_function=os.link)
            report = provision(staged_root, cache, lock=lock)
            if rootfs.exists() or rootfs.is_symlink():
                raise FileExistsError(f"rootfs destination already exists: {rootfs}")
            os.replace(staged_root, rootfs)
        except BaseException:
            # TemporaryDirectory may chmod files when cleaning readonly trees.
            # Those files can be hard links to the base, so adjust only our dirs.
            _make_cloned_directories_writable(staged_root)
            raise
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rootfs", required=True, type=Path)
    parser.add_argument(
        "--base", type=Path, help="clone an existing immutable rootfs to a new --rootfs"
    )
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--lock", type=Path, default=LOCK)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    if args.base and args.verify:
        parser.error("--base cannot be used with --verify")
    if not args.verify and args.cache is None:
        parser.error("--cache is required to provision tooling")
    try:
        if args.base and args.rootfs.resolve().is_relative_to(args.base.resolve()):
            parser.error("--rootfs must be outside --base")
        if args.base:
            report = _clone_and_provision(
                args.base, args.rootfs, args.cache, args.lock, args.fetch
            )
        elif args.verify:
            report = verify(args.rootfs, args.lock)
        else:
            report = provision(
                args.rootfs, args.cache, lock=args.lock, fetch=args.fetch
            )
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        parser.exit(2, f"rootfs tooling: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
