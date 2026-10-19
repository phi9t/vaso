#!/usr/bin/env python3
"""Smoke tests for PyTorch committed-plan metadata outputs."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("metadata_writer.py")
GRAPH_ROOT = SCRIPT.parents[2]
PLAN = GRAPH_ROOT / "plans" / "torch" / "cu130" / "pytorch.plan.json"
KEYS = GRAPH_ROOT / "plans" / "keys.json"

SPEC = importlib.util.spec_from_file_location("pytorch_metadata_writer", SCRIPT)
assert SPEC is not None
writer = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = writer
SPEC.loader.exec_module(writer)


def scratch_parent() -> Path:
    return Path(os.environ.get("TEST_TMPDIR") or os.environ.get("VASO_AGENT_IO_ROOT") or Path.cwd())


class PytorchActionSmokeTest(unittest.TestCase):
    def test_metadata_writer_emits_release_repair_contract(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            tmp_path = Path(tmp)
            build_plan = tmp_path / "build_plan.json"
            metadata = tmp_path / "provider_metadata.json"
            marker = tmp_path / "result.txt"
            rootfs = GRAPH_ROOT / "plans" / "torch" / "cu130" / "rootfs.identity.json"
            expected_abi = writer.python_abi_from_plan(json.loads(PLAN.read_text(encoding="utf-8")))
            self.assertIsNotNone(expected_abi)
            wheel = tmp_path / f"torch-2.14.0+vaso.cu130.gcommit-{expected_abi}-{expected_abi}-linux_x86_64.whl"
            wheel.write_text("wheel fixture\n", encoding="utf-8")

            rc = writer.main(
                [
                    "--plan",
                    str(PLAN),
                    "--keys",
                    str(KEYS),
                    "--rootfs-identity",
                    str(rootfs),
                    "--wheel",
                    str(wheel),
                    "--build-plan-out",
                    str(build_plan),
                    "--provider-metadata-out",
                    str(metadata),
                    "--result-marker-out",
                    str(marker),
                    "--dep-prefix",
                    "cuda=bazel-out/dep/cuda/prefix",
                    "--dep-prefix",
                    "nvshmem=bazel-out/dep/nvshmem/prefix",
                ]
            )

            self.assertEqual(rc, 0)
            plan_doc = json.loads(build_plan.read_text(encoding="utf-8"))
            metadata_doc = json.loads(metadata.read_text(encoding="utf-8"))
            keys_doc = json.loads(KEYS.read_text(encoding="utf-8"))
            self.assertEqual(plan_doc["package"]["name"], "py-torch")
            self.assertEqual(metadata_doc["package"], "py-torch")
            self.assertEqual(metadata_doc["version"], "2.14.0")
            self.assertEqual(metadata_doc["action_input_digest"], keys_doc["packages"]["torch/cu130/py-torch"]["merkle_key"])
            self.assertEqual(metadata_doc["declared_cuda_architectures"], ["sm_100+PTX"])
            self.assertEqual(metadata_doc["torch_cuda_arch_list"], "10.0+PTX")
            self.assertEqual(metadata_doc["python_abi"], expected_abi)
            self.assertEqual(metadata_doc["mechanism"], "committed-plan")
            self.assertEqual(metadata_doc["input_prefixes"]["cuda"], "bazel-out/dep/cuda/prefix")
            self.assertEqual(metadata_doc["input_prefixes"]["nvshmem"], "bazel-out/dep/nvshmem/prefix")
            self.assertEqual(metadata_doc["wheel"], str(wheel))
            self.assertIn("committed-plan wheel build completed", marker.read_text(encoding="utf-8"))

    def test_metadata_writer_derives_python_abi_from_committed_plan_for_declared_wheel_output(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            tmp_path = Path(tmp)
            wheel = tmp_path / "pytorch_action_wheel.whl"
            wheel.write_text("wheel fixture\n", encoding="utf-8")
            metadata = tmp_path / "provider_metadata.json"

            rc = writer.main(
                [
                    "--plan",
                    str(PLAN),
                    "--keys",
                    str(KEYS),
                    "--rootfs-identity",
                    str(GRAPH_ROOT / "plans" / "torch" / "cu130" / "rootfs.identity.json"),
                    "--wheel",
                    str(wheel),
                    "--build-plan-out",
                    str(tmp_path / "build_plan.json"),
                    "--provider-metadata-out",
                    str(metadata),
                    "--result-marker-out",
                    str(tmp_path / "result.txt"),
                ]
            )

            self.assertEqual(rc, 0)
            metadata_doc = json.loads(metadata.read_text(encoding="utf-8"))
            expected_abi = writer.python_abi_from_plan(json.loads(PLAN.read_text(encoding="utf-8")))
            self.assertEqual(metadata_doc["python_abi"], expected_abi)

    def test_metadata_writer_rejects_missing_key_snapshot_entry(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            tmp_path = Path(tmp)
            keys = tmp_path / "keys.json"
            keys.write_text(json.dumps({"schema": 1, "packages": {}}), encoding="utf-8")
            with self.assertRaises(SystemExit) as raised:
                writer.main(
                    [
                        "--plan",
                        str(PLAN),
                        "--keys",
                        str(keys),
                        "--rootfs-identity",
                        str(GRAPH_ROOT / "plans" / "torch" / "cu130" / "rootfs.identity.json"),
                        "--wheel",
                        str(tmp_path / "torch.whl"),
                        "--build-plan-out",
                        str(tmp_path / "build_plan.json"),
                        "--provider-metadata-out",
                        str(tmp_path / "provider_metadata.json"),
                        "--result-marker-out",
                        str(tmp_path / "result.txt"),
                    ]
                )

        self.assertIn("plans/keys.json is missing torch/cu130/py-torch", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
