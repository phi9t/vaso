#!/usr/bin/env python3
"""Ratchet guard: native rules must not hard-code a Python ABI.

Only native/python/ may name a concrete `pythonX.Y`. Every other native rule
derives the ABI from the Python prefix it is given, so one rule can serve any
Python line (see .scratch/pytorch-frontier-convergence ticket 06).

Files that still carry literals are listed in an allowlist as `path count`,
where count is the number of lines with a literal. The guard fails when a file
outside the allowlist gains a literal (a new ABI-bound capture), when an
allowlisted file gains literal lines, and when it loses some or all of them (the
count must be lowered or the entry removed), so the list can only shrink.

A literal is any concrete CPython ABI spelling: `python3.X` paths,
`cpython-3XY` extension-module tags, `pip3.X` launchers, and `cp3XY` wheel tags.

The scan only sees files declared as Bazel data, so `--coverage-anchor`
additionally resolves a declared file back to the source tree and fails on any
`native/*/*.bzl` or `native/*/*.py` that is not declared: a new capture cannot
escape the native guards by not registering. This coverage check deliberately
reads the source tree (like migration_ledger_check's --root resolution); the
literal scan itself stays on declared inputs.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


PYTHON_ABI_LITERAL = re.compile(r"python3\.\d+|cpython-3\d+|pip3\.\d+|\bcp3\d{2}\b")
EXEMPT_DIRS = ("native/python/",)
RULE_IDS = frozenset(
    (
        "python-abi-allowlist-ratchet",
        "python-abi-coverage",
        "python-abi-literal",
    )
)


def _normalize(path: Path) -> str:
    text = path.as_posix()
    index = text.find("native/")
    return text[index:] if index >= 0 else text


def read_allowlist(path: Path) -> dict[str, int]:
    entries: dict[str, int] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        fields = line.split("#", 1)[0].split()
        if not fields:
            continue
        if len(fields) != 2 or not fields[1].isdigit():
            raise ValueError(f"{path}:{number}: expected '<path> <literal line count>', got {line!r}")
        entries[fields[0]] = int(fields[1])
    return entries


def literal_lines(path: Path) -> list[int]:
    return [
        number
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1)
        if PYTHON_ABI_LITERAL.search(line)
    ]


def source_root_from(anchor: Path) -> Path:
    """Source-tree experiment root for a declared runfile like tools/<file>."""
    return anchor.resolve().parent.parent


def coverage_errors(files: list[Path], source_root: Path) -> list[str]:
    declared = {_normalize(path) for path in files}
    on_disk = sorted(
        _normalize(path.relative_to(source_root))
        for pattern in ("native/*/*.bzl", "native/*/*.py")
        for path in source_root.glob(pattern)
    )
    return [
        f"{rel}: not declared in hermetic_native_deps_guard_test data; the guards never scan it"
        for rel in on_disk
        if rel not in declared
    ]


def check(files: list[Path], allowlist: dict[str, int]) -> list[str]:
    errors: list[str] = []
    scanned: set[str] = set()
    for path in files:
        rel = _normalize(path)
        if rel.startswith(EXEMPT_DIRS):
            continue
        scanned.add(rel)
        lines = literal_lines(path)
        allowed = allowlist.get(rel)
        if allowed is None:
            if lines:
                errors.append(
                    f"{rel}:{lines[0]}: hard-coded Python ABI literal; derive it from the Python prefix "
                    f"({len(lines)} line(s))"
                )
        elif not lines:
            errors.append(f"{rel}: no Python ABI literal left; remove it from the allowlist")
        elif len(lines) > allowed:
            errors.append(
                f"{rel}:{lines[0]}: {len(lines)} literal line(s), allowlist permits {allowed}; "
                "derive the ABI from the Python prefix instead of adding literals"
            )
        elif len(lines) < allowed:
            errors.append(f"{rel}: {len(lines)} literal line(s) left; lower its allowlist count to {len(lines)}")
    for rel in sorted(set(allowlist) - scanned):
        errors.append(f"{rel}: allowlisted but not scanned; remove it from the allowlist")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allowlist", type=Path, required=True)
    parser.add_argument(
        "--coverage-anchor",
        type=Path,
        help="declared runfile under tools/ used to locate the source tree for the coverage check",
    )
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)

    try:
        allowlist = read_allowlist(args.allowlist)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    errors = check(args.files, allowlist)
    if args.coverage_anchor is not None:
        errors.extend(coverage_errors(args.files, source_root_from(args.coverage_anchor)))
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        return 1
    print(f"python ABI literal guard passed ({len(args.files)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
