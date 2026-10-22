"""Pinned source discovery and verified estate storage."""

import hashlib
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
TEST_TMP_ROOT = Path(os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR") or HERE)
sys.path.insert(0, str(HERE))
import build_inputs as b


class BuildInputsTest(unittest.TestCase):
    def make_store(self, temp):
        return b.InputStore(temp / "new", seed_estate=temp / "seed", run_id="test-run")

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
