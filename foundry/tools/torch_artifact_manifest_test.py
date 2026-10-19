#!/usr/bin/env python3
"""Schema checks for committed torch artifact pin manifests."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")


class TorchArtifactManifestTest(unittest.TestCase):
    def test_torch_artifact_manifests_match_keys_snapshot(self) -> None:
        keys = json.loads((ROOT / "plans" / "keys.json").read_text(encoding="utf-8"))
        packages = keys["packages"]

        for line in ("cu129", "cu130"):
            with self.subTest(line=line):
                manifest_path = ROOT / "plans" / "torch" / line / "torch-artifact.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

                self.assertEqual(
                    sorted(manifest),
                    ["archive", "plan_key", "sha256", "source_commit"],
                )
                self.assertIsInstance(manifest["archive"], str)
                self.assertTrue(manifest["archive"])
                if not manifest["archive"].startswith("step-b-pending://"):
                    self.assertFalse(Path(manifest["archive"]).is_absolute())
                    self.assertTrue(manifest["archive"].startswith(f"vaso/artifacts/torch/{line}/"))
                self.assertRegex(manifest["sha256"], HEX_64)
                self.assertRegex(manifest["source_commit"], GIT_SHA)
                self.assertEqual(
                    manifest["plan_key"],
                    packages[f"torch/{line}/py-torch"]["merkle_key"],
                )


if __name__ == "__main__":
    unittest.main()
