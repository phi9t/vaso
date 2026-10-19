#!/usr/bin/env python3
"""Bazel action-key fixtures for native dependency content inputs."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from nested_bazel_fixture import local_override_block, write, write_module_override_stubs


GRAPH_ROOT = Path(__file__).resolve().parents[2]
HELPER_BZL = Path(__file__).with_name("native_action_inputs.bzl")
BUILD_SETTINGS_BZL = Path(__file__).with_name("build_settings.bzl")


def scratch_parent() -> Path:
    if value := os.environ.get("TEST_TMPDIR"):
        return Path(value)
    if value := os.environ.get("VASO_AGENT_IO_ROOT"):
        path = Path(value) / "tmp"
        path.mkdir(parents=True, exist_ok=True)
        return path
    if value := os.environ.get("VASO_ESTATE_ROOT"):
        path = Path(value) / "agents" / os.environ.get("VASO_AGENT", "native-action-inputs-test") / "tmp"
        path.mkdir(parents=True, exist_ok=True)
        return path
    return Path.cwd()


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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class NativeActionInputsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(dir=scratch_parent())
        self.root = Path(self.tmp.name) / "repo"
        self.output_base = Path(self.tmp.name) / "bazel-ob"
        self.bazel = bazel_binary()
        self.write_fixture(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_fixture(self, root: Path) -> None:
        overrides = root / "module-overrides"
        write_module_override_stubs(overrides)
        write(
            root / "MODULE.bazel",
            'module(name = "native_action_inputs_fixture", version = "0.0.0")\n\n'
            + local_override_block(overrides)
            + "\n\n"
            + textwrap.dedent(
                """\
                local_repository = use_repo_rule("@bazel_tools//tools/build_defs/repo:local.bzl", "local_repository")

                local_repository(
                    name = "dep_native",
                    path = "dep_native",
                )
                """
            ),
        )
        write(root / "native" / "common" / "build_settings.bzl", BUILD_SETTINGS_BZL.read_text(encoding="utf-8"))
        write(root / "native" / "common" / "native_action_inputs.bzl", HELPER_BZL.read_text(encoding="utf-8"))
        write(
            root / "native" / "common" / "BUILD.bazel",
            textwrap.dedent(
                """\
                load(":build_settings.bzl", "release_cuda_line")

                package(default_visibility = ["//visibility:public"])

                release_cuda_line(
                    name = "release_line",
                    build_setting_default = "cu130",
                )
                """
            ),
        )
        write(
            root / "plans" / "BUILD.bazel",
            textwrap.dedent(
                """\
                package(default_visibility = ["//visibility:public"])

                exports_files([
                    "torch/cu130/rootfs.identity.json",
                ])
                """
            ),
        )
        write(root / "plans" / "torch" / "cu130" / "rootfs.identity.json", '{"compiler":"gcc-13.3"}\n')
        write(
            root / "fixture_rule.bzl",
            textwrap.dedent(
                """\
                load("//native/common:native_action_inputs.bzl", "NativeActionPrefixInfo", "native_action_prefix_argv", "native_action_prefix_manifest_attrs", "native_action_prefix_manifest_inputs", "native_action_rootfs_identity_attrs", "native_action_rootfs_identity_file")

                _PREFIX_KEY_ATTRS = (("dep", "dep_prefix_file"),)

                def _fixture_action_prefix_impl(ctx):
                    prefix = ctx.actions.declare_directory(ctx.label.name + "_prefix")
                    manifest = ctx.actions.declare_file(ctx.label.name + "_prefix_manifest.json")
                    ctx.actions.run_shell(
                        outputs = [prefix, manifest],
                        arguments = [prefix.path, manifest.path],
                        command = "mkdir -p \\"$1/lib\\" && printf 'fixture\\n' > \\"$1/lib/libdep.so\\" && printf '{\\"tree_sha256\\":\\"action-dep\\"}\\n' > \\"$2\\"",
                        mnemonic = "FixtureActionPrefix",
                        use_default_shell_env = False,
                    )
                    return [
                        DefaultInfo(files = depset([prefix])),
                        NativeActionPrefixInfo(
                            prefix = prefix,
                            manifest = manifest,
                            package = "dep",
                            version = "1.0",
                        ),
                    ]

                fixture_action_prefix = rule(
                    implementation = _fixture_action_prefix_impl,
                )

                def _fixture_native_key_impl(ctx):
                    out = ctx.actions.declare_file(ctx.label.name + ".txt")
                    inputs = []
                    inputs.extend(native_action_prefix_manifest_inputs(ctx, _PREFIX_KEY_ATTRS, require_all = True))
                    rootfs_identity = native_action_rootfs_identity_file(ctx, profile = "torch", consumer = "fixture")
                    inputs.append(rootfs_identity)
                    ctx.actions.run_shell(
                        inputs = depset(inputs),
                        outputs = [out],
                        arguments = [
                            ",".join(native_action_prefix_argv(ctx, _PREFIX_KEY_ATTRS)),
                            rootfs_identity.path,
                            out.path,
                        ],
                        command = "printf '%s\\n%s\\n' \\"$1\\" \\"$2\\" > \\"$3\\"",
                        mnemonic = "FixtureNativeKey",
                        use_default_shell_env = False,
                    )
                    return [DefaultInfo(files = depset([out]))]

                fixture_native_key = rule(
                    implementation = _fixture_native_key_impl,
                    attrs = {
                        "dep_prefix_file": attr.label(allow_single_file = True, mandatory = True),
                    } | native_action_prefix_manifest_attrs() | native_action_rootfs_identity_attrs(),
                )
                """
            ),
        )
        write(
            root / "BUILD.bazel",
            textwrap.dedent(
                """\
                load(":fixture_rule.bzl", "fixture_native_key")
                load(":fixture_rule.bzl", "fixture_action_prefix")

                package(default_visibility = ["//visibility:public"])

                fixture_action_prefix(
                    name = "action_dep",
                )

                fixture_native_key(
                    name = "consumer",
                    dep_prefix_file = "@dep_native//:prefix_path.txt",
                    prefix_manifest_files = {
                        "@dep_native//:prefix_manifest.json": "dep",
                    },
                    rootfs_identity = "//plans:torch/cu130/rootfs.identity.json",
                )

                fixture_native_key(
                    name = "consumer_action_dep",
                    dep_prefix_file = ":action_dep",
                    rootfs_identity = "//plans:torch/cu130/rootfs.identity.json",
                )
                """
            ),
        )
        write(root / "dep_native" / "MODULE.bazel", 'module(name = "dep_native", version = "0.0.0")\n')
        write(
            root / "dep_native" / "BUILD.bazel",
            textwrap.dedent(
                """\
                package(default_visibility = ["//visibility:public"])

                exports_files([
                    "prefix_manifest.json",
                    "prefix_path.txt",
                ])
                """
            ),
        )
        write(root / "dep_native" / "prefix_path.txt", "/output-base-one/external/dep_native/prefix\n")
        write(root / "dep_native" / "prefix_manifest.json", '{"tree_sha256":"dep-content-one","dependencies":[{"name":"zlib-ng","tree_sha256":"zlib-one"}]}\n')

    def run_bazel(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(self.bazel),
                f"--output_base={self.output_base}",
                "--nohome_rc",
                "--nosystem_rc",
                args[0],
                "--lockfile_mode=off",
                "--registry=file:///nonexistent",
                "--override_repository=dep_native=dep_native",
                *args[1:],
            ],
            cwd=self.root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )

    def action_snapshot(self) -> dict[str, object]:
        return self.target_action_snapshot("//:consumer")

    def target_action_snapshot(self, target: str) -> dict[str, object]:
        result = self.run_bazel(
            "aquery",
            "--noshow_progress",
            "--include_artifacts=true",
            "--output=jsonproto",
            'mnemonic("FixtureNativeKey", {})'.format(target),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        actions = [action for action in data.get("actions", []) if action.get("mnemonic") == "FixtureNativeKey"]
        self.assertEqual(len(actions), 1, result.stdout)
        input_paths = sorted(self.action_input_paths(data, actions[0]))
        input_digests = []
        for path in input_paths:
            digest = self.digest_action_input(path)
            if digest is not None:
                input_digests.append((path, digest))
        return {
            "actionKey": actions[0]["actionKey"],
            "effective_key": hashlib.sha256(json.dumps([actions[0]["actionKey"], input_digests]).encode()).hexdigest(),
            "input_paths": input_paths,
            "input_digests": input_digests,
            "arguments": actions[0].get("arguments", []),
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

    def digest_action_input(self, path: str) -> str:
        candidates = [
            self.root / path,
            self.output_base / path,
            self.output_base / "execroot" / "_main" / path,
        ]
        for candidate in candidates:
            if candidate.is_file():
                return sha256_file(candidate)
        return None

    def test_dep_manifest_content_change_at_same_path_changes_consumer_key(self) -> None:
        first = self.action_snapshot()

        write(self.root / "dep_native" / "prefix_manifest.json", '{"tree_sha256":"dep-content-two"}\n')
        second = self.action_snapshot()

        self.assertTrue(
            any(path.endswith("/prefix_manifest.json") for path in second["input_paths"]),
            second["input_paths"],
        )
        self.assertNotEqual(first["effective_key"], second["effective_key"])

    def test_prefix_path_text_change_does_not_change_consumer_key(self) -> None:
        first = self.action_snapshot()

        write(self.root / "dep_native" / "prefix_path.txt", "/different-output-base/external/dep_native/prefix\n")
        second = self.action_snapshot()

        self.assertFalse(
            any(path.endswith("/prefix_path.txt") for path in second["input_paths"]),
            second["input_paths"],
        )
        self.assertEqual(first["effective_key"], second["effective_key"])

    def test_manifest_derived_prefix_argument_does_not_name_prefix_path_txt(self) -> None:
        snapshot = self.action_snapshot()
        result = self.run_bazel(
            "aquery",
            "--noshow_progress",
            "--output=jsonproto",
            'mnemonic("FixtureNativeKey", //:consumer)',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        action = [action for action in data["actions"] if action.get("mnemonic") == "FixtureNativeKey"][0]
        joined_args = "\n".join(action.get("arguments", []))

        self.assertIn("dep=", joined_args)
        self.assertIn("/prefix", joined_args)
        self.assertNotIn("prefix_path.txt", joined_args)
        self.assertNotIn("prefix_manifest.json", joined_args)
        self.assertFalse(
            any(path.endswith("/prefix_path.txt") for path in snapshot["input_paths"]),
            snapshot["input_paths"],
        )

    def test_transitive_manifest_change_below_direct_dep_changes_consumer_key(self) -> None:
        first = self.action_snapshot()

        write(
            self.root / "dep_native" / "prefix_manifest.json",
            '{"tree_sha256":"dep-content-one","dependencies":[{"name":"zlib-ng","tree_sha256":"zlib-two"}]}\n',
        )
        second = self.action_snapshot()

        self.assertTrue(any(path.endswith("/prefix_manifest.json") for path in second["input_paths"]))
        self.assertFalse(any("zlib" in path for path in second["input_paths"]), second["input_paths"])
        self.assertNotEqual(first["effective_key"], second["effective_key"])

    def test_action_prefix_provider_declares_prefix_tree_and_manifest_inputs(self) -> None:
        snapshot = self.target_action_snapshot("//:consumer_action_dep")
        joined_args = "\n".join(snapshot["arguments"])

        self.assertIn("dep=", joined_args)
        self.assertIn("action_dep_prefix", joined_args)
        self.assertNotIn("prefix_path.txt", joined_args)
        self.assertTrue(
            any(path.endswith("/action_dep_prefix") for path in snapshot["input_paths"]),
            snapshot["input_paths"],
        )
        self.assertTrue(
            any(path.endswith("/action_dep_prefix_manifest.json") for path in snapshot["input_paths"]),
            snapshot["input_paths"],
        )

    def test_real_torch_python_zlib_manifest_chain_is_declared(self) -> None:
        pytorch_build = (GRAPH_ROOT / "native" / "pytorch" / "BUILD.bazel").read_text(encoding="utf-8")
        pytorch_rule = (GRAPH_ROOT / "native" / "pytorch" / "pytorch_action.bzl").read_text(encoding="utf-8")
        python_rule = (GRAPH_ROOT / "native" / "python" / "python.bzl").read_text(encoding="utf-8")
        manifest_helper = HELPER_BZL.read_text(encoding="utf-8")

        self.assertIn('python_prefix_file = "@python_313_native//:prefix_path.txt"', pytorch_build)
        self.assertIn('zstd_prefix_file = "//native/zstd:zstd_action_prefix"', pytorch_build)
        self.assertIn('("python", "python_prefix_file")', pytorch_rule)
        self.assertIn("native_action_prefix_manifest_map(_PREFIX_KEY_ATTRS, kwargs)", pytorch_rule)
        self.assertIn("native_action_dep_prefix_args(ctx, _PREFIX_KEY_ATTRS)", pytorch_rule)
        self.assertIn('native_action_prefix_path(ctx, _PREFIX_KEY_ATTRS, "zstd")', pytorch_rule)
        self.assertIn('"zlib_prefix_file": attr.label', python_rule)
        self.assertIn("write_prefix_manifest(repository_ctx, prefix)", python_rule)
        self.assertIn("text[:-len(\"prefix_path.txt\")] + \"prefix_manifest.json\"", manifest_helper)

    def test_rootfs_identity_content_change_changes_consumer_key(self) -> None:
        first = self.action_snapshot()

        write(self.root / "plans" / "torch" / "cu130" / "rootfs.identity.json", '{"compiler":"gcc-13.4"}\n')
        second = self.action_snapshot()

        self.assertIn("plans/torch/cu130/rootfs.identity.json", second["input_paths"])
        self.assertNotEqual(first["effective_key"], second["effective_key"])


if __name__ == "__main__":
    unittest.main()
