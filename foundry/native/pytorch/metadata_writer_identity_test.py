import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import metadata_writer as writer


class MetadataIdentityTest(unittest.TestCase):
    def setUp(self):
        self.plan = {
            "profile": "torch", "line": "cu133",
            "package": {"name": "py-torch", "version": "2.15.0"},
            "sources": [{"name": "main", "sha256": "a" * 64}],
            "toolchain": {"python": "python_313_native"},
            "gpu_archs": ["sm_100"],
        }

    def test_variant_reuses_archive_receipt_without_hashing(self):
        with patch.object(hashlib, "sha256", side_effect=AssertionError("no hashing")):
            self.assertEqual(writer.metadata_identity(self.plan, {}, source_archive_only=True),
                             ("a" * 64, "source-archive-only"))

    def test_legacy_still_requires_explicit_key(self):
        with self.assertRaises(SystemExit):
            writer.metadata_identity(self.plan, {"packages": {}})
        keys = {"packages": {"torch/cu133/py-torch": {"merkle_key": "existing", "key_kind": "declared-metadata"}}}
        self.assertEqual(writer.metadata_identity(self.plan, keys), ("existing", "declared-metadata"))

    def test_rejects_missing_duplicate_and_malformed_archive_receipts(self):
        for sources in ([], self.plan["sources"] * 2, [{"name": "main", "sha256": "bad"}]):
            with self.subTest(sources=sources), self.assertRaises(SystemExit):
                writer.metadata_identity({**self.plan, "sources": sources}, {}, source_archive_only=True)

    def test_outputs_do_not_read_legacy_keys_for_explicit_variant(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = root / "plan.json"
            plan.write_text(json.dumps(self.plan))
            args = argparse.Namespace(
                plan=plan, keys=root / "not-present.json", source_archive_identity=True, relocation_audit_deferred=True,
                rootfs_identity=root / "rootfs.json", wheel=root / "torch-2.15.0-cp313-cp313-linux_x86_64.whl",
                build_plan_out=root / "build-plan.json", provider_metadata_out=root / "metadata.json",
                result_marker_out=root / "result.txt", dep_prefix=[], python_abi="derived")
            with patch.object(hashlib, "sha256", side_effect=AssertionError("no hashing")):
                writer.write_outputs(args)
            output = json.loads(args.provider_metadata_out.read_text())
            self.assertEqual(output["action_input_identity_kind"], "source-archive-only")
            self.assertEqual(output["action_input_digest"], "a" * 64)
            self.assertEqual(output["python_abi"], "cp313")
            self.assertEqual(output["build_relocation_audit"], "deferred-not-verified")


if __name__ == "__main__":
    unittest.main()
