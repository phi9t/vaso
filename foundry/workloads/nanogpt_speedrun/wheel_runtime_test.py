#!/usr/bin/env python3
"""Contract tests for the pinned JAX wheel runtime materializer."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from workloads.nanogpt_speedrun import wheel_runtime


class WheelRuntimeTest(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path]:
        distdir = root / "distdir"
        distdir.mkdir()
        archives = []
        for prefix in wheel_runtime.REQUIRED_WHEEL_PREFIXES:
            filename = f"{prefix}1.0-py3-none-any.whl"
            wheel = distdir / filename
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(f"{prefix.rstrip('-')}/__init__.py", "VALUE = 1\n")
            archives.append(
                {
                    "filename": filename,
                    "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                }
            )
        manifest = root / "manifest.json"
        manifest.write_text(json.dumps({"archives": archives}) + "\n", encoding="utf-8")
        return manifest, distdir

    def test_materialize_verifies_and_extracts_all_required_wheels(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as raw_root:
            root = Path(raw_root)
            manifest, distdir = self._fixture(root)
            prefix = root / "runtime-prefix"

            identity = wheel_runtime.materialize(
                manifest=manifest,
                distdir=distdir,
                prefix=prefix,
                python_version="3.13",
            )
            repeated = wheel_runtime.materialize(
                manifest=manifest,
                distdir=distdir,
                prefix=prefix,
                python_version="3.13",
            )

            self.assertEqual(identity, repeated)
            self.assertEqual(len(identity["wheels"]), 4)
            self.assertTrue(
                (
                    prefix / "lib/python3.13/site-packages/ml_dtypes/__init__.py"
                ).is_file()
            )

    def test_materialize_rejects_hash_mismatch_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as raw_root:
            root = Path(raw_root)
            manifest, distdir = self._fixture(root)
            document = json.loads(manifest.read_text(encoding="utf-8"))
            document["archives"][0]["sha256"] = "0" * 64
            manifest.write_text(json.dumps(document) + "\n", encoding="utf-8")
            prefix = root / "runtime-prefix"

            with self.assertRaisesRegex(ValueError, "sha256 mismatch"):
                wheel_runtime.materialize(
                    manifest=manifest,
                    distdir=distdir,
                    prefix=prefix,
                    python_version="3.13",
                )

            self.assertFalse(prefix.exists())

    def test_safe_extract_rejects_parent_traversal(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as raw_root:
            root = Path(raw_root)
            wheel = root / "bad.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("../escaped.py", "bad\n")
            destination = root / "prefix"
            destination.mkdir()

            with self.assertRaisesRegex(ValueError, "escapes destination"):
                wheel_runtime._safe_extract(wheel, destination)

            self.assertFalse((root / "escaped.py").exists())


if __name__ == "__main__":
    unittest.main()
