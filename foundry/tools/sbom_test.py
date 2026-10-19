#!/usr/bin/env python3
"""Tests for tools/sbom.py."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("sbom.py")
SCHEMA = Path(__file__).with_name("fixtures") / "cyclonedx-1.5-subset.schema.json"
ROOTFS_LOCK = Path(__file__).resolve().parents[1] / "rootfs" / "cuda_ecosystem.lock.json"
SPEC = importlib.util.spec_from_file_location("sbom", SCRIPT)
assert SPEC is not None
sbom = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = sbom
SPEC.loader.exec_module(sbom)


def write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def scratch_tmpdir() -> Path:
    raw = os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR")
    if not raw:
        raise RuntimeError("TEST_TMPDIR or TMPDIR must be set")
    root = Path(raw)
    root.mkdir(parents=True, exist_ok=True)
    return root


class SbomTest(unittest.TestCase):
    def test_emits_cyclonedx_components_from_spack_bazel_and_rootfs_inputs(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_tmpdir()) as td:
            root = Path(td)
            spack_lock = write_json(
                root / "torch_spack_graph.lock.json",
                {
                    "packages": {
                        "spack_zlib_ng": {
                            "package": "zlib-ng",
                            "version": "2.2.5",
                            "spack_hash": "abc123",
                            "license": "Zlib",
                        }
                    }
                },
            )
            build_graph = write_json(
                root / "torch_build_graph.json",
                {
                    "nodes": [
                        {
                            "package": "zlib-ng",
                            "version": "2.2.5",
                            "spack_hash": "abc123",
                            "status": "native",
                        },
                        {
                            "package": "py-torch",
                            "version": "2.14.0",
                            "status": "consumer",
                        },
                    ]
                },
            )
            rootfs_lock = write_json(
                root / "cuda_ecosystem.lock.json",
                {
                    "lines": {
                        "cu129": {
                            "apt": {
                                "packages": [
                                    {
                                        "name": "cuda-cudart-12-9",
                                        "version": "12.9.79-1",
                                        "sha256": "0" * 64,
                                        "license": "LicenseRef-NVIDIA-CUDA",
                                    }
                                ]
                            }
                        }
                    }
                },
            )
            dpkg = root / "dpkg.txt"
            dpkg.write_text(
                "Desired=Unknown/Install/Remove/Purge/Hold\n"
                "| Status=Not/Inst/Conf-files/Unpacked/halF-conf/Half-inst/trig-aWait/Trig-pend\n"
                "|/ Err?=(none)/Reinst-required (Status,Err: uppercase=bad)\n"
                "||/ Name              Version      Architecture Description\n"
                "+++-=================-============-============-====================\n"
                "ii  cuda-cudart-12-9  12.9.79-1    amd64        CUDA Runtime\n",
                encoding="utf-8",
            )
            policy = write_json(
                root / "license_policy.json",
                {
                    "allowed": ["Zlib", "LicenseRef-NVIDIA-CUDA"],
                    "review": ["NOASSERTION"],
                    "denied": ["GPL-3.0-only"],
                },
            )
            out = root / "sbom.json"
            policy_out = root / "policy.json"

            rc = sbom.main(
                [
                    "--profile",
                    "torch",
                    "--line",
                    "cu129",
                    "--spack-lock",
                    str(spack_lock),
                    "--build-graph",
                    str(build_graph),
                    "--rootfs-lock",
                    str(rootfs_lock),
                    "--dpkg-list",
                    str(dpkg),
                    "--license-policy",
                    str(policy),
                    "--policy-out",
                    str(policy_out),
                    "--out",
                    str(out),
                ]
            )

            self.assertEqual(rc, 0)
            doc = json.loads(out.read_text(encoding="utf-8"))
            schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
            for key in schema["required"]:
                self.assertIn(key, doc)
            for key, rule in schema["properties"].items():
                if "const" in rule:
                    self.assertEqual(doc[key], rule["const"])
            self.assertEqual(doc["bomFormat"], "CycloneDX")
            self.assertEqual(doc["specVersion"], "1.5")
            self.assertEqual(doc["metadata"]["properties"][0], {"name": "vaso:profile", "value": "torch"})
            self.assertEqual(doc["metadata"]["properties"][1], {"name": "vaso:line", "value": "cu129"})
            components = {component["bom-ref"]: component for component in doc["components"]}
            self.assertEqual(
                components["spack:zlib-ng@2.2.5"]["licenses"],
                [{"license": {"id": "Zlib"}}],
            )
            self.assertEqual(
                components["spack:zlib-ng@2.2.5"]["properties"],
                [{"name": "spack:hash", "value": "abc123"}],
            )
            self.assertEqual(components["bazel:py-torch@2.14.0"]["purl"], "pkg:generic/py-torch@2.14.0")
            self.assertEqual(
                components["rootfs:cuda-cudart-12-9@12.9.79-1"]["hashes"],
                [{"alg": "SHA-256", "content": "0" * 64}],
            )
            policy_doc = json.loads(policy_out.read_text(encoding="utf-8"))
            self.assertEqual(policy_doc["verdict"], "passed")
            self.assertEqual(policy_doc["denied"], [])
            self.assertEqual(policy_doc["unknown"], [])

    def test_license_policy_reports_denied_and_unknown_licenses(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_tmpdir()) as td:
            root = Path(td)
            spack_lock = write_json(
                root / "jax_spack_graph.lock.json",
                {
                    "packages": {
                        "spack_bad": {
                            "package": "badlib",
                            "version": "1.0",
                            "license": "GPL-3.0-only",
                        },
                        "spack_missing": {
                            "package": "mystery",
                            "version": "2.0",
                        },
                    }
                },
            )
            build_graph = write_json(root / "jax_build_graph.json", {"nodes": []})
            rootfs_lock = write_json(root / "cuda_ecosystem.lock.json", {"lines": {"cu130": {"apt": {"packages": []}}}})
            dpkg = root / "dpkg.txt"
            dpkg.write_text("", encoding="utf-8")
            policy = write_json(root / "license_policy.json", {"allowed": [], "review": [], "denied": ["GPL-3.0-only"]})

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--profile",
                    "jax",
                    "--line",
                    "cu130",
                    "--spack-lock",
                    str(spack_lock),
                    "--build-graph",
                    str(build_graph),
                    "--rootfs-lock",
                    str(rootfs_lock),
                    "--dpkg-list",
                    str(dpkg),
                    "--license-policy",
                    str(policy),
                    "--policy-out",
                    str(root / "policy.json"),
                    "--out",
                    str(root / "sbom.json"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 1)
            policy_doc = json.loads((root / "policy.json").read_text(encoding="utf-8"))
            self.assertEqual(
                policy_doc["denied"],
                [{"component": "spack:badlib@1.0", "license": "GPL-3.0-only"}],
            )
            self.assertEqual(
                policy_doc["unknown"],
                [{"component": "spack:mystery@2.0", "license": "NOASSERTION"}],
            )
            self.assertIn("LICENSE_POLICY_FAILED", result.stderr)

    def test_rootfs_lock_accepts_committed_apt_package_map_and_reports_missing_observed_packages(self) -> None:
        lock_doc = json.loads(ROOTFS_LOCK.read_text(encoding="utf-8"))
        self.assertIsInstance(lock_doc["lines"]["cu129"]["apt"]["packages"], dict)

        with tempfile.TemporaryDirectory(dir=scratch_tmpdir()) as td:
            root = Path(td)
            spack_lock = write_json(root / "torch_spack_graph.lock.json", {"packages": {}})
            build_graph = write_json(root / "torch_build_graph.json", {"nodes": []})
            dpkg = root / "dpkg.txt"
            dpkg.write_text(
                "ii  cuda-cudart-12-9        12.9.79-1    amd64        CUDA Runtime\n"
                "ii  package-outside-lock    1.0         amd64        Unexpected Package\n",
                encoding="utf-8",
            )
            policy = write_json(
                root / "license_policy.json",
                {"allowed": [], "review": ["NOASSERTION"], "denied": []},
            )

            rc = sbom.main(
                [
                    "--profile",
                    "torch",
                    "--line",
                    "cu129",
                    "--spack-lock",
                    str(spack_lock),
                    "--build-graph",
                    str(build_graph),
                    "--rootfs-lock",
                    str(ROOTFS_LOCK),
                    "--dpkg-list",
                    str(dpkg),
                    "--license-policy",
                    str(policy),
                    "--policy-out",
                    str(root / "policy.json"),
                    "--out",
                    str(root / "sbom.json"),
                ]
            )

            self.assertEqual(rc, 0)
            doc = json.loads((root / "sbom.json").read_text(encoding="utf-8"))
            components = {component["bom-ref"]: component for component in doc["components"]}
            self.assertEqual(
                components["rootfs:cuda-cudart-12-9@12.9.79-1"]["properties"],
                [{"name": "rootfs:locked_version", "value": "12.9.79-1"}],
            )
            policy_doc = json.loads((root / "policy.json").read_text(encoding="utf-8"))
            self.assertEqual(
                policy_doc["rootfs_lock"]["missing_observed_packages"],
                [{"name": "package-outside-lock", "version": "1.0"}],
            )

            strict_result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--profile",
                    "torch",
                    "--line",
                    "cu129",
                    "--spack-lock",
                    str(spack_lock),
                    "--build-graph",
                    str(build_graph),
                    "--rootfs-lock",
                    str(ROOTFS_LOCK),
                    "--dpkg-list",
                    str(dpkg),
                    "--license-policy",
                    str(policy),
                    "--policy-out",
                    str(root / "strict-policy.json"),
                    "--out",
                    str(root / "strict-sbom.json"),
                    "--strict-rootfs-lock",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(strict_result.returncode, 1)
            strict_policy = json.loads((root / "strict-policy.json").read_text(encoding="utf-8"))
            self.assertEqual(strict_policy["verdict"], "failed")
            self.assertEqual(
                strict_policy["rootfs_lock"]["missing_observed_packages"],
                [{"name": "package-outside-lock", "version": "1.0"}],
            )
            self.assertIn("ROOTFS_LOCK_MISSING_PACKAGES count=1", strict_result.stderr)


if __name__ == "__main__":
    unittest.main()
