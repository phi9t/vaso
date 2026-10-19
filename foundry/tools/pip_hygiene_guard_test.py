#!/usr/bin/env python3
"""Tests for pip_hygiene_guard.py."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("pip_hygiene_guard.py")
assert SCRIPT.exists(), "pip hygiene guard is missing"
SPEC = importlib.util.spec_from_file_location("pip_hygiene_guard", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
guard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = guard
SPEC.loader.exec_module(guard)


def rule_fixture(rule_id: str, test_method: str) -> tuple[str, str]:
    assert rule_id in guard.RULE_IDS
    return rule_id, test_method


RULE_FIXTURES = dict(
    (
        rule_fixture("missing-no-index", "test_pip_command_requires_no_index"),
        rule_fixture("missing-hash-policy", "test_pip_command_requires_hashes_or_manifested_wheelhouse"),
        rule_fixture("wheelhouse-manifest", "test_local_wheelhouse_with_sha256_manifest_passes"),
        rule_fixture("native-source-scope", "test_default_sources_cover_native_action_drivers_plans_and_run_sh"),
    )
)


def scratch_tmpdir() -> Path:
    raw = os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR")
    if not raw:
        raise RuntimeError("TEST_TMPDIR or TMPDIR must be set")
    root = Path(raw)
    root.mkdir(parents=True, exist_ok=True)
    return root


class PipHygieneGuardTest(unittest.TestCase):
    def test_rule_fixture_manifest_matches_guard_rule_ids(self) -> None:
        self.assertEqual(set(RULE_FIXTURES), guard.RULE_IDS)
        missing = sorted(name for name in RULE_FIXTURES.values() if not hasattr(self, name))
        self.assertEqual(missing, [])

    def test_pip_command_requires_no_index(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_tmpdir()) as tmp:
            root = Path(tmp)
            source = root / "native" / "pkg" / "action_driver.py"
            source.parent.mkdir(parents=True)
            source.write_text(
                "cmd = ['python', '-m', 'pip', 'install', '--no-deps', 'pkg.whl']\n",
                encoding="utf-8",
            )

            errors = guard.check_sources([source], repo_root=root)

        self.assertEqual(
            errors,
            [
                "native/pkg/action_driver.py:1: pip install is missing --no-index",
                "native/pkg/action_driver.py:1: pip install lacks --require-hashes or a sha256-pinned local wheelhouse",
            ],
        )

    def test_pip_command_requires_hashes_or_manifested_wheelhouse(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_tmpdir()) as tmp:
            root = Path(tmp)
            source = root / "native" / "pkg" / "action_driver.py"
            source.parent.mkdir(parents=True)
            source.write_text(
                "cmd = ['python', '-m', 'pip', 'install', '--no-index', '--no-deps', 'pkg.whl']\n",
                encoding="utf-8",
            )

            errors = guard.check_sources([source], repo_root=root)

        self.assertEqual(
            errors,
            ["native/pkg/action_driver.py:1: pip install lacks --require-hashes or a sha256-pinned local wheelhouse"],
        )

    def test_local_wheelhouse_with_sha256_manifest_passes(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_tmpdir()) as tmp:
            root = Path(tmp)
            source = root / "native" / "pkg" / "action_driver.py"
            source.parent.mkdir(parents=True)
            source.write_text(
                "wheelhouse = build_work / 'wheelhouse'\n"
                "manifest = [{'name': wheel.name, 'sha256': _file_sha256(wheel)} for wheel in wheels]\n"
                "cmd = [\n"
                "    python,\n"
                "    '-m',\n"
                "    'pip',\n"
                "    'install',\n"
                "    '--no-index',\n"
                "    '--no-deps',\n"
                "    str(wheelhouse / manifest[0]['name']),\n"
                "]\n",
                encoding="utf-8",
            )

            self.assertEqual(guard.check_sources([source], repo_root=root), [])

    def test_default_sources_cover_native_action_drivers_plans_and_run_sh(self) -> None:
        rels = [path.as_posix() for path in guard.default_source_paths(Path("repo"))]

        self.assertIn("repo/run.sh", rels)
        self.assertIn("repo/native/pytorch/action_driver.py", rels)
        self.assertIn("repo/native/jaxlib/plan.py", rels)


if __name__ == "__main__":
    unittest.main()
