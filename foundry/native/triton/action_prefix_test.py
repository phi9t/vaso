#!/usr/bin/env python3
"""Aquery contract tests for the committed-plan Triton action."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys_path_tools = Path(__file__).resolve().parents[2] / "tools"
if str(sys_path_tools) not in sys.path:
    sys.path.insert(0, str(sys_path_tools))
from nested_bazel_fixture import local_override_block, write, write_module_override_stubs


ALLOWLIST_ENV = {
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
    "PYTHONHASHSEED": "0",
    "VASO_ACTION_RESERVED_CPUS": "4",
}

PREFIX_REPOS = (
    "cmake_native",
    "cuda_native",
    "ninja_native",
    "nlohmann_json_native",
    "py_filelock_native",
    "py_lit_native",
    "py_pip_native",
    "py_pybind11_native",
    "py_setuptools_native",
    "py_wheel_native",
    "python_313_native",
    "python_venv_native",
    "rootfs_llvm_23_native",
    "zlib_ng_native",
)


def bazel_binary() -> Path:
    estate = os.environ.get("VASO_ESTATE_ROOT")
    candidates = [
        str(Path(estate) / "opt-vaso" / "bin" / "bazel-real") if estate else None,
        os.environ.get("BAZEL_BIN"),
        os.environ.get("BAZEL_REAL"),
        os.environ.get("BAZEL"),
        shutil.which("bazel-real"),
        shutil.which("bazel"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise FileNotFoundError("bazel-real")


def scratch_parent() -> Path:
    test_tmpdir = os.environ.get("TEST_TMPDIR")
    if test_tmpdir:
        return Path(test_tmpdir)
    agent_root = os.environ.get("VASO_AGENT_IO_ROOT")
    if agent_root:
        path = Path(agent_root) / "tmp"
        path.mkdir(parents=True, exist_ok=True)
        return path
    return Path.cwd()


class TritonActionPrefixTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        parser = argparse.ArgumentParser()
        parser.add_argument("--repo-root", required=True)
        args, _ = parser.parse_known_args()
        cls.repo_root = Path(args.repo_root).resolve().parent
        cls.bazel = bazel_binary()
        cls._tmp = tempfile.TemporaryDirectory(dir=scratch_parent())
        cls.tmp_path = Path(cls._tmp.name)
        cls.nested_output_base = str(cls.tmp_path / "bazel-ob")
        cls.second_output_base = str(cls.tmp_path / "bazel-ob-2")
        cls.module_overrides = cls.tmp_path / "module-overrides"
        write_module_override_stubs(cls.module_overrides)
        override_block = local_override_block(cls.module_overrides)
        cls.module_override_args = [
            f"--override_module={match.group(1)}={match.group(2)}"
            for match in re.finditer(r'module_name = "([^"]+)", path = "([^"]+)"', override_block)
        ]
        cls.repository_cache = str(cls.tmp_path / "repository-cache")
        cls.distdir = str(cls.tmp_path / "distdir")
        Path(cls.repository_cache).mkdir()
        Path(cls.distdir).mkdir()
        cls.source_repo = cls.tmp_path / "triton-source-archive"
        write(cls.source_repo / "MODULE.bazel", 'module(name = "triton_v2_14_0_source_archive")\n')
        write(
            cls.source_repo / "file" / "BUILD.bazel",
            'package(default_visibility = ["//visibility:public"])\n'
            'exports_files(["triton-675c59878aa2280b31f722aaf42b825fcee21de8.tar.gz"])\n'
            'filegroup(name = "file", srcs = ["triton-675c59878aa2280b31f722aaf42b825fcee21de8.tar.gz"])\n',
        )
        (cls.source_repo / "file" / "triton-675c59878aa2280b31f722aaf42b825fcee21de8.tar.gz").write_bytes(
            b"offline aquery source fixture\n"
        )
        cls.prefix_repos = cls.tmp_path / "prefix-repos"
        for repo in PREFIX_REPOS:
            cls.write_prefix_repo(cls.prefix_repos / repo, repo)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    @classmethod
    def write_prefix_repo(cls, root: Path, repo: str) -> None:
        write(root / "MODULE.bazel", f'module(name = "{repo}")\n')
        write(
            root / "BUILD.bazel",
            'package(default_visibility = ["//visibility:public"])\n'
            'exports_files(["prefix_manifest.json", "prefix_path.txt"])\n'
            'filegroup(name = "prefix", srcs = glob(["prefix/**"], allow_empty = True))\n',
        )
        write(root / "prefix_path.txt", "prefix\n")
        write(
            root / "prefix_manifest.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "prefix": "prefix",
                    "files": [{"path": "prefix/.keep", "sha256": hashlib.sha256(repo.encode()).hexdigest()}],
                },
                sort_keys=True,
            )
            + "\n",
        )
        write(root / "prefix" / ".keep", repo + "\n")

    def run_bazel(self, *args: str, output_base: str | None = None) -> subprocess.CompletedProcess[str]:
        startup_args = [
            str(self.bazel),
            f"--output_base={output_base or self.nested_output_base}",
            "--nohome_rc",
            "--nosystem_rc",
        ]
        hermetic_args = [
            "--lockfile_mode=off",
            "--repository_disable_download",
            "--registry=file:///nonexistent",
            f"--distdir={self.distdir}",
            f"--repository_cache={self.repository_cache}",
            f"--override_repository=triton_v2_14_0_source_archive={self.source_repo}",
            *[
                f"--override_repository={repo}={self.prefix_repos / repo}"
                for repo in PREFIX_REPOS
            ],
            *self.module_override_args,
        ]
        return subprocess.run(
            [*startup_args, args[0], *hermetic_args, *args[1:]],
            cwd=self.repo_root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )

    def triton_action_snapshot(self, *extra_args: str, output_base: str | None = None) -> dict[str, object]:
        result = self.run_bazel(
            "aquery",
            "--noshow_progress",
            "--output=jsonproto",
            "--include_artifacts=true",
            "--//native/triton:token=build-native-triton",
            *extra_args,
            'mnemonic("TritonNativePrefix", deps(//native/triton:triton_action))',
            output_base=output_base,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        actions = [action for action in data.get("actions", []) if action.get("mnemonic") == "TritonNativePrefix"]
        self.assertEqual(len(actions), 1, result.stdout)
        input_paths = sorted(self.action_input_paths(data, actions[0]))
        return {
            "action": actions[0],
            "actionKey": actions[0]["actionKey"],
            "input_digests": [(path, self.digest_action_input(path, output_base or self.nested_output_base)) for path in input_paths],
        }

    def action_input_paths(self, data: dict[str, object], action: dict[str, object]) -> list[str]:
        fragments = {fragment["id"]: fragment for fragment in data["pathFragments"]}

        def path_for_fragment(fragment_id: int) -> str:
            parts: list[str] = []
            while fragment_id:
                fragment = fragments[fragment_id]
                parts.append(fragment["label"])
                fragment_id = fragment.get("parentId", 0)
            return "/".join(reversed(parts))

        artifacts = {artifact["id"]: path_for_fragment(artifact["pathFragmentId"]) for artifact in data["artifacts"]}
        dep_sets = {dep_set["id"]: dep_set for dep_set in data["depSetOfFiles"]}
        visited: set[int] = set()
        paths: list[str] = []

        def visit(dep_set_id: int) -> None:
            if dep_set_id in visited:
                return
            visited.add(dep_set_id)
            dep_set = dep_sets[dep_set_id]
            for artifact_id in dep_set.get("directArtifactIds", []):
                paths.append(artifacts[artifact_id])
            for transitive_id in dep_set.get("transitiveDepSetIds", []):
                visit(transitive_id)

        for dep_set_id in action.get("inputDepSetIds", []):
            visit(dep_set_id)
        return paths

    def digest_action_input(self, path: str, output_base: str) -> str:
        candidates = [
            self.repo_root / path,
            Path(output_base) / path,
            Path(output_base) / "execroot" / "_main" / path,
        ]
        for candidate in candidates:
            if candidate.is_file():
                digest = hashlib.sha256()
                with candidate.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                return digest.hexdigest()
        if "external/" in path:
            suffix = path.split("external/", 1)[1]
            repo_segment, _, relative = suffix.partition("/")
            repo_name = repo_segment.split("+")[-1] if "+" in repo_segment else repo_segment
            candidate = self.prefix_repos / repo_name / relative
            if candidate.is_file():
                return hashlib.sha256(candidate.read_bytes()).hexdigest()
        self.fail(f"could not resolve action input {path!r} under {output_base}")

    def test_action_env_and_argv_are_committed_plan_contract(self) -> None:
        snapshot = self.triton_action_snapshot(
            "--action_env=CUDA_VISIBLE_DEVICES=7",
            "--action_env=VASO_GPU_SET=7",
            "--action_env=TMPDIR=/should/not/be-keyed",
            "--action_env=VASO_BAZEL_OB=/should/not/be-keyed",
            "--action_env=VASO_HOST_CPU_JOBS=99",
            "--action_env=PYTORCH_MAX_JOBS=99",
            "--action_env=TORCHAUDIO_MAX_JOBS=99",
        )

        action = snapshot["action"]
        self.assertEqual({item["key"]: item["value"] for item in action["environmentVariables"]}, ALLOWLIST_ENV)
        joined_args = "\n".join(action["arguments"])
        for forbidden in (
            "CUDA_VISIBLE_DEVICES",
            "VASO_GPU_SET",
            "TMPDIR",
            "VASO_BAZEL_OB",
            "VASO_HOST_CPU_JOBS",
            "PYTORCH_MAX_JOBS",
            "TORCHAUDIO_MAX_JOBS",
            "build-native-triton",
            str(self.repo_root),
            self.nested_output_base,
        ):
            self.assertNotIn(forbidden, joined_args)
        input_paths = {path for path, _ in snapshot["input_digests"]}
        self.assertIn("native/common/plan_executor.py", input_paths)
        self.assertIn("native/common/plan_schema.py", input_paths)
        self.assertIn("plans/schema/v1.json", input_paths)
        self.assertIn("plans/torch/cu130/triton.plan.json", input_paths)
        self.assertIn("plans/torch/cu130/rootfs.identity.json", input_paths)
        self.assertIn("native/triton/patches/0001-llvm-35901313.patch", input_paths)
        self.assertIn("native/triton/upstream_pins.json", input_paths)
        self.assertNotIn("native/triton/plan.py", input_paths)
        self.assertNotIn("native/triton/action_driver.py", input_paths)

    def test_action_key_and_input_digests_ignore_jobs_action_env_and_output_base(self) -> None:
        first = self.triton_action_snapshot("--jobs=1")
        second = self.triton_action_snapshot(
            "--jobs=7",
            "--action_env=CUDA_VISIBLE_DEVICES=6",
            "--action_env=VASO_GPU_SET=6",
            output_base=self.second_output_base,
        )

        self.assertEqual(first["actionKey"], second["actionKey"])
        self.assertEqual(first["input_digests"], second["input_digests"])


if __name__ == "__main__":
    unittest.main(argv=[__file__])
