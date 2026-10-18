#!/usr/bin/env python3
"""Aquery contract tests for public torch extension rules."""

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
import textwrap
import unittest
from pathlib import Path

sys_path_tools = Path(__file__).resolve().parents[2] / "tools"
if str(sys_path_tools) not in sys.path:
    sys.path.insert(0, str(sys_path_tools))
from nested_bazel_fixture import local_override_block, write, write_module_override_stubs


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


class ExtensionActionTest(unittest.TestCase):
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
        cls.module_overrides = cls.tmp_path / "module-overrides"
        write_module_override_stubs(cls.module_overrides)
        cls.write_rules_cc_override(cls.module_overrides / "rules_cc")
        cls.write_rules_python_override(cls.module_overrides / "rules_python")
        override_block = local_override_block(cls.module_overrides)
        cls.module_override_args = [
            f"--override_module={match.group(1)}={match.group(2)}"
            for match in re.finditer(r'module_name = "([^"]+)", path = "([^"]+)"', override_block)
        ]
        cls.repository_cache = str(cls.tmp_path / "repository-cache")
        cls.distdir = str(cls.tmp_path / "distdir")
        Path(cls.repository_cache).mkdir()
        Path(cls.distdir).mkdir()
        cls.prefix_repos = cls.tmp_path / "prefix-repos"
        for repo in (
            "cuda_native",
            "nccl_native",
            "python_313_native",
        ):
            cls.write_prefix_repo(cls.prefix_repos / repo, repo)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    @classmethod
    def write_rules_cc_override(cls, root: Path) -> None:
        write(
            root / "cc" / "find_cc_toolchain.bzl",
            textwrap.dedent(
                """\
                CC_TOOLCHAIN_ATTRS = {}

                def find_cpp_toolchain(_ctx):
                    return struct(
                        all_files = depset(),
                        toolchain_id = "vaso-rootfs-gcc-13.3",
                    )

                def use_cc_toolchain():
                    return ["@bazel_tools//tools/cpp:toolchain_type"]
                """
            ),
        )
        write(root / "cc" / "common" / "cc_info.bzl", "CcInfo = provider()\n")

    @classmethod
    def write_rules_python_override(cls, root: Path) -> None:
        rule_shim = textwrap.dedent(
            """\
            def _py_rule_impl(ctx):
                return [DefaultInfo(files = depset(ctx.files.srcs))]

            def _py_binary_impl(ctx):
                ctx.actions.write(
                    output = ctx.outputs.executable,
                    content = "#!/bin/sh\\nexit 127\\n",
                    is_executable = True,
                )
                return [DefaultInfo(files = depset([ctx.outputs.executable] + ctx.files.srcs), executable = ctx.outputs.executable)]

            _py_rule = rule(
                implementation = _py_rule_impl,
                attrs = {
                    "args": attr.string_list(),
                    "data": attr.label_list(allow_files = True),
                    "deps": attr.label_list(),
                    "env_inherit": attr.string_list(),
                    "main": attr.string(),
                    "precompile": attr.string(),
                    "srcs": attr.label_list(allow_files = [".py"]),
                },
            )

            _py_binary_rule = rule(
                implementation = _py_binary_impl,
                attrs = {
                    "data": attr.label_list(allow_files = True),
                    "deps": attr.label_list(),
                    "env_inherit": attr.string_list(),
                    "main": attr.string(),
                    "precompile": attr.string(),
                    "srcs": attr.label_list(allow_files = [".py"]),
                },
                executable = True,
            )

            def _filtered(kwargs):
                return {
                    key: value
                    for key, value in kwargs.items()
                    if key not in ("size", "timeout")
                }

            def py_binary(name, **kwargs):
                _py_binary_rule(name = name, **_filtered(kwargs))

            def py_library(name, **kwargs):
                _py_rule(name = name, **_filtered(kwargs))

            def py_test(name, **kwargs):
                _py_binary_rule(name = name, **_filtered(kwargs))
            """
        )
        write(root / "python" / "defs.bzl", rule_shim)

    @classmethod
    def write_prefix_repo(cls, root: Path, repo: str) -> None:
        write(root / "MODULE.bazel", f'module(name = "{repo}")\n')
        write(
            root / "BUILD.bazel",
            'load("@rules_cc//cc:defs.bzl", "cc_library")\n'
            'package(default_visibility = ["//visibility:public"])\n'
            'exports_files(["prefix_manifest.json", "prefix_path.txt"])\n'
            'filegroup(name = "prefix", srcs = glob(["prefix/**"], allow_empty = True))\n'
            'cc_library(name = "lib", hdrs = glob(["prefix/include/**"], allow_empty = True), includes = ["prefix/include"], linkopts = ["-Lprefix/lib64", "-Wl,-rpath,prefix/lib64"])\n',
        )
        write(root / "prefix_path.txt", "prefix\n")
        write(
            root / "prefix_manifest.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "prefix": "prefix",
                    "files": [{"path": "prefix/.keep", "sha256": hashlib.sha256(repo.encode()).hexdigest()}],
                    "tree_sha256": hashlib.sha256(("tree:" + repo).encode()).hexdigest(),
                },
                sort_keys=True,
            )
            + "\n",
        )
        write(root / "prefix" / ".keep", repo + "\n")
        write(root / "prefix" / "include" / "cuda.h", "")
        write(root / "prefix" / "include" / "nccl.h", "")

    @classmethod
    def write_torch_provider_fixture(cls, root: Path) -> None:
        write(root / "MODULE.bazel", 'module(name = "torch_provider_fixture")\n')
        write(
            root / "BUILD.bazel",
            textwrap.dedent(
                """\
                load("@vaso_graph//native/pytorch:pytorch_action.bzl", "PytorchNativePrefixInfo")

                _ABI = "cp" + "3" + "13"

                def _torch_fixture_impl(ctx):
                    prefix = ctx.actions.declare_directory(ctx.label.name + "_prefix")
                    wheel = ctx.actions.declare_file(ctx.label.name + "-0.1.0-" + _ABI + "-" + _ABI + "-linux_x86_64.whl")
                    metadata = ctx.actions.declare_file(ctx.label.name + "_provider_metadata.json")
                    ctx.actions.run_shell(
                        inputs = [ctx.file.plan],
                        outputs = [prefix, wheel, metadata],
                        command = "mkdir -p '$1/torch/include/torch/csrc/api/include' '$1/torch/lib' && touch '$1/torch/include/torch/extension.h' '$1/torch/lib/libtorch.so' '$1/torch/lib/libtorch_cpu.so' '$1/torch/lib/libtorch_cuda.so' '$1/torch/lib/libtorch_python.so' '$1/torch/lib/libc10.so' '$1/torch/lib/libc10_cuda.so' '$2' && printf '%s\\n' '{\"schema_version\":1}' > '$3'",
                        arguments = [prefix.path, wheel.path, metadata.path],
                    )
                    return [
                        DefaultInfo(files = depset([prefix, wheel, ctx.file.plan, metadata])),
                        PytorchNativePrefixInfo(
                            prefix = prefix,
                            wheel = wheel,
                            build_plan = ctx.file.plan,
                            provider_metadata = metadata,
                            package = "py-torch",
                            version = "2.14.0",
                            python_abi = _ABI,
                        ),
                    ]

                torch_fixture = rule(
                    implementation = _torch_fixture_impl,
                    attrs = {"plan": attr.label(allow_single_file = True, mandatory = True)},
                )

                torch_fixture(
                    name = "torch",
                    plan = "@vaso_graph//plans:torch/cu130/pytorch.plan.json",
                )
                """
            ),
        )

    def run_bazel(self, *args: str) -> subprocess.CompletedProcess[str]:
        startup_args = [
            str(self.bazel),
            f"--output_base={self.nested_output_base}",
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
                f"--override_repository={repo}={self.prefix_repos / repo}"
                for repo in ("cuda_native", "nccl_native", "python_313_native")
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

    def aquery_extension(self) -> tuple[dict[str, object], dict[str, object]]:
        result = self.run_bazel(
            "aquery",
            "--noshow_progress",
            "--output=jsonproto",
            "--include_artifacts=true",
            "--//native/common:release_line=cu130",
            "deps(//native/common:fixture_extension_raw)",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        actions = [action for action in data.get("actions", []) if action.get("mnemonic") == "VasoTorchExtensionBuild"]
        self.assertEqual(len(actions), 1, result.stdout)
        return actions[0], data

    def test_fixture_extension_action_uses_profile_torch_cuda_and_nccl_inputs(self) -> None:
        action, _ = self.aquery_extension()
        arguments = "\n".join(action.get("arguments", []))

        self.assertIn("--torch-prefix", arguments)
        self.assertIn("--torch-build-plan", arguments)
        self.assertIn("--cuda-prefix", arguments)
        self.assertIn("--nccl-prefix", arguments)
        self.assertIn("--cxx11-abi", arguments)
        self.assertIn("1", arguments)
        self.assertIn("--cxx-standard", arguments)
        self.assertIn("c++20", arguments)
        self.assertNotIn("--python-prefix", arguments)
        self.assertIn("--cuda-arch-list", arguments)
        self.assertIn("10.0+PTX", arguments)
        self.assertIn("--py-src", arguments)
        self.assertIn("fixtures/torch_extension/__init__.py", arguments)
        self.assertIn("_GLIBCXX_USE_CXX11_ABI=1", arguments)
        self.assertIn("-DVASO_FIXTURE_EXTRA_CXX_FLAG", arguments)
        self.assertIn("--expt-relaxed-constexpr", arguments)
        self.assertIn("-I", arguments)
        self.assertIn("/torch/include", arguments)
        self.assertIn("-ltorch_cuda", arguments)
        self.assertIn("-lc10_cuda", arguments)
        self.assertIn("-lnccl", arguments)
        self.assertNotIn("use_default_shell_env", json.dumps(action))

        env = {item["key"]: item["value"] for item in action.get("environmentVariables", [])}
        self.assertEqual(
            env,
            {
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
                "PYTHONHASHSEED": "0",
                "VASO_IN_INSULA": "1",
                "VASO_ROOTFS_BUNDLE_MANIFEST": "/run/vaso/rootfs-bundle.json",
            },
        )

    def test_fixture_extension_action_supports_rdc_device_link_with_nvshmem(self) -> None:
        action, _ = self.aquery_extension()
        arguments = "\n".join(action.get("arguments", []))

        self.assertIn("--rdc", arguments)
        self.assertIn("--nvshmem-prefix", arguments)
        self.assertIn("--nvshmem-prefix-manifest", arguments)
        self.assertIn("torch_extension_nvshmem_rootfs", arguments)
        self.assertIn("-rdc=true", arguments)
        self.assertIn("--device-link-file", arguments)
        self.assertIn("libnvshmem_device.a", arguments)
        self.assertIn("-lnvshmem_host", arguments)

    def test_raw_extension_action_does_not_depend_on_native_python_prefix(self) -> None:
        _, data = self.aquery_extension()
        self.assertNotIn("python_313_native", json.dumps(data))

    def test_extension_builder_uses_deterministic_nvcc_intermediates(self) -> None:
        builder = (self.repo_root / "native" / "common" / "extension_builder.py").read_text(encoding="utf-8")

        self.assertIn("def _nvcc_random_seed", builder)
        self.assertIn('"--objdir-as-tempdir"', builder)
        self.assertIn('"--frandom-seed"', builder)
        self.assertIn("_sha256_file(src)", builder)
        self.assertIn("declared_archs = [args.cuda_arch_list]", builder)

    def test_rootfs_prefix_action_is_not_disk_cached(self) -> None:
        rule = (self.repo_root / "native" / "common" / "extension_action.bzl").read_text(encoding="utf-8")

        self.assertIn('"no-cache": "1"', rule)
        self.assertIn('"no-sandbox": "1"', rule)

    def test_extension_wheel_is_accepted_by_release_repair(self) -> None:
        result = self.run_bazel(
            "aquery",
            "--noshow_progress",
            "--output=jsonproto",
            "--include_artifacts=true",
            "--//native/common:release_line=cu130",
            "deps(//native/common:fixture_extension_release)",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        repair_actions = [action for action in data.get("actions", []) if action.get("mnemonic") == "VasoReleaseWheelRepair"]
        self.assertEqual(len(repair_actions), 1, result.stdout)
        arguments = "\n".join(repair_actions[0].get("arguments", []))
        self.assertIn("--wheel", arguments)
        self.assertIn("--provider-metadata", arguments)

    def test_extension_release_repair_does_not_depend_on_native_python_prefix(self) -> None:
        result = self.run_bazel(
            "aquery",
            "--noshow_progress",
            "--output=jsonproto",
            "--include_artifacts=true",
            "--//native/common:release_line=cu130",
            "deps(//native/common:fixture_extension_release)",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("python_313_native", result.stdout)

    def test_host_execution_fails_with_clear_insula_message(self) -> None:
        result = self.run_bazel(
            "build",
            "--//native/common:release_line=cu130",
            "//native/common:fixture_extension",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("vaso_torch_extension must run inside the hermetic insula", result.stderr)


if __name__ == "__main__":
    unittest.main(argv=[__file__])
