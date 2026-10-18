"""The rootfs tooling boundary validates pinned contents before execution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import foundry_tools


class FoundryToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / "cache"
        self.cache.mkdir()
        self.rootfs = self.root / "rootfs"
        self.payload = b"#!/bin/sh\nexit 0\n"
        self.sha = hashlib.sha256(self.payload).hexdigest()
        self.lock = self.root / "lock.json"
        self.lock.write_text(json.dumps({"tools": [{
            "name": "bazel", "version": "test", "kind": "binary",
            "sha256": self.sha, "path": "opt/vaso/bin/bazel-real",
            "url": "https://invalid.example/must-not-fetch",
        }]}))

    def test_missing_or_corrupt_acquisition_leaves_rootfs_untouched(self) -> None:
        for contents in (None, b"corrupt"):
            if contents is not None:
                (self.cache / self.sha).write_bytes(contents)
            with self.assertRaisesRegex(ValueError, "missing or mismatched pinned input"):
                foundry_tools.provision(self.rootfs, self.cache, lock=self.lock)
            self.assertFalse(self.rootfs.exists())

    def test_provision_verify_and_fail_closed_on_installed_drift(self) -> None:
        (self.cache / self.sha).write_bytes(self.payload)
        report = foundry_tools.provision(self.rootfs, self.cache, lock=self.lock)
        self.assertEqual(report["files"]["opt/vaso/bin/bazel-real"], self.sha)
        self.assertEqual(foundry_tools.verify(self.rootfs, self.lock), report)
        binary = self.rootfs / "opt/vaso/bin/bazel-real"
        binary.chmod(0o755)
        binary.write_bytes(b"drift")
        with self.assertRaisesRegex(ValueError, "mismatched rootfs tool"):
            foundry_tools.verify(self.rootfs, self.lock)

    def test_missing_tool_and_lock_mismatch_are_prerequisite_errors(self) -> None:
        (self.cache / self.sha).write_bytes(self.payload)
        foundry_tools.provision(self.rootfs, self.cache, lock=self.lock)
        binary = self.rootfs / "opt/vaso/bin/bazel-real"
        binary.unlink()
        with self.assertRaisesRegex(ValueError, "missing or mismatched rootfs tool"):
            foundry_tools.verify(self.rootfs, self.lock)
        self.lock.write_text(self.lock.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "lock mismatch"):
            foundry_tools.verify(self.rootfs, self.lock)

    def test_rejects_path_traversal(self) -> None:
        for name in ("/usr/bin/tool", "../tool", "opt/../../../tool"):
            with self.assertRaises(ValueError):
                foundry_tools.relative(name)


if __name__ == "__main__":
    unittest.main()
