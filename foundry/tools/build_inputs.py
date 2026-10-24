"""Pinned source discovery and verified storage for Foundry builds.

``selected_assets`` reads the source closure without evaluating repository code.
``rules_python_asset_from_mirror`` adds the runtime pin from a verified BCR archive.
``InputStore`` finds and installs verified blobs; it never launches commands or
fetches from the network. The build workflow owns acquisition and its evidence.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import tarfile
from urllib.parse import urlparse

from bcr_mirror import (
    archive_store_path,
    copy_verified_archive,
    load_manifest,
    seed_repository_cache,
)
from build_selection import PACKAGES
from native_source_versions import module_text_with_includes

SHA = re.compile(r"[0-9a-f]{64}\Z")
REPO = re.compile(r"@([A-Za-z0-9_.+-]+)//")
RULES_PYTHON_PLATFORM = "x86_64-unknown-linux-gnu"


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


@dataclass(frozen=True)
class Asset:
    """A validated archive pin, with an optional single distdir filename.

    URL schemes remain acquisition policy: legacy HTTP pins may be satisfied
    from verified local storage even though the fetch runner requires HTTPS.
    """

    owner: str
    sha256: str
    urls: tuple[str, ...]
    filename: str | None = None  # Nested JAX inputs also need a distdir filename.

    def __post_init__(self):
        if not isinstance(self.sha256, str) or not SHA.fullmatch(self.sha256):
            raise ValueError(f"{self.owner}: invalid SHA-256")
        if (
            not isinstance(self.urls, tuple)
            or not self.urls
            or any(not isinstance(url, str) or not url for url in self.urls)
        ):
            raise ValueError(f"{self.owner}: expected nonempty archive URLs")
        if self.filename is not None and (
            not isinstance(self.filename, str)
            or self.filename in ("", ".", "..")
            or "\x00" in self.filename
            or Path(self.filename).name != self.filename
        ):
            raise ValueError(f"{self.owner}: invalid distdir filename: {self.filename!r}")


def repo_declarations(text: str) -> dict[str, dict]:
    """Read literal repository attributes, never execute the Starlark module."""
    repos = {}
    for stmt in ast.parse(text).body:
        if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
            continue
        attrs = {}
        for keyword in stmt.value.keywords:
            try:
                attrs[keyword.arg] = ast.literal_eval(keyword.value)
            except (ValueError, TypeError):
                # Non-literal unrelated settings do not supply archive identities.
                if keyword.arg and (
                    "sha256" in keyword.arg or keyword.arg.endswith("urls")
                ):
                    raise ValueError(
                        f"non-literal acquisition attribute: {keyword.arg}"
                    )
        if isinstance(attrs.get("name"), str):
            attrs["__refs__"] = [
                node.value
                for node in ast.walk(stmt.value)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            ]
            repos[attrs["name"]] = attrs
    return repos


def repo_assets(name: str, attrs: dict) -> list[Asset]:
    result = []
    for key, value in attrs.items():
        if not key.endswith(("sha256", "sha256s")):
            continue
        stem = key.removesuffix("sha256s").removesuffix("sha256")
        urls = attrs.get(stem + "urls", attrs.get(stem + "url"))
        if urls is None and stem == "patch_":
            urls = attrs.get("patches")
        if isinstance(urls, str):
            urls = [urls]
        if (
            not isinstance(urls, list)
            or not urls
            or not all(isinstance(u, str) for u in urls)
        ):
            raise ValueError(f"{name}: missing literal URLs for {key}")
        if isinstance(value, list):
            if len(value) != len(urls):
                raise ValueError(f"{name}: resource URL/hash count mismatch")
            pairs = [(sha, (url,)) for sha, url in zip(value, urls)]
        else:
            pairs = [(value, tuple(urls))]
        for sha, mirrors in pairs:
            result.append(Asset(name, sha, mirrors))
    return result


def literal_assignment(text: str, name: str):
    """Read one literal top-level assignment from Python-compatible Starlark."""
    for stmt in ast.parse(text).body:
        if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
            continue
        target = stmt.targets[0]
        if isinstance(target, ast.Name) and target.id == name:
            try:
                return ast.literal_eval(stmt.value)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"non-literal Starlark assignment: {name}") from exc
    raise ValueError(f"missing Starlark assignment: {name}")


def rules_python_default(module_text: str) -> str:
    """Return the default minor selected by rules_python's module extension."""
    versions = []
    for stmt in ast.parse(module_text).body:
        if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
            continue
        call = stmt.value
        if not isinstance(call.func, ast.Attribute) or call.func.attr != "defaults":
            continue
        if not isinstance(call.func.value, ast.Name) or call.func.value.id != "python":
            continue
        for keyword in call.keywords:
            if keyword.arg == "python_version":
                value = ast.literal_eval(keyword.value)
                if isinstance(value, str):
                    versions.append(value)
    if len(versions) != 1:
        raise ValueError(
            f"rules_python: expected one default Python version, found {versions}"
        )
    return versions[0]


