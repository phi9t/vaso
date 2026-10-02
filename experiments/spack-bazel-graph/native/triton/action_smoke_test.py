#!/usr/bin/env python3
"""Smoke tests for the configured Triton dry-run action target."""

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


class TritonActionSmokeTest(unittest.TestCase):
    def test_action_dry_run_outputs_metadata_without_building(self) -> None:
        plan = json.loads(unique_runfile("triton_action_dry_run_build_plan.json").read_text())
        metadata = json.loads(unique_runfile("triton_action_dry_run_provider_metadata.json").read_text())
        marker = unique_runfile("triton_action_dry_run_result.txt")
        wheel = unique_runfile("triton_action_dry_run_wheel.whl")

        self.assertEqual(plan["mode"], "dry-run")
        self.assertTrue(plan["preflight_ok"])
        self.assertFalse(plan["will_build"])
        self.assertFalse(plan["authorization"]["token_present"])
        self.assertEqual(plan["authorization"]["required_token"], "build-native-triton")
        self.assertEqual(metadata["package"], "py-triton")
        self.assertEqual(metadata["version"], "3.8.0")
        self.assertEqual(metadata["mode"], "dry-run")
        self.assertFalse(metadata["token_present"])
        self.assertFalse(metadata["will_build"])
        self.assertEqual(metadata["build_work"], "")
        self.assertEqual(set(metadata["input_prefixes"]), set(plan["required_prefixes"]))
        self.assertIn("rootfs-bundle.json", metadata["rootfs_manifest"])
        self.assertIn("No wheel was built", marker.read_text())
        self.assertIn("No wheel was built", wheel.read_text())


if __name__ == "__main__":
    unittest.main()
