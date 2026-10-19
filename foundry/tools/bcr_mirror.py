#!/usr/bin/env python3
"""Populate the estate-hosted sparse BCR mirror for consumer tests.

This hand-run tool is the consumer harness's network boundary. It reads the
checked-in TSV manifest, verifies each downloaded byte stream by sha256, writes
the sparse registry under sources/bcr/registry/current, stores source archives
by sha under sources/bcr/archives/sha256, and seeds Bazel's
repository-cache/content_addressable/sha256 layout for offline builds.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import shutil
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterable


BCR_ARCHIVE_FILE_URL_PREFIX = "file:///vaso/sources/bcr/archives/sha256"
BCR_REGISTRY_FILE_URL_PREFIX = "file:///vaso/sources/bcr/registry"
VALID_KINDS = {
    "metadata",
    "module",
    "overlay",
    "patch",
    "source-archive",
    "source-json",
    "source-json-original",
}
REPO_LOCAL_KINDS = VALID_KINDS - {"source-archive"}


@dataclasses.dataclass(frozen=True)
class ManifestRow:
    module: str
    version: str
    kind: str
    url: str
    sha256: str
    size: int


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _url_basename(url: str, fallback: str) -> str:
    name = Path(urllib.parse.urlparse(url).path).name
    return name or fallback


def repo_root_for_tool(tool: Path) -> Path:
    """The root that repo:// manifest paths are relative to.

    That is the repository above the foundry workspace; a workspace laid out
    on its own (Bazel runfiles) is its own root.
    """
    workspace = tool.resolve().parents[1]
    return workspace.parent if workspace.name == "foundry" else workspace


def _is_repo_url(url: str) -> bool:
    return urllib.parse.urlparse(url).scheme == "repo"


def _repo_url_path(url: str) -> Path:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "repo":
        raise ValueError(f"not a repo url: {url!r}")
    if parsed.params or parsed.query or parsed.fragment:
        raise ValueError(f"repo url must not include params, query or fragment: {url!r}")
    raw = urllib.parse.unquote((parsed.netloc + parsed.path).lstrip("/"))
    if not raw:
        raise ValueError(f"repo url must include a relative path: {url!r}")
    rel = Path(raw)
    if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts):
        raise ValueError(f"repo url must stay under the repository: {url!r}")
    return rel


def load_manifest(path: Path) -> list[ManifestRow]:
    rows: list[ManifestRow] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if parts == ["module", "version", "kind", "url", "sha256", "size"]:
            continue
        if len(parts) != 6:
            raise ValueError(f"{path}:{lineno}: expected 6 tab-separated fields")
        module, version, kind, url, sha256, size = parts
        if kind not in VALID_KINDS:
            raise ValueError(f"{path}:{lineno}: unsupported kind {kind!r}")
        if _is_repo_url(url):
            if kind not in REPO_LOCAL_KINDS:
                raise ValueError(f"{path}:{lineno}: non-https url {url!r}")
            _repo_url_path(url)
        elif not url.startswith("https://"):
            raise ValueError(f"{path}:{lineno}: non-https url {url!r}")
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValueError(f"{path}:{lineno}: invalid sha256 {sha256!r}")
        if not re.fullmatch(r"[0-9]+", size):
            raise ValueError(f"{path}:{lineno}: invalid size {size!r}")
        rows.append(ManifestRow(module, version, kind, url, sha256, int(size)))
    return rows


def registry_relative_path(row: ManifestRow) -> Path:
    if row.kind == "metadata" and row.module == "registry":
        return Path("bazel_registry.json")
    if row.kind == "metadata":
        return Path("modules") / row.module / "metadata.json"
    if row.kind == "module":
        return Path("modules") / row.module / row.version / "MODULE.bazel"
    if row.kind in {"source-json", "source-json-original"}:
        return Path("modules") / row.module / row.version / "source.json"
    if row.kind == "patch":
        return (
            Path("modules")
            / row.module
            / row.version
            / "patches"
            / _url_basename(row.url, f"{row.module}-{row.version}.patch")
        )
    if row.kind == "overlay":
        return (
            Path("modules")
            / row.module
            / row.version
            / "overlay"
            / _url_basename(row.url, f"{row.module}-{row.version}.overlay")
        )
    raise ValueError(f"row kind {row.kind!r} does not have a registry path")


def archive_store_path(bcr_root: Path, row: ManifestRow) -> Path:
    if row.kind != "source-archive":
        raise ValueError(f"row kind {row.kind!r} is not a source archive")
    return (
        bcr_root
        / "archives"
        / "sha256"
        / row.sha256
        / _url_basename(row.url, f"{row.module}-{row.version}.tar.gz")
    )


def archive_file_url(row: ManifestRow) -> str:
    archive_name = _url_basename(row.url, f"{row.module}-{row.version}.tar.gz")
    return f"{BCR_ARCHIVE_FILE_URL_PREFIX}/{row.sha256}/{archive_name}"


def registry_file_url(registry_name: str, row: ManifestRow) -> str:
    return f"{BCR_REGISTRY_FILE_URL_PREFIX}/{registry_name}/{registry_relative_path(row).as_posix()}"


def rewrite_source_json(source_json: bytes, archive_row: ManifestRow) -> bytes:
    if archive_row.kind != "source-archive":
        raise ValueError("source.json rewrite requires a source-archive row")
    doc = json.loads(source_json.decode("utf-8"))
    archive_url = archive_file_url(archive_row)
    doc.pop("mirror_urls", None)
    if "urls" in doc:
        doc["urls"] = [archive_url]
        doc.pop("url", None)
    else:
        doc["url"] = archive_url
    return (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8")


def seed_repository_cache(
    repository_cache: Path,
    archive: Path,
    sha256: str,
    canonical_ids: Iterable[str] = (),
) -> Path:
    actual = _sha256_file(archive)
    if actual != sha256:
        raise ValueError(f"archive sha256 mismatch: {archive}: expected {sha256}, got {actual}")
    dest = repository_cache / "content_addressable" / "sha256" / sha256 / "file"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists() or _sha256_file(dest) != sha256:
        tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
        shutil.copyfile(archive, tmp)
        os.replace(tmp, dest)
    for canonical_id in canonical_ids:
        marker = dest.parent / f"id-{hashlib.sha256(canonical_id.encode('utf-8')).hexdigest()}"
        marker.touch()
    return dest


def _verify_row_bytes(row: ManifestRow, data: bytes) -> None:
    actual = _sha256_bytes(data)
    if actual != row.sha256:
        raise ValueError(f"{row.url}: expected sha256 {row.sha256}, got {actual}")
    if row.size and len(data) != row.size:
        raise ValueError(f"{row.url}: expected {row.size} bytes, got {len(data)}")


def _fetch(row: ManifestRow, *, allow_network: bool, repo_root: Path) -> bytes:
    if _is_repo_url(row.url):
        source = repo_root / _repo_url_path(row.url)
        data = source.read_bytes()
        _verify_row_bytes(row, data)
        return data
    if not allow_network:
        raise FileNotFoundError(f"offline mode has no cached bytes for {row.url}")
    req = urllib.request.Request(row.url, headers={"User-Agent": "vaso-bcr-mirror/1"})
    with urllib.request.urlopen(req, timeout=120) as response:
        data = response.read()
    _verify_row_bytes(row, data)
    return data


def _materialize_registry_bytes(dest: Path, row: ManifestRow, data: bytes) -> None:
    actual = _sha256_bytes(data)
    if actual != row.sha256:
        raise ValueError(f"{row.url}: expected sha256 {row.sha256}, got {actual}")
    if row.size and len(data) != row.size:
        raise ValueError(f"{row.url}: expected {row.size} bytes, got {len(data)}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


def _materialize_registry_file(dest: Path, row: ManifestRow, *, allow_network: bool, repo_root: Path) -> None:
    if dest.exists() and _sha256_file(dest) == row.sha256:
        return
    _materialize_registry_bytes(dest, row, _fetch(row, allow_network=allow_network, repo_root=repo_root))


def _materialize_archive(dest: Path, row: ManifestRow, *, allow_network: bool, repo_root: Path) -> None:
    if dest.exists() and _sha256_file(dest) == row.sha256:
        return
    data = _fetch(row, allow_network=allow_network, repo_root=repo_root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


def _write_synthetic_metadata(registry_dir: Path, rows: Iterable[ManifestRow]) -> None:
    versions_by_module: dict[str, list[str]] = {}
    explicit_metadata = {
        row.module
        for row in rows
        if row.kind == "metadata" and row.module != "registry"
    }
    for row in rows:
        if row.kind != "module":
            continue
        versions_by_module.setdefault(row.module, [])
        if row.version not in versions_by_module[row.module]:
            versions_by_module[row.module].append(row.version)

    for module, versions in sorted(versions_by_module.items()):
        if module in explicit_metadata:
            continue
        dest = registry_dir / "modules" / module / "metadata.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "homepage": "",
            "maintainers": [],
            "repository": [],
            "versions": sorted(versions),
            "yanked_versions": {},
        }
        dest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def acquire(
    manifest: Path,
    estate_root: Path,
    *,
    repo_root: Path | None = None,
    registry_name: str = "current",
    allow_network: bool = True,
) -> dict[str, int]:
    rows = load_manifest(manifest)
    if repo_root is None:
        repo_root = repo_root_for_tool(Path(__file__))
    bcr_root = estate_root / "sources" / "bcr"
    registry_dir = bcr_root / "registry" / registry_name
    repository_cache = estate_root / "vaso" / "cache" / "bazel" / "repository-cache"
    archive_rows = {
        (row.module, row.version): row
        for row in rows
        if row.kind == "source-archive"
    }

    counts = {"registry": 0, "archives": 0, "repository_cache": 0}
    for row in rows:
        if row.kind == "source-archive":
            archive = archive_store_path(bcr_root, row)
            _materialize_archive(archive, row, allow_network=allow_network, repo_root=repo_root)
            seed_repository_cache(
                repository_cache,
                archive,
                row.sha256,
                canonical_ids=(row.url, archive_file_url(row)),
            )
            counts["archives"] += 1
            counts["repository_cache"] += 1
            continue
        if row.kind in {"source-json", "source-json-original"}:
            archive = archive_rows.get((row.module, row.version))
            if archive is None:
                raise ValueError(f"{row.module}@{row.version} source.json has no source-archive row")
            dest = registry_dir / registry_relative_path(row)
            original_path = bcr_root / "originals" / "sha256" / row.sha256
            if not allow_network and dest.exists():
                rewritten_doc = json.loads(dest.read_text(encoding="utf-8"))
                expected_url = archive_file_url(archive)
                actual_urls = rewritten_doc.get("urls", [rewritten_doc.get("url")])
                if actual_urls != [expected_url] or "mirror_urls" in rewritten_doc:
                    raise ValueError(f"{dest}: expected local-only archive URL {expected_url}")
                if not original_path.is_file() or _sha256_file(original_path) != row.sha256:
                    raise ValueError(f"{dest}: missing or mismatched pinned source.json original; provision the mirror first")
                expected = rewrite_source_json(original_path.read_bytes(), archive)
                if dest.read_bytes() != expected:
                    raise ValueError(f"{dest}: rewritten source.json differs from pinned original")
                counts["registry"] += 1
                continue
            original = _fetch(row, allow_network=allow_network, repo_root=repo_root)
            original_path.parent.mkdir(parents=True, exist_ok=True)
            original_path.write_bytes(original)
            rewritten = rewrite_source_json(original, archive)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(rewritten)
            counts["registry"] += 1
            continue
        if row.kind in {"overlay", "patch"}:
            registry_file = registry_dir / registry_relative_path(row)
            _materialize_registry_file(registry_file, row, allow_network=allow_network, repo_root=repo_root)
            archive = archive_rows.get((row.module, row.version))
            canonical_ids = [row.url, registry_file_url(registry_name, row)]
            if archive is not None:
                canonical_ids.extend([archive.url, archive_file_url(archive)])
            seed_repository_cache(
                repository_cache,
                registry_file,
                row.sha256,
                canonical_ids=canonical_ids,
            )
            counts["registry"] += 1
            counts["repository_cache"] += 1
            continue
        _materialize_registry_file(
            registry_dir / registry_relative_path(row),
            row,
            allow_network=allow_network,
            repo_root=repo_root,
        )
        counts["registry"] += 1

    _write_synthetic_metadata(registry_dir, rows)
    return counts


def _default_manifest() -> Path:
    return Path(__file__).resolve().parents[1] / "examples" / "consumers" / "bcr-manifest.tsv"


def verify(manifest: Path, estate_root: Path, *, registry_name: str = "current") -> int:
    """Check an acquired closure without writing caches or using the network."""
    rows = load_manifest(manifest)
    bcr_root = estate_root / "sources/bcr"
    registry = bcr_root / "registry" / registry_name
    archives = {(r.module, r.version): r for r in rows if r.kind == "source-archive"}
    for row in rows:
        if row.kind == "source-archive":
            path = archive_store_path(bcr_root, row)
            _verify_row_bytes(row, path.read_bytes())
        elif row.kind in {"source-json", "source-json-original"}:
            original = (bcr_root / "originals/sha256" / row.sha256).read_bytes()
            _verify_row_bytes(row, original)
            expected = rewrite_source_json(original, archives[(row.module, row.version)])
            path = registry / registry_relative_path(row)
            if path.read_bytes() != expected:
                raise ValueError(f"{path}: rewritten source.json differs from pinned original")
        else:
            _verify_row_bytes(row, (registry / registry_relative_path(row)).read_bytes())
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=_default_manifest())
    parser.add_argument(
        "--estate-root",
        type=Path,
        default=Path(os.environ["VASO_ESTATE_ROOT"]) if "VASO_ESTATE_ROOT" in os.environ else None,
        help="estate root; defaults to VASO_ESTATE_ROOT",
    )
    parser.add_argument("--registry-name", default="current")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=repo_root_for_tool(Path(__file__)),
        help="root for repo:// manifest artifacts; defaults to the repository root",
    )
    parser.add_argument("--offline", action="store_true", help="verify only already materialized files")
    parser.add_argument("--check", action="store_true", help="read-only verification; no acquisition, cache seeding, or network")
    args = parser.parse_args(argv)

    if args.estate_root is None:
        parser.error("--estate-root is required unless VASO_ESTATE_ROOT is set")
    if args.check:
        count = verify(args.manifest, args.estate_root, registry_name=args.registry_name)
        print(f"bcr mirror verified: {count} pinned inputs")
        return 0
    counts = acquire(
        args.manifest,
        args.estate_root,
        repo_root=args.repo_root,
        registry_name=args.registry_name,
        allow_network=not args.offline,
    )
    print(
        "bcr mirror:"
        f" registry={counts['registry']}"
        f" archives={counts['archives']}"
        f" repository_cache={counts['repository_cache']}"
        f" root={args.estate_root / 'sources' / 'bcr'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
