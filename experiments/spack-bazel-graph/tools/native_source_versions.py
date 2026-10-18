#!/usr/bin/env python3
"""Check native_overrides.json provided_versions against the native sources.

`provided_versions` records the concrete Spack version each native recipe
builds. This check ties that record to the source the native repository rule
actually fetches, so bumping a MODULE.bazel source (URL, strip_prefix, or a
version attribute) without updating provided_versions, or the reverse, fails.

A provided version is witnessed by its override label's MODULE.bazel repo block:

- directly: its normalized spelling equals a version attribute, the version
  suffix of `strip_prefix`, or the version in a URL basename;
- through SOURCE_SPELLINGS: Spack spells the version differently from upstream,
  and the upstream spelling must appear in the block;
- through a pinned commit (COMMIT_PINNED_PACKAGES): Spack versions the package
  by date, and the recipe doc names both the block's pinned commit and
  `<package>@<provided version>`;
- DOC_ONLY_PACKAGES have no upstream source archive; the recipe doc must name
  `<package>@<provided version>`.
- ROOTFS_BOUNDARY_PACKAGES have no source archive in MODULE.bazel. Their
  repository rule must instead be an sdk-boundary whose exact version is read
  from the selected rootfs manifest at fetch time.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


# Override key -> (Spack provided spelling, upstream spelling in MODULE.bazel, why).
SOURCE_SPELLINGS: dict[str, tuple[str, str, str]] = {
    "ca-certificates-mozilla": (
        "2026-03-19",
        "cacert-2026-03-19.pem",
        "the CA bundle is versioned by its release date",
    ),
    "libedit": (
        "3.1-20251016",
        "20251016-3.1",
        "Spack reorders libedit's <date>-<release> as <release>-<date>",
    ),
    "libevent": (
        "2.1.12",
        "libevent-2.1.12-stable",
        "libevent publishes release tarballs as <version>-stable",
    ),
    "protobuf@21.12": (
        "21.12",
        "3.21.12",
        "protobuf >= 3.20 tags vX.Y.Z upstream; Spack drops the leading 3.",
    ),
    "sqlite": (
        "3.53.1",
        "3530100",
        "SQLite autoconf tarballs encode X.Y.Z as XYYZZ00",
    ),
    "unzip": (
        "6.0",
        "unzip60.tar.gz",
        "Info-ZIP tarballs drop the version dot (unzip60)",
    ),
}
COMMIT_PINNED_PACKAGES = {"cpuinfo", "fp16", "fxdiv", "psimd", "pthreadpool"}
DOC_ONLY_PACKAGES = {
    "python-venv": "generic Spack virtualenv package with no upstream source archive",
}
ROOTFS_BOUNDARY_PACKAGES = {
    "cuda",
    "cudnn",
    "cudss",
    "cusparselt",
    "llvm",
    "nccl",
    "nvshmem",
}

_VERSION = r"v?(\d+(?:[._-]\d+)*(?:\.?post\d+|[a-z]\d*)?)"
_ARCHIVE_SUFFIX = re.compile(r"\.(tar\.(gz|xz|bz2|zst)|tgz|zip|whl|pem)$")
_COMMIT = re.compile(r"\b[0-9a-f]{40}\b")


def normalize_version(version: str) -> str:
    return version.lower().removeprefix("v").replace("_", ".").replace("-", ".")


def module_repo_block(module_text: str, repo: str) -> str | None:
    match = re.search(
        rf'\n[ \t]*name\s*=\s*"{re.escape(repo)}",\n((?:[ \t]+[^\n]*\n)*)[ \t]*\)',
        module_text,
        re.M,
    )
    return match.group(1) if match else None


def module_source_versions(block: str) -> set[str]:
    """Versions a MODULE.bazel repo block names as its source."""
    versions: set[str] = set()
    for value in re.findall(r'\b\w*version\s*=\s*"([^"]+)"', block):
        versions.add(value)
    for strip_prefix in re.findall(r'\bstrip_prefix\s*=\s*"([^"]+)"', block):
        match = re.search(rf"[-_]{_VERSION}(?:[-_](?:src|source))?$", strip_prefix.split("/")[0])
        if match:
            versions.add(match.group(1))
    for url in re.findall(r'"(https?://[^"]+)"', block):
        name = _ARCHIVE_SUFFIX.sub("", url.rsplit("/", 1)[-1])
        name = re.sub(r"-py\d.*$", "", name)
        match = re.search(rf"(?:^|[-_]){_VERSION}(?:[-_](?:src|source|archive))?$", name)
        if match:
            versions.add(match.group(1))
    return versions


def _recipe_doc(root: Path, package: str) -> str:
    path = root / "docs" / "recipes" / f"{package}.md"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _names_package_version(doc: str, package: str, version: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(package)}@={re.escape(version)}\b|"
                     rf"(?<![\w-]){re.escape(package)}@{re.escape(version)}(?![\w.-])", doc) is not None


def _native_rule_text(root: Path, repo: str) -> str:
    if not repo.endswith("_native"):
        return ""
    package_dir = repo.removesuffix("_native")
    path = root / "native" / package_dir / f"{package_dir}.bzl"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def source_version_errors(
    root: Path,
    *,
    spellings: dict[str, tuple[str, str, str]] = SOURCE_SPELLINGS,
    commit_pinned: set[str] = COMMIT_PINNED_PACKAGES,
    doc_only: dict[str, str] = DOC_ONLY_PACKAGES,
    rootfs_boundaries: set[str] = ROOTFS_BOUNDARY_PACKAGES,
) -> list[str]:
    overrides = json.loads((root / "native_overrides.json").read_text(encoding="utf-8"))
    native: dict[str, str] = overrides.get("native", {})
    provided_versions: dict[str, str] = overrides.get("provided_versions", {})
    module_text = (root / "MODULE.bazel").read_text(encoding="utf-8")
    errors: list[str] = []

    for key in sorted(set(spellings) - set(native)):
        errors.append(f"SOURCE_SPELLINGS entry {key!r} has no native override")
    for package in sorted(commit_pinned | set(doc_only)):
        if package not in native:
            errors.append(f"source-version exception {package!r} has no native override")

    for key, label in sorted(native.items()):
        provided = provided_versions.get(key)
        if provided is None:
            continue  # completeness is reported by migration_ledger_check
        package = key.split("@", 1)[0]
        repo = label.split("//", 1)[0].lstrip("@")

        if package in doc_only:
            if not _names_package_version(_recipe_doc(root, package), package, provided):
                errors.append(
                    f"{key}: docs/recipes/{package}.md does not name {package}@{provided} "
                    f"({doc_only[package]})"
                )
            continue

        block = module_repo_block(module_text, repo)
        if block is None:
            errors.append(f"{key}: MODULE.bazel has no repository block named {repo!r} for {label}")
            continue

        if package in rootfs_boundaries:
            rule_text = _native_rule_text(root, repo)
            if "sdk-boundary" not in rule_text:
                errors.append(
                    f"{key}: native/{package}/{package}.bzl must declare an sdk-boundary "
                    "instead of a source/archive provider"
                )
            continue

        if key in spellings:
            spack_spelling, upstream_spelling, why = spellings[key]
            if provided != spack_spelling:
                errors.append(
                    f"{key}: provided_versions says {provided} but SOURCE_SPELLINGS maps "
                    f"{spack_spelling} ({why}); update both together"
                )
            elif upstream_spelling not in block:
                errors.append(
                    f"{key}: MODULE.bazel {repo} no longer fetches {upstream_spelling!r}, "
                    f"the upstream spelling of provided version {provided} ({why})"
                )
            continue

        if package in commit_pinned:
            commits = set(_COMMIT.findall(block))
            doc = _recipe_doc(root, package)
            named = sorted(commit for commit in commits if commit in doc)
            if not commits:
                errors.append(f"{key}: MODULE.bazel {repo} pins no source commit")
            elif not named:
                errors.append(
                    f"{key}: docs/recipes/{package}.md does not name the commit "
                    f"MODULE.bazel {repo} pins ({', '.join(sorted(commits))})"
                )
            elif not _names_package_version(doc, package, provided):
                errors.append(
                    f"{key}: docs/recipes/{package}.md names pinned commit {named[0]} "
                    f"but not {package}@{provided}"
                )
            continue

        sources = module_source_versions(block)
        if normalize_version(provided) not in {normalize_version(v) for v in sources}:
            errors.append(
                f"{key}: provided_versions says {provided} but MODULE.bazel {repo} "
                f"sources name {', '.join(sorted(sources)) or 'no version'}"
            )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--native-overrides",
        type=Path,
        required=True,
        help="native_overrides.json; MODULE.bazel and docs/recipes are read beside it",
    )
    args = parser.parse_args(argv)

    # Do not resolve symlinks: under Bazel the runfiles tree must supply every input.
    errors = source_version_errors(args.native_overrides.parent)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
