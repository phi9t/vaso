#!/usr/bin/env python3
"""Smoke tests for the configured PyTorch dry-run action targets."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path


def runfiles_root() -> Path:
    for name in ("RUNFILES_DIR", "TEST_SRCDIR"):
        value = os.environ.get(name)
        if value:
            return Path(value)
    return Path.cwd()


def unique_runfile(name: str) -> Path:
    matches = list(runfiles_root().rglob(name))
    if len(matches) != 1:
        raise AssertionError(f"expected one runfile named {name}, found {matches}")
    return matches[0]


class PytorchActionSmokeTest(unittest.TestCase):
    def check_target(self, target: str) -> None:
        plan = json.loads(unique_runfile(f"{target}_build_plan.json").read_text())
        metadata = json.loads(unique_runfile(f"{target}_provider_metadata.json").read_text())
        marker = unique_runfile(f"{target}_result.txt")

        self.assertEqual(plan["mode"], "dry-run")
        self.assertTrue(plan["preflight_ok"])
        self.assertFalse(plan["will_build"])
        self.assertFalse(plan["authorization"]["token_present"])
        self.assertEqual(metadata["package"], "py-torch")
        self.assertEqual(metadata["version"], "2.14.0")
        self.assertEqual(metadata["mode"], "dry-run")
        self.assertFalse(metadata["token_present"])
        self.assertFalse(metadata["will_build"])
        self.assertIn("rootfs-bundle.json", metadata["rootfs_manifest"])
        self.assertIn("No wheel was built", marker.read_text())

    def test_action_dry_run_outputs_metadata_without_building(self) -> None:
        self.check_target("pytorch_action_dry_run")


if __name__ == "__main__":
    unittest.main()
