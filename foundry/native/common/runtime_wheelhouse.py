#!/usr/bin/env python3
"""Build release-installable wheels from Bazel-owned Python runtime prefixes."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import zipfile
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

try:
    from native.common.repair_wheel import _repair_dynamic_entries
except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
    from repair_wheel import _repair_dynamic_entries


DIST_INFO_LOCAL_ONLY = frozenset(("direct_url.json", "INSTALLER", "REQUESTED"))


def _read_prefix_file(path: Path) -> Path:
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError(f"prefix file is empty: {path}")
    prefix = Path(value)
    if not prefix.is_dir():
        raise ValueError(f"prefix does not exist: {prefix}")
    return prefix


def _site_packages(prefix: Path) -> Path:
    matches = sorted((prefix / "lib").glob("python*/site-packages"))
    if len(matches) != 1:
        raise ValueError(f"{prefix} must provide exactly one lib/pythonX.Y/site-packages tree")
    return matches[0]


def _dist_info(site_packages: Path) -> Path:
    matches = sorted(site_packages.glob("*.dist-info"))
    if len(matches) != 1:
        raise ValueError(f"{site_packages} must contain exactly one *.dist-info directory")
    return matches[0]


def _metadata(dist_info: Path) -> dict[str, str]:
    message = Parser().parsestr((dist_info / "METADATA").read_text(encoding="utf-8"))
    name = message.get("Name")
    version = message.get("Version")
    if not name or not version:
        raise ValueError(f"{dist_info / 'METADATA'} must contain Name and Version")
    return {"Name": name, "Version": version}


def _wheel_tags(dist_info: Path) -> list[str]:
    tags: list[str] = []
    for line in (dist_info / "WHEEL").read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition(":")
        if sep and key.lower() == "tag" and value.strip():
            tags.append(value.strip())
    if not tags:
        raise ValueError(f"{dist_info / 'WHEEL'} must contain at least one Tag")
    return tags


def _wheel_escape(value: str) -> str:
    return re.sub(r"[^\w\d.]+", "_", value, flags=re.ASCII)


def _wheel_distribution_name(name: str) -> str:
    return re.sub(r"[-.]+", "_", name).lower()


def _record_digest(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode("ascii").rstrip("=")
    return f"sha256={digest}"


def _skip_site_file(path: Path, site_packages: Path, dist_info: Path) -> bool:
    rel = path.relative_to(site_packages)
    if "__pycache__" in rel.parts or path.suffix == ".pyc":
        return True
    try:
        dist_rel = path.relative_to(dist_info)
    except ValueError:
        return False
    if dist_rel.as_posix() == "RECORD":
        return True
    return dist_rel.as_posix() in DIST_INFO_LOCAL_ONLY


def _copy_tree_contents(source: Path, dest: Path) -> None:
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(source)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def _stage_prefix(
    prefix: Path,
    stage: Path,
    *,
    provider_prefixes: Iterable[Path] = (),
    patchelf: str = "patchelf",
    readelf: str = "readelf",
) -> tuple[str, str, str, PurePosixPath]:
    site_packages = _site_packages(prefix)
    dist_info = _dist_info(site_packages)
    metadata = _metadata(dist_info)
    tags = _wheel_tags(dist_info)
    provider_prefix_list = list(provider_prefixes)

    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    for path in sorted(site_packages.rglob("*")):
        if not path.is_file() or _skip_site_file(path, site_packages, dist_info):
            continue
        rel = path.relative_to(site_packages)
        target = stage / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)

    bin_dir = prefix / "bin"
    if bin_dir.is_dir():
        scripts_dir = stage / f"{dist_info.name}.data" / "scripts"
        for path in sorted(bin_dir.iterdir()):
            if not path.is_file():
                continue
            scripts_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, scripts_dir / path.name)

    if provider_prefix_list:
        _repair_dynamic_entries(
            stage,
            PurePosixPath(f"{_wheel_distribution_name(metadata['Name'])}.libs"),
            (),
            provider_prefix_list,
            patchelf,
            readelf,
        )

    record_rel = PurePosixPath(dist_info.name) / "RECORD"
    _write_record(stage, record_rel)
    return metadata["Name"], metadata["Version"], ".".join(tags), record_rel


def _write_record(stage: Path, record_rel: PurePosixPath) -> None:
    rows: list[list[str]] = []
    for path in sorted(stage.rglob("*")):
        if not path.is_file():
            continue
        rel = PurePosixPath(path.relative_to(stage).as_posix())
        if rel == record_rel:
            continue
        data = path.read_bytes()
        rows.append([rel.as_posix(), _record_digest(data), str(len(data))])
    rows.append([record_rel.as_posix(), "", ""])

    record_path = stage / record_rel.as_posix()
    record_path.parent.mkdir(parents=True, exist_ok=True)
    with record_path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)


def _wheel_timestamp() -> tuple[int, int, int, int, int, int]:
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0") or "0")
    epoch = max(epoch, 315532800)
    return time.gmtime(epoch)[:6]


def _write_wheel(stage: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    timestamp = _wheel_timestamp()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as wheel:
        for path in sorted(stage.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(stage).as_posix()
            mode = 0o755 if os.access(path, os.X_OK) else 0o644
            info = zipfile.ZipInfo(rel, timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = mode << 16
            wheel.writestr(info, path.read_bytes())


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_manifest(
    path: Path,
    wheels: Iterable[Path],
    prefix_files: Iterable[Path],
    provider_prefix_files: Iterable[Path] = (),
) -> None:
    entries = [
        {
            "name": wheel.name,
            "path": str(wheel),
            "sha256": _file_sha256(wheel),
        }
        for wheel in sorted(wheels)
    ]
    doc: dict[str, Any] = {
        "schema_version": 1,
        "prefix_files": [str(path) for path in prefix_files],
        "provider_prefix_files": [str(path) for path in provider_prefix_files],
        "wheels": entries,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def package_prefix(
    prefix_file: Path,
    *,
    out_dir: Path,
    work_root: Path,
    provider_prefixes: Iterable[Path] = (),
    patchelf: str = "patchelf",
    readelf: str = "readelf",
) -> Path:
    prefix = _read_prefix_file(prefix_file)
    stage = work_root / prefix_file.name
    name, version, tags, _record_rel = _stage_prefix(
        prefix,
        stage,
        provider_prefixes=provider_prefixes,
        patchelf=patchelf,
        readelf=readelf,
    )
    wheel_name = f"{_wheel_escape(name)}-{_wheel_escape(version)}-{tags}.whl"
    output = out_dir / wheel_name
    _write_wheel(stage, output)
    return output


def build_wheelhouse(
    prefix_files: Iterable[Path],
    *,
    out_dir: Path,
    manifest_out: Path | None = None,
    provider_prefix_files: Iterable[Path] = (),
    patchelf: str = "patchelf",
    readelf: str = "readelf",
) -> list[Path]:
    prefix_file_list = list(prefix_files)
    if not prefix_file_list:
        raise ValueError("at least one --prefix-file is required")
    provider_prefix_file_list = list(provider_prefix_files)
    provider_prefixes = [_read_prefix_file(prefix_file) for prefix_file in provider_prefix_file_list]
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".runtime-wheelhouse-", dir=out_dir.parent) as raw_tmp:
        work_root = Path(raw_tmp)
        wheels = [
            package_prefix(
                prefix_file,
                out_dir=out_dir,
                work_root=work_root,
                provider_prefixes=provider_prefixes,
                patchelf=patchelf,
                readelf=readelf,
            )
            for prefix_file in prefix_file_list
        ]
    wheels = sorted(wheels)
    if manifest_out is not None:
        _write_manifest(manifest_out, wheels, prefix_file_list, provider_prefix_file_list)
    return wheels


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix-file", type=Path, action="append", default=[])
    parser.add_argument("--provider-prefix-file", type=Path, action="append", default=[])
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--manifest-out", type=Path)
    parser.add_argument("--patchelf", default=os.environ.get("PATCHELF", "patchelf"))
    parser.add_argument("--readelf", default=os.environ.get("READELF", "readelf"))
    args = parser.parse_args(argv)

    for wheel in build_wheelhouse(
        args.prefix_file,
        out_dir=args.out_dir,
        manifest_out=args.manifest_out,
        provider_prefix_files=args.provider_prefix_file,
        patchelf=args.patchelf,
        readelf=args.readelf,
    ):
        print(wheel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
