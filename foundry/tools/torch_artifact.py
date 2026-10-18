#!/usr/bin/env python3
"""Package a Vaso PyTorch prefix as a deterministic pinned artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tarfile
import tempfile
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
if str(EXPERIMENT_ROOT) not in sys.path:
    sys.path.insert(0, str(EXPERIMENT_ROOT))

from native.common.repair_wheel import repair_prefix_runtime_closure  # noqa: E402


LINES = ("cu129", "cu130")
PYTHON_VERSION = "3.13"
TORCH_PACKAGE_REL = Path("lib") / f"python{PYTHON_VERSION}" / "site-packages" / "torch"
TORCH_PACKAGE_LIBS_REL = Path("lib") / f"python{PYTHON_VERSION}" / "site-packages" / "torch.libs"


class ArtifactError(ValueError):
    """Invalid torch artifact input or output."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ArtifactError(f"missing JSON input: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ArtifactError(f"invalid JSON input {path}: {exc}") from exc


def source_commit(source_manifest: Path) -> str:
    doc = read_json(source_manifest)
    commit = doc.get("source", {}).get("commit") if isinstance(doc, dict) else None
    if not isinstance(commit, str) or not commit:
        raise ArtifactError(f"{source_manifest} does not contain source.commit")
    return commit


def plan_key(keys: Path, line: str) -> str:
    doc = read_json(keys)
    packages = doc.get("packages", {}) if isinstance(doc, dict) else {}
    package = packages.get(f"torch/{line}/py-torch", {}) if isinstance(packages, dict) else {}
    key = package.get("merkle_key") if isinstance(package, dict) else None
    if not isinstance(key, str) or len(key) != 64:
        raise ArtifactError(f"{keys} does not contain packages['torch/{line}/py-torch'].merkle_key")
    return key


def _has_any(prefix: Path, rels: tuple[Path, ...]) -> bool:
    return any((prefix / rel).exists() for rel in rels)


def validate_torch_prefix(prefix: Path) -> None:
    if not prefix.is_dir():
        raise ArtifactError(f"torch prefix is not a directory: {prefix}")
    package = prefix / TORCH_PACKAGE_REL
    missing = []
    if not package.is_dir():
        missing.append(TORCH_PACKAGE_REL.as_posix() + "/")
    if not _has_any(
        prefix,
        (
            Path("include/torch/extension.h"),
            Path("torch/include/torch/extension.h"),
            TORCH_PACKAGE_REL / "include/torch/extension.h",
        ),
    ):
        missing.append("torch extension headers")
    if not _has_any(
        prefix,
        (
            Path("lib/libtorch.so"),
            Path("torch/lib/libtorch.so"),
            TORCH_PACKAGE_REL / "lib/libtorch.so",
        ),
    ):
        missing.append("libtorch.so")
    if missing:
        raise ArtifactError("torch prefix is missing required path(s): " + ", ".join(missing))


def _tar_paths(prefix: Path) -> list[Path]:
    paths = sorted(prefix.rglob("*"), key=lambda path: path.relative_to(prefix).as_posix())
    for path in paths:
        if path.is_file() or path.is_dir() or path.is_symlink():
            continue
        raise ArtifactError(f"torch prefix contains unsupported file type: {path}")
    return paths


def _normalized_tar_info(tar: tarfile.TarFile, prefix: Path, path: Path) -> tarfile.TarInfo:
    info = tar.gettarinfo(str(path), arcname=path.relative_to(prefix).as_posix())
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.pax_headers = {}
    return info


def write_deterministic_tar(prefix: Path, archive: Path) -> None:
    validate_torch_prefix(prefix)
    archive.parent.mkdir(parents=True, exist_ok=True)
    tmp = archive.with_name("." + archive.name + ".tmp")
    try:
        with tarfile.open(tmp, "w", format=tarfile.PAX_FORMAT, dereference=False) as tar:
            for path in _tar_paths(prefix):
                info = _normalized_tar_info(tar, prefix, path)
                if path.is_file() and not path.is_symlink():
                    with path.open("rb") as handle:
                        tar.addfile(info, handle)
                else:
                    tar.addfile(info)
        os.replace(tmp, archive)
    finally:
        if tmp.exists():
            tmp.unlink()


