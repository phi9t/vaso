#!/usr/bin/env python3
"""Tests for jaxlib nested Bazel cache content manifests."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


def load_generator():
    spec = importlib.util.spec_from_file_location(
        "nested_cache_manifest_under_test",
        Path(__file__).with_name("nested_cache_manifest.py"),
    )
    if spec is None or spec.loader is None:
        raise AssertionError("could not load nested_cache_manifest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


nested_cache_manifest = load_generator()


def write(path: Path, content: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def scratch_parent() -> Path:
    if value := os.environ.get("TEST_TMPDIR"):
        return Path(value)
    if value := os.environ.get("VASO_AGENT_IO_ROOT"):
        path = Path(value) / "tmp"
        path.mkdir(parents=True, exist_ok=True)
        return path
    if value := os.environ.get("VASO_ESTATE_ROOT"):
        path = Path(value) / "agents" / os.environ.get("VASO_AGENT", "nested-cache-manifest-test") / "tmp"
        path.mkdir(parents=True, exist_ok=True)
        return path
    return Path.cwd()


class NestedCacheManifestTest(unittest.TestCase):
    def pins(self, archive_sha: str) -> dict[str, object]:
        return {
            "archives": [
                {
                    "filename": "archive.tar.gz",
                    "sha256": archive_sha,
                }
            ],
            "distdir": "/first/path",
            "jaxlib_version": "0.10.2",
            "package": "py-jaxlib",
            "repository_cache": "/second/path",
            "schema_version": 1,
        }

    def write_cache(self, root: Path, archive_bytes: bytes) -> str:
        archive_sha = digest(archive_bytes)
        write(root / "distdir" / "archive.tar.gz", archive_bytes)
        write(root / "repo-cache" / "content_addressable" / "sha256" / archive_sha / "file", archive_bytes)
        return archive_sha

    def test_content_change_fails_instead_of_rekeying_on_cache_contents(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            archive_sha = self.write_cache(root, b"first")
            first = nested_cache_manifest.manifest_payload(
                self.pins(archive_sha),
                distdir=root / "distdir",
                repository_cache=root / "repo-cache",
            )

            write(root / "distdir" / "archive.tar.gz", b"second")
            with self.assertRaisesRegex(ValueError, "sha256 mismatch"):
                nested_cache_manifest.manifest_payload(
                    self.pins(archive_sha),
                    distdir=root / "distdir",
                    repository_cache=root / "repo-cache",
                )

            self.assertEqual(first["entries"][0]["expected_sha256"], archive_sha)

    def test_path_only_change_keeps_manifest_tree_digest(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            archive_bytes = b"same content"
            archive_sha = self.write_cache(root / "one", archive_bytes)
            self.write_cache(root / "two", archive_bytes)

            first = nested_cache_manifest.manifest_payload(
                self.pins(archive_sha),
                distdir=root / "one" / "distdir",
                repository_cache=root / "one" / "repo-cache",
            )
            second = nested_cache_manifest.manifest_payload(
                self.pins(archive_sha),
                distdir=root / "two" / "distdir",
                repository_cache=root / "two" / "repo-cache",
            )

            self.assertEqual(first["tree_sha256"], second["tree_sha256"])
            self.assertNotIn(str(root / "one"), json.dumps(first, sort_keys=True))
            self.assertNotIn(str(root / "two"), json.dumps(second, sort_keys=True))

    def test_manifest_keys_on_expected_sha_and_requires_archive_presence(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            archive_bytes = b"pinned content"
            archive_sha = self.write_cache(root, archive_bytes)

            first = nested_cache_manifest.manifest_payload(
                self.pins(archive_sha),
                distdir=root / "distdir",
                repository_cache=root / "repo-cache",
            )
            second = nested_cache_manifest.manifest_payload(
                self.pins(archive_sha),
                distdir=root / "distdir",
                repository_cache=root / "repo-cache",
            )

            self.assertEqual(first["tree_sha256"], second["tree_sha256"])
            self.assertNotIn("actual_sha256", json.dumps(first, sort_keys=True))
            self.assertNotIn("present", json.dumps(first, sort_keys=True))

            (root / "distdir" / "archive.tar.gz").unlink()
            with self.assertRaisesRegex(FileNotFoundError, "archive.tar.gz"):
                nested_cache_manifest.manifest_payload(
                    self.pins(archive_sha),
                    distdir=root / "distdir",
                    repository_cache=root / "repo-cache",
                )


if __name__ == "__main__":
    unittest.main()
