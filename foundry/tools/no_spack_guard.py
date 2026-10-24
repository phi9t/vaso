#!/usr/bin/env python3
"""Enforce that active build entrypoints no longer invoke Spack."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


RULE_IDS = frozenset(
    (
        "build-spack-lock",
        "spack-env-var",
        "spack-executable",
        "spack-repository",
    )
)

TEXT_SCAN_DIRS = (
    "tools",
    "native",
    "scripts/agents",
    "scripts/ci",
    "tools/release",
    "scripts/insula",
)
ROOT_TEXT_FILES = ("MODULE.bazel", "run.sh")
TEXT_SUFFIXES = frozenset((".bzl", ".py", ".sh", ".bash"))
SELF_EXCLUDED_NAMES = frozenset(
    (
        "no_spack_guard.py",
        "no_spack_guard_baseline.txt",
    )
)
LEGACY_SPACK_TOOL_PATHS = frozenset(
    (
        "tools/build_graph.py",
        "tools/hermetic_spack_guard_test.sh",
        "native/py_llvmlite/recipe_provenance_test.sh",
        "tools/pytorch_recipe_provenance_test.sh",
        "tools/spack_dist.bzl",
        "tools/spack_lock.sh",
        "tools/spack_lock_test.sh",
        "tools/spack_recipe_provenance.py",
        "tools/spack_to_bazel.py",
    )
)

SPACK_REPO_RE = re.compile(r"@spack_dist\b")
SPACK_ENV_RE = re.compile(r"\b(?:VASO_SPACK_[A-Z0-9_]+|SPACK_[A-Z0-9_]+)\b")
COMPAT_PACKAGE_ENV_RE = re.compile(r"\bSPACK_[A-Z0-9_]+_(?:PREFIX|BUILD|VERSION)\b")
SPACK_EXECUTABLE_LITERAL_RE = re.compile(
    r"(?:\b(?:spack|spack_bin|spack_executable)\s*=|\bdefault\s*=)\s*['\"]spack['\"]"
)
SPACK_ARGV_LITERAL_RE = re.compile(r"[\[(]\s*['\"]spack['\"]\s*,")
SPACK_COMMAND_RE = re.compile(
    r"(?:^|[\s;&|({])(?:command\s+)?spack\s+"
    r"(?:spec|install|location|compiler|find|external|repo|solve|concretize)\b"
)
SPACK_BIN_RE = re.compile(r"(?:^|[\s='\"(])(?:[^'\"\s]*/)?bin/spack\b")


@dataclass(frozen=True, order=True)
class Finding:
    rule: str
    path: str
    line: int | None
    detail: str

    @property
    def identity(self) -> str:
        location = self.path if self.line is None else f"{self.path}:{self.line}"
        return f"{self.rule}\t{location}\t{self.detail}"

    @property
    def baseline_key(self) -> tuple[str, str]:
        return self.rule, self.path


def source_root_from(anchor: Path) -> Path:
    """Return the experiment root from a runfile or source-tree anchor."""
    resolved = anchor.resolve()
    if resolved.parent.name == "tools":
        return resolved.parents[1]
    return resolved.parent


def display_path(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def is_allowlisted_path(path: Path, repo_root: Path) -> bool:
    rel = Path(display_path(path, repo_root))
    return (
        ".scratch" in rel.parts
        or (rel.parts and rel.parts[0] == "docs")
        or rel.as_posix() in LEGACY_SPACK_TOOL_PATHS
    )


def is_text_scan_candidate(path: Path, repo_root: Path) -> bool:
    if not path.is_file() or is_allowlisted_path(path, repo_root):
        return False
    if path.name in SELF_EXCLUDED_NAMES or path.name.endswith("_test.py"):
        return False
    return path.name.startswith("BUILD") or path.name in ROOT_TEXT_FILES or path.suffix in TEXT_SUFFIXES


def _git_tracked_paths(repo_root: Path) -> list[Path] | None:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        return None
    return [repo_root / raw.decode("utf-8") for raw in result.stdout.split(b"\0") if raw]


def _default_candidate_paths(repo_root: Path) -> list[Path]:
    tracked = _git_tracked_paths(repo_root)
    if tracked is not None:
        return sorted(path for path in tracked if path.is_file())
    return sorted(path for path in repo_root.rglob("*") if path.is_file())


def is_default_text_path(path: Path, repo_root: Path) -> bool:
    rel = Path(display_path(path, repo_root))
    if len(rel.parts) == 1:
        return path.name in ROOT_TEXT_FILES or path.suffix == ".bzl" or path.name.startswith("BUILD")
    return any(path.is_relative_to(repo_root / rel_dir) for rel_dir in TEXT_SCAN_DIRS)


def default_text_paths(repo_root: Path) -> list[Path]:
    return [
        path
        for path in _default_candidate_paths(repo_root)
        if is_default_text_path(path, repo_root) and is_text_scan_candidate(path, repo_root)
    ]


def default_profile_lock_paths(repo_root: Path) -> list[Path]:
    return [
        path
        for path in _default_candidate_paths(repo_root)
        if path.parent == repo_root / "locks"
        and path.name.endswith(".lock.json")
        and not is_allowlisted_path(path, repo_root)
    ]


def _spack_executable_detail(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "@spack_dist" in stripped:
        return None
    if (
        SPACK_COMMAND_RE.search(stripped)
        or SPACK_EXECUTABLE_LITERAL_RE.search(stripped)
        or SPACK_ARGV_LITERAL_RE.search(stripped)
        or SPACK_BIN_RE.search(stripped)
    ):
        return stripped
    return None


def scan_text_file(path: Path, repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    if is_allowlisted_path(path, repo_root):
        return findings
    rel = display_path(path, repo_root)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return findings
    for line_number, line in enumerate(lines, start=1):
        if SPACK_REPO_RE.search(line):
            findings.append(Finding("spack-repository", rel, line_number, "@spack_dist"))
        env_vars = sorted(
            env_var
            for env_var in set(SPACK_ENV_RE.findall(line))
            if not COMPAT_PACKAGE_ENV_RE.fullmatch(env_var)
        )
        if env_vars:
            findings.append(Finding("spack-env-var", rel, line_number, ",".join(env_vars)))
        executable = _spack_executable_detail(line)
        if executable is not None:
            findings.append(Finding("spack-executable", rel, line_number, executable))
    return findings


def _iter_lock_nodes(doc: object) -> list[tuple[str, dict]]:
    if not isinstance(doc, dict):
        return []
    packages = doc.get("packages")
    if isinstance(packages, dict):
        return [(str(name), value) for name, value in packages.items() if isinstance(value, dict)]
    nodes = doc.get("nodes")
    if isinstance(nodes, list):
        result: list[tuple[str, dict]] = []
        for index, node in enumerate(nodes):
            if not isinstance(node, dict):
                continue
            name = node.get("name") or node.get("package") or f"node[{index}]"
            result.append((str(name), node))
        return result
    return []


def scan_profile_lock(path: Path, repo_root: Path) -> list[Finding]:
    if is_allowlisted_path(path, repo_root):
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [Finding("build-spack-lock", display_path(path, repo_root), None, f"unreadable JSON: {exc}")]
    rel = display_path(path, repo_root)
    return [
        Finding("build-spack-lock", rel, None, node_name)
        for node_name, node in _iter_lock_nodes(doc)
        if node.get("build") == "spack"
    ]


def _normalize_input_paths(paths: list[Path], repo_root: Path) -> list[Path]:
    result: list[Path] = []
    for path in paths:
        result.append(path if path.is_absolute() else repo_root / path)
    return result


def collect_findings(
    repo_root: Path,
    *,
    text_paths: list[Path] | None = None,
    lock_paths: list[Path] | None = None,
) -> list[Finding]:
    repo_root = repo_root.resolve()
    selected_text_paths = _normalize_input_paths(text_paths, repo_root) if text_paths is not None else default_text_paths(repo_root)
    selected_lock_paths = _normalize_input_paths(lock_paths, repo_root) if lock_paths is not None else default_profile_lock_paths(repo_root)

    findings: list[Finding] = []
    for path in selected_text_paths:
        if path.is_file() and is_text_scan_candidate(path, repo_root):
            findings.extend(scan_text_file(path, repo_root))
    for path in selected_lock_paths:
        if path.is_file():
            findings.extend(scan_profile_lock(path, repo_root))
    return sorted(set(findings))


def baseline_counts(findings: list[Finding]) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for finding in findings:
        counts[finding.baseline_key] = counts.get(finding.baseline_key, 0) + 1
    return counts


def read_baseline(path: Path | None) -> dict[tuple[str, str], int]:
    if path is None or not path.exists():
        return {}
    entries: dict[tuple[str, str], int] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 3:
            raise ValueError(f"{path}:{line_number}: expected '<rule><TAB><path><TAB><count>'")
        rule, rel, count_s = fields
        try:
            count = int(count_s)
        except ValueError as exc:
            raise ValueError(f"{path}:{line_number}: count must be an integer") from exc
        entries[(rule, rel)] = count
    return entries


def unbaselined_findings(findings: list[Finding], baseline: dict[tuple[str, str], int]) -> list[Finding]:
    by_key: dict[tuple[str, str], list[Finding]] = {}
    for finding in findings:
        by_key.setdefault(finding.baseline_key, []).append(finding)

    failures: list[Finding] = []
    for key, key_findings in sorted(by_key.items()):
        allowed = baseline.get(key, 0)
        if len(key_findings) > allowed:
            failures.extend(sorted(key_findings)[allowed:])
    return failures


def print_baseline(findings: list[Finding]) -> None:
    print("# no_spack_guard enforcement baseline")
    print("# Format: <rule><TAB><path><TAB><count>")
    for (rule, rel), count in sorted(baseline_counts(findings).items()):
        print(f"{rule}\t{rel}\t{count}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-anchor",
        type=Path,
        default=Path(__file__),
        help="declared source-tree or runfile anchor used to locate graph",
    )
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--source", type=Path, action="append", default=[])
    parser.add_argument("--lock", type=Path, action="append", default=[])
    parser.add_argument("--print-baseline", action="store_true")
    parser.add_argument("--enforce", action="store_true", help="ignore baseline and fail on every hit")
    args = parser.parse_args(argv)

    repo_root = source_root_from(args.source_anchor)
    baseline_path = args.baseline or repo_root / "tools" / "no_spack_guard_baseline.txt"
    findings = collect_findings(
        repo_root,
        text_paths=args.source or None,
        lock_paths=args.lock or None,
    )

    if args.print_baseline:
        print_baseline(findings)
        return 0

    try:
        baseline = {} if args.enforce else read_baseline(baseline_path)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    failures = unbaselined_findings(findings, baseline)
    for finding in failures:
        print(finding.identity, file=sys.stderr)
    if failures:
        print(
            f"no-Spack guard found {len(failures)} unbaselined hit(s); "
            f"{display_path(baseline_path, repo_root)} must stay empty except for comments",
            file=sys.stderr,
        )
        return 1
    print(f"no-Spack guard passed ({len(findings)} active hit(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
