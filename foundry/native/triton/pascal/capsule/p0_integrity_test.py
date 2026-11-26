"""Offline regressions for checkpoint admission and transactional preparation."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import inkling_acquire_production as acquire

HERE = Path(__file__).resolve().parent


class Response(io.BytesIO):
    status = 200
    headers = {}


class AcquisitionTests(unittest.TestCase):
    good = b"abc123"
    bad = b"xyz123"
    name = "asset.bin"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.digest = hashlib.sha256(self.good).hexdigest()
        self.files = patch.object(acquire, "FILES", {self.name: (len(self.good), self.digest)})
        self.total = patch.object(acquire, "TOTAL_BYTES", len(self.good))
        self.files.start()
        self.total.start()
        self.addCleanup(self.files.stop)
        self.addCleanup(self.total.stop)

    def invoke(self):
        args = SimpleNamespace(output=self.root, max_seconds=30, retries=1,
                               socket_timeout=1, progress_seconds=1)
        with contextlib.redirect_stdout(io.StringIO()):
            result = acquire.acquire(args)
        return result, json.loads((self.root / "acquisition.json").read_text())

    def test_existing_same_size_corrupt_even_with_old_receipt(self):
        (self.root / self.name).write_bytes(self.bad)
        (self.root / "acquisition.json").write_text(json.dumps({
            "repo": acquire.REPO, "revision": acquire.REVISION,
            "complete": True, "files": [
                {"name": self.name, "bytes": len(self.good), "sha256": self.digest}
            ]}))
        with patch.object(acquire.urllib.request, "urlopen", side_effect=lambda *a, **k: Response(self.good)) as net:
            rc, receipt = self.invoke()
        self.assertEqual(rc, 0)
        self.assertTrue(receipt["complete"])
        self.assertEqual(net.call_count, 1)
        self.assertEqual((self.root / self.name).read_bytes(), self.good)
        self.assertEqual(next(self.root.glob("*.corrupt-*")).joinpath(self.name).read_bytes(), self.bad)

    def test_valid_existing_rehashed_without_network_even_without_receipt(self):
        (self.root / self.name).write_bytes(self.good)
        with patch.object(acquire.urllib.request, "urlopen", side_effect=AssertionError("network")):
            rc, receipt = self.invoke()
        self.assertEqual(rc, 0)
        self.assertTrue(receipt["files"][0]["sha256_matches_pinned"])

    def test_corrupted_full_partial_recovered(self):
        (self.root / (self.name + ".part")).write_bytes(self.bad)
        with patch.object(acquire.urllib.request, "urlopen", side_effect=lambda *a, **k: Response(self.good)) as net:
            rc, receipt = self.invoke()
        self.assertEqual(rc, 0)
        self.assertTrue(receipt["complete"])
        self.assertEqual(net.call_count, 1)
        self.assertEqual((self.root / self.name).read_bytes(), self.good)
        self.assertEqual(next(self.root.glob("*.corrupt-*")).joinpath(self.name + ".part").read_bytes(), self.bad)

    def test_fresh_corrupt_download_is_quarantined_then_retry_recovers(self):
        with patch.object(acquire.urllib.request, "urlopen", side_effect=lambda *a, **k: Response(self.bad)):
            rc, receipt = self.invoke()
        self.assertEqual(rc, 1)
        self.assertFalse(receipt["complete"])
        self.assertFalse((self.root / self.name).exists())
        self.assertFalse((self.root / (self.name + ".part")).exists())
        with patch.object(acquire.urllib.request, "urlopen", side_effect=lambda *a, **k: Response(self.good)):
            rc, receipt = self.invoke()
        self.assertEqual(rc, 0)
        self.assertTrue(receipt["complete"])

    def test_corrupted_existing_is_not_admitted_when_reacquisition_fails(self):
        (self.root / self.name).write_bytes(self.bad)
        with patch.object(acquire.urllib.request, "urlopen", side_effect=OSError(28, "disk full")):
            rc, receipt = self.invoke()
        self.assertEqual(rc, 1)
        self.assertFalse(receipt["complete"])
        self.assertEqual(receipt["files"], [])
        self.assertFalse((self.root / self.name).exists())


class PreparationTests(unittest.TestCase):
    source_name = "triton-675c59878aa2280b31f722aaf42b825fcee21de8"
    valid_patch = "--- a/example.txt\n+++ b/example.txt\n@@ -1 +1 @@\n-old\n+new\n"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bundle = self.root / "pascal"
        self.workspace = self.root / "workspace"
        self.bundle.mkdir()
        self.workspace.mkdir()
        (self.bundle / "bin").mkdir()
        with tarfile.open(self.bundle / (self.source_name + ".tar.gz"), "w:gz") as archive:
            data = b"old\n"
            item = tarfile.TarInfo(self.source_name + "/example.txt")
            item.size = len(data)
            archive.addfile(item, io.BytesIO(data))
        self.patch = self.bundle / "sm61-signed-int8-dot.patch"
        self.patch.write_text(self.valid_patch)
        uv = self.bundle / "bin/uv"
        uv.write_text("#!/bin/sh\nset -eu\ntest \"$(cat example.txt)\" = new\nprintf 'build\\n' >> build-called\n")
        uv.chmod(0o755)
        self.script = self.root / "build.sh"
        self.script.write_text((HERE / "build-triton.sh").read_text()
                               .replace("/workspace", str(self.workspace))
                               .replace("/opt/vaso/pascal", str(self.bundle)))

    def invoke(self, *args):
        return subprocess.run(["bash", str(self.script), *args],
                              capture_output=True, text=True, timeout=15)

    def trees(self):
        return list(self.workspace.glob(self.source_name + ".prepared-*"))

    def test_failed_patch_never_publishes_or_builds_across_retries(self):
        self.patch.write_text("invalid patch\n")
        for _ in range(2):
            self.assertNotEqual(self.invoke().returncode, 0)
            self.assertEqual(self.trees(), [])
            self.assertEqual(list(self.workspace.glob(".triton-prepare.*")), [])

    def test_preexisting_legacy_tree_cannot_skip_patch(self):
        old = self.workspace / self.source_name
        old.mkdir()
        (old / "example.txt").write_text("old\n")
        self.assertEqual(self.invoke().returncode, 0)
        prepared, = self.trees()
        self.assertEqual((prepared / "example.txt").read_text(), "new\n")
        self.assertEqual((old / "example.txt").read_text(), "old\n")
        self.assertTrue((prepared / "build-called").is_file())

    def test_valid_preparation_reused_and_tampering_fails_closed(self):
        self.assertEqual(self.invoke().returncode, 0)
        prepared, = self.trees()
        self.assertEqual(self.invoke().returncode, 0)
        self.assertEqual((prepared / "build-called").read_text(), "build\nbuild\n")
        (prepared / "example.txt").write_text("old\n")
        self.assertNotEqual(self.invoke().returncode, 0)
        self.assertEqual((prepared / "build-called").read_text(), "build\nbuild\n")

    def test_missing_marker_fails_closed(self):
        self.assertEqual(self.invoke().returncode, 0)
        prepared, = self.trees()
        (prepared / ".vaso-prepared").unlink()
        self.assertNotEqual(self.invoke().returncode, 0)

    def test_prepare_only_does_not_build(self):
        self.assertEqual(self.invoke("--prepare-only").returncode, 0)
        prepared, = self.trees()
        self.assertFalse((prepared / "build-called").exists())
        self.assertEqual((prepared / "example.txt").read_text(), "new\n")

    def test_concurrent_preparation_serialized(self):
        first = subprocess.Popen(["bash", str(self.script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        second = subprocess.Popen(["bash", str(self.script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for child in (first, second):
            child.communicate(timeout=15)
            self.assertEqual(child.returncode, 0)
        prepared, = self.trees()
        self.assertEqual((prepared / "build-called").read_text(), "build\nbuild\n")


if __name__ == "__main__":
    unittest.main()
