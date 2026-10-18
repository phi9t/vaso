#!/usr/bin/env python3
"""Reject silent fallbacks to the legacy home estate."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


HOME_DEFAULT_RE = re.compile(
    r"(?:~|\$HOME|\$\{HOME\}|\{home\})/\.vaso-estate\b|Path\.home\(\)\s*/\s*['\"]\.vaso-estate['\"]"
)
DOT_LITERAL_RE = re.compile(r"(?<![-A-Za-z0-9_])\.vaso-estate(?=['\"])")
RULE_IDS = frozenset(("home-estate-default", "dot-estate-literal"))


@dataclass(frozen=True, order=True)
class Finding:
    pattern: str
    path: str
    line: int
    text: str

    @property
    def identity(self) -> str:
        return f"{self.pattern}\t{self.path}:{self.line}\t{self.text}"


@dataclass(frozen=True)
class Allow:
    pattern: str
    path: str
    text: str
    reason: str


def repo_root_from(anchor: Path) -> Path:
    resolved = anchor.resolve()
    for parent in (resolved.parent, *resolved.parents):
        if (parent / "CONSTITUTION.md").is_file() and (parent / "foundry").is_dir():
            return parent
    return resolved.parent


def _git_tracked_paths(repo_root: Path) -> list[Path] | None:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        return None
    return [repo_root / raw.decode("utf-8") for raw in proc.stdout.split(b"\0") if raw]


SELF_FIXTURE_FILES = (
    "tools/estate_default_guard_test.py",
    "tools/estate_default_guard_allowlist.json",
)


def candidate_paths(repo_root: Path) -> list[Path]:
    tracked = _git_tracked_paths(repo_root)
    paths = tracked if tracked is not None else list(repo_root.rglob("*"))
    result: list[Path] = []
    for path in paths:
        rel = path.relative_to(repo_root)
        if not path.is_file() or not rel.parts:
            continue
        if rel.parts[0] in {".git", ".scratch", "docs"}:
            continue
        if rel.as_posix().endswith(SELF_FIXTURE_FILES):
            # The guard's own fixtures and allowlist must contain the forbidden strings.
            continue
        result.append(path)
    return sorted(result)


def _display(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def scan_path(path: Path, repo_root: Path) -> list[Finding]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return []
    rel = _display(path, repo_root)
    findings: list[Finding] = []
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if HOME_DEFAULT_RE.search(line):
            findings.append(Finding("home-estate-default", rel, number, stripped))
        elif DOT_LITERAL_RE.search(line):
            findings.append(Finding("dot-estate-literal", rel, number, stripped))
    return findings


def collect_findings(repo_root: Path, paths: Iterable[Path] | None = None) -> list[Finding]:
    return sorted(
        finding
        for path in (list(paths) if paths is not None else candidate_paths(repo_root))
        for finding in scan_path(path, repo_root)
    )


def read_allowlist(path: Path) -> list[Allow]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError(f"{path}: expected object with schema_version=1")
    entries = raw.get("entries")
    if not isinstance(entries, list):
        raise ValueError(f"{path}: entries must be a list")
    allowed: list[Allow] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: entry {index} must be an object")
        missing = [key for key in ("pattern", "path", "text", "reason") if not entry.get(key)]
        if missing:
            raise ValueError(f"{path}: entry {index} missing: {', '.join(missing)}")
        allowed.append(
            Allow(
                pattern=str(entry["pattern"]),
                path=str(entry["path"]),
                text=str(entry["text"]),
                reason=str(entry["reason"]),
            )
        )
    return allowed


def unallowed_findings(findings: list[Finding], allowed: list[Allow]) -> list[Finding]:
    allow_keys = {(item.pattern, item.path, item.text) for item in allowed}
    return [
        finding
        for finding in findings
        if (finding.pattern, finding.path, finding.text) not in allow_keys
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--source-anchor", type=Path, default=Path(__file__))
    parser.add_argument("--allowlist", type=Path)
    parser.add_argument("--source", type=Path, action="append", default=[])
    args = parser.parse_args(argv)

    repo_root = (args.repo_root or repo_root_from(args.source_anchor)).resolve()
    allowlist = args.allowlist or (
        repo_root / "foundry/tools/estate_default_guard_allowlist.json"
    )
    paths = [path if path.is_absolute() else repo_root / path for path in args.source]
    findings = collect_findings(repo_root, paths or None)
    try:
        allowed = read_allowlist(allowlist)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    failures = unallowed_findings(findings, allowed)
    for finding in failures:
        print(finding.identity, file=sys.stderr)
    if failures:
        print(
            f"estate default guard found {len(failures)} unallowlisted legacy estate fallback(s); "
            "set VASO_ESTATE_ROOT explicitly or add an allowlist reason for non-default sanitizers",
            file=sys.stderr,
        )
        return 1
    print(f"estate default guard passed ({len(findings)} legacy estate literal(s), all allowlisted)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
