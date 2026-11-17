"""Rootfs acquisition mode tests without content-hash computation or network."""
import argparse
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("artifact_store", Path(__file__).with_name("artifact_store.py"))
store = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(store)


class RootfsArchivePullTest(unittest.TestCase):
    def args(self, target, **changes):
        values = dict(tag=None, digest="sha256:" + "a" * 64, registry=None,
                      dry_run=False, artifact="rootfs", profile=None, line="cu130",
                      target_dir=target, rootfs_archive_only=True)
        values.update(changes)
        return argparse.Namespace(**values)

    def fixture(self):
        return {
            "io.vaso.artifact.kind": "rootfs", "io.vaso.line": "cu130",
            "io.vaso.file.count": "2",
            "io.vaso.file.0000.path": "rootfs/bundle.tar.zst.part0000",
            "io.vaso.file.0000.sha256": "b" * 64, "io.vaso.file.0000.size": "7",
            "io.vaso.file.0001.path": "rootfs-bundle.json",
            "io.vaso.file.0001.sha256": "c" * 64, "io.vaso.file.0001.size": "2",
            "io.vaso.rootfs.archive": "bundle.tar.zst",
            "io.vaso.rootfs.digest": "d" * 64,
            "io.vaso.rootfs.size": "7", "io.vaso.rootfs.part.count": "1",
        }

    def run_pull(self, target, annotations=None, actual_digest=None, oras_error=None):
        calls = []
        reports = []
        def pull(args, argv, **kwargs):
            self.assertEqual(argv[0], "pull")
            if oras_error:
                raise store.StoreError(oras_error)
            stage = Path(argv[-1])
            (stage / "rootfs").mkdir()
            (stage / "rootfs/bundle.tar.zst.part0000").write_bytes(b"archive")
            (stage / "rootfs-bundle.json").write_text("{}")
        def archive_digest(path):
            self.assertEqual(path.name, "bundle.tar.zst", "must never hash metadata or parts")
            calls.append(path.name)
            return actual_digest or "d" * 64
        with patch.object(store, "target_reference", return_value=("ref", "package", None)), \
             patch.object(store, "prepare_remote_auth", return_value={}), \
             patch.object(store, "fetch_manifest", return_value={"annotations": annotations or self.fixture()}), \
             patch.object(store, "run_oras", side_effect=pull), \
             patch.object(store, "verify_files", side_effect=AssertionError("per-file hashing forbidden")), \
             patch.object(store, "sha256_file", side_effect=archive_digest), \
             patch.object(store, "emit", side_effect=lambda args, report: reports.append(report) or 0):
            result = store.pull_artifact(self.args(target))
        return result, calls, reports

    def test_complete_archive_is_still_checked_without_per_file_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "restored"
            result, calls, reports = self.run_pull(target)
            self.assertEqual(result, 0)
            self.assertEqual(calls, ["bundle.tar.zst"])
            self.assertEqual((target / "bundle.tar.zst").read_bytes(), b"archive")
            self.assertEqual(reports[0]["verified_files"], 0)
            self.assertEqual(reports[0]["verification"], "oci-blobs-and-rootfs-archive")

    def test_bad_archive_digest_blocks_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "restored"
            with self.assertRaisesRegex(store.StoreError, "archive sha256 mismatch"):
                self.run_pull(target, actual_digest="e" * 64)
            self.assertFalse(target.exists())

    def test_bad_archive_size_blocks_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            annotations = self.fixture()
            annotations["io.vaso.rootfs.size"] = "8"
            with self.assertRaisesRegex(store.StoreError, "archive size mismatch"):
                self.run_pull(Path(tmp) / "restored", annotations=annotations)

    def test_oras_verification_failure_is_not_bypassed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(store.StoreError, "OCI blob mismatch"):
                self.run_pull(Path(tmp) / "restored", oras_error="OCI blob mismatch")

    def test_requires_rootfs_and_immutable_digest_before_transport(self):
        for changes in (dict(artifact="release"), dict(digest=None),
                        dict(tag="latest"), dict(digest="not-a-digest")):
            with self.subTest(changes=changes), patch.object(store, "target_reference", side_effect=AssertionError("transport reached")):
                with self.assertRaisesRegex(store.StoreError, "requires --artifact rootfs"):
                    store.pull_artifact(self.args(Path("unused"), **changes))

    def test_cli_option_is_explicit_not_default(self):
        argv = ["pull", "--artifact", "rootfs", "--line", "cu130", "--target-dir", "unused"]
        self.assertFalse(store.parser().parse_args(argv).rootfs_archive_only)
        self.assertTrue(store.parser().parse_args(argv + ["--rootfs-archive-only"]).rootfs_archive_only)


if __name__ == "__main__":
    unittest.main()
