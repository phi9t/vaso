"""Pinned source discovery and verified estate storage."""

import hashlib
import os
import shutil
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
TEST_TMP_ROOT = Path(os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR") or tempfile.gettempdir())
sys.path.insert(0, str(HERE))
import build_inputs as b


class BuildInputsTest(unittest.TestCase):
    def make_store(self, temp):
        return b.InputStore(temp / "new", seed_estate=temp / "seed")

    def test_asset_rejects_invalid_identity_before_storage(self):
        cases = [
            {"sha256": "../outside"},
            {"sha256": None},
            {"urls": ()},
            {"urls": ["https://a/1"]},
            {"urls": (None,)},
            {"urls": ("",)},
            {"filename": "../archive.tar.gz"},
            {"filename": "/archive.tar.gz"},
            {"filename": "."},
            {"filename": ".."},
            {"filename": ""},
            {"filename": "archive\x00.tar.gz"},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                values = dict(owner="fixture", sha256="a" * 64, urls=("https://a/1",))
                values.update(changes)
                with self.assertRaisesRegex(ValueError, "fixture"):
                    b.Asset(**values)

    def test_actual_profiles_include_fp16_resource_and_jax_wheels(self):
        assets = b.selected_assets(HERE.parents[1], ["torch", "jax"])
        hashes = {a.sha256 for a in assets}
        self.assertIn(
            "90f20492621d5ed80b442aa682ff92d7ccf333ac8fac4a10e7e02afb159f3c13", hashes
        )
        self.assertIn(
            "f6c4dab91ae9a03b3019e7cab0572743afd0e1b6e75b97fcca50259c737c924e", hashes
        )
        self.assertIn(
            "b5cd66e240201f5a06af2a75eaffdf05a6c63829edada33ff569ada0037f8086", hashes
        )
        self.assertTrue(any(a.filename and a.filename.endswith(".whl") for a in assets))
        self.assertFalse(
            any(a.owner in {"tla2tools", "lean_dist", "llvm_native"} for a in assets)
        )

    def test_patch_arrays_are_individual_pinned_resources(self):
        assets = b.repo_assets(
            "patches",
            {
                "patches": ["https://a/1", "https://a/2"],
                "patch_sha256s": ["a" * 64, "b" * 64],
            },
        )
        self.assertEqual([a.urls for a in assets], [("https://a/1",), ("https://a/2",)])
        with self.assertRaisesRegex(ValueError, "count mismatch"):
            b.repo_assets(
                "bad",
                {"patch_urls": ["https://a/1"], "patch_sha256s": ["a" * 64, "b" * 64]},
            )

    def test_dynamic_download_pin_is_rejected_without_evaluation(self):
        with self.assertRaisesRegex(ValueError, "non-literal"):
            b.repo_declarations(
                'repo(name="x", urls=["https://a/1"], sha256=calculate())'
            )

    def test_rules_python_runtime_is_read_from_the_pinned_bcr_source(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            root = Path(directory)
            (root / "foundry/examples/consumers").mkdir(parents=True)
            (root / "foundry/MODULE.bazel").write_text(
                'bazel_dep(name = "rules_python", version = "1.2.3")\n'
            )
            module = """
python = use_extension("//python/extensions:python.bzl", "python")
python.defaults(python_version = "3.11")
"""
            versions = """
DEFAULT_RELEASE_BASE_URL = "https://example.org/releases"
TOOL_VERSIONS = {"3.11.9": {"url": "20240101/cpython-{python_version}-{platform}-{build}.{ext}", "sha256": {"x86_64-unknown-linux-gnu": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}}
MINOR_MAPPING = {"3.11": "3.11.9"}
"""
            source = root / "rules_python-1.2.3.tar.gz"
            with tarfile.open(source, "w:gz") as bundle:
                for name, text in (
                    ("rules_python-1.2.3/MODULE.bazel", module),
                    ("rules_python-1.2.3/python/versions.bzl", versions),
                ):
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text)
                    bundle.add(path, arcname=name)
            sha = b.digest(source)
            manifest = root / "foundry/examples/consumers/bcr-manifest.tsv"
            manifest.write_text(
                "module\tversion\tkind\turl\tsha256\tsize\n"
                f"rules_python\t1.2.3\tsource-archive\thttps://example.org/rules_python.tar.gz\t{sha}\t{source.stat().st_size}\n"
            )
            archive = (
                root
                / "estate/sources/bcr/archives/sha256"
                / sha
                / "rules_python.tar.gz"
            )
            archive.parent.mkdir(parents=True)
            archive.write_bytes(source.read_bytes())

            asset = b.rules_python_asset_from_mirror(root, root / "estate")

            self.assertEqual(asset.sha256, "a" * 64)
            self.assertEqual(
                asset.urls,
                (
                    "https://example.org/releases/20240101/"
                    "cpython-3.11.9-x86_64-unknown-linux-gnu-install_only.tar.gz",
                ),
            )

    def test_completed_downloads_are_verified_but_partials_are_not_inputs(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            store = self.make_store(Path(directory))
            payload = b"completed download"
            asset = b.Asset(
                "fixture", hashlib.sha256(payload).hexdigest(), ("https://a/archive",)
            )
            for estate in (store.estate, store.seed_estate):
                with self.subTest(seed=estate == store.seed_estate):
                    cache = estate / "vaso/cache/build-inputs"
                    cache.mkdir(parents=True)
                    partial = cache / (asset.sha256 + ".attempt.part")
                    partial.write_bytes(payload)
                    self.assertIsNone(store.find(asset))
                    completed = cache / asset.sha256
                    completed.write_bytes(b"corrupt")
                    self.assertIsNone(store.find(asset))
                    completed.write_bytes(payload)
                    self.assertEqual(store.find(asset), completed)
                    completed.unlink()

    def test_local_seed_is_hashed_and_canonical_markers_are_restored(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            r = self.make_store(Path(directory))
            payload = b"native archive"
            sha = hashlib.sha256(payload).hexdigest()
            path = (
                r.seed_estate
                / "vaso/cache/bazel/repository-cache/content_addressable/sha256"
                / sha
                / "file"
            )
            path.parent.mkdir(parents=True)
            path.write_bytes(payload)
            asset = b.Asset("fp16", sha, ("https://a/archive",))
            r.install(asset, r.find(asset))
            cached = (
                r.estate
                / "vaso/cache/bazel/repository-cache/content_addressable/sha256"
                / sha
            )
            self.assertEqual((cached / "file").read_bytes(), payload)
            self.assertTrue(list(cached.glob("id-*")))
            path.write_bytes(b"corrupt")
            (cached / "file").unlink()
            self.assertIsNone(r.find(asset))
            with self.assertRaisesRegex(ValueError, "archive sha256 mismatch"):
                r.install(asset, path)

    def test_nested_install_uses_verified_cache_if_original_source_changes(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            root = Path(directory)
            store = self.make_store(root)
            payload = b"pinned wheel"
            source = root / "wheel"
            source.write_bytes(payload)
            asset = b.Asset("nested", b.digest(source), ("https://a/pkg.whl",), "pkg.whl")
            seed = b.seed_repository_cache

            def seed_then_change_source(*args, **kwargs):
                cached = seed(*args, **kwargs)
                source.write_bytes(b"changed seed")
                return cached

            with patch.object(b, "seed_repository_cache", side_effect=seed_then_change_source):
                store.install(asset, source)

            self.assertEqual((store.estate / "sources/jax/distdir/pkg.whl").read_bytes(), payload)

    def test_failed_nested_copy_leaves_no_partial_wheel_and_can_retry(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            root = Path(directory)
            store = self.make_store(root)
            source = root / "wheel"
            source.write_bytes(b"pinned wheel")
            asset = b.Asset("nested", b.digest(source), ("https://a/pkg.whl",), "pkg.whl")
            distdir = store.estate / "sources/jax/distdir"
            # Both installers use this same implementation; fail only the
            # distdir copy after the repository cache has been seeded.
            copy = shutil.copyfile

            def fail_distdir_copy(source, target):
                if Path(target).parent.parent == distdir:
                    Path(target).write_bytes(b"partial wheel")
                    raise OSError("copy interrupted")
                return copy(source, target)

            with patch.object(shutil, "copyfile", side_effect=fail_distdir_copy):
                with self.assertRaisesRegex(OSError, "copy interrupted"):
                    store.install(asset, source)
            self.assertEqual(list(distdir.iterdir()), [])
            store.install(asset, source)
            self.assertEqual((distdir / "pkg.whl").read_bytes(), source.read_bytes())

    def test_nested_wheels_are_materialized_for_offline_pip(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            r = self.make_store(Path(directory))
            payload = b"wheel"
            source = Path(directory) / "wheel"
            source.write_bytes(payload)
            sha = hashlib.sha256(payload).hexdigest()
            r.install(b.Asset("nested", sha, ("https://a/pkg.whl",), "pkg.whl"), source)
            self.assertEqual(
                (r.estate / "sources/jax/distdir/pkg.whl").read_bytes(), payload
            )
            (r.estate / "sources/jax/distdir/pkg.whl").write_bytes(b"other")
            with self.assertRaisesRegex(RuntimeError, "conflicting nested"):
                r.install(
                    b.Asset("nested", sha, ("https://a/pkg.whl",), "pkg.whl"), source
                )


if __name__ == "__main__":
    unittest.main()
