#!/usr/bin/env python3
"""Unit tests for bcr_mirror.py."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("bcr_mirror.py")
SPEC = importlib.util.spec_from_file_location("bcr_mirror", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
bcr_mirror = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bcr_mirror
SPEC.loader.exec_module(bcr_mirror)


def scratch_parent() -> str:
    return os.environ.get("TEST_TMPDIR") or os.environ.get("VASO_AGENT_IO_ROOT") or str(Path.cwd())


class BcrMirrorTest(unittest.TestCase):
    def test_offline_verification_rejects_rewritten_source_metadata_drift(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as temp:
            root = Path(temp)
            archive = b"pinned archive"
            archive_sha = hashlib.sha256(archive).hexdigest()
            (root / "archive.tgz").write_bytes(archive)
            source = json.dumps({"url": "https://invalid.example/archive.tgz", "strip_prefix": "original"}).encode()
            source_sha = hashlib.sha256(source).hexdigest()
            (root / "source.json").write_bytes(source)
            manifest = root / "manifest.tsv"
            manifest.write_text(
                f"foo\t1\tsource-archive\thttps://invalid.example/archive.tgz\t{archive_sha}\t{len(archive)}\n"
                f"foo\t1\tsource-json-original\trepo://source.json\t{source_sha}\t{len(source)}\n"
            )
            estate = root / "estate"
            cached = estate / "sources/bcr/archives/sha256" / archive_sha / "archive.tgz"
            cached.parent.mkdir(parents=True)
            cached.write_bytes(archive)
            bcr_mirror.acquire(manifest, estate, repo_root=root)
            bcr_mirror.acquire(manifest, estate, repo_root=root, allow_network=False)
            dest = estate / "sources/bcr/registry/current/modules/foo/1/source.json"
            doc = json.loads(dest.read_text())
            doc["strip_prefix"] = "unrecorded-change"
            dest.write_text(json.dumps(doc))
            with self.assertRaisesRegex(ValueError, "differs from pinned original"):
                bcr_mirror.acquire(manifest, estate, repo_root=root, allow_network=False)

    def test_manifest_rows_require_sha256_and_https_urls(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            manifest = Path(tmp) / "manifest.tsv"
            manifest.write_text(
                "foo\t1.0.0\tmodule\thttps://bcr.bazel.build/modules/foo/1.0.0/MODULE.bazel\t"
                + ("a" * 64)
                + "\t123\n",
                encoding="utf-8",
            )

            rows = bcr_mirror.load_manifest(manifest)

            self.assertEqual(rows[0].module, "foo")
            self.assertEqual(rows[0].version, "1.0.0")
            self.assertEqual(rows[0].kind, "module")

            manifest.write_text(
                "foo\t1.0.0\tmodule\thttp://bcr.bazel.build/modules/foo/1.0.0/MODULE.bazel\t"
                + ("a" * 64)
                + "\t123\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "non-https"):
                bcr_mirror.load_manifest(manifest)

    def test_repo_root_is_above_the_foundry_workspace(self) -> None:
        self.assertEqual(
            bcr_mirror.repo_root_for_tool(Path("/r/foundry/tools/bcr_mirror.py")),
            Path("/r"),
        )

    def test_repo_root_is_the_workspace_when_run_from_a_bare_workspace(self) -> None:
        # Bazel runfiles lay the workspace out without the enclosing repository.
        self.assertEqual(
            bcr_mirror.repo_root_for_tool(Path("/runfiles/_main/tools/bcr_mirror.py")),
            Path("/runfiles/_main"),
        )

    def test_manifest_accepts_repo_local_registry_artifacts_only_for_registry_rows(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            manifest = Path(tmp) / "manifest.tsv"
            manifest.write_text(
                "\n".join(
                    [
                        "foo\t1.0.0\tmodule\trepo://third_party/foo/MODULE.bazel\t"
                        + ("a" * 64)
                        + "\t123",
                        "foo\t1.0.0\toverlay\trepo://third_party/foo/BUILD.bazel\t"
                        + ("b" * 64)
                        + "\t456",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            rows = bcr_mirror.load_manifest(manifest)

            self.assertEqual(rows[0].url, "repo://third_party/foo/MODULE.bazel")
            self.assertEqual(rows[1].kind, "overlay")

            manifest.write_text(
                "foo\t1.0.0\tsource-archive\trepo://third_party/foo/foo.tar.gz\t"
                + ("c" * 64)
                + "\t789\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "non-https"):
                bcr_mirror.load_manifest(manifest)

    def test_offline_acquire_materializes_repo_local_registry_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            repo_root = root / "repo"
            estate_root = root / "estate"
            module_data = b'module(name = "foo", version = "1.0.0")\n'
            module_digest = hashlib.sha256(module_data).hexdigest()
            module_source = repo_root / "third_party/foo/MODULE.bazel"
            module_source.parent.mkdir(parents=True)
            module_source.write_bytes(module_data)
            manifest = root / "manifest.tsv"
            manifest.write_text(
                "foo\t1.0.0\tmodule\trepo://third_party/foo/MODULE.bazel\t"
                + module_digest
                + f"\t{len(module_data)}\n",
                encoding="utf-8",
            )

            result = bcr_mirror.acquire(manifest, estate_root, repo_root=repo_root, allow_network=False)

            self.assertEqual(result["registry"], 1)
            registry_file = estate_root / "sources/bcr/registry/current/modules/foo/1.0.0/MODULE.bazel"
            self.assertEqual(registry_file.read_bytes(), module_data)

    def test_registry_paths_follow_sparse_bcr_layout(self) -> None:
        row = bcr_mirror.ManifestRow(
            module="foo",
            version="1.2.3",
            kind="source-json",
            url="https://bcr.bazel.build/modules/foo/1.2.3/source.json",
            sha256="a" * 64,
            size=42,
        )

        self.assertEqual(
            bcr_mirror.registry_relative_path(row),
            Path("modules/foo/1.2.3/source.json"),
        )

    def test_source_json_is_rewritten_to_the_insula_archive_store(self) -> None:
        archive = bcr_mirror.ManifestRow(
            module="foo",
            version="1.2.3",
            kind="source-archive",
            url="https://example.invalid/foo-1.2.3.tar.gz",
            sha256="b" * 64,
            size=9,
        )
        source_json = json.dumps(
            {
                "integrity": "sha256-test",
                "mirror_urls": [
                    "https://mirror.example.invalid/foo-1.2.3.tar.gz",
                ],
                "strip_prefix": "foo-1.2.3",
                "url": "https://example.invalid/foo-1.2.3.tar.gz",
            }
        ).encode("utf-8")

        rewritten = json.loads(bcr_mirror.rewrite_source_json(source_json, archive).decode("utf-8"))

        self.assertEqual(
            rewritten["url"],
            "file:///vaso/sources/bcr/archives/sha256/" + ("b" * 64) + "/foo-1.2.3.tar.gz",
        )
        self.assertEqual(rewritten["integrity"], "sha256-test")
        self.assertNotIn("mirror_urls", rewritten)

    def test_seed_repository_cache_copies_verified_archive_by_sha(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            data = b"archive bytes\n"
            digest = hashlib.sha256(data).hexdigest()
            archive = root / "archives" / "file.tar.gz"
            archive.parent.mkdir()
            archive.write_bytes(data)

            dest = bcr_mirror.seed_repository_cache(
                repository_cache=root / "repository-cache",
                archive=archive,
                sha256=digest,
                canonical_ids=("file:///vaso/sources/bcr/archives/sha256/" + digest + "/file.tar.gz",),
            )

            self.assertEqual(dest, root / "repository-cache/content_addressable/sha256" / digest / "file")
            self.assertEqual(dest.read_bytes(), data)
            marker = dest.parent / (
                "id-"
                + hashlib.sha256(
                    ("file:///vaso/sources/bcr/archives/sha256/" + digest + "/file.tar.gz").encode("utf-8")
                ).hexdigest()
            )
            self.assertTrue(marker.exists())

    def test_offline_acquire_rejects_source_json_with_remote_mirrors(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            estate_root = root / "estate"
            data = b"archive bytes\n"
            digest = hashlib.sha256(data).hexdigest()
            manifest = root / "manifest.tsv"
            manifest.write_text(
                "\n".join(
                    [
                        "foo\t1.0.0\tsource-archive\thttps://example.invalid/foo-1.0.0.tar.gz\t"
                        + digest
                        + f"\t{len(data)}",
                        "foo\t1.0.0\tsource-json-original\thttps://bcr.bazel.build/modules/foo/1.0.0/source.json\t"
                        + ("a" * 64)
                        + "\t0",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            archive = estate_root / "sources/bcr/archives/sha256" / digest / "foo-1.0.0.tar.gz"
            archive.parent.mkdir(parents=True)
            archive.write_bytes(data)
            source_json = estate_root / "sources/bcr/registry/current/modules/foo/1.0.0/source.json"
            source_json.parent.mkdir(parents=True)
            source_json.write_text(
                json.dumps(
                    {
                        "integrity": "sha256-test",
                        "mirror_urls": ["https://mirror.example.invalid/foo-1.0.0.tar.gz"],
                        "url": bcr_mirror.archive_file_url(
                            bcr_mirror.ManifestRow(
                                module="foo",
                                version="1.0.0",
                                kind="source-archive",
                                url="https://example.invalid/foo-1.0.0.tar.gz",
                                sha256=digest,
                                size=len(data),
                            )
                        ),
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "expected local-only archive URL"):
                bcr_mirror.acquire(manifest, estate_root, allow_network=False)

    def test_offline_acquire_seeds_remote_patch_and_overlay_repository_cache(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            estate_root = root / "estate"
            archive_data = b"archive bytes\n"
            archive_digest = hashlib.sha256(archive_data).hexdigest()
            patch_data = b"--- a/BUILD.bazel\n+++ b/BUILD.bazel\n"
            patch_digest = hashlib.sha256(patch_data).hexdigest()
            overlay_data = b"cc_library(name = \"lib\")\n"
            overlay_digest = hashlib.sha256(overlay_data).hexdigest()
            manifest = root / "manifest.tsv"
            manifest.write_text(
                "\n".join(
                    [
                        "foo\t1.0.0\tsource-archive\thttps://example.invalid/foo-1.0.0.tar.gz\t"
                        + archive_digest
                        + f"\t{len(archive_data)}",
                        "foo\t1.0.0\tpatch\thttps://bcr.bazel.build/modules/foo/1.0.0/patches/build.patch\t"
                        + patch_digest
                        + f"\t{len(patch_data)}",
                        "foo\t1.0.0\toverlay\thttps://bcr.bazel.build/modules/foo/1.0.0/overlay/BUILD.bazel\t"
                        + overlay_digest
                        + f"\t{len(overlay_data)}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            archive = estate_root / "sources/bcr/archives/sha256" / archive_digest / "foo-1.0.0.tar.gz"
            archive.parent.mkdir(parents=True)
            archive.write_bytes(archive_data)
            patch = estate_root / "sources/bcr/registry/current/modules/foo/1.0.0/patches/build.patch"
            patch.parent.mkdir(parents=True)
            patch.write_bytes(patch_data)
            overlay = estate_root / "sources/bcr/registry/current/modules/foo/1.0.0/overlay/BUILD.bazel"
            overlay.parent.mkdir(parents=True)
            overlay.write_bytes(overlay_data)

            result = bcr_mirror.acquire(manifest, estate_root, allow_network=False)

            self.assertEqual(result["repository_cache"], 3)
            patch_cache_dir = estate_root / "vaso/cache/bazel/repository-cache/content_addressable/sha256" / patch_digest
            self.assertEqual((patch_cache_dir / "file").read_bytes(), patch_data)
            overlay_cache_dir = estate_root / "vaso/cache/bazel/repository-cache/content_addressable/sha256" / overlay_digest
            self.assertEqual((overlay_cache_dir / "file").read_bytes(), overlay_data)
            archive_row = bcr_mirror.ManifestRow(
                module="foo",
                version="1.0.0",
                kind="source-archive",
                url="https://example.invalid/foo-1.0.0.tar.gz",
                sha256=archive_digest,
                size=len(archive_data),
            )
            marker = patch_cache_dir / (
                "id-"
                + hashlib.sha256(bcr_mirror.archive_file_url(archive_row).encode("utf-8")).hexdigest()
            )
            self.assertTrue(marker.exists())
            self.assertTrue((overlay_cache_dir / marker.name).exists())


if __name__ == "__main__":
    unittest.main()