def rules_python_asset(module_text: str, versions_text: str) -> Asset:
    """Resolve rules_python's default Linux interpreter archive from its source."""
    minor = rules_python_default(module_text)
    mapping = literal_assignment(versions_text, "MINOR_MAPPING")
    versions = literal_assignment(versions_text, "TOOL_VERSIONS")
    base_url = literal_assignment(versions_text, "DEFAULT_RELEASE_BASE_URL")
    try:
        version = mapping[minor]
        release = versions[version]
        sha256 = release["sha256"][RULES_PYTHON_PLATFORM]
        url_templates = release["url"]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            f"rules_python: no {RULES_PYTHON_PLATFORM} runtime for Python {minor}"
        ) from exc
    if isinstance(url_templates, dict):
        url_templates = url_templates[RULES_PYTHON_PLATFORM]
    if isinstance(url_templates, str):
        url_templates = [url_templates]
    if not isinstance(url_templates, list) or not url_templates:
        raise ValueError(f"rules_python: invalid runtime URLs for Python {version}")
    urls = []
    for template in url_templates:
        rendered = template.format(
            platform=RULES_PYTHON_PLATFORM,
            python_version=version,
            build="install_only",
            ext="tar.gz",
        )
        url = rendered if "://" in rendered else base_url.rstrip("/") + "/" + rendered
        if not url.startswith("https://"):
            raise ValueError(f"rules_python: non-HTTPS runtime URL: {url}")
        urls.append(url)
    return Asset(
        f"rules_python:python_{minor}_{RULES_PYTHON_PLATFORM}", sha256, tuple(urls)
    )


def rules_python_asset_from_mirror(repo_root: Path, estate: Path) -> Asset:
    """Read rules_python's generated download pin from its verified BCR archive."""
    dependencies = repo_declarations((repo_root / "foundry/MODULE.bazel").read_text())
    try:
        version = dependencies["rules_python"]["version"]
    except (KeyError, TypeError) as exc:
        raise ValueError("missing literal rules_python dependency version") from exc
    manifest = repo_root / "foundry/examples/consumers/bcr-manifest.tsv"
    rows = [
        row
        for row in load_manifest(manifest)
        if row.module == "rules_python"
        and row.version == version
        and row.kind == "source-archive"
    ]
    if len(rows) != 1:
        raise ValueError(
            f"rules_python@{version}: expected one BCR source archive, found {len(rows)}"
        )
    archive = archive_store_path(estate / "sources/bcr", rows[0])
    if not archive.is_file() or digest(archive) != rows[0].sha256:
        raise ValueError(
            f"rules_python@{version}: missing or mismatched BCR source archive: {archive}"
        )
    with tarfile.open(archive, "r:*") as bundle:
        members = {
            member.name: member for member in bundle.getmembers() if member.isfile()
        }

        version_paths = [
            name for name in members if name.endswith("/python/versions.bzl")
        ]
        if len(version_paths) != 1:
            raise ValueError(
                f"rules_python@{version}: expected one python/versions.bzl in source archive, "
                f"found {len(version_paths)}"
            )
        archive_root = version_paths[0].removesuffix("/python/versions.bzl")

        def read_member(relative: str) -> str:
            name = f"{archive_root}/{relative}"
            if name not in members:
                raise ValueError(
                    f"rules_python@{version}: missing {relative} in source archive"
                )
            handle = bundle.extractfile(members[name])
            if handle is None:
                raise ValueError(f"rules_python@{version}: cannot read {name}")
            return handle.read().decode("utf-8")

        return rules_python_asset(
            read_member("MODULE.bazel"), read_member("python/versions.bzl")
        )


