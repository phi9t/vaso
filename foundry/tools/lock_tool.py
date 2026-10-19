#!/usr/bin/env python3
"""Edit and validate checked-in graph lock files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

for _import_dir in (Path(__file__).parent, Path(__file__).resolve().parent):
    _path = str(_import_dir)
    if _path not in sys.path:
        sys.path.insert(0, _path)

import build_graph
import migration_ledger_check
import spack_to_bazel


SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
TERMINAL_STATUSES = migration_ledger_check.TERMINAL_STATUSES
IN_PROGRESS_STATUSES = migration_ledger_check.IN_PROGRESS_STATUSES


def _read_json(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as f:
            value = json.load(f)
    except FileNotFoundError as exc:
        raise SystemExit(f"{path}: file does not exist") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{path}: JSON root must be an object")
    return value


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _package_repo(package: str) -> str:
    if package.startswith("vaso_"):
        return package
    return spack_to_bazel.repo_name(_package_name(package))


def _package_name(value: str) -> str:
    if value.startswith("vaso_"):
        return value.removeprefix("vaso_").replace("_", "-")
    if value.startswith("spack_"):
        return value.removeprefix("spack_").replace("_", "-")
    return value


def _label_repo(label: str) -> str | None:
    if not label.startswith("@") or "//:" not in label:
        return None
    return label[1:].split("//:", 1)[0]


def _archive_filename(url: str) -> str:
    parsed = urlparse(url)
    name = Path(parsed.path).name
    if not name:
        raise SystemExit(f"{url!r}: source URL has no archive filename")
    return name


def _source_from(urls: list[str], sha256: str) -> dict[str, object]:
    if not SHA256_RE.fullmatch(sha256):
        raise SystemExit(f"sha256 must be 64 hex characters, got {sha256!r}")
    if not urls:
        raise SystemExit("at least one --url is required")
    filename = _archive_filename(urls[0])
    return {
        "filename": filename,
        "sha256": sha256.lower(),
        "urls": urls,
    }


def _node_sources(node: dict) -> list[dict]:
    raw = node.get("source")
    if raw is None:
        return []
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [entry for entry in raw if isinstance(entry, dict)]
    return []


def _default_repo_root(lock_paths: list[Path]) -> Path:
    if lock_paths:
        first = lock_paths[0].resolve()
        return first.parent.parent if first.parent.name == "locks" else first.parent
    cwd = Path.cwd()
    return cwd if (cwd / "locks").is_dir() else Path(__file__).resolve().parents[1]


def _default_distdirs(repo_root: Path) -> list[Path]:
    paths: list[Path] = []
    for env_name in ("VASO_LOCK_DISTDIR", "VASO_SOURCE_DISTDIR"):
        value = os.environ.get(env_name)
        if value:
            paths.append(Path(value))
    estate = os.environ.get("VASO_ESTATE_ROOT")
    if estate:
        source_root = Path(estate) / "sources"
        for child in ("native", "pytorch", "triton", "jax", "bazel"):
            paths.append(source_root / child / "distdir")
    paths.append(repo_root / "distdir")
    return paths


def _lock_paths_from_args(paths: list[Path], repo_root: Path) -> list[Path]:
    if paths:
        return [path if path.is_absolute() else Path.cwd() / path for path in paths]
    lock_dir = repo_root / "locks"
    return sorted(lock_dir.glob("*.lock.json"))


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_ledger(path: Path | None) -> dict[str, dict]:
    if path is None:
        return {}
    ledger = _read_json(path)
    packages = ledger.get("packages", {})
    if not isinstance(packages, dict):
        raise SystemExit(f"{path}: packages must be an object")
    return {str(name): value for name, value in packages.items() if isinstance(value, dict)}


def _native_override_labels(overrides: dict) -> dict[str, list[str]]:
    labels: dict[str, list[str]] = {}
    for key, label in overrides.get("native", {}).items():
        if isinstance(key, str) and isinstance(label, str):
            labels.setdefault(label, []).append(key)
    return labels


def _validate_source(
    *,
    lock_path: Path,
    repo: str,
    node: dict,
    source: dict,
    distdirs: list[Path],
) -> list[str]:
    errors: list[str] = []
    package = node.get("package", repo)
    version = node.get("version", "")
    sha256 = source.get("sha256")
    if not isinstance(sha256, str) or not SHA256_RE.fullmatch(sha256):
        return [f"{lock_path}: {repo}: source sha256 must be 64 hex characters"]
    urls = source.get("urls", [])
    if not isinstance(urls, list) or not all(isinstance(url, str) and url for url in urls):
        errors.append(f"{lock_path}: {repo}: source urls must be a non-empty string list")
    filename = source.get("filename")
    if filename is None and urls:
        filename = _archive_filename(str(urls[0]))
    if not isinstance(filename, str) or not filename or "/" in filename or filename in {".", ".."}:
        errors.append(f"{lock_path}: {repo}: source filename must be a plain archive name")
        return errors
    if not distdirs:
        errors.append(f"{lock_path}: {repo}: no distdir configured for source {filename}")
        return errors

    found = [distdir / filename for distdir in distdirs if (distdir / filename).is_file()]
    if not found:
        errors.append(
            f"{lock_path}: {package}@{version} source {filename} is missing from configured distdir"
        )
        return errors
    for candidate in found:
        actual = _hash_file(candidate)
        if actual == sha256.lower():
            return errors
    actuals = ", ".join(f"{path}:{_hash_file(path)}" for path in found)
    errors.append(
        f"{lock_path}: {package}@{version} source {filename} sha256 mismatch: "
        f"expected {sha256.lower()}, got {actuals}"
    )
    return errors


def validate_lock(
    lock_path: Path,
    *,
    ledger_packages: dict[str, dict],
    overrides_doc: dict,
    distdirs: list[Path],
) -> list[str]:
    lock = _read_json(lock_path)
    errors: list[str] = []
    if lock.get("schema_version") != 1:
        errors.append(f"{lock_path}: schema_version must be 1")
    packages = lock.get("packages")
    if not isinstance(packages, dict) or not packages:
        return errors + [f"{lock_path}: packages must be a non-empty object"]
    root = lock.get("root")
    if not isinstance(root, str) or root not in packages:
        errors.append(f"{lock_path}: root {root!r} is not present in packages")

    native_overrides = overrides_doc.get("native", {})
    provided_versions = overrides_doc.get("provided_versions", {})
    known_version_substitutions = overrides_doc.get("known_version_substitutions", [])
    labels_by_override = _native_override_labels(overrides_doc)
    provider_nodes: list[dict] = []
    topo_nodes: list[dict] = []

    for repo, raw_node in sorted(packages.items()):
        if not isinstance(repo, str) or not isinstance(raw_node, dict):
            errors.append(f"{lock_path}: {repo}: package record must be an object")
            continue
        node = raw_node
        package = node.get("package")
        version = node.get("version")
        if not isinstance(package, str) or not package:
            errors.append(f"{lock_path}: {repo}: package must be a non-empty string")
            continue
        if not isinstance(version, str) or not version:
            errors.append(f"{lock_path}: {repo}: version must be a non-empty string")
        expected_repo = spack_to_bazel.repo_name(package)
        if repo != expected_repo:
            errors.append(f"{lock_path}: {repo}: package {package!r} belongs in {expected_repo}")

        ledger_entry = ledger_packages.get(package)
        status = ledger_entry.get("status") if ledger_entry else None
        if ledger_packages and ledger_entry is None:
            errors.append(f"{lock_path}: {repo}: unknown package record {package!r}")
        elif status in IN_PROGRESS_STATUSES:
            errors.append(f"{lock_path}: {package}: ledger status {status!r} is non-terminal")
        elif status is not None and status not in TERMINAL_STATUSES:
            errors.append(f"{lock_path}: {package}: ledger status {status!r} is unknown")

        build = node.get("build", "spack")
        if build != "native":
            errors.append(f"{lock_path}: {repo}: checked-in profile locks require build='native', got {build!r}")
        native_prefix = node.get("native_prefix")
        if not isinstance(native_prefix, str) or not native_prefix:
            errors.append(f"{lock_path}: {repo}: missing native provider label")
        elif not native_prefix.endswith("//:lib") or _label_repo(native_prefix) is None:
            errors.append(f"{lock_path}: {repo}: native provider label must be an external :lib target")
        elif native_prefix not in labels_by_override:
            errors.append(f"{lock_path}: {repo}: unknown native provider label {native_prefix!r}")
        else:
            expected = spack_to_bazel.native_override_for(package, str(version or ""), native_overrides)
            if expected != native_prefix:
                errors.append(
                    f"{lock_path}: {repo}: native_prefix {native_prefix!r} does not match "
                    f"native_overrides.json for {package}@{version}: {expected!r}"
                )

        for field in ("link_deps", "link_libs", "include_dirs"):
            if not isinstance(node.get(field), list) or not all(isinstance(value, str) for value in node.get(field, [])):
                errors.append(f"{lock_path}: {repo}: {field} must be a string list")
        link_deps = node.get("link_deps", [])
        if isinstance(link_deps, list):
            if link_deps != sorted(set(link_deps)):
                errors.append(f"{lock_path}: {repo}: link_deps must be sorted and unique")
            for dep in link_deps:
                if dep not in packages:
                    errors.append(f"{lock_path}: {repo}: link_dep {dep} is missing from packages")

        for source in _node_sources(node):
            errors.extend(
                _validate_source(
                    lock_path=lock_path,
                    repo=repo,
                    node=node,
                    source=source,
                    distdirs=distdirs,
                )
            )

        topo_nodes.append(
            {
                "hash": repo,
                "name": package,
                "dependencies": [
                    {"hash": dep, "name": dep, "parameters": {"deptypes": ["link"]}}
                    for dep in link_deps
                    if isinstance(dep, str)
                ],
            }
        )
        provider_nodes.append(
            {
                "name": package,
                "package": package,
                "version": version or "",
            }
        )
        build_system_name = node.get("build_system")
        if build_system_name is not None and build_system_name not in build_graph.BUILD_SYSTEM_STRATEGY:
            errors.append(f"{lock_path}: {repo}: unknown build_system {build_system_name!r}")

    try:
        build_graph.topo_order(topo_nodes, {node["hash"]: node for node in topo_nodes})
    except SystemExit as exc:
        errors.append(f"{lock_path}: dependency graph is not acyclic: {exc}")

    if native_overrides and isinstance(provided_versions, dict):
        errors.extend(
            f"{lock_path}: native provider version mismatch: {error}"
            for error in spack_to_bazel.native_provider_version_errors(
                provider_nodes,
                native_overrides,
                provided_versions,
                known_version_substitutions if isinstance(known_version_substitutions, list) else [],
                report_stale=False,
            )
        )

    return errors


def validate_locks(
    lock_paths: list[Path],
    *,
    ledger_path: Path | None,
    native_overrides_path: Path | None,
    distdirs: list[Path],
) -> list[str]:
    ledger_packages = _load_ledger(ledger_path)
    overrides_doc = _read_json(native_overrides_path) if native_overrides_path is not None else {}
    errors: list[str] = []
    for lock_path in lock_paths:
        errors.extend(
            validate_lock(
                lock_path,
                ledger_packages=ledger_packages,
                overrides_doc=overrides_doc,
                distdirs=distdirs,
            )
        )
    return errors


def _load_lock_for_edit(path: Path) -> dict:
    lock = _read_json(path)
    if not isinstance(lock.get("packages"), dict):
        raise SystemExit(f"{path}: packages must be an object")
    return lock


def _edit_lock_paths(lock_args: list[Path], repo_root_arg: Path | None) -> list[Path]:
    repo_root = repo_root_arg.resolve() if repo_root_arg else _default_repo_root(lock_args)
    paths = _lock_paths_from_args(lock_args, repo_root)
    if not paths:
        raise SystemExit(f"{repo_root / 'locks'}: no *.lock.json files found")
    return paths


def _find_existing_repo(lock: dict, package: str) -> str:
    packages = lock["packages"]
    repo = _package_repo(package)
    if repo in packages:
        return repo
    normalized = _package_name(package)
    matches = [key for key, node in packages.items() if node.get("package") == normalized]
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise SystemExit(f"{package}: multiple lock records match; use the lock repo key")
    raise SystemExit(f"{package}: package is not present in lock")


def _normalize_deps(deps: list[str]) -> list[str]:
    return sorted({_package_repo(dep) for dep in deps})


def bump(args: argparse.Namespace) -> int:
    edited: list[Path] = []
    for path in _edit_lock_paths(args.lock, args.repo_root):
        lock = _load_lock_for_edit(path)
        try:
            repo = _find_existing_repo(lock, args.package)
        except SystemExit:
            if args.lock:
                raise
            continue
        node = lock["packages"][repo]
        node["version"] = args.version
        node["source"] = _source_from(args.url, args.sha256)
        _write_json(path, lock)
        edited.append(path)
    if not edited:
        raise SystemExit(f"{args.package}: package is not present in any selected lock")
    print(f"bumped {args.package} in {len(edited)} lock(s)")
    return 0


def add(args: argparse.Namespace) -> int:
    package = _package_name(args.package)
    native_label = args.native_label or f"@{package.replace('-', '_')}_native//:lib"
    edited: list[Path] = []
    for path in _edit_lock_paths(args.lock, args.repo_root):
        lock = _load_lock_for_edit(path)
        repo = spack_to_bazel.repo_name(package)
        if repo in lock["packages"]:
            if args.lock:
                raise SystemExit(f"{package}: package already exists in {path}")
            continue
        node = {
            "build": "native",
            "include_dirs": sorted(set(args.include_dir)),
            "link_deps": _normalize_deps(args.dep),
            "link_libs": sorted(set(args.link_lib)),
            "native_prefix": native_label,
            "package": package,
            "prefix": args.prefix,
            "source": _source_from(args.url, args.sha256),
            "spack_hash": args.spack_hash or hashlib.sha256(
                f"{package}@{args.version}".encode("utf-8")
            ).hexdigest()[:32],
            "version": args.version,
        }
        lock["packages"][repo] = node
        lock["packages"] = dict(sorted(lock["packages"].items()))
        _write_json(path, lock)
        edited.append(path)
    if not edited:
        raise SystemExit(f"{package}: package already exists in all selected locks")
    print(f"added {package} to {len(edited)} lock(s)")
    return 0


def remove(args: argparse.Namespace) -> int:
    edited: list[Path] = []
    for path in _edit_lock_paths(args.lock, args.repo_root):
        lock = _load_lock_for_edit(path)
        try:
            repo = _find_existing_repo(lock, args.package)
        except SystemExit:
            if args.lock:
                raise
            continue
        dependents = sorted(
            parent
            for parent, node in lock["packages"].items()
            if parent != repo and repo in node.get("link_deps", [])
        )
        if dependents and not args.force:
            raise SystemExit(f"{args.package}: package is still required in {path} by: {', '.join(dependents)}")
        del lock["packages"][repo]
        if lock.get("root") == repo:
            lock["root"] = sorted(lock["packages"])[0] if lock["packages"] else ""
        _write_json(path, lock)
        edited.append(path)
    if not edited:
        raise SystemExit(f"{args.package}: package is not present in any selected lock")
    print(f"removed {args.package} from {len(edited)} lock(s)")
    return 0


def validate(args: argparse.Namespace) -> int:
    repo_root = args.repo_root.resolve() if args.repo_root else _default_repo_root(args.locks)
    lock_paths = _lock_paths_from_args(args.locks, repo_root)
    if not lock_paths:
        raise SystemExit(f"{repo_root / 'locks'}: no *.lock.json files found")
    ledger_path = args.ledger or repo_root / "migration_ledger.json"
    native_overrides_path = args.native_overrides or repo_root / "native_overrides.json"
    distdirs = args.distdir if args.distdir else _default_distdirs(repo_root)
    errors = validate_locks(
        lock_paths,
        ledger_path=ledger_path,
        native_overrides_path=native_overrides_path,
        distdirs=distdirs,
    )
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        return 1
    print(f"validated {len(lock_paths)} lock(s)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    validate_parser = sub.add_parser("validate", help="validate checked-in lock files")
    validate_parser.add_argument("locks", type=Path, nargs="*", help="lock files; defaults to locks/*.lock.json")
    validate_parser.add_argument("--repo-root", type=Path)
    validate_parser.add_argument("--ledger", type=Path)
    validate_parser.add_argument("--native-overrides", type=Path)
    validate_parser.add_argument("--distdir", type=Path, action="append", default=[])
    validate_parser.set_defaults(func=validate)

    bump_parser = sub.add_parser("bump", help="bump an existing package source/version")
    bump_parser.add_argument("package")
    bump_parser.add_argument("--repo-root", type=Path)
    bump_parser.add_argument("--lock", type=Path, action="append", default=[])
    bump_parser.add_argument("--version", required=True)
    bump_parser.add_argument("--url", action="append", required=True)
    bump_parser.add_argument("--sha256", required=True)
    bump_parser.set_defaults(func=bump)

    add_parser = sub.add_parser("add", help="add a native package record")
    add_parser.add_argument("package")
    add_parser.add_argument("--repo-root", type=Path)
    add_parser.add_argument("--lock", type=Path, action="append", default=[])
    add_parser.add_argument("--version", required=True)
    add_parser.add_argument("--url", action="append", required=True)
    add_parser.add_argument("--sha256", required=True)
    add_parser.add_argument("--native-label")
    add_parser.add_argument("--dep", action="append", default=[])
    add_parser.add_argument("--link-lib", action="append", default=[])
    add_parser.add_argument("--include-dir", action="append", default=[])
    add_parser.add_argument("--prefix", default="")
    add_parser.add_argument("--spack-hash", default="")
    add_parser.set_defaults(func=add)

    remove_parser = sub.add_parser("remove", help="remove a package record")
    remove_parser.add_argument("package")
    remove_parser.add_argument("--repo-root", type=Path)
    remove_parser.add_argument("--lock", type=Path, action="append", default=[])
    remove_parser.add_argument("--force", action="store_true")
    remove_parser.set_defaults(func=remove)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
