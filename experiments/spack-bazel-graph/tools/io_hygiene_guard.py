#!/usr/bin/env python3
"""Ratchet guard for execution temp-file placement.

Execution scripts and native repository rules must not drift back to implicit
shared scratch. A few legacy tests still use Python's default tempfile location
or TEST_TMPDIR fallbacks; those are counted in an allowlist so the list can only
shrink.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


SCAN_SUFFIXES = (".sh", ".py", ".bzl")
SKIP_DIRS = {
    ".git",
    ".trae",
    "bazel-bin",
    "bazel-out",
    "bazel-spack-bazel-graph",
    "bazel-testlogs",
}
RULE_IDS = frozenset(
    (
        "bare-mktemp",
        "io-allowlist-ratchet",
        "tempfile-default",
        "tmp-literal",
    )
)
TEMPFILE_CALL = re.compile(
    r"\btempfile\.(TemporaryDirectory|NamedTemporaryFile|mkdtemp|mkstemp|TemporaryFile|SpooledTemporaryFile)\s*\("
)
MKTEMP_CALL = re.compile(r"(^|[^A-Za-z0-9_./-])mktemp(\s|$)")
TMP_LITERAL = re.compile(r"(?<![A-Za-z0-9_./])/(?:var/)?tmp(?:/|$|[\s\"'`:;),}])")


@dataclass(frozen=True)
class Finding:
    rel: str
    line: int
    kind: str
    message: str


def source_root_from(anchor: Path) -> Path:
    """Source-tree experiment root for a declared runfile under tools/."""
    return anchor.resolve().parent.parent


def read_allowlist(path: Path) -> dict[tuple[str, str], int]:
    entries: dict[tuple[str, str], int] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        fields = line.split("#", 1)[0].split()
        if not fields:
            continue
        if len(fields) != 3 or not fields[2].isdigit():
            raise ValueError(f"{path}:{number}: expected '<path> <kind> <count>', got {line!r}")
        entries[(fields[0], fields[1])] = int(fields[2])
    return entries


def _is_full_line_comment(line: str) -> bool:
    stripped = line.strip()
    return not stripped or stripped.startswith("#")


def _is_bounded_tmpfs_tmp(lines: list[str], index: int) -> bool:
    line = lines[index]
    if "--tmpfs /tmp" not in line:
        return False
    previous = lines[index - 1] if index > 0 else ""
    return "--size" in previous or "--size" in line


def _is_tmp_refusal_pattern(lines: list[str], index: int) -> bool:
    line = lines[index]
    if not re.search(r"/(?:var/)?tmp/\*", line):
        return False
    window = "\n".join(lines[index : index + 5]).lower()
    return "refus" in window and "exit" in window


def _is_python_tmp_refusal_pattern(rel: str, lines: list[str], index: int) -> bool:
    line = lines[index]
    if re.search(r"\b(?:text|path)\s*==\s*['\"]/(?:var/)?tmp['\"]", line):
        return True
    if re.search(r"\.startswith\(\s*['\"]/(?:var/)?tmp/", line):
        return True
    if "TMPDIR" in line and re.search(r"['\"]/(?:var/)?tmp['\"]", line):
        window = "\n".join(lines[max(0, index - 6) : index + 5]).lower()
        if "shared_tmp" in window and ("required_tmpdir" in window or "fallback" in window):
            return True
        if rel.endswith("_test.py"):
            fallback_window = "\n".join(lines[max(0, index - 6) : index + 120])
            return (
                "VASO_HOME" in fallback_window
                and "VASO_CUDA_LINE" in fallback_window
                and ("native-actions" in fallback_window or "action_tmp" in fallback_window)
            )
    return False


def _is_bare_mktemp(line: str) -> bool:
    if not MKTEMP_CALL.search(line):
        return False
    safe_tokens = (
        '-p "$TMPDIR"',
        "-p '${TMPDIR}'",
        "-p ${TMPDIR}",
        "--tmpdir=\"$TMPDIR\"",
        "--tmpdir=${TMPDIR}",
    )
    return not any(token in line for token in safe_tokens)


def _call_args_from(lines: list[str], index: int, match: re.Match[str]) -> str:
    parts: list[str] = []
    depth = 1
    for offset in range(index, min(len(lines), index + 50)):
        segment = lines[offset][match.end() :] if offset == index else lines[offset]
        for char_index, char in enumerate(segment):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    parts.append(segment[:char_index])
                    return "\n".join(parts)
        parts.append(segment)
    return "\n".join(parts)


def _tempfile_without_dir(lines: list[str], index: int) -> bool:
    match = TEMPFILE_CALL.search(lines[index])
    if not match:
        return False
    return re.search(r"\bdir\s*=", _call_args_from(lines, index, match)) is None


def scan_file(path: Path, source_root: Path) -> list[Finding]:
    rel = path.relative_to(source_root).as_posix()
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    findings: list[Finding] = []
    for index, line in enumerate(lines):
        number = index + 1
        if _is_full_line_comment(line):
            continue
        if (
            TMP_LITERAL.search(line)
            and not _is_bounded_tmpfs_tmp(lines, index)
            and not _is_tmp_refusal_pattern(lines, index)
            and not _is_python_tmp_refusal_pattern(rel, lines, index)
        ):
            findings.append(Finding(rel, number, "tmp-literal", "implicit /tmp literal"))
        if _is_bare_mktemp(line):
            findings.append(Finding(rel, number, "bare-mktemp", 'mktemp must use -p "$TMPDIR"'))
        if _tempfile_without_dir(lines, index):
            findings.append(Finding(rel, number, "tempfile-default", "tempfile call must pass dir="))
    return findings


def iter_scan_files(source_root: Path) -> list[Path]:
    files: list[Path] = []
    for path in source_root.rglob("*"):
        if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
            continue
        rel_parts = path.relative_to(source_root).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        files.append(path)
    return sorted(files)


def check(source_root: Path, allowlist: dict[tuple[str, str], int]) -> list[str]:
    counts: dict[tuple[str, str], list[Finding]] = {}
    for path in iter_scan_files(source_root):
        for finding in scan_file(path, source_root):
            counts.setdefault((finding.rel, finding.kind), []).append(finding)

    errors: list[str] = []
    for key, findings in sorted(counts.items()):
        allowed = allowlist.get(key)
        if allowed is None:
            first = findings[0]
            errors.append(f"{first.rel}:{first.line}: {first.message} ({len(findings)} {first.kind})")
        elif len(findings) > allowed:
            first = findings[0]
            errors.append(
                f"{first.rel}:{first.line}: {len(findings)} {first.kind} finding(s), "
                f"allowlist permits {allowed}"
            )
        elif len(findings) < allowed:
            rel, kind = key
            errors.append(f"{rel}: {len(findings)} {kind} finding(s) left; lower allowlist count to {len(findings)}")

    for rel, kind in sorted(set(allowlist) - set(counts)):
        errors.append(f"{rel}: allowlisted {kind} but no finding remains; remove it from the allowlist")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allowlist", type=Path, required=True)
    parser.add_argument(
        "--source-anchor",
        type=Path,
        required=True,
        help="declared runfile under tools/ used to locate the source tree",
    )
    args = parser.parse_args(argv)

    try:
        allowlist = read_allowlist(args.allowlist)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    errors = check(source_root_from(args.source_anchor), allowlist)
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        return 1
    print(f"I/O hygiene guard passed ({len(iter_scan_files(source_root_from(args.source_anchor)))} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
