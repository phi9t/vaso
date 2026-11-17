"""Default preparation checks selected paths, never a transitive tree audit."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import prepare
import foundry_tools


class PreparationReadinessTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.destination = self.base / "selected"
        self.rootfs = self.destination / "rootfs"
        for name in ("bin/sh", "opt/vaso/bin/bazel-real"):
            path = self.rootfs / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n")
            path.chmod(0o755)
        self.tools = [{"name": "bazel", "version": "fixture", "kind": "binary",
                       "path": "opt/vaso/bin/bazel-real", "sha256": "archive-receipt-fixture"}]
        self.lock = self.base / "tools.json"
        self.lock.write_text(json.dumps({"tools": self.tools}))
        manifest = {"schema_version": 2, "verification": "selected-path-type-size-not-content",
                    "lock_expected_text": self.lock.read_text(), "wheel_prefixes": [],
                    "tools": [{"name":"bazel","version":"fixture","sha256":"archive-receipt-fixture"}],
                    "files":{"opt/vaso/bin/bazel-real":{"type":"file","size":8}}}
        (self.rootfs / "opt/vaso/foundry-tools.json").write_text(json.dumps(manifest))
        self.selection = {"ecosystem_line":{"components":{"cuda":{"version":"13.3"}}},
                          "ecosystem_components":{}}
        self.bundle = self.destination / "rootfs-bundle.json"
        self.bundle.write_text(json.dumps({"line":"cu133", "lock":{},
                                          "component_versions":{"cuda":"13.3"}}))
        (self.destination / "rootfs-preparation.json").write_text(json.dumps(self.selection))
        self.args = SimpleNamespace(line="cu133", tools_lock=self.lock, lock=self.lock)
        self.attempt = self.base / "attempt"
        self.attempt.mkdir()

    def test_entire_verify_path_never_walks_or_hashes(self):
        with patch.object(os, "walk", side_effect=AssertionError("tree scan forbidden")), \
             patch.object(Path, "rglob", side_effect=AssertionError("tree scan forbidden")), \
             patch.object(foundry_tools, "digest", side_effect=AssertionError("file hash forbidden")), \
             patch.object(prepare.verify_cuda_ecosystem, "build_report", side_effect=AssertionError("full audit forbidden")):
            prepare.verify(self.destination, self.args, self.selection, self.attempt, receipt=True)
        report = json.loads((self.attempt / "ecosystem-readiness.json").read_text())
        self.assertEqual(report["verification"], "selected-readiness-not-full-audit")
        self.assertFalse(report["gpu_qualified"])

    def test_component_mismatch_rejected(self):
        self.bundle.write_text(json.dumps({"line":"cu133", "lock":{}, "component_versions":{}}))
        with self.assertRaisesRegex(ValueError, "component version mismatch"):
            prepare.verify(self.destination, self.args, self.selection, self.attempt, receipt=True)

    def test_missing_selected_executable_rejected(self):
        (self.rootfs / "opt/vaso/bin/bazel-real").unlink()
        with self.assertRaisesRegex(ValueError, "missing or mismatched rootfs tool"):
            prepare.verify(self.destination, self.args, self.selection, self.attempt, receipt=True)

if __name__ == "__main__":
    unittest.main()
