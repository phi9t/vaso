"""Focused behavioral checks for the real Pascal Bazel build path."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

HERE = Path(__file__).resolve().parent
FOUNDRY = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(FOUNDRY / "native/common"))
import build_native
from install_builder import create_builder, replace_copy
from native_step import prepare_torch, validate_wheel
from plan_schema import validate_plan
from plans import PROVIDERS, WHEEL_NAMES, build_plan, pascal_schema, provider_contract, rootfs_identity


class PascalNativeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def inputs(self):
        return (
            (FOUNDRY / "native/pytorch215/profiles/cu126-sm61-r4.json").read_text(),
            (FOUNDRY / "native/triton/pascal/profile.json").read_text(),
            (FOUNDRY / "native/triton/pascal/signed-int8-dot.patch").read_text(),
        )

    def test_both_plans_share_executor_contract_and_complete_capsule(self):
        torch_text, triton_text, patch = self.inputs()
        create_builder(self.root, torch_text, triton_text, patch, "a" * 64)
        schema = json.loads((self.root / "schema.json").read_text())
        original_schema = json.loads((FOUNDRY / "plans/schema/v1.json").read_text())
        self.assertNotIn("cu126", original_schema["properties"]["line"]["enum"])
        for target in WHEEL_NAMES:
            plan = json.loads((self.root / (target + ".plan.json")).read_text())
            self.assertEqual(validate_plan(plan, schema), [])
            self.assertEqual(set(plan["deps"]), set(PROVIDERS))
            self.assertEqual(plan["gpu_archs"], ["sm_61"])
            self.assertEqual(plan["toolchain"]["env"]["CC"], "gcc")
            self.assertEqual(plan["toolchain"]["env"]["MAX_JOBS"], "{jobs}")
            self.assertEqual(plan["toolchain"]["env"]["UV_CACHE_DIR"], "{work}/uv-cache")
            self.assertIn("/opt/vaso/pascal/bin/uv", plan["build"]["argv"])
            self.assertNotIn("source_build.py", json.dumps(plan))
        self.assertEqual((self.root / "plan_executor.py").read_bytes(),
                         (FOUNDRY / "native/common/plan_executor.py").read_bytes())

    def test_torch_feature_selection_is_preserved(self):
        torch_text, triton_text, patch = self.inputs()
        create_builder(self.root, torch_text, triton_text, patch, "a" * 64)
        profile = json.loads(torch_text)
        plan = json.loads((self.root / "torch.plan.json").read_text())
        for name, value in profile["environment"].items():
            self.assertEqual(plan["toolchain"]["env"][name], value)
        self.assertEqual(plan["sources"][0]["sha256"], profile["torch"]["archive_sha256"])
        self.assertEqual(plan["toolchain"]["env"]["CMAKE_EXECUTABLE"],
                         PROVIDERS["cmake"]["prefix"] + "/bin/cmake")

    def test_bad_capsule_identity_rejected(self):
        for bad in ("", "a"*63, "G"*64, "../x"):
            with self.assertRaises(ValueError):
                provider_contract(bad)

    def test_native_source_edit_exact_preimage(self):
        source = self.root / "source"
        source.mkdir()
        (source / "main.cpp").write_text("old\n")
        profile = {"critical_source_files": ["main.cpp"], "source_edits": [{
            "path": "main.cpp", "before_text": "old\n", "after_text": "new\n", "old": "old", "new": "new",
        }]}
        prepare_torch(source, profile)
        self.assertEqual((source / "main.cpp").read_text(), "new\n")
        with self.assertRaisesRegex(ValueError, "preimage"):
            prepare_torch(source, profile)

    def test_wheel_requires_exact_native_extension_and_version(self):
        wheel = self.root / WHEEL_NAMES["torch"]
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("torch-2.15.0.dist-info/METADATA", "Name: torch\nVersion: 2.15.0+vaso.cu126.sm61\n")
            archive.writestr("torch/_C.cpython-312-x86_64-linux-gnu.so", b"unit-test fixture")
        self.assertEqual(validate_wheel(wheel, "torch")["python_abi"], "cp312")
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("torch-2.15.0.dist-info/METADATA", "Name: torch\nVersion: 2.15.0+vaso.cu126.sm61\n")
        with self.assertRaisesRegex(ValueError, "native extension"):
            validate_wheel(wheel, "torch")

    def test_bazel_receipt_distinguishes_actual_native_spawn(self):
        log = self.root / "execution.json"
        with self.assertRaisesRegex(ValueError, "execution log missing"):
            build_native.execution_counts(log)
        log.write_text('{"mnemonic":"PascalNativeWheel","cacheHit":false}\n')
        self.assertTrue(build_native.execution_counts(log)["native_execution"])
        log.write_text('{"mnemonic":"PascalNativeWheel","cacheHit":true}\n')
        self.assertTrue(build_native.execution_counts(log)["cache_hit"])
        log.write_text("")
        self.assertEqual(build_native.execution_counts(log)["native_execution_count"], 0)

    def test_request_and_jobs_do_not_enter_native_action_command(self):
        attempt = self.root / "attempt"
        command = build_native.build_command("torch", "build", attempt)
        self.assertNotIn("--request-id", command)
        self.assertIn("--spawn_strategy=local", command)
        self.assertIn("--repository_disable_download", command)
        self.assertIn("--nobuild", build_native.build_command("triton", "prepare", attempt))
        text = (HERE / "native_wheel.bzl").read_text()
        self.assertIn('execution_requirements = {"no-sandbox": "1"}', text)
        wrapper = (HERE / "build-native.sh").read_text()
        self.assertIn("UV_CACHE_DIR=/workspace/native-build/uv-cache", wrapper)
        self.assertIn("TMPDIR=/workspace/native-build/tmp", wrapper)
        self.assertIn("export USER=vaso", wrapper)
        self.assertIn('"/workspace/native-build/" + ctx.attr.package + "/jobs.txt"', text)

    def test_atomic_install_does_not_mutate_shared_parent_inode(self):
        parent = self.root / "parent"
        parent.write_text("precious")
        staged = self.root / "staged"
        os.link(parent, staged)
        incoming = self.root / "incoming"
        incoming.write_text("replacement")
        replace_copy(incoming, staged, 0o755)
        self.assertEqual(parent.read_text(), "precious")
        self.assertNotEqual(parent.stat().st_ino, staged.stat().st_ino)


if __name__ == "__main__":
    unittest.main()
