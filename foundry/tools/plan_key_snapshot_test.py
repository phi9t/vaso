#!/usr/bin/env python3
"""Tests for committed native plan key snapshots."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import shutil
import unittest
from pathlib import Path


GRAPH_ROOT = Path(__file__).resolve().parents[1]


def scratch_parent() -> Path:
    return Path(os.environ.get("TEST_TMPDIR") or os.environ.get("VASO_AGENT_IO_ROOT") or Path.cwd())


class PlanKeySnapshotTest(unittest.TestCase):
    def run_snapshot(self, repo_root: Path, out: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(GRAPH_ROOT / "tools" / "plan_key_snapshot.py"),
                "--repo-root",
                str(repo_root),
                "--out",
                str(out),
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def fake_repo(self, root: Path) -> Path:
        fake_root = root / "repo"
        shutil.copytree(GRAPH_ROOT / "plans", fake_root / "plans")
        (fake_root / "native" / "common").mkdir(parents=True)
        for filename in ("committed_plan_prefix.bzl", "plan_executor.py", "plan_schema.py", "prefix_manifest.py"):
            source = GRAPH_ROOT / "native" / "common" / filename
            if source.exists():
                shutil.copy2(source, fake_root / "native" / "common" / filename)
        (fake_root / "native" / "zlib_ng").mkdir(parents=True)
        shutil.copy2(
            GRAPH_ROOT / "native" / "zlib_ng" / "zlib_ng_action.bzl",
            fake_root / "native" / "zlib_ng" / "zlib_ng_action.bzl",
        )
        for relative in (
            "native/bzip2/bzip2_action.bzl",
            "native/pytorch/pytorch_action.bzl",
            "native/pytorch/pytorch-v2.14.0-2b3ec348-submodules.json",
            "native/pytorch/runtime_jobs_executor.py",
            "native/triton/triton_action.bzl",
            "native/triton/upstream_pins.json",
            "native/triton/patches/0001-llvm-35901313.patch",
            "native/torchvision/torchvision_action.bzl",
            "native/torchvision/upstream_pins.json",
            "native/torchaudio/torchaudio_action.bzl",
            "native/torchaudio/upstream_pins.json",
            "native/xz/xz_action.bzl",
            "native/jaxlib/jaxlib_action.bzl",
            "native/jaxlib/runtime_jobs_executor.py",
            "native/jaxlib/committed_plan_support.py",
            "native/jaxlib/upstream_pins.json",
            "native/jaxlib/nested_http_archives.json",
            "native/zstd/zstd_action.bzl",
        ):
            source = GRAPH_ROOT / relative
            target = fake_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        return fake_root

    def add_plan_fixture(self, repo_root: Path, filename: str, package_name: str) -> Path:
        plan_path = repo_root / "plans" / "torch" / "cu130" / filename
        base = json.loads((repo_root / "plans" / "torch" / "cu130" / "zlib_ng.plan.json").read_text(encoding="utf-8"))
        base["package"] = {"name": package_name, "version": "1.0"}
        base["sources"][0]["strip_prefix"] = f"{package_name}-1.0"
        base["outputs"] = [{"kind": "prefix", "path": "{out}"}]
        base["deps"] = {}
        plan_path.write_text(json.dumps(base, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return plan_path

    def test_committed_key_snapshot_matches_merkle_inputs(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(GRAPH_ROOT / "tools" / "plan_key_snapshot.py"),
                "--repo-root",
                str(GRAPH_ROOT),
                "--check",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_snapshot_excludes_generator_code(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            generated = Path(tmp) / "keys.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(GRAPH_ROOT / "tools" / "plan_key_snapshot.py"),
                    "--repo-root",
                    str(GRAPH_ROOT),
                    "--out",
                    str(generated),
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            snapshot = json.loads(generated.read_text(encoding="utf-8"))
            files = {
                item["path"]
                for package in snapshot["packages"].values()
                for item in package["inputs"]
                if "path" in item
            }
            self.assertNotIn("tools/gen_plans.py", files)
            self.assertNotIn("tools/plan_key_snapshot.py", files)
            self.assertIn("native/common/plan_executor.py", files)

    def test_plan_flag_change_changes_merkle_key(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            fake_root = self.fake_repo(Path(tmp))
            first = Path(tmp) / "first.json"
            second = Path(tmp) / "second.json"

            first_result = self.run_snapshot(fake_root, first)
            self.assertEqual(first_result.returncode, 0, first_result.stderr)

            plan_path = fake_root / "plans" / "torch" / "cu130" / "zlib_ng.plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["configure"]["argv"].append("--without-optimizations")
            plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            second_result = self.run_snapshot(fake_root, second)
            self.assertEqual(second_result.returncode, 0, second_result.stderr)

            first_key = json.loads(first.read_text(encoding="utf-8"))["packages"]["torch/cu130/zlib-ng"]["merkle_key"]
            second_key = json.loads(second.read_text(encoding="utf-8"))["packages"]["torch/cu130/zlib-ng"]["merkle_key"]
            self.assertNotEqual(first_key, second_key)

    def test_patch_file_content_digest_is_verified_and_snapshot_input(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            fake_root = self.fake_repo(Path(tmp))
            patch_path = fake_root / "native" / "zlib_ng" / "testdata" / "message.patch"
            patch_path.parent.mkdir(parents=True)
            patch_path.write_text("one-line patch fixture\n", encoding="utf-8")
            plan_path = fake_root / "plans" / "torch" / "cu130" / "zlib_ng.plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["sources"][0]["patches"] = [
                {
                    "path": patch_path.relative_to(fake_root).as_posix(),
                    "sha256": hashlib_sha256(patch_path),
                }
            ]
            plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            out = Path(tmp) / "with-patch.json"

            result = self.run_snapshot(fake_root, out)

            self.assertEqual(result.returncode, 0, result.stderr)
            package = json.loads(out.read_text(encoding="utf-8"))["packages"]["torch/cu130/zlib-ng"]
            self.assertIn(
                {
                    "kind": "patch",
                    "path": "native/zlib_ng/testdata/message.patch",
                    "sha256": hashlib_sha256(patch_path),
                },
                package["inputs"],
            )

            patch_path.write_text("changed patch fixture\n", encoding="utf-8")
            stale = self.run_snapshot(fake_root, Path(tmp) / "stale.json")
            self.assertNotEqual(stale.returncode, 0)
            self.assertIn("patch sha256 mismatch", stale.stderr)

    def test_dep_manifest_content_change_at_same_path_changes_merkle_key(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            fake_root = self.fake_repo(Path(tmp))
            manifest = fake_root / "plans" / "deps" / "zdep.manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text('{"files":[{"path":"include/z.h","sha256":"aaa"}]}\n', encoding="utf-8")
            plan_path = fake_root / "plans" / "torch" / "cu130" / "zlib_ng.plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["deps"] = {
                "zdep": {
                    "label": "//deps:zdep",
                    "manifest": "//plans:deps/zdep.manifest.json",
                    "key": "sha256:first",
                }
            }
            plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            first = Path(tmp) / "first.json"
            second = Path(tmp) / "second.json"

            first_result = self.run_snapshot(fake_root, first)
            self.assertEqual(first_result.returncode, 0, first_result.stderr)
            manifest.write_text('{"files":[{"path":"include/z.h","sha256":"bbb"}]}\n', encoding="utf-8")
            second_result = self.run_snapshot(fake_root, second)
            self.assertEqual(second_result.returncode, 0, second_result.stderr)

            first_key = json.loads(first.read_text(encoding="utf-8"))["packages"]["torch/cu130/zlib-ng"]["merkle_key"]
            second_key = json.loads(second.read_text(encoding="utf-8"))["packages"]["torch/cu130/zlib-ng"]["merkle_key"]
            self.assertNotEqual(first_key, second_key)
            self.assertEqual(plan["deps"]["zdep"]["key"], "sha256:first")

    def test_dep_key_must_match_computed_dep_merkle_key(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            fake_root = self.fake_repo(Path(tmp))
            self.add_plan_fixture(fake_root, "zdep.plan.json", "zdep")
            plan_path = fake_root / "plans" / "torch" / "cu130" / "zlib_ng.plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["deps"] = {"zdep": {"label": "//deps:zdep", "key": "stale-copied-key"}}
            plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            result = self.run_snapshot(fake_root, Path(tmp) / "stale.json")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("dependency key mismatch for zdep", result.stderr)

    def test_dep_key_match_uses_plan_package_when_action_label_target_differs(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            fake_root = self.fake_repo(Path(tmp))
            self.add_plan_fixture(fake_root, "zstd.plan.json", "zstd")
            plan_path = fake_root / "plans" / "torch" / "cu130" / "zlib_ng.plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["deps"] = {
                "zstd": {
                    "label": "//native/zstd:zstd_action_prefix",
                    "key": "stale-copied-key",
                }
            }
            plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            result = self.run_snapshot(fake_root, Path(tmp) / "stale-action-label.json")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("dependency key mismatch for zstd", result.stderr)

    def test_dep_content_change_changes_dependent_merkle_key(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            fake_root = self.fake_repo(Path(tmp))
            dep_plan_path = self.add_plan_fixture(fake_root, "zdep.plan.json", "zdep")
            parent_plan_path = fake_root / "plans" / "torch" / "cu130" / "zlib_ng.plan.json"
            parent = json.loads(parent_plan_path.read_text(encoding="utf-8"))

            dep_only = Path(tmp) / "dep-only.json"
            dep_result = self.run_snapshot(fake_root, dep_only)
            self.assertEqual(dep_result.returncode, 0, dep_result.stderr)
            first_dep_key = json.loads(dep_only.read_text(encoding="utf-8"))["packages"]["torch/cu130/zdep"]["merkle_key"]
            parent["deps"] = {"zdep": {"label": "//deps:zdep", "key": first_dep_key}}
            parent_plan_path.write_text(json.dumps(parent, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            first = Path(tmp) / "first.json"
            first_result = self.run_snapshot(fake_root, first)
            self.assertEqual(first_result.returncode, 0, first_result.stderr)
            first_parent_key = json.loads(first.read_text(encoding="utf-8"))["packages"]["torch/cu130/zlib-ng"]["merkle_key"]

            dep = json.loads(dep_plan_path.read_text(encoding="utf-8"))
            dep["configure"]["argv"].append("--changed-dep-content")
            dep_plan_path.write_text(json.dumps(dep, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            stale = self.run_snapshot(fake_root, Path(tmp) / "stale.json")
            self.assertNotEqual(stale.returncode, 0)
            self.assertIn("dependency key mismatch for zdep", stale.stderr)

            dep_changed = Path(tmp) / "dep-changed.json"
            dep_changed_result = self.run_snapshot_without_parent_dep(fake_root, parent_plan_path, dep_changed)
            self.assertEqual(dep_changed_result.returncode, 0, dep_changed_result.stderr)
            second_dep_key = json.loads(dep_changed.read_text(encoding="utf-8"))["packages"]["torch/cu130/zdep"]["merkle_key"]
            parent["deps"]["zdep"]["key"] = second_dep_key
            parent_plan_path.write_text(json.dumps(parent, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            second = Path(tmp) / "second.json"
            second_result = self.run_snapshot(fake_root, second)
            self.assertEqual(second_result.returncode, 0, second_result.stderr)
            second_parent_key = json.loads(second.read_text(encoding="utf-8"))["packages"]["torch/cu130/zlib-ng"]["merkle_key"]

            self.assertNotEqual(first_dep_key, second_dep_key)
            self.assertNotEqual(first_parent_key, second_parent_key)

    def run_snapshot_without_parent_dep(
        self,
        repo_root: Path,
        parent_plan_path: Path,
        out: Path,
    ) -> subprocess.CompletedProcess[str]:
        parent = json.loads(parent_plan_path.read_text(encoding="utf-8"))
        saved_deps = parent["deps"]
        parent["deps"] = {}
        parent_plan_path.write_text(json.dumps(parent, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        try:
            return self.run_snapshot(repo_root, out)
        finally:
            parent["deps"] = saved_deps
            parent_plan_path.write_text(json.dumps(parent, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def test_action_wrapper_contract_is_in_snapshot(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            generated = Path(tmp) / "keys.json"
            result = self.run_snapshot(GRAPH_ROOT, generated)
            self.assertEqual(result.returncode, 0, result.stderr)
            package = json.loads(generated.read_text(encoding="utf-8"))["packages"]["torch/cu130/zlib-ng"]
            wrapper = [item for item in package["inputs"] if item["kind"] == "action-wrapper"]
            self.assertEqual(len(wrapper), 1)
            self.assertEqual(
                wrapper[0]["env"],
                {
                    "LC_ALL": "C",
                    "PATH": "/usr/bin:/bin",
                    "PYTHONHASHSEED": "0",
                    "VASO_ACTION_RESERVED_CPUS": "4",
                },
            )
            self.assertIn("--schema", wrapper[0]["argv"])

    def test_triton_snapshot_records_committed_plan_executor_and_action_contract(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            generated = Path(tmp) / "keys.json"
            result = self.run_snapshot(GRAPH_ROOT, generated)
            self.assertEqual(result.returncode, 0, result.stderr)
            package = json.loads(generated.read_text(encoding="utf-8"))["packages"]["torch/cu130/py-triton"]

        paths = {item["path"] for item in package["inputs"] if "path" in item}
        self.assertIn("plans/torch/cu130/triton.plan.json", paths)
        self.assertIn("native/common/plan_executor.py", paths)
        self.assertIn("native/common/plan_schema.py", paths)
        self.assertNotIn("native/triton/plan.py", paths)
        self.assertNotIn("native/triton/action_driver.py", paths)
        wrapper = [item for item in package["inputs"] if item["kind"] == "action-wrapper"]
        self.assertEqual(len(wrapper), 1)
        self.assertEqual(
            wrapper[0]["env"],
            {
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
                "PYTHONHASHSEED": "0",
                "VASO_ACTION_RESERVED_CPUS": "4",
            },
        )
        argv = " ".join(wrapper[0]["argv"])
        self.assertIn("--wheel-out", argv)
        self.assertNotIn("action_driver.py", argv)
        self.assertNotIn("plan.py", argv)

    def test_torchvision_snapshot_records_committed_plan_executor_and_action_contract(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            generated = Path(tmp) / "keys.json"
            result = self.run_snapshot(GRAPH_ROOT, generated)
            self.assertEqual(result.returncode, 0, result.stderr)
            package = json.loads(generated.read_text(encoding="utf-8"))["packages"]["torch/cu130/py-torchvision"]

        paths = {item["path"] for item in package["inputs"] if "path" in item}
        self.assertIn("plans/torch/cu130/torchvision.plan.json", paths)
        self.assertIn("native/common/plan_executor.py", paths)
        self.assertIn("native/common/plan_schema.py", paths)
        self.assertIn("native/common/prefix_manifest.py", paths)
        self.assertNotIn("native/torchvision/plan.py", paths)
        self.assertNotIn("native/torchvision/action_driver.py", paths)
        deps = {item["name"]: item for item in package["inputs"] if item["kind"] == "dep"}
        self.assertIn("torch", deps)
        self.assertEqual(deps["torch"]["label"], "//native/pytorch:pytorch_action")
        self.assertIn("key", deps["torch"])
        wrapper = [item for item in package["inputs"] if item["kind"] == "action-wrapper"]
        self.assertEqual(len(wrapper), 1)
        self.assertEqual(
            wrapper[0]["env"],
            {
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
                "PYTHONHASHSEED": "0",
                "VASO_ACTION_RESERVED_CPUS": "4",
            },
        )
        argv = " ".join(wrapper[0]["argv"])
        self.assertIn("--wheel-out", argv)
        self.assertIn("--dep-prefix", argv)
        self.assertNotIn("--max-jobs", argv)
        self.assertNotIn("action_driver.py", argv)
        self.assertNotIn("plan.py", argv)

    def test_torchaudio_snapshot_records_committed_plan_executor_and_action_contract(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            generated = Path(tmp) / "keys.json"
            result = self.run_snapshot(GRAPH_ROOT, generated)
            self.assertEqual(result.returncode, 0, result.stderr)
            package = json.loads(generated.read_text(encoding="utf-8"))["packages"]["torch/cu130/py-torchaudio"]

        paths = {item["path"] for item in package["inputs"] if "path" in item}
        self.assertIn("plans/torch/cu130/torchaudio.plan.json", paths)
        self.assertIn("native/common/plan_executor.py", paths)
        self.assertIn("native/common/plan_schema.py", paths)
        self.assertIn("native/common/prefix_manifest.py", paths)
        self.assertNotIn("native/torchaudio/plan.py", paths)
        self.assertNotIn("native/torchaudio/action_driver.py", paths)
        deps = {item["name"]: item for item in package["inputs"] if item["kind"] == "dep"}
        self.assertIn("torch", deps)
        self.assertEqual(deps["torch"]["label"], "//native/pytorch:pytorch_action")
        self.assertIn("key", deps["torch"])
        wrapper = [item for item in package["inputs"] if item["kind"] == "action-wrapper"]
        self.assertEqual(len(wrapper), 1)
        self.assertEqual(
            wrapper[0]["env"],
            {
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
                "PYTHONHASHSEED": "0",
                "VASO_ACTION_RESERVED_CPUS": "4",
            },
        )
        argv = " ".join(wrapper[0]["argv"])
        self.assertIn("--wheel-out", argv)
        self.assertIn("--dep-prefix", argv)
        self.assertNotIn("--max-jobs", argv)
        self.assertNotIn("action_driver.py", argv)
        self.assertNotIn("plan.py", argv)

    def test_jaxlib_snapshot_records_committed_plan_executor_nested_bazel_and_action_contract(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            generated = Path(tmp) / "keys.json"
            result = self.run_snapshot(GRAPH_ROOT, generated)
            self.assertEqual(result.returncode, 0, result.stderr)
            package = json.loads(generated.read_text(encoding="utf-8"))["packages"]["jax/cu130/py-jaxlib"]

        paths = {item["path"] for item in package["inputs"] if "path" in item}
        self.assertIn("plans/jax/cu130/jaxlib.plan.json", paths)
        self.assertIn("native/common/plan_executor.py", paths)
        self.assertIn("native/common/plan_schema.py", paths)
        self.assertIn("native/common/prefix_manifest.py", paths)
        self.assertIn("native/jaxlib/upstream_pins.json", paths)
        self.assertIn("native/jaxlib/nested_http_archives.json", paths)
        self.assertNotIn("native/jaxlib/plan.py", paths)
        self.assertNotIn("native/jaxlib/action_driver.py", paths)
        nested_inputs = [item for item in package["inputs"] if item["kind"] == "nested-bazel"]
        self.assertEqual(len(nested_inputs), 1)
        self.assertEqual(nested_inputs[0]["archive_count"], 155)
        self.assertIn("native/jaxlib/runtime_jobs_executor.py", paths)
        self.assertEqual(nested_inputs[0]["distdir_manifest"], "@jaxlib_nested_cache_manifest//:manifest.json")
        self.assertEqual(nested_inputs[0]["repository_cache_manifest"], "@jaxlib_nested_cache_manifest//:manifest.json")
        wrapper = [item for item in package["inputs"] if item["kind"] == "action-wrapper"]
        self.assertEqual(len(wrapper), 1)
        self.assertEqual(
            wrapper[0]["env"],
            {
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
                "PYTHONHASHSEED": "0",
                "VASO_ACTION_RESERVED_CPUS": "4",
            },
        )
        argv = " ".join(wrapper[0]["argv"])
        self.assertIn("--wheel-out", argv)
        self.assertIn("--nested-cache-manifest", argv)
        self.assertIn("--nested-distdir", argv)
        self.assertIn("--nested-repository-cache", argv)
        self.assertIn("--nested-disk-cache", argv)
        self.assertIn("--dep-prefix", argv)
        self.assertNotIn("--max-jobs", argv)
        self.assertNotIn("--output_base", argv)
        self.assertNotIn("action_driver.py", argv)
        self.assertNotIn("plan.py", argv)

    def test_pytorch_snapshot_records_committed_plan_executor_and_action_contract(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            generated = Path(tmp) / "keys.json"
            result = self.run_snapshot(GRAPH_ROOT, generated)
            self.assertEqual(result.returncode, 0, result.stderr)
            package = json.loads(generated.read_text(encoding="utf-8"))["packages"]["torch/cu130/py-torch"]

        paths = {item["path"] for item in package["inputs"] if "path" in item}
        self.assertIn("plans/torch/cu130/pytorch.plan.json", paths)
        self.assertIn("native/common/plan_executor.py", paths)
        self.assertIn("native/common/plan_schema.py", paths)
        self.assertIn("native/pytorch/runtime_jobs_executor.py", paths)
        self.assertIn("native/pytorch/pytorch-v2.14.0-2b3ec348-submodules.json", paths)
        self.assertNotIn("native/pytorch/plan.py", paths)
        self.assertNotIn("native/pytorch/action_driver.py", paths)
        deps = {item["name"]: item for item in package["inputs"] if item["kind"] == "dep"}
        for name in ("cuda", "cudnn", "nccl", "python", "cmake", "ninja", "openblas", "cudss", "nvshmem"):
            self.assertIn(name, deps)
            self.assertIn("manifest", deps[name])
            self.assertIn("key", deps[name])
        wrapper = [item for item in package["inputs"] if item["kind"] == "action-wrapper"]
        self.assertEqual(len(wrapper), 1)
        self.assertEqual(
            wrapper[0]["env"],
            {
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
                "PYTHONHASHSEED": "0",
                "VASO_ACTION_RESERVED_CPUS": "4",
            },
        )
        argv = " ".join(wrapper[0]["argv"])
        self.assertIn("--wheel-out", argv)
        self.assertIn("--dep-prefix", argv)
        self.assertIn("--input", argv)
        self.assertNotIn("--max-jobs", argv)
        self.assertNotIn("--token", argv)
        self.assertNotIn("action_driver.py", argv)
        self.assertNotIn("plan.py", argv)


def hashlib_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    unittest.main()