def _write_json(path: Path, doc: dict[str, str], *, read_only: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    if read_only:
        path.chmod(0o444)


def _manifest_archive_path(archive: Path, estate_root: Path | None) -> str:
    if estate_root is None:
        return str(archive)
    try:
        return archive.relative_to(estate_root.resolve()).as_posix()
    except ValueError:
        return str(archive)


def _temp_parent(out_dir: Path) -> Path:
    for value in (os.environ.get("TEST_TMPDIR"), os.environ.get("TMPDIR")):
        if value:
            return Path(value)
    estate_root = os.environ.get("VASO_ESTATE_ROOT")
    if estate_root:
        agent = os.environ.get("VASO_AGENT", "torch-artifact")
        path = Path(estate_root) / "agents" / agent / "tmp"
        path.mkdir(parents=True, exist_ok=True)
        return path
    path = out_dir / ".tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _make_tree_owner_writable(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            continue
        try:
            path.chmod(path.stat().st_mode | stat.S_IWUSR)
        except FileNotFoundError:
            continue


def _repairable_prefix_copy(
    prefix: Path,
    *,
    out_dir: Path,
    line: str,
    lock_path: Path,
    provider_metadata: Path | None,
    provider_prefixes: tuple[Path, ...],
    patchelf: str,
    readelf: str,
) -> Path:
    temp_root = Path(tempfile.mkdtemp(prefix=f"torch-artifact-{line}-", dir=_temp_parent(out_dir)))
    repaired = temp_root / "prefix"
    shutil.copytree(prefix, repaired, symlinks=True)
    _make_tree_owner_writable(repaired)
    repair_prefix_runtime_closure(
        repaired,
        package_libs_dir=PurePosixPath(TORCH_PACKAGE_LIBS_REL.as_posix()),
        lock_path=lock_path,
        line=line,
        provider_prefixes=provider_prefixes,
        provider_metadata=provider_metadata,
        include_rootfs_runpaths=False,
        patchelf=patchelf,
        readelf=readelf,
    )
    return repaired


def produce_torch_artifact(
    *,
    prefix: Path,
    line: str,
    out_dir: Path,
    repo_root: Path,
    source_manifest: Path | None = None,
    keys: Path | None = None,
    lock_path: Path | None = None,
    provider_metadata: Path | None = None,
    provider_prefixes: tuple[Path, ...] = (),
    patchelf: str = "patchelf",
    readelf: str = "readelf",
    manifest_out: Path | None = None,
    estate_root: Path | None = None,
) -> dict[str, str]:
    if line not in LINES:
        raise ArtifactError(f"unknown line={line}; expected one of: {', '.join(LINES)}")
    repo_root = repo_root.resolve()
    source_manifest = source_manifest or repo_root / "native" / "pytorch" / "pytorch-v2.14.0-2b3ec348-submodules.json"
    keys = keys or repo_root / "plans" / "keys.json"
    lock_path = lock_path or repo_root / "rootfs" / "cuda_ecosystem.lock.json"
    prefix = prefix.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    repaired_prefix = _repairable_prefix_copy(
        prefix,
        out_dir=out_dir,
        line=line,
        lock_path=lock_path,
        provider_metadata=provider_metadata,
        provider_prefixes=provider_prefixes,
        patchelf=patchelf,
        readelf=readelf,
    )
    tmp_archive = out_dir / f"torch-prefix-{line}.tar"
    try:
        write_deterministic_tar(repaired_prefix, tmp_archive)
    finally:
        shutil.rmtree(repaired_prefix.parent, ignore_errors=True)
    digest = sha256_file(tmp_archive)
    final_archive = out_dir / f"torch-prefix-{line}-{digest}.tar"
    if final_archive.exists():
        if sha256_file(final_archive) != digest:
            raise ArtifactError(f"content-addressed artifact collision: {final_archive}")
        tmp_archive.unlink()
    else:
        os.replace(tmp_archive, final_archive)
    final_archive.chmod(0o444)

    manifest = {
        "archive": _manifest_archive_path(final_archive, estate_root),
        "plan_key": plan_key(keys, line),
        "sha256": digest,
        "source_commit": source_commit(source_manifest),
    }
    _write_json(out_dir / "torch-artifact.json", manifest, read_only=True)
    _write_json(out_dir / "torch-artifact.provenance.json", manifest, read_only=True)
    if manifest_out is not None:
        _write_json(manifest_out, manifest, read_only=False)
    return manifest


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_out_dir(line: str, estate_root: Path | None) -> Path:
    root = estate_root
    if root is None:
        raw = os.environ.get("VASO_ESTATE_ROOT")
        if not raw:
            raise ArtifactError("set VASO_ESTATE_ROOT or pass --out-dir")
        root = Path(raw)
    return root / "vaso" / "artifacts" / "torch" / line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--line", choices=LINES, required=True)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--estate-root", type=Path)
    parser.add_argument("--repo-root", type=Path, default=_default_repo_root())
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--keys", type=Path)
    parser.add_argument("--lock", type=Path)
    parser.add_argument("--provider-metadata", type=Path)
    parser.add_argument("--provider-prefix", type=Path, action="append", default=[])
    parser.add_argument("--patchelf", default=os.environ.get("PATCHELF", "patchelf"))
    parser.add_argument("--readelf", default=os.environ.get("READELF", "readelf"))
    parser.add_argument("--manifest-out", type=Path)
    args = parser.parse_args(argv)

    if args.out_dir is not None and args.estate_root is not None:
        raise ArtifactError("pass only one of --out-dir or --estate-root")
    estate_root = args.estate_root
    if args.out_dir is None and estate_root is None and os.environ.get("VASO_ESTATE_ROOT"):
        estate_root = Path(os.environ["VASO_ESTATE_ROOT"])
    out_dir = args.out_dir or _default_out_dir(args.line, estate_root)
    manifest = produce_torch_artifact(
        prefix=args.prefix,
        line=args.line,
        out_dir=out_dir,
        repo_root=args.repo_root,
        source_manifest=args.source_manifest,
        keys=args.keys,
        lock_path=args.lock,
        provider_metadata=args.provider_metadata,
        provider_prefixes=tuple(args.provider_prefix),
        patchelf=args.patchelf,
        readelf=args.readelf,
        manifest_out=args.manifest_out,
        estate_root=estate_root,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ArtifactError as exc:
        raise SystemExit(str(exc))
