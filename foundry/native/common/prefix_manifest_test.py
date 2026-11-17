"""Repository receipt tests: no recursive inventory, content reads, or hashes."""
import ast
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).parent
def script():
    text = (HERE / "prefix_manifest.bzl").read_text()
    # Starlark constant is valid as a standalone Python assignment.
    start = text.index('_PREFIX_MANIFEST_SCRIPT = r"""')
    end = text.index('\n"""', start) + 4
    return ast.literal_eval(ast.parse(text[start:end]).body[0].value)

class ReceiptTest(unittest.TestCase):
    def run_receipt(self, prefix, out, *deps):
        code = script()
        original_read = Path.read_text
        allowed = {Path(dep.split("=", 1)[1]) for dep in deps if "=" in dep}
        def read_receipt(path, *args, **kwargs):
            if path not in allowed:
                raise AssertionError("only explicit dependency receipts may be read")
            return original_read(path, *args, **kwargs)
        with patch.object(Path, "read_text", read_receipt), patch.object(sys, "argv", ["receipt", str(prefix), str(out), *deps]), \
             patch.object(os, "walk", side_effect=AssertionError("no inventory")), \
             patch.object(Path, "read_bytes", side_effect=AssertionError("no content reads")), \
             patch.object(hashlib, "sha256", side_effect=AssertionError("no hashes")):
            exec(compile(code, "prefix receipt", "exec"), {})

    def test_presence_only_and_no_identity_claim(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); prefix = root / "prefix"; prefix.mkdir()
            (prefix / "unexamined").write_text("private path is not audited here")
            out = root / "receipt.json"
            self.run_receipt(prefix, out)
            doc = json.loads(out.read_text())
            self.assertEqual(doc["schema_version"], 2)
            self.assertEqual(doc["integrity"], "not_checked")
            self.assertTrue(doc["prefix_present"])
            self.assertNotIn("entries", doc)
            self.assertNotIn("tree_sha256", doc)
            self.assertNotIn(str(prefix), out.read_text())
            before = out.read_text()
            (prefix / "unexamined").write_text("changed")
            self.run_receipt(prefix, out)
            self.assertEqual(before, out.read_text())

    def test_dependencies_are_receipts_not_rehashed_content(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); prefix = root / "prefix"; prefix.mkdir()
            dep = root / "dep.json"; dep.write_text('{"schema_version":1,"tree_sha256":"legacy"}')
            out = root / "receipt.json"
            self.run_receipt(prefix, out, "dep=" + str(dep))
            self.assertEqual(json.loads(out.read_text())["dependencies"],
                             [{"name":"dep","receipt_present":True,"schema_version":1}])

    def test_missing_and_invalid_inputs_fail(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with self.assertRaises(SystemExit): self.run_receipt(root/"missing", root/"out")
            for dep in ("bad", "dep="+str(root/"missing")):
                with self.assertRaises(SystemExit): self.run_receipt(root, root/"out", dep)
            f=root/"invalid";f.write_text("[]")
            with self.assertRaises(SystemExit):self.run_receipt(root, root/"out", "dep="+str(f))

if __name__ == "__main__": unittest.main()