def jax_cccl_asset(repo_root: Path) -> Asset:
    """Read the separately cached CCCL source pin required by JAX preparation."""
    support = (
        repo_root / "foundry/native/jaxlib/committed_plan_support.py"
    ).read_text()
    sha256 = literal_assignment(support, "JAX_LOCAL_CCCL_ARCHIVE_SHA256")
    filename = (
        literal_assignment(support, "JAX_LOCAL_CCCL_ARCHIVE_STRIP_PREFIX") + ".tar.gz"
    )
    tag = filename.removeprefix("cccl-src-").removesuffix(".tar.gz")
    if not tag.startswith("v") or Path(filename).name != filename:
        raise ValueError(f"jaxlib: invalid CCCL release filename: {filename}")
    url = f"https://github.com/NVIDIA/cccl/releases/download/{tag}/{filename}"
    return Asset("jaxlib:cuda_cccl", sha256, (url,))


def selected_assets(repo_root: Path, profiles: list[str]) -> list[Asset]:
    foundry = repo_root / "foundry"
    repos = repo_declarations(module_text_with_includes(foundry))
    pending = []
    for profile in profiles:
        for package in PACKAGES[profile]:
            for path in (foundry / "native" / package).iterdir():
                if path.name == "BUILD.bazel" or path.suffix == ".bzl":
                    pending.extend(REPO.findall(path.read_text()))
    visited = set()
    assets = []
    while pending:
        name = pending.pop()
        if name in visited or name not in repos:
            continue  # Bzlmod libraries are provisioned by bcr_mirror.py.
        visited.add(name)
        attrs = repos[name]
        pending.extend(REPO.findall(repr(attrs)))
        assets.extend(repo_assets(name, attrs))
    if "jax" in profiles:
        manifest = json.loads(
            (foundry / "native/jaxlib/nested_http_archives.json").read_text()
        )
        archives = manifest["archives"]
        if manifest["archive_count"] != len(archives):
            raise ValueError("JAX nested archive count mismatch")
        for item in archives:
            name, sha, filename, urls = (
                item[k] for k in ("name", "sha256", "filename", "urls")
            )
            if (
                not isinstance(urls, list)
                or not urls
                or any(
                    not isinstance(u, str) or not u.startswith("https://")
                    for u in urls
                )
            ):
                raise ValueError(f"invalid nested JAX URLs: {name}")
            assets.append(Asset("jax-nested:" + name, sha, tuple(urls), filename))
        assets.append(jax_cccl_asset(repo_root))
    if not assets:
        raise ValueError("selected build has no declared source closure")
    return list(dict.fromkeys(assets))


class InputStore:
    """Copy verified source blobs into one estate, optionally reading a seed.

    A seed supplies source bytes only. Native output bases, disk caches, and
    action results are never imported. ``install`` verifies the source again
    at the write boundary, including sources supplied by acquisition.
    """

    def __init__(self, estate: Path, *, seed_estate: Path | None):
        self.estate = estate
        self.seed_estate = seed_estate.expanduser().resolve() if seed_estate else None

    def find(self, asset: Asset) -> Path | None:
        cache_rel = (
            Path("vaso/cache/bazel/repository-cache/content_addressable/sha256")
            / asset.sha256
            / "file"
        )
        estates = [self.estate, *([self.seed_estate] if self.seed_estate else [])]
        candidates = [estate / cache_rel for estate in estates]
        # Acquisition publishes only a hash-verified complete download here.
        # Recheck its bytes on lookup; a failed cache install must not force a
        # repeat network fetch. Attempt-specific .part files are never inputs.
        candidates.extend(
            estate / "vaso/cache/build-inputs" / asset.sha256 for estate in estates
        )
        filenames = {Path(urlparse(url).path).name for url in asset.urls}
        if asset.filename:
            filenames.add(asset.filename)
        for estate in estates:
            for sub in ("native", "pytorch", "triton", "jax", "bazel"):
                candidates.extend(
                    estate / "sources" / sub / "distdir" / name
                    for name in filenames
                    if name
                )
        for candidate in candidates:
            if candidate.is_file() and digest(candidate) == asset.sha256:
                return candidate
        return None

    def install(self, asset: Asset, source: Path) -> None:
        cache = self.estate / "vaso/cache/bazel/repository-cache"
        cached = seed_repository_cache(
            cache,
            source,
            asset.sha256,
            canonical_ids=(*asset.urls, " ".join(asset.urls)),
        )
        if asset.filename:
            dest = self.estate / "sources/jax/distdir" / asset.filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.is_file() and digest(dest) != asset.sha256:
                raise RuntimeError(f"conflicting nested archive: {dest}")
            if not dest.exists():
                copy_verified_archive(cached, dest, asset.sha256)
