#!/usr/bin/env python3
"""Require live Bazel guard targets to opt out of test result caching."""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path


SKIP_DIRS = {
    ".git",
    "bazel-bin",
    "bazel-out",
    "bazel-spack-bazel-graph",
    "bazel-testlogs",
}
RULE_IDS = frozenset(
    (
        "build-parse-error",
        "live-no-cache-tag",
        "live-py-precompile-disabled",
    )
)


@dataclass(frozen=True)
class Target:
    label: str
    path: Path
    line: int
    rule: str
    name: str
    precompile: str | None
    tags: tuple[str, ...]


def source_root_from(anchor: Path) -> Path:
    """Source-tree experiment root for a declared runfile under tools/."""
    return anchor.resolve().parent.parent


def iter_build_files(source_root: Path) -> list[Path]:
    files: list[Path] = []
    for path in source_root.rglob("*"):
        if not path.is_file() or path.name not in {"BUILD", "BUILD.bazel"}:
            continue
        rel_parts = path.relative_to(source_root).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        files.append(path)
    return sorted(files)


def _literal_strings(node: ast.AST | None) -> tuple[str, ...] | None:
    if node is None:
        return ()
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values: list[str] = []
        for element in node.elts:
            if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
                return None
            values.append(element.value)
        return tuple(values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_strings(node.left)
        right = _literal_strings(node.right)
        if left is None or right is None:
            return None
        return left + right
    return None


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _literal_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _target_label(source_root: Path, build_file: Path, name: str) -> str:
    package = build_file.parent.relative_to(source_root).as_posix()
    if package == ".":
        return f"//:{name}"
    return f"//{package}:{name}"


def targets_in_build_file(source_root: Path, build_file: Path) -> list[Target]:
    tree = ast.parse(build_file.read_text(encoding="utf-8"), filename=str(build_file))
    targets: list[Target] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        rule = _call_name(node)
        name_node = _keyword(node, "name")
        if not isinstance(name_node, ast.Constant) or not isinstance(name_node.value, str):
            continue
        tags = _literal_strings(_keyword(node, "tags"))
        targets.append(
            Target(
                label=_target_label(source_root, build_file, name_node.value),
                path=build_file,
                line=node.lineno,
                rule="" if rule is None else rule,
                name=name_node.value,
                precompile=_literal_string(_keyword(node, "precompile")),
                tags=() if tags is None else tags,
            )
        )
    return targets


def check(source_root: Path) -> list[str]:
    errors: list[str] = []
    for build_file in iter_build_files(source_root):
        try:
            targets = targets_in_build_file(source_root, build_file)
        except SyntaxError as exc:
            errors.append(f"{build_file.relative_to(source_root)}:{exc.lineno}: could not parse BUILD file: {exc.msg}")
            continue
        for target in targets:
            if "_live" not in target.name:
                continue
            if "no-cache" not in target.tags:
                rel = target.path.relative_to(source_root)
                errors.append(f"{rel}:{target.line}: {target.label} must tag live guards no-cache")
            if target.rule == "py_test" and target.precompile != "disabled":
                rel = target.path.relative_to(source_root)
                errors.append(f"{rel}:{target.line}: {target.label} must disable Python precompile")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-anchor",
        type=Path,
        required=True,
        help="declared runfile under tools/ used to locate the source tree",
    )
    args = parser.parse_args(argv)

    source_root = source_root_from(args.source_anchor)
    errors = check(source_root)
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        return 1
    print(f"live guard no-cache meta-test passed ({len(iter_build_files(source_root))} BUILD files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
