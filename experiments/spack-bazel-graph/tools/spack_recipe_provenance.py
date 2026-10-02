#!/usr/bin/env python3
"""Inspect recipe provenance from the Bazel-vendored hermetic Spack repo."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable


BUILD_SYSTEMS = {
    "AutotoolsPackage": "autotools",
    "CMakePackage": "cmake",
    "CudaPackage": "cuda",
    "MakefilePackage": "makefile",
    "PythonPackage": "python",
    "ROCmPackage": "rocm",
}

CLASS_RE = re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\(([^)]*)\):", re.MULTILINE)
VERSION_RE = re.compile(r"^\s*version\(\s*[\"']([^\"']+)[\"']", re.MULTILINE)
VARIANT_RE = re.compile(r"^\s*variant\(\s*[\"']([^\"']+)[\"']", re.MULTILINE)
DEP_RE = re.compile(r"^\s*depends_on\(\s*(?:f)?[\"']([^\"']+)[\"']", re.MULTILINE)


def spack_name_to_dir(name: str) -> str:
    return name.replace("-", "_")


def dir_to_spack_name(path: Path) -> str:
    return path.name.replace("_", "-")


def _sorted_unique(values: Iterable[str]) -> list[str]:
    return sorted(set(values))


def parse_package(path: Path) -> dict:
    package_py = path / "package.py"
    if not package_py.exists():
        return {"present": False}
    text = package_py.read_text(encoding="utf-8")
    class_match = CLASS_RE.search(text)
    bases = []
    if class_match:
        bases = [base.strip().split(".")[-1] for base in class_match.group(2).split(",")]
    build_systems = _sorted_unique(
        mechanism for base, mechanism in BUILD_SYSTEMS.items() if base in bases or base in text
    )
    return {
        "present": True,
        "class": class_match.group(1) if class_match else None,
        "bases": bases,
        "build_systems": build_systems,
        "versions": VERSION_RE.findall(text),
        "variants": _sorted_unique(VARIANT_RE.findall(text)),
        "dependencies": _sorted_unique(DEP_RE.findall(text)),
        "recipe": str(package_py),
    }


def _inherits_builtin_recipe(package: dict) -> bool:
    return any(base.startswith("Builtin") for base in package.get("bases", []))


def _merge_inherited_recipe_metadata(packages: list[dict]) -> dict:
    primary = dict(packages[0])
    primary["recipes"] = [package["recipe"] for package in packages]
    for key in ("build_systems", "versions", "variants", "dependencies"):
        primary[key] = _sorted_unique(
            item
            for package in packages
            for item in package.get(key, [])
        )
    return primary


def _package_root_for_repo(repo_root: Path) -> Path:
    if (repo_root / "repo.yaml").exists() and (repo_root / "packages").is_dir():
        return repo_root / "packages"
    return repo_root


def inspect_recipe_roots(
    package_roots: Iterable[Path],
    *,
    present: Iterable[str],
    absent: Iterable[str],
    search_terms: Iterable[str],
    spack: dict | None = None,
) -> dict:
    roots = [Path(root) for root in package_roots]
    packages: dict[str, dict] = {}
    for name in list(present) + list(absent):
        packages[name] = {"present": False}
        matches = []
        for package_root in roots:
            candidate = parse_package(package_root / spack_name_to_dir(name))
            if candidate.get("present", False):
                matches.append(candidate)
        if matches:
            if len(matches) > 1 and _inherits_builtin_recipe(matches[0]):
                packages[name] = _merge_inherited_recipe_metadata(matches)
            else:
                packages[name] = matches[0]

    all_names = sorted({
        dir_to_spack_name(path)
        for package_root in roots
        if package_root.is_dir()
        for path in package_root.iterdir()
        if path.is_dir()
    })
    search = {
        term: [name for name in all_names if term.replace("-", "_").lower() in name.replace("-", "_").lower()]
        for term in search_terms
    }
    report = {
        "package_root": str(roots[0]) if roots else "",
        "package_roots": [str(root) for root in roots],
        "spack": spack or {},
        "packages": packages,
        "search": search,
    }
    return report


def inspect_recipe_root(
    package_root: Path,
    *,
    present: Iterable[str],
    absent: Iterable[str],
    search_terms: Iterable[str],
    spack: dict | None = None,
) -> dict:
    return inspect_recipe_roots(
        [package_root],
        present=present,
        absent=absent,
        search_terms=search_terms,
        spack=spack,
    )


def _run_spack(spack_bin: Path, *args: str) -> str:
    result = subprocess.run(
        [str(spack_bin), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    seen = set()
    out = []
    for path in paths:
        resolved = str(path)
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(path)
    return out


def find_hermetic_package_roots(spack_bin: Path) -> tuple[list[Path], dict]:
    if os.environ.get("VASO_IN_INSULA") != "1":
        raise SystemExit("spack provenance inspection must run inside the hermetic insula")

    version = _run_spack(spack_bin, "--version")
    repo_list = ""
    repo_errors: list[str] = []
    try:
        repo_list = _run_spack(spack_bin, "repo", "list", "--json")
    except subprocess.CalledProcessError as exc:
        repo_errors.append(exc.stderr.strip() or str(exc))

    roots: list[Path] = []
    if repo_list:
        for item in json.loads(repo_list):
            path = item.get("path")
            if path:
                roots.append(_package_root_for_repo(Path(path)))

    overlay_env = os.environ.get("VASO_SPACK_OVERLAY_ROOTS", "")
    for item in overlay_env.split(os.pathsep):
        if item:
            roots.append(_package_root_for_repo(Path(item)))

    # Force Spack's builtin package repository cache to exist before scanning it.
    _run_spack(spack_bin, "list", "py-torch")
    cache_root = Path(os.environ.get("SPACK_USER_CACHE_PATH", "/vaso/cache/spack/user"))
    candidates = sorted(cache_root.glob("package_repos/*/repos/*/builtin/packages"))
    candidates = [path for path in candidates if (path / "py_torch" / "package.py").exists()]
    roots.extend(candidates)
    roots = _dedupe_paths(root for root in roots if root.is_dir())
    if not roots:
        raise SystemExit(f"no hermetic Spack builtin package repo found under {cache_root}")
    return roots, {
        "executable": str(spack_bin),
        "version": version,
        "user_cache": str(cache_root),
        "repo_list_errors": repo_errors,
    }


def find_hermetic_package_root(spack_bin: Path) -> tuple[Path, dict]:
    roots, spack = find_hermetic_package_roots(spack_bin)
    return roots[0], spack


def validate(
    report: dict,
    required_present: Iterable[str],
    required_absent: Iterable[str],
    required_versions: Iterable[str],
) -> list[str]:
    errors = []
    for name in required_present:
        if not report["packages"].get(name, {}).get("present", False):
            errors.append(f"required Spack package is absent: {name}")
    for name in required_absent:
        if report["packages"].get(name, {}).get("present", False):
            errors.append(f"Spack package must be absent in this provenance boundary: {name}")
    for spec in required_versions:
        if "@" not in spec:
            errors.append(f"--require-version must be NAME@VERSION, got {spec!r}")
            continue
        name, version = spec.rsplit("@", 1)
        package = report["packages"].get(name)
        if not package or not package.get("present", False):
            errors.append(f"required Spack package is absent: {name}")
            continue
        if version not in package.get("versions", []):
            errors.append(f"required Spack package version is absent: {spec}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--package-root", type=Path, action="append")
    source.add_argument("--spack", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--require-present", action="append", default=[])
    parser.add_argument("--require-absent", action="append", default=[])
    parser.add_argument("--require-version", action="append", default=[])
    args = parser.parse_args(argv)

    spack = {}
    package_roots = args.package_root
    if args.spack:
        package_roots, spack = find_hermetic_package_roots(args.spack)

    report = inspect_recipe_roots(
        package_roots,
        present=args.require_present,
        absent=args.require_absent,
        search_terms=args.query,
        spack=spack,
    )
    errors = validate(report, args.require_present, args.require_absent, args.require_version)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
