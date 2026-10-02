#!/usr/bin/env python3
"""Tests for tracker_lint.py."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("tracker_lint.py")
SPEC = importlib.util.spec_from_file_location("tracker_lint", SCRIPT)
assert SPEC is not None
tracker_lint = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = tracker_lint
SPEC.loader.exec_module(tracker_lint)


def scratch_parent() -> str | None:
    return os.environ.get("TEST_TMPDIR") or os.environ.get("VASO_AGENT_IO_ROOT")


class TrackerLintTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(dir=scratch_parent())
        self.root = Path(self._tmp.name)
        self.tracker = self.root / ".scratch" / "effort"
        (self.tracker / "issues").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_issue(self, name: str, text: str) -> Path:
        path = self.tracker / "issues" / name
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
        return path

    def write_spec(self, text: str) -> Path:
        path = self.tracker / "spec.md"
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
        return path

    def test_rejects_status_outside_defined_set(self) -> None:
        self.write_issue(
            "01-invalid.md",
            """
            # Invalid

            Status: shipped
            Type: task
            """,
        )

        errors = tracker_lint.check_tracker(self.root / ".scratch", allowlist=set())

        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "invalid-status")
        self.assertIn("shipped", errors[0].message)

    def test_closed_build_action_ticket_requires_real_run_evidence(self) -> None:
        self.write_issue(
            "02-build.md",
            """
            # Build

            Status: ready-for-review
            Type: build-action

            ## Comments

            - Built //native/pytorch:pytorch_action.
            """,
        )

        errors = tracker_lint.check_tracker(self.root / ".scratch", allowlist=set())

        self.assertEqual([error.code for error in errors], ["missing-real-run"])

    def test_action_target_mentions_also_require_real_run_evidence(self) -> None:
        self.write_issue(
            "03-target.md",
            """
            # Build

            Status: done
            Type: task

            ## Comments

            - Built //native/triton:triton_action in the insula.
            """,
        )

        errors = tracker_lint.check_tracker(self.root / ".scratch", allowlist=set())

        self.assertEqual([error.code for error in errors], ["missing-real-run"])

    def test_dry_run_evidence_is_rejected(self) -> None:
        self.write_issue(
            "04-dry.md",
            """
            # Build

            Status: resolved
            Type: build-action

            ## Comments

            real-run: log=/logs/build.log execute=True prefixes=real --nobuild
            """,
        )

        errors = tracker_lint.check_tracker(self.root / ".scratch", allowlist=set())

        self.assertEqual([error.code for error in errors], ["invalid-real-run"])
        self.assertIn("--nobuild", errors[0].message)

    def test_accepts_closed_build_action_with_real_run_log_and_prefixes(self) -> None:
        self.write_issue(
            "05-real.md",
            """
            # Build

            Status: ready-for-review
            Type: build-action

            ## Comments

            real-run: log=/vaso/logs/torch-build.log execute=True prefixes=real
            """,
        )

        self.assertEqual(tracker_lint.check_tracker(self.root / ".scratch", allowlist=set()), [])

    def test_allowlist_grandfathers_existing_violation_but_must_shrink(self) -> None:
        path = self.write_issue(
            "06-grandfather.md",
            """
            # Build

            Status: done
            Type: build-action
            """,
        )

        key = tracker_lint.AllowlistEntry(".scratch/effort/issues/06-grandfather.md", "missing-real-run")
        self.assertEqual(tracker_lint.check_tracker(self.root / ".scratch", allowlist={key}), [])

        path.write_text(
            "# Build\n\nStatus: done\nType: build-action\n\n"
            "real-run: log=/vaso/logs/build.log execute=True prefixes=real\n",
            encoding="utf-8",
        )

        errors = tracker_lint.check_tracker(self.root / ".scratch", allowlist={key})
        self.assertEqual([error.code for error in errors], ["stale-allowlist"])

    def test_lints_specs_as_well_as_issues(self) -> None:
        self.write_spec(
            """
            # Spec

            Status: definitely-maybe
            """,
        )

        errors = tracker_lint.check_tracker(self.root / ".scratch", allowlist=set())

        self.assertEqual([error.code for error in errors], ["invalid-status"])


if __name__ == "__main__":
    unittest.main()
