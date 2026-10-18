#!/usr/bin/env python3
"""Validate the runtime closure of a pinned consumer torch artifact."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
if str(EXPERIMENT_ROOT) not in sys.path:
    sys.path.insert(0, str(EXPERIMENT_ROOT))

from native.common.release_wheel import is_under_rootfs_dir, load_rootfs_lock, rootfs_library_dirs  # noqa: E402


ELF_MAGIC = b"\x7fELF"
DYNAMIC_ENTRY = re.compile(r"\((?P<tag>NEEDED|RUNPATH|RPATH|SONAME)\).*?\[(?P<value>[^\]]*)\]")


@dataclass(frozen=True)
class Finding:
    rule_id: str
    path: str
    message: str


@dataclass(frozen=True)
class DynamicInfo:
    needed: tuple[str, ...]
    rpaths: tuple[str, ...]
    soname: str | None


@dataclass(frozen=True)
class ElfObject:
    logical_path: str
    fs_path: Path
    origin_dir: PurePosixPath


@dataclass(frozen=True)
class ArtifactScan:
    files: frozenset[str]
    basenames: frozenset[str]
    elfs: tuple[ElfObject, ...]


def _temp_parent() -> Path | None:
    value = os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR")
    return Path(value) if value else None


def _is_elf(data: bytes) -> bool:
    return data.startswith(ELF_MAGIC)


def _safe_posix_path(value: str) -> PurePosixPath:
    rel = PurePosixPath(value)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"unsafe artifact member path {value!r}")
    return rel


def _materialize_elf(data: bytes, temp_dir: Path, index: int, logical_path: str) -> Path:
    leaf = PurePosixPath(logical_path.split("!", 1)[-1]).name
    safe_leaf = re.sub(r"[^A-Za-z0-9._+-]+", "_", leaf)[:80] or "elf"
    path = temp_dir / f"{index:06d}-{safe_leaf}"
    path.write_bytes(data)
    return path


def _record_member(
    *,
    rel: PurePosixPath,
    logical_path: str,
    data: bytes | None,
    files: set[str],
    basenames: set[str],
    elfs: list[ElfObject],
    temp_dir: Path,
) -> None:
    rel_text = rel.as_posix()
    files.add(rel_text)
    basenames.add(rel.name)
    if data is not None and _is_elf(data):
        materialized = _materialize_elf(data, temp_dir, len(elfs), logical_path)
        elfs.append(ElfObject(logical_path, materialized, rel.parent))


def _scan_directory(path: Path, temp_dir: Path) -> ArtifactScan:
    files: set[str] = set()
    basenames: set[str] = set()
    elfs: list[ElfObject] = []
    for file_path in sorted(path.rglob("*"), key=lambda candidate: candidate.relative_to(path).as_posix()):
        if not (file_path.is_file() or file_path.is_symlink()):
            continue
        rel = PurePosixPath(file_path.relative_to(path).as_posix())
        data = file_path.read_bytes() if file_path.is_file() else None
        _record_member(
            rel=rel,
            logical_path=f"{path}!{rel.as_posix()}",
            data=data,
            files=files,
            basenames=basenames,
            elfs=elfs,
            temp_dir=temp_dir,
        )
    return ArtifactScan(frozenset(files), frozenset(basenames), tuple(elfs))


def _scan_tar(path: Path, temp_dir: Path) -> ArtifactScan:
    files: set[str] = set()
    basenames: set[str] = set()
    elfs: list[ElfObject] = []
    with tarfile.open(path, "r:*") as archive:
        for member in sorted(archive.getmembers(), key=lambda item: item.name):
            if member.isdir():
                continue
            rel = _safe_posix_path(member.name)
            data = None
            if member.isfile():
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ValueError(f"could not read artifact member {member.name!r}")
                data = extracted.read()
            _record_member(
                rel=rel,
                logical_path=f"{path}!{rel.as_posix()}",
                data=data,
                files=files,
                basenames=basenames,
                elfs=elfs,
                temp_dir=temp_dir,
            )
    return ArtifactScan(frozenset(files), frozenset(basenames), tuple(elfs))


def _scan_input(path: Path, temp_dir: Path) -> ArtifactScan:
    if path.is_dir():
        return _scan_directory(path, temp_dir)
    if path.is_file() and tarfile.is_tarfile(path):
        return _scan_tar(path, temp_dir)
    raise ValueError(f"expected a torch artifact directory or tar archive, got {path}")


def _readelf_dynamic(path: Path, readelf: str) -> DynamicInfo:
    result = subprocess.run(
        [readelf, "-d", str(path)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        return DynamicInfo(needed=(), rpaths=(), soname=None)
    needed: list[str] = []
    rpaths: list[str] = []
    soname: str | None = None
    for line in result.stdout.splitlines():
        match = DYNAMIC_ENTRY.search(line)
        if not match:
            continue
        tag = match.group("tag")
        value = match.group("value")
        if tag == "NEEDED":
            needed.append(value)
        elif tag in {"RUNPATH", "RPATH"}:
            rpaths.extend(part for part in value.split(":") if part)
        elif tag == "SONAME":
            soname = value
    return DynamicInfo(needed=tuple(needed), rpaths=tuple(rpaths), soname=soname)


def _origin_relative(path: str) -> bool:
    return path == "$ORIGIN" or path.startswith("$ORIGIN/") or path == "${ORIGIN}" or path.startswith("${ORIGIN}/")


def _rootfs_path(root: Path, path: PurePosixPath) -> Path:
    if path.is_absolute():
        path = PurePosixPath(*path.parts[1:])
    return root.joinpath(*path.parts)


def _resolve_inside_rootfs(root: Path, path: PurePosixPath) -> Path | None:
    if not path.is_absolute():
        path = PurePosixPath("/") / path
    resolved = PurePosixPath("/")
    pending = list(path.parts[1:])
    seen_symlinks = 0
    while pending:
        part = pending.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if resolved != PurePosixPath("/"):
                resolved = resolved.parent
            continue

        candidate = _rootfs_path(root, resolved / part)
        if candidate.is_symlink():
            seen_symlinks += 1
            if seen_symlinks > 40:
                return None
            target = PurePosixPath(os.readlink(candidate))
            if target.is_absolute():
                pending = list(target.parts[1:]) + pending
            else:
                pending = list((resolved / target).parts[1:]) + pending
            resolved = PurePosixPath("/")
            continue

        resolved = resolved / part
    return _rootfs_path(root, resolved)


def _soname_exists_under_root(soname: str, root: Path, rootfs_dirs: Iterable[str]) -> bool:
    for rootfs_dir in rootfs_dirs:
        candidate = _resolve_inside_rootfs(root, PurePosixPath(rootfs_dir) / soname)
        if candidate is not None and candidate.exists():
            return True
    return False


def _runtime_driver_sonames(lock: dict[str, object], line: str) -> frozenset[str]:
    line_doc = lock.get("lines", {}).get(line, {})
    if not isinstance(line_doc, dict):
        return frozenset()
    runtime_driver = line_doc.get("runtime_driver", {})
    if not isinstance(runtime_driver, dict):
        return frozenset()
    return frozenset(
        PurePosixPath(path).name
        for path in runtime_driver.get("bind_libs", [])
        if isinstance(path, str) and path.startswith("/")
    )


def _needed_is_allowed(
    needed: str,
    scan: ArtifactScan,
    root: Path,
    rootfs_dirs: tuple[str, ...],
    input_sonames: frozenset[str],
    runtime_driver_sonames: frozenset[str],
) -> bool:
    if needed.startswith("/"):
        return is_under_rootfs_dir(needed, rootfs_dirs)
    if "/" in needed:
        return False
    if needed in input_sonames:
        return True
    if needed in runtime_driver_sonames:
        return True
    return _soname_exists_under_root(needed, root, rootfs_dirs)


def check_artifact_runtime_closure(
    path: Path,
    *,
    lock_path: Path,
    line: str,
    root: Path = Path("/"),
    readelf: str = "readelf",
) -> list[Finding]:
    lock = load_rootfs_lock(lock_path)
    rootfs_dirs = rootfs_library_dirs(lock, line)
    runtime_sonames = _runtime_driver_sonames(lock, line)
    findings: list[Finding] = []
    with tempfile.TemporaryDirectory(dir=_temp_parent()) as temp:
        scan = _scan_input(path, Path(temp))
        sonames: set[str] = set(scan.basenames)
        dynamic_by_path: dict[Path, DynamicInfo] = {}
        for elf in scan.elfs:
            dynamic = _readelf_dynamic(elf.fs_path, readelf)
            dynamic_by_path[elf.fs_path] = dynamic
            if dynamic.soname:
                sonames.add(dynamic.soname)
        input_sonames = frozenset(sonames)

        for elf in scan.elfs:
            dynamic = dynamic_by_path[elf.fs_path]
            for rpath in dynamic.rpaths:
                if _origin_relative(rpath):
                    continue
                findings.append(
                    Finding(
                        "bad-rpath",
                        elf.logical_path,
                        f"RPATH/RUNPATH entry {rpath!r} is not $ORIGIN-relative",
                    )
                )
            for needed in dynamic.needed:
                if _needed_is_allowed(needed, scan, root, rootfs_dirs, input_sonames, runtime_sonames):
                    continue
                findings.append(
                    Finding(
                        "bad-needed",
                        elf.logical_path,
                        f"NEEDED entry {needed!r} is not provided by the artifact or declared rootfs/driver set",
                    )
                )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--line", required=True, choices=("cu129", "cu130"))
    parser.add_argument("--lock", type=Path, default=Path("rootfs/cuda_ecosystem.lock.json"))
    parser.add_argument("--root", type=Path, default=Path("/"))
    parser.add_argument("--readelf", default=os.environ.get("READELF", "readelf"))
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args(argv)

    try:
        findings = check_artifact_runtime_closure(
            args.artifact,
            lock_path=args.lock,
            line=args.line,
            root=args.root,
            readelf=args.readelf,
        )
    except (FileNotFoundError, ValueError, tarfile.TarError) as exc:
        print(f"torch artifact runtime closure setup error: {exc}", file=sys.stderr)
        return 2
    for finding in findings:
        print(f"{finding.path}: {finding.message} [{finding.rule_id}]", file=sys.stderr)
    if findings:
        return 1
    print(f"torch artifact runtime closure passed ({args.artifact}, line={args.line})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
