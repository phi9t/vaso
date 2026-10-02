#!/usr/bin/env python3
"""Meta-test for the guard change protocol.

Every guard module declares the rule IDs it emits. Its unit test declares at
least one executable fixture test per rule ID, so guard rule changes land with
coverage instead of prose-only justification.

Protocol: change a guard rule only together with a `rule_fixture("<rule-id>")`
fixture in the guard's unit test.
"""

from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path


RULE_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


def source_root_from(anchor: Path) -> Path:
    return anchor.resolve().parent.parent


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
    return None


def _string_constants(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values: list[str] = []
        for child in node.elts:
            values.extend(_string_constants(child))
        return values
    if isinstance(node, ast.Call) and _call_name(node) == "frozenset" and node.args:
        return _string_constants(node.args[0])
    return []


def module_rule_ids(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == "RULE_IDS" for target in targets):
            continue
        return set(_string_constants(node.value))
    return set()


def _test_methods(tree: ast.Module) -> set[str]:
    methods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            methods.add(node.name)
    return methods


def fixture_rule_ids(path: Path) -> tuple[set[str], list[str]]:
    if not path.exists():
        return set(), [f"{path.name}: missing unit test file"]
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    methods = _test_methods(tree)
    ids: set[str] = set()
    errors: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) != "rule_fixture":
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            ids.add(first.value)
        if len(node.args) >= 2:
            second = node.args[1]
            if isinstance(second, ast.Constant) and isinstance(second.value, str) and second.value not in methods:
                errors.append(f"{path.name}: rule_fixture points at missing test method {second.value}")
    return ids, errors


def guard_modules(source_root: Path) -> list[Path]:
    tools = source_root / "tools"
    return sorted(
        path
        for path in tools.glob("*guard*.py")
        if not path.name.endswith("_test.py") and path.name != "guard_change_protocol_test.py"
    )


class GuardChangeProtocolTest(unittest.TestCase):
    def test_every_guard_rule_has_a_fixture(self) -> None:
        source_root = source_root_from(Path(__file__))
        errors: list[str] = []
        for guard_path in guard_modules(source_root):
            rel = guard_path.relative_to(source_root).as_posix()
            rule_ids = module_rule_ids(guard_path)
            if not rule_ids:
                errors.append(f"{rel}: missing RULE_IDS")
                continue
            invalid = sorted(rule_id for rule_id in rule_ids if not RULE_ID_RE.fullmatch(rule_id))
            if invalid:
                errors.append(f"{rel}: invalid RULE_IDS: {', '.join(invalid)}")
            test_path = guard_path.with_name(guard_path.stem + "_test.py")
            fixtures, fixture_errors = fixture_rule_ids(test_path)
            errors.extend(fixture_errors)
            missing = sorted(rule_ids - fixtures)
            if missing:
                test_rel = test_path.relative_to(source_root).as_posix()
                errors.append(f"{test_rel}: missing rule_fixture coverage for {', '.join(missing)}")
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
