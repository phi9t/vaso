import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import repair_wheel


class RepairAuditProvenanceTest(unittest.TestCase):
    def test_preserves_explicit_deferred_audit_without_hashing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provider = {"action_input_digest": "existing-source-receipt",
                        "action_input_identity_kind": "source-archive-only",
                        "package": "py-torch", "declared_cuda_architectures": ["sm_100"]}
            for deferred in (False, True):
                doc = dict(provider)
                if deferred:
                    doc["build_relocation_audit"] = "deferred-not-verified"
                output = root / "provenance.json"
                with patch.object(hashlib, "sha256", side_effect=AssertionError("no custom hashing")):
                    repair_wheel._write_vaso_provenance(
                        output, provider_doc=doc, git_sha="source-commit", profile="torch", line="cu133",
                        lock_path=root / "absent-lock", spack_lock_path=root / "absent-spack",
                        upstream_version="2.15.0")
                result = json.loads(output.read_text())
                if deferred:
                    self.assertEqual(result["build_relocation_audit"], "deferred-not-verified")
                else:
                    self.assertNotIn("build_relocation_audit", result)
                self.assertEqual(result["action_input_identity_kind"], "source-archive-only")


if __name__ == "__main__":
    unittest.main()
