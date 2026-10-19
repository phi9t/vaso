#!/usr/bin/env python3
"""Live checks for checked-in profile locks."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "tools" / "lock_tool.py"
CANONICAL_LOCK = ROOT / "locks" / "torch.lock.json"


class LockToolLiveTest(unittest.TestCase):
    def test_all_checked_in_profile_locks_validate(self) -> None:
        locks = sorted((ROOT / "locks").glob("*.lock.json"))
        self.assertGreaterEqual(len(locks), 1)

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "validate",
                *[str(path) for path in locks],
                "--ledger",
                str(ROOT / "migration_ledger.json"),
                "--native-overrides",
                str(ROOT / "native_overrides.json"),
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"validated {len(locks)} lock(s)", result.stdout)

    def test_validate_does_not_rewrite_canonical_lock(self) -> None:
        before = CANONICAL_LOCK.read_bytes()

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "validate",
                str(CANONICAL_LOCK),
                "--ledger",
                str(ROOT / "migration_ledger.json"),
                "--native-overrides",
                str(ROOT / "native_overrides.json"),
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(CANONICAL_LOCK.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
