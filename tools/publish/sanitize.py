#!/usr/bin/env python3
"""Scrub host-, employer- and internal-infra-specific strings from a tree.

    sanitize.py --rules RULES.json ROOT [--check]

RULES.json is a list of [regex, replacement] pairs, applied in order (most
specific first). The rules name the strings being scrubbed, so they live in
the estate (outside git), never in the published tree. --check rewrites
nothing and exits 1 when any rule still matches.
"""
import argparse
import json
import re
import sys
from pathlib import Path

SKIP_DIRS = {".git"}


def load_rules(path: Path) -> list[tuple[re.Pattern[str], str]]:
    rules = json.loads(path.read_text(encoding="utf-8"))
    return [(re.compile(pattern), replacement) for pattern, replacement in rules]


def sanitize(root: Path, rules, check: bool) -> tuple[int, int]:
    changed = hits = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file() or any(p in SKIP_DIRS for p in path.relative_to(root).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        new = text
        for rx, rep in rules:
            new, n = rx.subn(rep, new)
            hits += n
            if n and check:
                print(f"{path.relative_to(root)}: {rx.pattern} x{n}")
        if new != text and not check:
            path.write_text(new, encoding="utf-8")
            changed += 1
    return changed, hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", type=Path)
    ap.add_argument("--rules", type=Path, required=True)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    changed, hits = sanitize(args.root, load_rules(args.rules), args.check)
    if args.check:
        print(f"hits: {hits}")
        return 1 if hits else 0
    print(f"changed files: {changed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
