"""The rootfs tooling boundary validates pinned contents before execution."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import foundry_tools


class FoundryToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / "cache"
        self.cache.mkdir()
        self.rootfs = self.root / "rootfs"
        self.payload = b"#!/bin/sh\nexit 0\n"
        self.sha = hashlib.sha256(self.payload).hexdigest()
        self.lock = self.root / "lock.json"
        self.lock.write_text(
            json.dumps(
                {
                    "tools": [
                        {
                            "name": "bazel",
                            "version": "test",
                            "kind": "binary",
                            "sha256": self.sha,
                            "path": "opt/vaso/bin/bazel-real",
                            "url": "https://invalid.example/must-not-fetch",
                        }
                    ]
                }
            )
        )

    def test_missing_or_corrupt_acquisition_leaves_rootfs_untouched(self) -> None:
        for contents in (None, b"corrupt"):
            if contents is not None:
                (self.cache / self.sha).write_bytes(contents)
            with self.assertRaisesRegex(
                ValueError, "missing or mismatched pinned input"
            ):
                foundry_tools.provision(self.rootfs, self.cache, lock=self.lock)
            self.assertFalse(self.rootfs.exists())

    def test_provision_verify_and_fail_closed_on_installed_drift(self) -> None:
        (self.cache / self.sha).write_bytes(self.payload)
        report = foundry_tools.provision(self.rootfs, self.cache, lock=self.lock)
        self.assertEqual(report["files"]["opt/vaso/bin/bazel-real"], self.sha)
        self.assertEqual(foundry_tools.verify(self.rootfs, self.lock), report)
        binary = self.rootfs / "opt/vaso/bin/bazel-real"
        inode = binary.stat().st_ino
        self.assertEqual(
            foundry_tools.provision(self.rootfs, self.cache, lock=self.lock), report
        )
        self.assertEqual(binary.stat().st_ino, inode)
        binary.chmod(0o755)
        binary.write_bytes(b"drift")
        with self.assertRaisesRegex(ValueError, "mismatched rootfs tool"):
            foundry_tools.verify(self.rootfs, self.lock)

    def test_malformed_tool_manifests_have_context_and_preserve_installed_files(
        self,
    ) -> None:
        (self.cache / self.sha).write_bytes(self.payload)
        valid = foundry_tools.provision(self.rootfs, self.cache, lock=self.lock)
        manifest = self.rootfs / "opt/vaso/foundry-tools.json"
        binary = self.rootfs / "opt/vaso/bin/bazel-real"
        cases = [
            "invalid JSON",
            [],
            None,
            {**valid, "schema_version": 2},
            {**valid, "files": ["opt/vaso/bin/bazel-real"]},
            {**valid, "files": {"opt/vaso/bin/bazel-real": None}},
            {**valid, "files": {"opt/vaso/bin/bazel-real": "bad hash"}},
        ]
        for value in cases:
            with self.subTest(value=value):
                raw = value if isinstance(value, str) else json.dumps(value)
                manifest.write_text(raw)
                result = subprocess.run(
                    [
                        sys.executable,
                        str(Path(foundry_tools.__file__)),
                        "--rootfs",
                        str(self.rootfs),
                        "--lock",
                        str(self.lock),
                        "--verify",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn(str(manifest), result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(manifest.read_text(), raw)
                self.assertEqual(binary.read_bytes(), self.payload)

    def test_missing_tool_and_lock_mismatch_are_prerequisite_errors(self) -> None:
        (self.cache / self.sha).write_bytes(self.payload)
        foundry_tools.provision(self.rootfs, self.cache, lock=self.lock)
        binary = self.rootfs / "opt/vaso/bin/bazel-real"
        binary.unlink()
        with self.assertRaisesRegex(ValueError, "missing or mismatched rootfs tool"):
            foundry_tools.verify(self.rootfs, self.lock)
        self.lock.write_text(self.lock.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "lock mismatch"):
            foundry_tools.verify(self.rootfs, self.lock)

    def test_failed_install_leaves_the_tools_prefix_retryable(self) -> None:
        (self.cache / self.sha).write_bytes(self.payload)
        doc = json.loads(self.lock.read_text())
        second = dict(doc["tools"][0], name="second", path="opt/vaso/bin/second")
        doc["tools"].append(second)
        self.lock.write_text(json.dumps(doc))
        copy = shutil.copyfile

        def fail_second(source, target):
            if Path(target).name == "second":
                raise OSError("installation interrupted")
            return copy(source, target)

        for empty_prefix in (False, True):
            with self.subTest(empty_prefix=empty_prefix):
                rootfs = self.root / ("empty" if empty_prefix else "fresh")
                prefix = rootfs / "opt/vaso"
                if empty_prefix:
                    prefix.mkdir(parents=True)
                with patch.object(shutil, "copyfile", side_effect=fail_second):
                    with self.assertRaisesRegex(OSError, "installation interrupted"):
                        foundry_tools.provision(rootfs, self.cache, lock=self.lock)
                self.assertFalse((prefix / "bin/bazel-real").exists())
                self.assertFalse((prefix / "foundry-tools.json").exists())
                report = foundry_tools.provision(rootfs, self.cache, lock=self.lock)
                self.assertEqual(foundry_tools.verify(rootfs, self.lock), report)

    def test_tool_outside_the_prefix_cannot_modify_a_hardlinked_base(self) -> None:
        base = self.root / "base"
        original = base / "usr/bin/bazel-real"
        original.parent.mkdir(parents=True)
        original.write_bytes(b"base executable")
        shutil.copytree(base, self.rootfs, copy_function=os.link)
        (self.cache / self.sha).write_bytes(self.payload)
        doc = json.loads(self.lock.read_text())
        doc["tools"][0]["path"] = "usr/bin/bazel-real"
        self.lock.write_text(json.dumps(doc))
        with self.assertRaisesRegex(ValueError, "under opt/vaso"):
            foundry_tools.provision(self.rootfs, self.cache, lock=self.lock)
        self.assertEqual(original.read_bytes(), b"base executable")
        self.assertEqual(
            (self.rootfs / "usr/bin/bazel-real").read_bytes(), b"base executable"
        )

    def test_invalid_wheel_reports_failure_without_a_partial_prefix(self) -> None:
        (self.cache / self.sha).write_bytes(self.payload)
        invalid = b"not a zip archive"
        sha = hashlib.sha256(invalid).hexdigest()
        (self.cache / sha).write_bytes(invalid)
        doc = json.loads(self.lock.read_text())
        doc["tools"].append(
            {
                "name": "broken-wheel",
                "version": "test",
                "kind": "wheel",
                "sha256": sha,
                "path": "opt/vaso/verification-python",
                "url": "https://invalid.example/wheel",
            }
        )
        self.lock.write_text(json.dumps(doc))
        result = subprocess.run(
            [
                sys.executable,
                foundry_tools.__file__,
                "--rootfs",
                str(self.rootfs),
                "--cache",
                str(self.cache),
                "--lock",
                str(self.lock),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("broken-wheel", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse((self.rootfs / "opt/vaso").exists())

    def test_staged_binary_and_wheel_install_preserves_the_base(self) -> None:
        base = self.root / "base"
        original = base / "usr/lib/vendor.so"
        original.parent.mkdir(parents=True)
        original.write_bytes(b"vendor bytes")
        shutil.copytree(base, self.rootfs, copy_function=os.link)
        (self.cache / self.sha).write_bytes(self.payload)
        archive = self.root / "package.whl"
        with zipfile.ZipFile(archive, "w") as wheel:
            wheel.writestr("package/__init__.py", "VALUE = 1\n")
        sha = foundry_tools.digest(archive)
        archive.rename(self.cache / sha)
        doc = json.loads(self.lock.read_text())
        doc["tools"].append(
            {
                "name": "package",
                "version": "test",
                "kind": "wheel",
                "sha256": sha,
                "path": "opt/vaso/verification-python",
                "url": "https://invalid.example/wheel",
            }
        )
        self.lock.write_text(json.dumps(doc))

        report = foundry_tools.provision(self.rootfs, self.cache, lock=self.lock)

        self.assertEqual(foundry_tools.verify(self.rootfs, self.lock), report)
        self.assertIn(
            "opt/vaso/verification-python/package/__init__.py", report["files"]
        )
        self.assertEqual(original.read_bytes(), b"vendor bytes")
        self.assertEqual(
            original.stat().st_ino, (self.rootfs / "usr/lib/vendor.so").stat().st_ino
        )
        self.assertFalse((base / "opt/vaso").exists())
        self.assertEqual([p.name for p in (self.rootfs / "opt").iterdir()], ["vaso"])

    def test_missing_cache_is_rejected_before_cloning_the_base(self) -> None:
        base = self.root / "base"
        base.mkdir()
        (base / "marker").write_text("base")
        result = subprocess.run(
            [
                sys.executable,
                foundry_tools.__file__,
                "--rootfs",
                str(self.rootfs),
                "--base",
                str(base),
                "--lock",
                str(self.lock),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--cache is required", result.stderr)
        self.assertFalse(self.rootfs.exists())
        self.assertEqual([p.name for p in base.iterdir()], ["marker"])

    def test_clone_destination_must_be_outside_the_base(self) -> None:
        base = self.root / "base"
        base.mkdir()
        (base / "marker").write_text("base")
        (self.cache / self.sha).write_bytes(self.payload)
        alias = self.root / "base-alias"
        alias.symlink_to(base, target_is_directory=True)
        for target in (base, base / "nested/rootfs", alias / "nested/rootfs"):
            with self.subTest(target=target):
                result = subprocess.run(
                    [
                        sys.executable,
                        foundry_tools.__file__,
                        "--rootfs",
                        str(target),
                        "--base",
                        str(base),
                        "--cache",
                        str(self.cache),
                        "--lock",
                        str(self.lock),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn("--rootfs must be outside --base", result.stderr)
                self.assertEqual([p.name for p in base.iterdir()], ["marker"])

    def test_clone_cli_installs_tools_without_modifying_base_files(self) -> None:
        base = self.root / "base"
        base.mkdir()
        original = base / "marker"
        original.write_text("base")
        (self.cache / self.sha).write_bytes(self.payload)
        result = subprocess.run(
            [
                sys.executable,
                foundry_tools.__file__,
                "--rootfs",
                str(self.rootfs),
                "--base",
                str(base),
                "--cache",
                str(self.cache),
                "--lock",
                str(self.lock),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout), foundry_tools.verify(self.rootfs, self.lock)
        )
        self.assertEqual(original.stat().st_ino, (self.rootfs / "marker").stat().st_ino)
        self.assertEqual(original.read_text(), "base")
        self.assertFalse((base / "opt").exists())

    def test_missing_pinned_input_is_rejected_before_cloning(self) -> None:
        base = self.root / "base"
        base.mkdir()
        (base / "marker").write_text("base")
        result = subprocess.run(
            [
                sys.executable,
                foundry_tools.__file__,
                "--rootfs",
                str(self.rootfs),
                "--base",
                str(base),
                "--cache",
                str(self.cache),
                "--lock",
                str(self.lock),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("missing or mismatched pinned input", result.stderr)
        self.assertFalse(self.rootfs.exists())
        self.assertEqual([p.name for p in base.iterdir()], ["marker"])

    def test_failed_clone_provision_can_retry_without_changing_base_permissions(
        self,
    ) -> None:
        base = self.root / "base"
        readonly = base / "readonly"
        readonly.mkdir(parents=True)
        original = readonly / "marker"
        original.write_bytes(b"base bytes")
        original.chmod(0o444)
        readonly.chmod(0o555)
        # The fixture's base must remain removable even if the regression fails.
        self.addCleanup(readonly.chmod, 0o755)
        (self.cache / self.sha).write_bytes(self.payload)
        argv = [
            "--rootfs",
            str(self.rootfs),
            "--base",
            str(base),
            "--cache",
            str(self.cache),
            "--lock",
            str(self.lock),
        ]
        with patch.object(shutil, "copyfile", side_effect=OSError("install failed")):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as failure:
                    foundry_tools.main(argv)
        self.assertEqual(failure.exception.code, 2)
        self.assertFalse(self.rootfs.exists())
        self.assertEqual(list(self.root.glob(".rootfs-provision-*")), [])
        self.assertEqual(original.stat().st_mode & 0o777, 0o444)
        self.assertEqual(readonly.stat().st_mode & 0o777, 0o555)
        self.assertEqual(original.read_bytes(), b"base bytes")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(foundry_tools.main(argv), 0)
        self.assertEqual(
            foundry_tools.verify(self.rootfs, self.lock)["lock_sha256"],
            foundry_tools.digest(self.lock),
        )
        self.assertEqual(
            original.stat().st_ino, (self.rootfs / "readonly/marker").stat().st_ino
        )
        self.assertEqual(original.stat().st_mode & 0o777, 0o444)

    def test_clone_preserves_destination_owned_by_another_writer(self) -> None:
        base = self.root / "base"
        base.mkdir()
        (self.cache / self.sha).write_bytes(self.payload)
        copytree = shutil.copytree
        for phase in ("existing", "during-clone"):
            with self.subTest(phase=phase):
                target = self.root / ("target-" + phase)
                marker = target / "keep"
                if phase == "existing":
                    target.mkdir()
                    marker.write_text("other writer")

                def copy_then_interfere(*args, **kwargs):
                    result = copytree(*args, **kwargs)
                    target.mkdir()
                    marker.write_text("other writer")
                    return result

                argv = [
                    "--rootfs",
                    str(target),
                    "--base",
                    str(base),
                    "--cache",
                    str(self.cache),
                    "--lock",
                    str(self.lock),
                ]
                with patch.object(
                    shutil, "copytree", side_effect=copy_then_interfere
                ) as copied:
                    with contextlib.redirect_stderr(io.StringIO()):
                        with self.assertRaises(SystemExit) as failure:
                            foundry_tools.main(argv)
                self.assertEqual(failure.exception.code, 2)
                self.assertEqual(marker.read_text(), "other writer")
                self.assertFalse((target / "opt").exists())
                self.assertEqual(copied.call_count, 0 if phase == "existing" else 1)
                self.assertEqual(
                    list(self.root.glob(f".{target.name}-provision-*")), []
                )

    def test_rejects_path_traversal(self) -> None:
        for name in ("/usr/bin/tool", "../tool", "opt/../../../tool"):
            with self.assertRaises(ValueError):
                foundry_tools.relative(name)


if __name__ == "__main__":
    unittest.main()
