#!/usr/bin/env python3
"""Validate release wheel relocatability."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
COMMON_MODULE = EXPERIMENT_ROOT / "native" / "common" / "release_wheel.py"
if str(EXPERIMENT_ROOT) not in sys.path:
    sys.path.insert(0, str(EXPERIMENT_ROOT))

from native.common.release_wheel import is_under_rootfs_dir, load_rootfs_lock, rootfs_library_dirs  # noqa: E402


RULE_IDS = frozenset(
    (
        "bad-needed",
        "bad-rpath",
        "forbidden-path-string",
    )
)

_DYNAMIC_ENTRY = re.compile(r"\((?P<tag>NEEDED|RUNPATH|RPATH|SONAME)\).*?\[(?P<value>[^\]]*)\]")
_ELF_MAGIC = b"\x7fELF"
_FORBIDDEN_STATIC_NEEDLES = (
    b"/vaso/",
    b".vaso-estate",
)
_FORBIDDEN_PATH_MARKERS = (
    b"/output-base",
    b"\\output-base",
    b"output-base/",
    b"/output_base/",
    b"\\output_base\\",
    b"output_base/",
)


@dataclass(frozen=True)
class Finding:
    rule_id: str
    path: str
    message: str


@dataclass(frozen=True)
class ElfObject:
    logical_path: str
    fs_path: Path
    origin_dir: PurePosixPath


@dataclass(frozen=True)
class DynamicInfo:
    needed: tuple[str, ...]
    rpaths: tuple[str, ...]
    soname: str | None


@dataclass(frozen=True)
class ScanInput:
    name: str
    files: frozenset[str]
    basenames: frozenset[str]
    elfs: tuple[ElfObject, ...]
    findings: tuple[Finding, ...]


def _temp_parent() -> Path | None:
    value = os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR")
    return Path(value) if value else None


def _is_elf(data: bytes) -> bool:
    return data.startswith(_ELF_MAGIC)


def _forbidden_needles() -> tuple[bytes, ...]:
    needles = list(_FORBIDDEN_STATIC_NEEDLES) + list(_FORBIDDEN_PATH_MARKERS)
    for name in ("VASO_ESTATE_ROOT", "VASO_BAZEL_OB"):
        value = os.environ.get(name)
        if value:
            path = value.rstrip("/")
            if path:
                needles.append(f"{path}/".encode("utf-8"))
    return tuple(dict.fromkeys(needles))


def _forbidden_finding(logical_path: str, data: bytes) -> Finding | None:
    for needle in _forbidden_needles():
        if needle and needle in data:
            return Finding(
                "forbidden-path-string",
                logical_path,
                f"contains forbidden build path marker {needle.decode('utf-8', errors='replace')!r}",
            )
    return None


def _materialize_elf(data: bytes, temp_dir: Path, index: int, logical_path: str) -> Path:
    digest = hashlib.sha256(logical_path.encode("utf-8")).hexdigest()[:16]
    leaf = PurePosixPath(logical_path.split("!", 1)[-1]).name
    safe_leaf = re.sub(r"[^A-Za-z0-9._+-]+", "_", leaf)[:80] or "elf"
    name = f"{digest}-{safe_leaf}"
    path = temp_dir / f"{index:06d}-{name}"
    path.write_bytes(data)
    return path


def _scan_directory(path: Path, temp_dir: Path) -> ScanInput:
    files: set[str] = set()
    basenames: set[str] = set()
    elfs: list[ElfObject] = []
    findings: list[Finding] = []
    for file_path in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        rel = file_path.relative_to(path).as_posix()
        logical = f"{path}!{rel}"
        data = file_path.read_bytes()
        files.add(rel)
        basenames.add(PurePosixPath(rel).name)
        is_elf = _is_elf(data)
        if not is_elf:
            finding = _forbidden_finding(logical, data)
            if finding is not None:
                findings.append(finding)
        if is_elf:
            materialized = _materialize_elf(data, temp_dir, len(elfs), logical)
            elfs.append(ElfObject(logical, materialized, PurePosixPath(rel).parent))
    return ScanInput(str(path), frozenset(files), frozenset(basenames), tuple(elfs), tuple(findings))


def _scan_wheel(path: Path, temp_dir: Path) -> ScanInput:
    files: set[str] = set()
    basenames: set[str] = set()
    elfs: list[ElfObject] = []
    findings: list[Finding] = []
    with zipfile.ZipFile(path) as wheel:
        for info in sorted(wheel.infolist(), key=lambda item: item.filename):
            if info.is_dir():
                continue
            rel = PurePosixPath(info.filename).as_posix()
            logical = f"{path}!{rel}"
            data = wheel.read(info)
            files.add(rel)
            basenames.add(PurePosixPath(rel).name)
            is_elf = _is_elf(data)
            if not is_elf:
                finding = _forbidden_finding(logical, data)
                if finding is not None:
                    findings.append(finding)
            if is_elf:
                materialized = _materialize_elf(data, temp_dir, len(elfs), logical)
                elfs.append(ElfObject(logical, materialized, PurePosixPath(rel).parent))
    return ScanInput(str(path), frozenset(files), frozenset(basenames), tuple(elfs), tuple(findings))


def _scan_file(path: Path, temp_dir: Path) -> ScanInput:
    data = path.read_bytes()
    is_elf = _is_elf(data)
    finding = None if is_elf else _forbidden_finding(str(path), data)
    elfs = ()
    if is_elf:
        materialized = _materialize_elf(data, temp_dir, 0, str(path))
        elfs = (ElfObject(str(path), materialized, PurePosixPath(".")),)
    return ScanInput(
        str(path),
        frozenset([path.name]),
        frozenset([path.name]),
        elfs,
        tuple([finding] if finding is not None else []),
    )


def _scan_input(path: Path, temp_dir: Path) -> ScanInput:
    if path.is_dir():
        return _scan_directory(path, temp_dir)
    if path.suffix == ".whl":
        return _scan_wheel(path, temp_dir)
    if path.is_file():
        return _scan_file(path, temp_dir)
    raise FileNotFoundError(path)


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
        match = _DYNAMIC_ENTRY.search(line)
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


def _origin_target(path: str, origin_dir: PurePosixPath) -> str | None:
    if path == "$ORIGIN":
        suffix = "."
    elif path.startswith("$ORIGIN/"):
        suffix = path[len("$ORIGIN/") :]
    elif path == "${ORIGIN}":
        suffix = "."
    elif path.startswith("${ORIGIN}/"):
        suffix = path[len("${ORIGIN}/") :]
    else:
        return None
    return (origin_dir / suffix).as_posix()


def _path_exists_under_inputs(rel: str, inputs: Iterable[ScanInput]) -> bool:
    normalized = PurePosixPath(rel).as_posix()
    return any(normalized in item.files for item in inputs)


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
    elf: ElfObject,
    inputs: tuple[ScanInput, ...],
    root: Path,
    rootfs_dirs: tuple[str, ...],
    input_sonames: frozenset[str],
    runtime_driver_sonames: frozenset[str],
) -> bool:
    if needed.startswith("/"):
        return is_under_rootfs_dir(needed, rootfs_dirs)
    if _origin_relative(needed):
        target = _origin_target(needed, elf.origin_dir)
        return target is not None and _path_exists_under_inputs(target, inputs)
    if "/" in needed:
        return False
    if needed in input_sonames:
        return True
    if needed in runtime_driver_sonames:
        return True
    return _soname_exists_under_root(needed, root, rootfs_dirs)


def check_paths(
    paths: Iterable[Path],
    *,
    lock_path: Path,
    line: str,
    root: Path,
    readelf: str = "readelf",
) -> list[Finding]:
    lock = load_rootfs_lock(lock_path)
    rootfs_dirs = rootfs_library_dirs(lock, line)
    runtime_driver_sonames = _runtime_driver_sonames(lock, line)
    findings: list[Finding] = []
    with tempfile.TemporaryDirectory(dir=_temp_parent()) as temp:
        temp_dir = Path(temp)
        inputs = tuple(_scan_input(path, temp_dir) for path in paths)
        findings.extend(finding for item in inputs for finding in item.findings)

        dynamic_by_path: dict[Path, DynamicInfo] = {}
        sonames: set[str] = set()
        for item in inputs:
            sonames.update(item.basenames)
            for elf in item.elfs:
                dynamic = _readelf_dynamic(elf.fs_path, readelf)
                dynamic_by_path[elf.fs_path] = dynamic
                if dynamic.soname:
                    sonames.add(dynamic.soname)
        input_sonames = frozenset(sonames)

        for item in inputs:
            for elf in item.elfs:
                dynamic = dynamic_by_path[elf.fs_path]
                for rpath in dynamic.rpaths:
                    if _origin_relative(rpath) or is_under_rootfs_dir(rpath, rootfs_dirs):
                        continue
                    findings.append(
                        Finding(
                            "bad-rpath",
                            elf.logical_path,
                            f"RPATH/RUNPATH entry {rpath!r} is not $ORIGIN-relative or under the rootfs allowlist",
                        )
                    )
                for needed in dynamic.needed:
                    if _needed_is_allowed(
                        needed,
                        elf,
                        inputs,
                        root,
                        rootfs_dirs,
                        input_sonames,
                        runtime_driver_sonames,
                    ):
                        continue
                    findings.append(
                        Finding(
                            "bad-needed",
                            elf.logical_path,
                            f"NEEDED entry {needed!r} does not resolve inside the inputs or the rootfs allowlist",
                        )
                    )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--line", required=True, choices=("cu129", "cu130"))
    parser.add_argument("--lock", type=Path, default=Path("rootfs/cuda_ecosystem.lock.json"))
    parser.add_argument("--root", type=Path, default=Path("/"))
    parser.add_argument("--readelf", default=os.environ.get("READELF", "readelf"))
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)

    try:
        findings = check_paths(args.paths, lock_path=args.lock, line=args.line, root=args.root, readelf=args.readelf)
    except (FileNotFoundError, ValueError, zipfile.BadZipFile) as exc:
        print(f"relocatability guard setup error: {exc}", file=sys.stderr)
        return 2

    for finding in findings:
        print(f"{finding.path}: {finding.message} [{finding.rule_id}]", file=sys.stderr)
    if findings:
        return 1

    print(f"relocatability guard passed ({len(args.paths)} input(s), line={args.line})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
