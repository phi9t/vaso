from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile
from unittest import mock

SPEC = importlib.util.spec_from_file_location("seed_capsule", Path(__file__).with_name("seed_pascal_capsule.py"))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class TransferSeedTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def archive(self, member="llvm-fixture/include/fixture.h"):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            payload = b"fixture header\n"
            info = tarfile.TarInfo(member)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        return stream.getvalue()

    def lock(self, data):
        return {
            "schema": 1, "nvidia": [],
            "cuda_debs": {"base": "https://developer.download.nvidia.com/", "packages": []},
            "llvm": {
                "url": "https://oaitriton.blob.core.windows.net/public/llvm-fixture.tar.gz",
                "filename": "llvm-fixture.tar.gz",
                "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
            },
            "allowed": {"cuda": [], "llvm": ["include/fixture.h"]},
        }

    def write_manifest(self, data):
        path = self.root / "manifest.json"
        path.write_text(json.dumps(data))
        return path

    def test_public_pins_and_relative_archive_names_are_required(self):
        lock = self.lock(self.archive())
        for key, value in [
            ("url", "file:///tmp/private"),
            ("url", "https://files.pythonhosted.org/archive?token=private"),
            ("filename", "../escape.tar.gz"),
            ("sha256", "not-a-sha"),
        ]:
            with self.subTest(key=key, value=value):
                invalid = json.loads(json.dumps(lock))
                invalid["llvm"][key] = value
                with self.assertRaises(ValueError):
                    MODULE.archives(invalid)

    def test_mismatched_acquisition_retains_partial_without_acceptance(self):
        data = self.archive()
        item = self.lock(data)["llvm"]
        cache = self.root / "cache"
        cache.mkdir()
        with mock.patch.object(MODULE.urllib.request, "urlopen", return_value=io.BytesIO(b"corrupt")):
            with self.assertRaisesRegex(ValueError, "does not match"):
                MODULE.acquire(item, cache)
        self.assertFalse((cache / item["filename"]).exists())
        self.assertTrue((cache / (item["filename"] + ".partial")).exists())

    def test_verified_archive_can_be_reused_without_network(self):
        data = self.archive()
        item = self.lock(data)["llvm"]
        cache = self.root / "cache"
        cache.mkdir()
        (cache / item["filename"]).write_bytes(data)
        with mock.patch.object(MODULE.urllib.request, "urlopen") as request:
            result = MODULE.acquire(item, cache)
        request.assert_not_called()
        self.assertEqual(result["sha256"], item["sha256"])

    def test_existing_rootfs_is_preserved(self):
        data = self.archive()
        manifest = self.write_manifest(self.lock(data))
        target = self.root / "rootfs"
        target.mkdir()
        important = target / "existing"
        important.write_text("preserve")
        with self.assertRaisesRegex(ValueError, "fresh, empty"):
            MODULE.seed(manifest, target, self.root / "cache", self.root / "receipt.json", 1)
        self.assertEqual(important.read_text(), "preserve")

    def test_manifest_path_escape_fails_before_mutation(self):
        lock = self.lock(self.archive())
        lock["allowed"]["llvm"] = ["../../escape"]
        with self.assertRaisesRegex(ValueError, "unsafe relative path"):
            MODULE.seed(self.write_manifest(lock), self.root / "rootfs",
                        self.root / "cache", self.root / "receipt.json", 1)
        self.assertFalse((self.root / "rootfs").exists())

    def test_archive_escape_is_failed_and_recorded(self):
        data = self.archive("../escape")
        receipt = self.root / "receipt.json"
        with mock.patch.object(MODULE.urllib.request, "urlopen", return_value=io.BytesIO(data)):
            with self.assertRaises(tarfile.FilterError):
                MODULE.seed(self.write_manifest(self.lock(data)), self.root / "rootfs",
                            self.root / "cache", receipt, 1)
        self.assertEqual(json.loads(receipt.read_text())["state"], "failed")
        self.assertFalse((self.root / "escape").exists())

    def test_cuda_provider_seeds_both_library_layouts(self):
        llvm = self.archive()
        wheel_buffer = io.BytesIO()
        with zipfile.ZipFile(wheel_buffer, "w") as archive:
            archive.writestr("nvidia/fixture/lib/libfixture.so.1", b"native library")
        wheel = wheel_buffer.getvalue()
        lock = self.lock(llvm)
        lock["nvidia"] = [{
            "filename": "nvidia_fixture.whl", "size": len(wheel),
            "sha256": hashlib.sha256(wheel).hexdigest(),
            "url": "https://files.pythonhosted.org/nvidia_fixture.whl",
        }]
        paths = [prefix + "/" + filename
                 for prefix in ["lib64", "targets/x86_64-linux/lib"]
                 for filename in ["libfixture.so", "libfixture.so.1"]]
        lock["allowed"]["cuda"] = paths
        def response(url, **kwargs):
            return io.BytesIO(wheel if url.endswith(".whl") else llvm)
        target = self.root / "rootfs"
        with mock.patch.object(MODULE.urllib.request, "urlopen", side_effect=response):
            MODULE.seed(self.write_manifest(lock), target, self.root / "cache",
                        self.root / "receipt.json", 1)
        for relative in paths:
            with self.subTest(relative=relative):
                self.assertEqual((target / "opt/vaso/cuda" / relative).read_bytes(), b"native library")

    def test_seed_success_still_requires_authoritative_reconciliation(self):
        data = self.archive()
        receipt = self.root / "receipt.json"
        target = self.root / "rootfs"
        with mock.patch.object(MODULE.urllib.request, "urlopen", return_value=io.BytesIO(data)):
            result = MODULE.seed(self.write_manifest(self.lock(data)), target,
                                 self.root / "cache", receipt, 1)
        self.assertEqual((target / "opt/vaso/pascal/llvm/include/fixture.h").read_bytes(), b"fixture header\n")
        self.assertEqual(result["state"], "transfer_seeds_ready")
        self.assertTrue(result["requires_authoritative_delta_reconciliation"])


if __name__ == "__main__":
    unittest.main()
