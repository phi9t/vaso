#!/usr/bin/env python3
"""Tests for triumvirate acceptance JSON assembly."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("triumvirate_acceptance.py")
SPEC = importlib.util.spec_from_file_location("triumvirate_acceptance", SCRIPT)
assert SPEC is not None
acceptance = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = acceptance
SPEC.loader.exec_module(acceptance)


class TriumvirateAcceptanceTest(unittest.TestCase):
    def test_build_doc_records_required_hashes_prefix_digests_and_pass_verdict(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
            root = Path(tmp)
            manifest = root / "rootfs-bundle.json"
            lock = root / "cuda_ecosystem.lock.json"
            manifest.write_text('{"line":"cu129"}\n', encoding="utf-8")
            lock.write_text('{"schema_version":1}\n', encoding="utf-8")
            prefixes = {}
            for name in ("python", "torch", "triton", "jax", "jaxlib"):
                prefix = root / name
                (prefix / "lib").mkdir(parents=True)
                (prefix / "lib" / f"{name}.txt").write_text(name, encoding="utf-8")
                prefixes[name] = prefix
            results = [
                {
                    "name": "rootfs_static",
                    "stage": "rootfs",
                    "returncode": 0,
                    "verdict": "passed",
                }
            ]

            doc = acceptance.build_acceptance_doc(
                profile="torch",
                line="cu129",
                commit="abc123",
                run_dir=root / "run",
                rootfs_manifest=manifest,
                lock=lock,
                prefixes=prefixes,
                sub_results=results,
                waivers=[],
                waiver_errors=[],
            )

        self.assertEqual(doc["verdict"], "passed")
        self.assertEqual(doc["profile"], "torch")
        self.assertEqual(doc["commit"], "abc123")
        self.assertTrue(str(doc["rootfs_manifest_digest"]).startswith("sha256:"))
        self.assertTrue(str(doc["lock_sha256"]).startswith("sha256:"))
        self.assertTrue(str(doc["prefix_digests"]["torch"]["sha256"]).startswith("sha256:"))
        self.assertEqual(doc["failed_sub_results"], [])

    def test_build_doc_records_release_artifact_paths_hashes_and_workloads(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
            root = Path(tmp)
            manifest = root / "rootfs-bundle.json"
            lock = root / "cuda_ecosystem.lock.json"
            run_dir = root / "run"
            wheel = root / "torch-2.14.0+vaso.cu129.gabc12345.whl"
            wheel_manifest = root / "pytorch_release_wheel_manifest.json"
            release_workloads = run_dir / "release-workloads" / "summary.json"
            release_gates = run_dir / "release-gates" / "results.json"
            sbom = run_dir / "sbom" / "cyclonedx.json"
            license_policy = run_dir / "sbom" / "license-policy.json"
            manifest.write_text('{"line":"cu129"}\n', encoding="utf-8")
            lock.write_text('{"schema_version":1}\n', encoding="utf-8")
            wheel.write_bytes(b"wheel-bytes")
            wheel_manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "wheels": [
                            {
                                "name": wheel.name,
                                "path": str(wheel),
                                "sha256": "9ceb18f15662bb87e54af2f5953c0484d2ef76f5444d87913360b9ef87d7296d",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            release_workloads.parent.mkdir(parents=True)
            release_workloads.write_text(
                json.dumps(
                    {
                        "verdict": "passed",
                        "passed_workloads": ["W1a", "W2a"],
                        "failed_workloads": [],
                        "results": [
                            {"id": "W1a", "verdict": "passed"},
                            {"id": "W2a", "verdict": "passed"},
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            release_gates.parent.mkdir(parents=True)
            release_gates.write_text(
                json.dumps(
                    {
                        "verdict": "passed",
                        "failed_blocking_gates": [],
                        "results": [
                            {"name": "import_cuda", "group": "core", "returncode": 0},
                            {"name": "torchvision_ops", "group": "vision_audio", "returncode": 0},
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            sbom.parent.mkdir(parents=True)
            sbom.write_text('{"bomFormat":"CycloneDX"}\n', encoding="utf-8")
            license_policy.write_text('{"verdict":"passed"}\n', encoding="utf-8")

            doc = acceptance.build_acceptance_doc(
                profile="torch",
                line="cu129",
                commit="abc123",
                run_dir=run_dir,
                rootfs_manifest=manifest,
                lock=lock,
                prefixes={},
                sub_results=[],
                waivers=[],
                waiver_errors=[],
                release_wheel_manifests={"pytorch": wheel_manifest},
                release_workload_summaries={"release_workloads": release_workloads},
                release_gate_results={"release_gates": release_gates},
                release_sbom=sbom,
                release_license_policy=license_policy,
            )

        self.assertEqual(doc["verdict"], "passed")
        self.assertNotIn("parity", doc["release"])
        self.assertEqual(doc["release"]["workload_summaries"]["release_workloads"]["verdict"], "passed")
        self.assertEqual(
            doc["release"]["workload_summaries"]["release_workloads"]["passed_workloads"],
            ["W1a", "W2a"],
        )
        self.assertEqual(
            doc["release"]["workload_summaries"]["release_workloads"]["results"][0]["id"],
            "W1a",
        )
        self.assertEqual(doc["release"]["gate_results"]["release_gates"]["verdict"], "passed")
        self.assertEqual(
            doc["release"]["gate_results"]["release_gates"]["results"][1]["name"],
            "torchvision_ops",
        )
        self.assertEqual(doc["release"]["sbom"]["path"], str(sbom))
        self.assertTrue(doc["release"]["sbom"]["sha256"].startswith("sha256:"))
        self.assertEqual(doc["release"]["license_policy"]["verdict"], "passed")
        self.assertEqual(doc["release"]["wheels"][0]["name"], wheel.name)
        self.assertEqual(doc["release"]["wheels"][0]["sha256"], "sha256:9ceb18f15662bb87e54af2f5953c0484d2ef76f5444d87913360b9ef87d7296d")

    def test_missing_or_failing_release_artifacts_fail_acceptance(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
            root = Path(tmp)
            manifest = root / "rootfs-bundle.json"
            lock = root / "cuda_ecosystem.lock.json"
            release_workloads = root / "release-workloads.json"
            release_gates = root / "release-gates.json"
            policy = root / "license-policy.json"
            manifest.write_text("{}\n", encoding="utf-8")
            lock.write_text("{}\n", encoding="utf-8")
            release_workloads.write_text(
                json.dumps({"verdict": "failed", "failed_workloads": ["W2c"]}) + "\n",
                encoding="utf-8",
            )
            release_gates.write_text(
                json.dumps({"verdict": "failed", "failed_blocking_gates": ["import_cuda"]}) + "\n",
                encoding="utf-8",
            )
            policy.write_text('{"verdict":"failed"}\n', encoding="utf-8")

            doc = acceptance.build_acceptance_doc(
                profile="torch",
                line="cu129",
                commit="abc123",
                run_dir=root / "run",
                rootfs_manifest=manifest,
                lock=lock,
                prefixes={},
                sub_results=[],
                waivers=[],
                waiver_errors=[],
                release_wheel_manifests={"pytorch": root / "missing-manifest.json"},
                release_workload_summaries={
                    "release_workloads": release_workloads,
                    "release_jax_model_workloads": root / "missing-workloads.json",
                },
                release_gate_results={
                    "release_gates": release_gates,
                    "release_jax_gates": root / "missing-gates.json",
                },
                release_sbom=root / "missing-sbom.json",
                release_license_policy=policy,
            )

        self.assertEqual(doc["verdict"], "failed")
        self.assertIn("missing_release_wheel_manifest:pytorch", doc["failed_sub_results"])
        self.assertIn("release_workloads:release_workloads", doc["failed_sub_results"])
        self.assertIn("missing_release_workload_summary:release_jax_model_workloads", doc["failed_sub_results"])
        self.assertIn("release_gates:release_gates", doc["failed_sub_results"])
        self.assertIn("missing_release_gate_results:release_jax_gates", doc["failed_sub_results"])
        self.assertIn("missing_release_sbom", doc["failed_sub_results"])
        self.assertIn("release_license_policy", doc["failed_sub_results"])

    def test_cli_rejects_retired_release_oracle_option(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
            root = Path(tmp)
            with self.assertRaises(SystemExit):
                acceptance.parse_args(
                    [
                        "--out",
                        str(root / "acceptance.json"),
                        "--line",
                        "cu129",
                        "--profile",
                        "torch",
                        "--commit",
                        "abc123",
                        "--run-dir",
                        str(root),
                        "--rootfs-manifest",
                        str(root / "rootfs-bundle.json"),
                        "--lock",
                        str(root / "cuda_ecosystem.lock.json"),
                        "--waivers",
                        str(root / "acceptance-waivers.json"),
                        "--sub-results-jsonl",
                        str(root / "sub-results.jsonl"),
                        "--release" + "-parity",
                        str(root / "parity.json"),
                    ]
                )

    def test_build_doc_records_split_jax_workload_summaries(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
            root = Path(tmp)
            manifest = root / "rootfs-bundle.json"
            lock = root / "cuda_ecosystem.lock.json"
            run_dir = root / "run"
            (run_dir / "workloads").mkdir(parents=True)
            (run_dir / "jax-model-workloads").mkdir(parents=True)
            manifest.write_text("{}\n", encoding="utf-8")
            lock.write_text("{}\n", encoding="utf-8")
            (run_dir / "workloads" / "summary.json").write_text(
                json.dumps({"verdict": "passed", "passed_workloads": ["W3a", "W3b", "W3c"]}) + "\n",
                encoding="utf-8",
            )
            (run_dir / "jax-model-workloads" / "summary.json").write_text(
                json.dumps({"verdict": "passed", "passed_workloads": ["W5a", "W5b", "W5c", "W5d", "W5e", "W5f", "W5g"]}) + "\n",
                encoding="utf-8",
            )

            doc = acceptance.build_acceptance_doc(
                profile="jax",
                line="cu129",
                commit="abc123",
                run_dir=run_dir,
                rootfs_manifest=manifest,
                lock=lock,
                prefixes={},
                sub_results=[],
                waivers=[],
                waiver_errors=[],
            )

        self.assertEqual(doc["workload_summary"]["passed_workloads"], ["W3a", "W3b", "W3c"])
        self.assertEqual(doc["workload_summaries"]["workloads"]["passed_workloads"], ["W3a", "W3b", "W3c"])
        self.assertEqual(
            doc["workload_summaries"]["jax_model_workloads"]["passed_workloads"],
            ["W5a", "W5b", "W5c", "W5d", "W5e", "W5f", "W5g"],
        )

    def test_failed_sub_result_fails_unless_waived_by_human_decision(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
            root = Path(tmp)
            manifest = root / "rootfs-bundle.json"
            lock = root / "cuda_ecosystem.lock.json"
            manifest.write_text("{}\n", encoding="utf-8")
            lock.write_text("{}\n", encoding="utf-8")
            result = {"name": "jax_gates", "stage": "jax_gates", "returncode": 1, "verdict": "failed"}
            base = {
                "line": "cu130",
                "profile": "torch",
                "commit": "abc123",
                "run_dir": root / "run",
                "rootfs_manifest": manifest,
                "lock": lock,
                "prefixes": {},
                "sub_results": [dict(result)],
                "waiver_errors": [],
            }

            failed = acceptance.build_acceptance_doc(waivers=[], **base)
            waived = acceptance.build_acceptance_doc(
                waivers=[{"stage": "jax_gates", "human_decision": "accepted before jaxlib lands"}],
                **base,
            )

        self.assertEqual(failed["verdict"], "failed")
        self.assertIn("jax_gates", failed["failed_sub_results"])
        self.assertEqual(waived["verdict"], "passed")
        self.assertEqual(waived["sub_results"][0]["verdict"], "waived")

    def test_skipped_upstream_release_stage_stays_reported_as_skipped(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
            root = Path(tmp)
            manifest = root / "rootfs-bundle.json"
            lock = root / "cuda_ecosystem.lock.json"
            manifest.write_text("{}\n", encoding="utf-8")
            lock.write_text("{}\n", encoding="utf-8")

            doc = acceptance.build_acceptance_doc(
                profile="torch",
                line="cu130",
                commit="abc123",
                run_dir=root / "run",
                rootfs_manifest=manifest,
                lock=lock,
                prefixes={},
                sub_results=[
                    {"name": "native_prefixes", "stage": "native_prefixes", "returncode": 1, "verdict": "failed"},
                    {
                        "name": "release_wheels",
                        "stage": "release_wheels",
                        "returncode": 1,
                        "verdict": "skipped (upstream failed)",
                    },
                    {
                        "name": "release_dir",
                        "stage": "release_dir",
                        "returncode": 1,
                        "verdict": "skipped (upstream failed)",
                    },
                ],
                waivers=[],
                waiver_errors=[],
            )

        self.assertEqual(doc["verdict"], "failed")
        self.assertEqual(doc["sub_results"][1]["verdict"], "skipped (upstream failed)")
        self.assertEqual(doc["sub_results"][2]["verdict"], "skipped (upstream failed)")
        self.assertIn("release_wheels", doc["failed_sub_results"])
        self.assertIn("release_dir", doc["failed_sub_results"])

    def test_waivers_are_scoped_by_profile(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
            root = Path(tmp)
            manifest = root / "rootfs-bundle.json"
            lock = root / "cuda_ecosystem.lock.json"
            manifest.write_text("{}\n", encoding="utf-8")
            lock.write_text("{}\n", encoding="utf-8")
            result = {"name": "jax_gates", "stage": "jax_gates", "returncode": 1, "verdict": "failed"}

            doc = acceptance.build_acceptance_doc(
                profile="torch",
                line="cu130",
                commit="abc123",
                run_dir=root / "run",
                rootfs_manifest=manifest,
                lock=lock,
                prefixes={},
                sub_results=[result],
                waivers=[
                    {
                        "stage": "jax_gates",
                        "profile": "jax",
                        "human_decision": "accepted only for the JAX profile",
                    }
                ],
                waiver_errors=[],
            )

        self.assertEqual(doc["verdict"], "failed")
        self.assertEqual(doc["sub_results"][0]["verdict"], "failed")

    def test_load_waivers_requires_json_list_and_human_decision(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
            path = Path(tmp) / "acceptance-waivers.json"
            path.write_text('{"waivers":[]}\n', encoding="utf-8")
            self.assertEqual(
                acceptance.load_waivers(path),
                ([], ["acceptance-waivers.json must be a JSON list"]),
            )
            path.write_text('[{"stage":"jax_gates"}]\n', encoding="utf-8")
            self.assertEqual(
                acceptance.load_waivers(path),
                ([{"stage": "jax_gates"}], ["waiver 0 must include human_decision"]),
            )

    def test_cli_writes_acceptance_json_and_returns_failure_for_missing_required_prefix(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
            root = Path(tmp)
            manifest = root / "rootfs-bundle.json"
            lock = root / "cuda_ecosystem.lock.json"
            waivers = root / "acceptance-waivers.json"
            results = root / "sub-results.jsonl"
            out = root / "acceptance-cu129.json"
            manifest.write_text("{}\n", encoding="utf-8")
            lock.write_text("{}\n", encoding="utf-8")
            waivers.write_text("[]\n", encoding="utf-8")
            results.write_text(
                json.dumps({"name": "prefix_preflight", "stage": "preflight", "returncode": 1, "verdict": "failed"})
                + "\n",
                encoding="utf-8",
            )

            rc = acceptance.main(
                [
                    "--out",
                    str(out),
                    "--line",
                    "cu129",
                    "--profile",
                    "torch",
                    "--commit",
                    "abc123",
                    "--run-dir",
                    str(root),
                    "--rootfs-manifest",
                    str(manifest),
                    "--lock",
                    str(lock),
                    "--waivers",
                    str(waivers),
                    "--sub-results-jsonl",
                    str(results),
                    "--prefix",
                    f"torch={root / 'missing-torch'}",
                ]
            )
            doc = json.loads(out.read_text(encoding="utf-8"))

        self.assertEqual(rc, 1)
        self.assertEqual(doc["verdict"], "failed")
        self.assertIn("missing_prefix_digest:torch", doc["failed_sub_results"])

    def test_missing_manifest_or_lock_digest_fails_acceptance(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
            root = Path(tmp)
            prefix = root / "torch"
            prefix.mkdir()
            doc = acceptance.build_acceptance_doc(
                profile="torch",
                line="cu129",
                commit="abc123",
                run_dir=root / "run",
                rootfs_manifest=root / "missing-manifest.json",
                lock=root / "missing-lock.json",
                prefixes={"torch": prefix},
                sub_results=[
                    {
                        "name": "rootfs_static",
                        "stage": "rootfs",
                        "returncode": 0,
                        "verdict": "passed",
                    }
                ],
                waivers=[],
                waiver_errors=[],
            )

        self.assertEqual(doc["verdict"], "failed")
        self.assertIn("missing_rootfs_manifest_digest", doc["failed_sub_results"])
        self.assertIn("missing_lock_sha256", doc["failed_sub_results"])


if __name__ == "__main__":
    unittest.main()
