#!/usr/bin/env python3
"""Tests for estate_default_guard.py."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("estate_default_guard.py")
SPEC = importlib.util.spec_from_file_location("estate_default_guard", SCRIPT)
assert SPEC is not None
guard = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = guard
SPEC.loader.exec_module(guard)


def rule_fixture(rule_id: str, test_method: str) -> tuple[str, str]:
    assert rule_id in guard.RULE_IDS
    return rule_id, test_method


RULE_FIXTURES = dict(
    (
        rule_fixture("home-estate-default", "test_home_estate_bazel_fallback_is_reported"),
        rule_fixture("dot-estate-literal", "test_allowlist_requires_a_reason_for_sanitizer_literals"),
    )
)


def temporary_directory() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class EstateDefaultGuardTest(unittest.TestCase):
    def test_rule_fixture_manifest_matches_guard_rule_ids(self) -> None:
        self.assertEqual(set(RULE_FIXTURES), guard.RULE_IDS)
        missing = sorted(name for name in RULE_FIXTURES.values() if not hasattr(self, name))
        self.assertEqual(missing, [])

    def test_home_estate_bazel_fallback_is_reported(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            write(
                root / "native" / "pkg" / "action_prefix_test.py",
                'str(Path.home() / ".vaso-estate" / "opt-vaso" / "bin" / "bazel-real")\n',
            )

            findings = guard.collect_findings(root)

        self.assertEqual(
            [finding.identity for finding in findings],
            [
                "home-estate-default\tnative/pkg/action_prefix_test.py:1\t"
                'str(Path.home() / ".vaso-estate" / "opt-vaso" / "bin" / "bazel-real")'
            ],
        )

    def test_shell_home_estate_fallback_is_reported(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            write(root / "scripts" / "agents" / "standing-verify.sh", 'BAZEL=${BAZEL_BIN:-$HOME/.vaso-estate/bin/bazel-real}\n')

            findings = guard.collect_findings(root)

        self.assertEqual(
            [finding.identity for finding in findings],
            [
                "home-estate-default\tscripts/agents/standing-verify.sh:1\t"
                "BAZEL=${BAZEL_BIN:-$HOME/.vaso-estate/bin/bazel-real}"
            ],
        )

    def test_allowlist_requires_a_reason_for_sanitizer_literals(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            path = root / "native" / "common" / "repair_wheel.py"
            write(path, 'FORBIDDEN = [b".vaso-estate"]\n')
            allowlist = root / "allow.json"
            allowlist.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "entries": [
                            {
                                "pattern": "dot-estate-literal",
                                "path": "native/common/repair_wheel.py",
                                "text": 'FORBIDDEN = [b".vaso-estate"]',
                                "reason": "fixture sanitizer",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            findings = guard.collect_findings(root)
            failures = guard.unallowed_findings(findings, guard.read_allowlist(allowlist))

        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
