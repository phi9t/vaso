#!/usr/bin/env python3
"""Tests for release directory assembly."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("release_dir.py")
SPEC = importlib.util.spec_from_file_location("release_dir", SCRIPT)
assert SPEC is not None
release_dir = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(release_dir)


def _tmp_root() -> Path:
    base = os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR")
    return Path(tempfile.mkdtemp(dir=base))


def _write_manifest(path: Path, wheel: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "wheels": [
                    {
                        "name": wheel.name,
                        "path": str(wheel),
                        "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                    }
                ],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_release_wheel(path: Path, git_sha: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    dist_info = f"{path.name.split('-', 1)[0]}-1.0.dist-info"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{dist_info}/vaso_provenance.json", json.dumps({"git_sha": git_sha}) + "\n")


class ReleaseDirTest(unittest.TestCase):
    def test_assembles_torch_runtime_wheelhouse_and_hashed_requirements(self) -> None:
        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        inputs = root / "inputs"
        inputs.mkdir()
        torch_wheel = inputs / "torch-2.14.0+vaso.cu130.gabcdef12-cp313-cp313-linux_x86_64.whl"
        typing_wheel = inputs / "typing_extensions-4.15.0-py3-none-any.whl"
        filelock_wheel = inputs / "filelock-3.29.1-py3-none-any.whl"
        numpy_wheel = inputs / "numpy-2.4.6-cp313-cp313-linux_x86_64.whl"
        _write_release_wheel(torch_wheel, "abcdef1234567890")
        for wheel in (typing_wheel, filelock_wheel, numpy_wheel):
            wheel.write_bytes(wheel.name.encode("utf-8"))
        first_party_manifest = inputs / "pytorch.json"
        runtime_manifest = inputs / "runtime.json"
        _write_manifest(first_party_manifest, torch_wheel)
        runtime_manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "wheels": [
                        {
                            "name": wheel.name,
                            "path": str(wheel),
                            "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                        }
                        for wheel in (filelock_wheel, numpy_wheel, typing_wheel)
                    ],
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        sbom = inputs / "cyclonedx.json"
        license_policy = inputs / "license-policy.json"
        sbom.write_text("{}\n", encoding="utf-8")
        license_policy.write_text("{}\n", encoding="utf-8")

        out = root / "release"
        release_dir.assemble_release_dir(
            release_dir=out,
            profile="torch",
            line="cu130",
            commit="abcdef1234567890",
            sbom=sbom,
            license_policy=license_policy,
            wheel_manifests=[("pytorch", first_party_manifest)],
            runtime_wheel_manifests=[("torch-runtime-deps", runtime_manifest)],
        )

        self.assertTrue((out / "wheels" / torch_wheel.name).is_file())
        for wheel in (typing_wheel, filelock_wheel, numpy_wheel):
            self.assertTrue((out / "wheelhouse" / wheel.name).is_file())
        requirements = (out / "requirements.txt").read_text(encoding="utf-8").splitlines()
        self.assertIn(
            f"/run/vaso/release/wheels/{torch_wheel.name} --hash=sha256:{hashlib.sha256(torch_wheel.read_bytes()).hexdigest()}",
            requirements,
        )
        self.assertIn(
            f"/run/vaso/release/wheelhouse/{typing_wheel.name} --hash=sha256:{hashlib.sha256(typing_wheel.read_bytes()).hexdigest()}",
            requirements,
        )
        release_doc = json.loads((out / "provenance" / "release.json").read_text(encoding="utf-8"))
        self.assertEqual([item["package"] for item in release_doc["runtime_manifests"]], ["torch-runtime-deps"])
        self.assertEqual(
            sorted(item["name"] for item in release_doc["runtime_wheels"]),
            sorted([typing_wheel.name, filelock_wheel.name, numpy_wheel.name]),
        )
        sums = (out / "SHA256SUMS").read_text(encoding="utf-8")
        self.assertIn("requirements.txt", sums)
        self.assertIn(f"wheelhouse/{typing_wheel.name}", sums)

    def test_refuses_stale_first_party_release_wheel_and_names_it(self) -> None:
        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        inputs = root / "inputs"
        current_wheel = inputs / "torch-2.14.0+vaso.cu130.gabcdef12-cp313-cp313-linux_x86_64.whl"
        _write_release_wheel(current_wheel, "abcdef1234567890")
        manifest = inputs / "pytorch.json"
        _write_manifest(manifest, current_wheel)
        stale_sha = "deadbeef00000000000000000000000000000000"
        stale_wheel = root / "release" / "wheels" / "torch-2.14.0+vaso.cu130.gdeadbeef-cp313-cp313-linux_x86_64.whl"
        _write_release_wheel(stale_wheel, stale_sha)
        sbom = inputs / "cyclonedx.json"
        license_policy = inputs / "license-policy.json"
        sbom.write_text("{}\n", encoding="utf-8")
        license_policy.write_text("{}\n", encoding="utf-8")
        out = root / "release"

        with self.assertRaises(ValueError) as raised:
            release_dir.assemble_release_dir(
                release_dir=out,
                profile="torch",
                line="cu130",
                commit="abcdef1234567890",
                sbom=sbom,
                license_policy=license_policy,
                wheel_manifests=[("pytorch", manifest)],
            )

        self.assertIn(stale_wheel.name, str(raised.exception))
        self.assertIn("does not match commit", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
