#!/usr/bin/env python3
"""Smoke tests for the configured jaxlib dry-run action target."""

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


class JaxlibActionSmokeTest(unittest.TestCase):
    def test_action_dry_run_outputs_metadata_without_building(self) -> None:
        plan = json.loads(unique_runfile("jaxlib_action_dry_run_build_plan.json").read_text())
        metadata = json.loads(unique_runfile("jaxlib_action_dry_run_provider_metadata.json").read_text())
        manifest = json.loads(unique_runfile("jaxlib_action_dry_run_wheel_manifest.json").read_text())
        marker = unique_runfile("jaxlib_action_dry_run_result.txt")

        self.assertEqual(plan["mode"], "dry-run")
        self.assertTrue(plan["preflight_ok"])
        self.assertFalse(plan["will_build"])
        self.assertFalse(plan["authorization"]["token_present"])
        self.assertEqual(plan["authorization"]["required_token"], "build-native-llvm")
        self.assertEqual(metadata["package"], "py-jaxlib")
        self.assertEqual(metadata["version"], "0.10.2")
        self.assertEqual(metadata["mode"], "dry-run")
        self.assertFalse(metadata["token_present"])
        self.assertFalse(metadata["will_build"])
        self.assertEqual(metadata["build_work"], "")
        self.assertEqual(set(metadata["input_prefixes"]), set(plan["required_prefixes"]))
        for key, prefix in plan["input_prefixes"].items():
            with self.subTest(prefix=key):
                self.assertFalse(prefix.startswith("/tmp/"))
                self.assertFalse(prefix.startswith("/var/tmp/"))
        self.assertIn("rootfs-bundle.json", metadata["rootfs_manifest"])
        self.assertEqual(manifest["wheels"], [])
        self.assertIn("No wheel was built", marker.read_text())


if __name__ == "__main__":
    unittest.main()
