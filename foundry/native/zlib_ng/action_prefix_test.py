#!/usr/bin/env python3
"""Tests for the action-backed zlib-ng prefix rule."""

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


ACTION_RULES = {
    "zlib-ng": Path("native/zlib_ng/zlib_ng_action.bzl"),
    "bzip2": Path("native/bzip2/bzip2_action.bzl"),
    "xz": Path("native/xz/xz_action.bzl"),
    "zstd": Path("native/zstd/zstd_action.bzl"),
}
LEAF_ACTIONS = {
    "zlib-ng": {
        "archive_repo": "zlib_ng_action_archive",
        "archive_name": "zlib-ng-2.3.3.tar.gz",
        "mnemonic": "ZlibNgActionPrefix",
        "plan": "zlib_ng",
        "target": "//native/zlib_ng:zlib_ng_action_prefix",
    },
    "bzip2": {
        "archive_repo": "bzip2_action_archive",
        "archive_name": "bzip2-1.0.8.tar.gz",
        "mnemonic": "Bzip2ActionPrefix",
        "plan": "bzip2",
        "target": "//native/bzip2:bzip2_action_prefix",
    },
    "xz": {
        "archive_repo": "xz_action_archive",
        "archive_name": "xz-5.8.3.tar.gz",
        "mnemonic": "XzActionPrefix",
        "plan": "xz",
        "target": "//native/xz:xz_action_prefix",
    },
    "zstd": {
        "archive_repo": "zstd_action_archive",
        "archive_name": "zstd-1.5.7.tar.gz",
        "mnemonic": "ZstdActionPrefix",
        "plan": "zstd",
        "target": "//native/zstd:zstd_action_prefix",
    },
}
ALLOWLIST_ENV = {
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
    "PYTHONHASHSEED": "0",
    "VASO_ACTION_RESERVED_CPUS": "4",
}


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
    estate = os.environ.get("VASO_ESTATE_ROOT")
    if estate:
        path = Path(estate) / "agents" / os.environ.get("VASO_AGENT", "zlib-ng-action-test") / "tmp"
        path.mkdir(parents=True, exist_ok=True)
        return path
    return Path.cwd()


