#!/usr/bin/env python3
"""Tests for collected_ids.py."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

SCRIPT = Path(__file__).with_name("collected_ids.py")
SPEC = importlib.util.spec_from_file_location("collected_ids", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
collected_ids = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = collected_ids
SPEC.loader.exec_module(collected_ids)


class NormalizeTest(unittest.TestCase):
    def test_prefix_and_suffix_forms_name_the_same_test(self) -> None:
        self.assertEqual(
            collected_ids.normalize("tests/test_cli.py::test_help"),
            collected_ids.normalize("harness/vaso/cli_test.py::test_help"),
        )

    def test_class_and_parameter_parts_are_kept(self) -> None:
        self.assertEqual(
            collected_ids.normalize(
                "tools/ci/ci_loop_test.py::TestLoop::test_runs[a-b]"
            ),
            "ci_loop::TestLoop::test_runs[a-b]",
        )

    def test_a_directory_move_does_not_change_the_id(self) -> None:
        self.assertEqual(
            collected_ids.normalize("tests/test_x.py::test_y"),
            collected_ids.normalize("tools/agents/x_test.py::test_y"),
        )

    def test_different_tests_stay_different(self) -> None:
        self.assertNotEqual(
            collected_ids.normalize("tests/test_x.py::test_a"),
            collected_ids.normalize("tests/test_x.py::test_b"),
        )


class CompareTest(unittest.TestCase):
    def test_identical_sets_have_no_difference(self) -> None:
        self.assertEqual(
            collected_ids.compare(["a::t", "b::t"], ["b::t", "a::t"]), ([], [])
        )

    def test_lost_and_gained_ids_are_reported(self) -> None:
        self.assertEqual(
            collected_ids.compare(["a::t", "b::t"], ["b::t", "c::t"]),
            (["a::t"], ["c::t"]),
        )

    def test_a_duplicated_id_counts(self) -> None:
        self.assertEqual(
            collected_ids.compare(["a::t", "a::t"], ["a::t"]), (["a::t"], [])
        )


class CollectTest(unittest.TestCase):
    def test_collection_ids_are_independent_of_terminal_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "x_test.py").write_text("def test_real(): pass\n")
            (root / "conftest.py").write_text("print('not_a_test.py::test_phantom')\n")
            for options in ("-s", "-s -qq", "-vv"):
                with (
                    self.subTest(options=options),
                    patch.dict(os.environ, {"PYTEST_ADDOPTS": options}),
                ):
                    self.assertEqual(
                        collected_ids.collect(["."], cwd=root),
                        ["x::test_real"],
                    )

    def test_empty_collection_has_no_phantom_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "conftest.py").write_text("print('not_a_test.py::test_phantom')\n")
            with patch.dict(os.environ, {"PYTEST_ADDOPTS": "-s"}):
                self.assertEqual(collected_ids.collect(["."], cwd=root), [])

    def test_default_collection_uses_the_project_testpaths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "selected").mkdir()
            (root / "selected/test_one.py").write_text("def test_selected(): pass\n")
            (root / "test_other.py").write_text("def test_unselected(): pass\n")
            (root / "pyproject.toml").write_text(
                '[tool.pytest.ini_options]\ntestpaths = ["selected"]\n'
            )
            baseline = root / "baseline.txt"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--cwd",
                    str(root),
                    "--write",
                    str(baseline),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("recorded 1 test ids", result.stdout)
            self.assertEqual(baseline.read_text(), "one::test_selected\n")

    def test_collects_prefix_and_suffix_named_tests_under_the_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "tests" / "test_one.py").write_text(
                "def test_a():\n    pass\n", encoding="utf-8"
            )
            (root / "pkg").mkdir()
            (root / "pkg" / "two_test.py").write_text(
                "import pytest\n\n@pytest.mark.parametrize('n', [1, 2])\ndef test_b(n):\n    pass\n",
                encoding="utf-8",
            )
            (root / "pkg" / "helper.py").write_text(
                "def test_not_collected():\n    pass\n", encoding="utf-8"
            )

            ids = collected_ids.collect(["tests", "pkg"], cwd=root)

            self.assertEqual(
                sorted(ids), ["one::test_a", "two::test_b[1]", "two::test_b[2]"]
            )

    def test_a_collection_error_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bad_test.py").write_text(
                "import not_a_real_module_xyz\n", encoding="utf-8"
            )

            with self.assertRaises(RuntimeError):
                collected_ids.collect(["."], cwd=root)

    def test_check_mode_exit_status_follows_the_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "x_test.py").write_text(
                "def test_a():\n    pass\n", encoding="utf-8"
            )
            baseline = root / "baseline.txt"
            run = lambda *args: subprocess.run(  # noqa: E731
                [
                    sys.executable,
                    str(SCRIPT),
                    "--cwd",
                    str(root),
                    "--roots",
                    ".",
                    *args,
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(run("--write", str(baseline)).returncode, 0)
            self.assertEqual(run("--check", str(baseline)).returncode, 0)
            (root / "y_test.py").write_text(
                "def test_b():\n    pass\n", encoding="utf-8"
            )
            changed = run("--check", str(baseline))
            self.assertEqual(changed.returncode, 1)
            self.assertIn("y::test_b", changed.stdout + changed.stderr)


if __name__ == "__main__":
    unittest.main()
