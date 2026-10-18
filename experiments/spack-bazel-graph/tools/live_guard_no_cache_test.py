#!/usr/bin/env python3
"""Tests for live_guard_no_cache.py."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("live_guard_no_cache.py")
SPEC = importlib.util.spec_from_file_location("live_guard_no_cache", SCRIPT)
assert SPEC is not None
guard = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = guard
SPEC.loader.exec_module(guard)


def scratch_parent() -> str | None:
    return os.environ.get("TEST_TMPDIR") or os.environ.get("VASO_AGENT_IO_ROOT") or str(Path.cwd())


def rule_fixture(rule_id: str, test_method: str) -> tuple[str, str]:
    assert rule_id in guard.RULE_IDS
    return rule_id, test_method


RULE_FIXTURES = dict(
    (
        rule_fixture("build-parse-error", "test_build_parse_error_is_reported"),
        rule_fixture("live-no-cache-tag", "test_missing_no_cache_tag_fails"),
        rule_fixture("live-py-precompile-disabled", "test_python_live_guard_must_disable_precompile"),
    )
)


class LiveGuardNoCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(dir=scratch_parent())
        self.root = Path(self._tmp.name)
        (self.root / "tools").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_build(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
        return path

    def test_rule_fixture_manifest_matches_guard_rule_ids(self) -> None:
        self.assertEqual(set(RULE_FIXTURES), guard.RULE_IDS)
        missing = sorted(name for name in RULE_FIXTURES.values() if not hasattr(self, name))
        self.assertEqual(missing, [])

    def test_missing_no_cache_tag_fails(self) -> None:
        self.write_build(
            "tools/BUILD.bazel",
            """
            py_test(
                name = "io_hygiene_guard_live_test",
                srcs = ["io_hygiene_guard.py"],
            )
            """,
        )

        errors = guard.check(self.root)

        self.assertIn(
            "tools/BUILD.bazel:1: //tools:io_hygiene_guard_live_test must tag live guards no-cache",
            errors,
        )

    def test_python_live_guard_must_disable_precompile(self) -> None:
        self.write_build(
            "tools/BUILD.bazel",
            """
            py_test(
                name = "io_hygiene_guard_live_test",
                srcs = ["io_hygiene_guard.py"],
                tags = ["no-cache"],
            )
            """,
        )

        errors = guard.check(self.root)

        self.assertEqual(
            errors,
            ["tools/BUILD.bazel:1: //tools:io_hygiene_guard_live_test must disable Python precompile"],
        )

    def test_build_parse_error_is_reported(self) -> None:
        self.write_build("tools/BUILD.bazel", "py_test(\n")

        errors = guard.check(self.root)

        self.assertEqual(len(errors), 1)
        self.assertIn("tools/BUILD.bazel:1: could not parse BUILD file:", errors[0])


if __name__ == "__main__":
    unittest.main()