class ZlibNgActionPrefixTest(unittest.TestCase):
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
        cls.dev_module_output_base = str(cls.tmp_path / "bazel-ob-dev-module")
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
        cls.source_repos = {}
        for package, config in LEAF_ACTIONS.items():
            repo = cls.tmp_path / (config["archive_repo"].replace("_", "-"))
            cls.source_repos[config["archive_repo"]] = repo
            write(repo / "MODULE.bazel", 'module(name = "{}")\n'.format(config["archive_repo"]))
            write(
                repo / "file" / "BUILD.bazel",
                'package(default_visibility = ["//visibility:public"])\nexports_files(["{0}"])\nfilegroup(name = "file", srcs = ["{0}"])\n'.format(config["archive_name"]),
            )
            (repo / "file" / config["archive_name"]).write_bytes(("offline aquery source fixture for " + package + "\n").encode())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def run_bazel(
        self,
        *args: str,
        output_base: str | None = None,
        repo_root: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        repo_root = repo_root or self.repo_root
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
            *[
                f"--override_repository={repo_name}={repo_path}"
                for repo_name, repo_path in sorted(self.source_repos.items())
            ],
            *self.module_override_args,
        ]
        return subprocess.run(
            [*startup_args, args[0], *hermetic_args, *args[1:]],
            cwd=repo_root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )

    def leaf_action_snapshot(
        self,
        package: str,
        *extra_args: str,
        output_base: str | None = None,
        repo_root: Path | None = None,
    ) -> dict[str, object]:
        config = LEAF_ACTIONS[package]
        repo_root = repo_root or self.repo_root
        result = self.run_bazel(
            "aquery",
            "--noshow_progress",
            "--output=jsonproto",
            "--include_artifacts=true",
            *extra_args,
            'mnemonic("{}", deps({}))'.format(config["mnemonic"], config["target"]),
            output_base=output_base,
            repo_root=repo_root,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        actions = [action for action in data.get("actions", []) if action.get("mnemonic") == config["mnemonic"]]
        self.assertEqual(len(actions), 1, result.stdout)
        input_paths = sorted(self.action_input_paths(data, actions[0]))
        return {
            "action": actions[0],
            "actionKey": actions[0]["actionKey"],
            "input_digests": [
                (path, self.digest_action_input(path, output_base or self.nested_output_base, repo_root))
                for path in input_paths
            ],
        }

    def zlib_action_snapshot(self, *extra_args: str, output_base: str | None = None) -> dict[str, object]:
        return self.leaf_action_snapshot("zlib-ng", *extra_args, output_base=output_base)

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

    def digest_action_input(self, path: str, output_base: str, repo_root: Path | None = None) -> str:
        repo_root = repo_root or self.repo_root
        candidates = [
            repo_root / path,
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
        self.fail(f"could not resolve action input {path!r} under {output_base}")

    def writable_graph_copy(self, name: str) -> Path:
        target = self.tmp_path / name
        shutil.copytree(
            self.repo_root,
            target,
            symlinks=True,
            ignore=shutil.ignore_patterns(
                "bazel-bin",
                "bazel-out",
                "bazel-testlogs",
                "bazel-graph",
                "bazel-foundry",
                "__pycache__",
                "*.pyc",
            ),
        )
        return target

    def zlib_action_key(self, *extra_args: str) -> str:
        return str(self.zlib_action_snapshot(*extra_args)["actionKey"])

    def test_action_uses_plan_toolchain_not_bazel_cpp_resolution(self) -> None:
        common_rule = (self.repo_root / "native/common/committed_plan_prefix.bzl").read_text(encoding="utf-8")
        for package, action_rule_path in ACTION_RULES.items():
            action_rule = (self.repo_root / action_rule_path).read_text(encoding="utf-8")
            self.assertIn(LEAF_ACTIONS[package]["mnemonic"], action_rule, package)
            if package == "zlib-ng":
                self.assertIn("The committed plan is the compiler source of truth", action_rule)
                self.assertNotIn("native_action_toolchains()", action_rule)
                self.assertNotIn('fragments = ["cpp"]', action_rule)
                self.assertIn('"patches": attr.label_keyed_string_dict(', action_rule)
                self.assertIn('"deps": attr.label_keyed_string_dict(', action_rule)
                self.assertIn("--patch", action_rule)
                self.assertIn("--dep-prefix", action_rule)
            else:
                self.assertIn("committed_plan_prefix(", action_rule, package)
                self.assertIn("NativeActionPrefixInfo(", common_rule)

    def test_leaf_action_build_files_expose_action_prefix_targets(self) -> None:
        for package, config in LEAF_ACTIONS.items():
            with self.subTest(package=package):
                package_dir = "zlib_ng" if package == "zlib-ng" else package
                build = (self.repo_root / "native" / package_dir / "BUILD.bazel").read_text(encoding="utf-8")
                self.assertIn(config["target"].rsplit(":", 1)[1] + "(", build)
                self.assertIn(config["archive_repo"], build)
                self.assertIn("rootfs_identity = select(", build)

    def test_legacy_source_repositories_remain_for_repo_rule_consumers(self) -> None:
        dev_module = (self.repo_root / "dev.MODULE.bazel").read_text(encoding="utf-8")
        for repo in ("zstd_native", "xz_native", "bzip2_native"):
            self.assertIn(repo + " = use_repo_rule", dev_module)
            self.assertIn('name = "{}"'.format(repo), dev_module)

    def test_new_leaf_manifest_filegroups_expose_manifest_outputs(self) -> None:
        for package in ("bzip2", "xz", "zstd"):
            with self.subTest(package=package):
                target = LEAF_ACTIONS[package]["target"] + "_manifest"
                result = self.run_bazel("cquery", "--output=files", target)
                self.assertEqual(result.returncode, 0, result.stderr)
                outputs = [line for line in result.stdout.splitlines() if line.strip()]
                self.assertEqual(len(outputs), 1, result.stdout)
                self.assertTrue(outputs[0].endswith("_action_prefix_manifest.json"), outputs[0])

    def test_leaf_actions_have_stable_keys_and_inputs(self) -> None:
        for package, config in LEAF_ACTIONS.items():
            with self.subTest(package=package):
                first = self.leaf_action_snapshot(package, "--jobs=1")
                second = self.leaf_action_snapshot(package, "--jobs=7", "--action_env=FOO=1", output_base=self.second_output_base)
                self.assertEqual(first["actionKey"], second["actionKey"])
                self.assertEqual(first["input_digests"], second["input_digests"])
                input_paths = {path for path, _ in first["input_digests"]}
                self.assertIn("native/common/plan_executor.py", input_paths)
                self.assertIn("native/common/plan_schema.py", input_paths)
                self.assertIn("plans/schema/v1.json", input_paths)
                self.assertIn("plans/torch/cu130/{}.plan.json".format(config["plan"]), input_paths)
                self.assertIn("plans/torch/cu130/rootfs.identity.json", input_paths)

    def test_leaf_actions_ignore_dev_module_unrelated_repo_edit(self) -> None:
        before = {
            package: self.leaf_action_snapshot(package, "--jobs=1")
            for package in LEAF_ACTIONS
        }
        edited_repo = self.writable_graph_copy("dev-module-edit-repo")
        dev_module = edited_repo / "dev.MODULE.bazel"
        original = dev_module.read_text(encoding="utf-8")
        dev_module.write_text(original + "\n# unrelated E11-style dev module edit\n", encoding="utf-8")
        after = {
            package: self.leaf_action_snapshot(
                package,
                "--jobs=7",
                output_base=self.dev_module_output_base,
                repo_root=edited_repo,
            )
            for package in LEAF_ACTIONS
        }
        for package in LEAF_ACTIONS:
            with self.subTest(package=package):
                self.assertEqual(before[package]["actionKey"], after[package]["actionKey"])
                self.assertEqual(before[package]["input_digests"], after[package]["input_digests"])

    def test_leaf_action_env_and_argv_are_committed_plan_contract(self) -> None:
        for package in LEAF_ACTIONS:
            with self.subTest(package=package):
                snapshot = self.leaf_action_snapshot(
                    package,
                    "--//native/common:source_date_epoch=1700000000",
                    "--//native/common:deterministic_seed=abcdef12",
                    "--//native/common:deterministic_prefix_map_roots=/vaso-ob/execroot/_main,/vaso/lines/cu130/work",
                    "--action_env=CUDA_VISIBLE_DEVICES=7",
                    "--action_env=VASO_GPU_SET=7",
                    "--action_env=VASO_BAZEL_OB=/should/not/be-keyed",
                )
                action = snapshot["action"]
                self.assertEqual({item["key"]: item["value"] for item in action["environmentVariables"]}, ALLOWLIST_ENV)
                for arg in action["arguments"]:
                    self.assertFalse(arg.startswith("/"), action["arguments"])
                    self.assertNotIn(str(self.repo_root), arg)
                    self.assertNotIn(self.nested_output_base, arg)

    def test_zlib_compatibility_wrapper_stays_on_original_pilot_rule(self) -> None:
        action_rule = (self.repo_root / ACTION_RULES["zlib-ng"]).read_text(encoding="utf-8")
        self.assertIn("_zlib_ng_action_prefix = rule(", action_rule)
        self.assertNotIn("committed_plan_prefix(", action_rule)

    def test_dead_action_source_repository_is_removed(self) -> None:
        dev_module = (self.repo_root / "dev.MODULE.bazel").read_text(encoding="utf-8")
        self.assertNotIn("zlib_ng_action_src", dev_module)

    def test_action_key_ignores_legacy_determinism_build_settings(self) -> None:
        first = self.zlib_action_key(
            "--//native/common:source_date_epoch=1700000000",
            "--//native/common:deterministic_seed=abcdef12",
            "--//native/common:deterministic_prefix_map_roots=/vaso-ob/execroot/_main,/vaso/lines/cu130/work",
        )
        second = self.zlib_action_key(
            "--//native/common:source_date_epoch=1800000000",
            "--//native/common:deterministic_seed=12345678",
            "--//native/common:deterministic_prefix_map_roots=/other-ob/execroot/_main,/other/work",
        )

        self.assertEqual(first, second)

    def test_action_env_and_argv_are_committed_plan_contract(self) -> None:
        snapshot = self.zlib_action_snapshot(
            "--//native/common:source_date_epoch=1700000000",
            "--//native/common:deterministic_seed=abcdef12",
            "--//native/common:deterministic_prefix_map_roots=/vaso-ob/execroot/_main,/vaso/lines/cu130/work",
            "--action_env=CUDA_VISIBLE_DEVICES=7",
            "--action_env=VASO_GPU_SET=7",
            "--action_env=VASO_BAZEL_OB=/should/not/be-keyed",
        )

        action = snapshot["action"]
        self.assertEqual({item["key"]: item["value"] for item in action["environmentVariables"]}, ALLOWLIST_ENV)
        for arg in action["arguments"]:
            self.assertFalse(arg.startswith("/"), action["arguments"])
            self.assertNotIn(str(self.repo_root), arg)
            self.assertNotIn(self.nested_output_base, arg)
        input_paths = {path for path, _ in snapshot["input_digests"]}
        self.assertIn("native/common/plan_executor.py", input_paths)
        self.assertIn("native/common/plan_schema.py", input_paths)
        self.assertIn("plans/schema/v1.json", input_paths)
        self.assertIn("plans/torch/cu130/zlib_ng.plan.json", input_paths)
        self.assertIn("plans/torch/cu130/rootfs.identity.json", input_paths)

    def test_action_key_and_input_digests_ignore_jobs_action_env_and_output_base(self) -> None:
        first = self.zlib_action_snapshot("--jobs=1")
        second = self.zlib_action_snapshot("--jobs=7", "--action_env=FOO=1", output_base=self.second_output_base)

        self.assertEqual(first["actionKey"], second["actionKey"])
        self.assertEqual(first["input_digests"], second["input_digests"])

    def test_leak_scan_runs_inside_real_bazel_action(self) -> None:
        result = self.run_bazel(
            "build",
            "--verbose_failures",
            "//native/zlib_ng:leak_scan_real_action_fixture",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("non-relocatable output leak", result.stderr)


if __name__ == "__main__":
    unittest.main(argv=[__file__])
