"""Action-time receipt writer must not enumerate or hash prefix contents."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("receipt", Path(__file__).with_name("prefix_manifest.py"))
writer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(writer)

class ActionReceiptTest(unittest.TestCase):
    def test_no_walk_hash_or_member_reads(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);prefix=root/"prefix";prefix.mkdir();out=root/"out"
            with patch.object(os,"walk",side_effect=AssertionError("no inventory")), \
                 patch.object(Path,"read_bytes",side_effect=AssertionError("no reads")), \
                 patch.object(Path,"read_text",side_effect=AssertionError("no reads")), \
                 patch.object(hashlib,"sha256",side_effect=AssertionError("no hash")):
                writer.write_manifest(prefix,out,name="torch")
            self.assertEqual(json.loads(out.read_text()),
                {"schema_version":2,"kind":"prefix_presence_receipt","prefix_present":True,
                 "integrity":"not_checked","name":"torch"})
    def test_missing_prefix_fails(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                writer.write_manifest(Path(d)/"missing",Path(d)/"out",name="torch")

if __name__ == "__main__":unittest.main()
