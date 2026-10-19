#!/usr/bin/env python3
"""Tests for the action-time prefix manifest writer."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("prefix_manifest.py")
SPEC = importlib.util.spec_from_file_location("prefix_manifest_action", SCRIPT)
assert SPEC is not None
prefix_manifest = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = prefix_manifest
SPEC.loader.exec_module(prefix_manifest)


def scratch_parent() -> Path:
    return Path(os.environ.get("TEST_TMPDIR") or os.environ.get("VASO_AGENT_IO_ROOT") or Path.cwd())


class PrefixManifestActionTest(unittest.TestCase):
    def test_tree_digest_ignores_absolute_prefix_location_and_python_cache(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            first = root / "first"
            second = root / "second"
            for prefix in (first, second):
                (prefix / "lib/python/site-packages/pkg/__pycache__").mkdir(parents=True)
                (prefix / "lib/python/site-packages/pkg/__init__.py").write_text("value = 1\n", encoding="utf-8")
                (prefix / "lib/python/site-packages/pkg/__pycache__/ignored.pyc").write_bytes(b"varying")
            first_out = root / "first.json"
            second_out = root / "second.json"

            prefix_manifest.write_manifest(first, first_out, name="torch")
            prefix_manifest.write_manifest(second, second_out, name="torch")

            first_doc = json.loads(first_out.read_text(encoding="utf-8"))
            second_doc = json.loads(second_out.read_text(encoding="utf-8"))
            self.assertEqual(first_doc["tree_sha256"], second_doc["tree_sha256"])
            text = json.dumps(first_doc, sort_keys=True)
            self.assertNotIn(str(first), text)
            self.assertNotIn("__pycache__", text)
            self.assertNotIn(".pyc", text)

    def test_tree_digest_changes_with_content(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            prefix = root / "prefix"
            (prefix / "lib").mkdir(parents=True)
            payload = prefix / "lib" / "libtorch.so"
            payload.write_text("one\n", encoding="utf-8")
            first_out = root / "first.json"
            second_out = root / "second.json"

            prefix_manifest.write_manifest(prefix, first_out, name="torch")
            payload.write_text("two\n", encoding="utf-8")
            prefix_manifest.write_manifest(prefix, second_out, name="torch")

            first_doc = json.loads(first_out.read_text(encoding="utf-8"))
            second_doc = json.loads(second_out.read_text(encoding="utf-8"))
            self.assertNotEqual(first_doc["tree_sha256"], second_doc["tree_sha256"])

    def test_rejects_missing_prefix(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            with self.assertRaises(SystemExit) as raised:
                prefix_manifest.write_manifest(root / "missing", root / "out.json", name="torch")

        self.assertIn("prefix manifest input is not a directory", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
