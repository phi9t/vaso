#!/usr/bin/env python3
"""Tests for native action toolchain descriptor helpers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from nested_bazel_fixture import local_override_block, write_module_override_stubs

from native.common import native_action_toolchain

HELPER_BZL = Path(__file__).with_name("native_action_toolchain.bzl")
BUILD_SETTINGS_BZL = Path(__file__).with_name("build_settings.bzl")


def scratch_parent() -> str | None:
    return os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR")


def bazel_binary() -> Path:
    estate = os.environ.get("VASO_ESTATE_ROOT")
    candidates = [
        str(Path(estate) / "opt-vaso" / "bin" / "bazel-real") if estate else None,
        os.environ.get("BAZEL_REAL"),
        os.environ.get("BAZEL"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise FileNotFoundError("bazel-real")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_prefix_repo(path: Path, prefix_text: str) -> None:
    write(path / "MODULE.bazel", f'module(name = "{path.name}", version = "0.0.0")\n')
    write(
        path / "BUILD.bazel",
        textwrap.dedent(
            """\
            package(default_visibility = ["//visibility:public"])

            exports_files(["prefix_path.txt"])

            filegroup(
                name = "prefix",
                srcs = ["prefix_path.txt"],
            )
            """
        ),
    )
    write(path / "prefix_path.txt", prefix_text)


def write_determinism_analysis_fixture(root: Path) -> None:
    overrides = root / "module-overrides"
    write_module_override_stubs(overrides)
    write(root / "native" / "common" / "build_settings.bzl", BUILD_SETTINGS_BZL.read_text(encoding="utf-8"))
    write(root / "native" / "common" / "native_action_toolchain.bzl", HELPER_BZL.read_text(encoding="utf-8"))
    write(
        root / "native" / "common" / "determinism_probe.bzl",
        textwrap.dedent(
            """\
            load(":native_action_toolchain.bzl", "native_action_determinism_attrs", "native_action_deterministic_env")

            def _determinism_probe_impl(ctx):
                determinism = native_action_deterministic_env(
                    ctx,
                    compiler_flags = True,
                    emit_nvcc_flags = True,
                    native_build_token = ctx.attr.native_build_token,
                )
                out = ctx.actions.declare_file(ctx.label.name + ".txt")
                ctx.actions.write(
                    output = out,
                    content = "epoch={}\\nseed={}\\n".format(
                        determinism.source_date_epoch,
                        determinism.seed,
                    ),
                )
                return [DefaultInfo(files = depset([out]))]

            determinism_probe = rule(
                implementation = _determinism_probe_impl,
                attrs = native_action_determinism_attrs() | {
                    "native_build_token": attr.string(default = ""),
                },
            )
            """
        ),
    )
    write(
        root / "native" / "common" / "BUILD.bazel",
        textwrap.dedent(
            """\
            load(":determinism_probe.bzl", "determinism_probe")
            load(":build_settings.bzl", "native_action_string_flag")

            native_action_string_flag(
                name = "source_date_epoch",
                build_setting_default = "0",
            )

            native_action_string_flag(
                name = "deterministic_seed",
                build_setting_default = "unknown",
            )

            native_action_string_flag(
                name = "deterministic_prefix_map_roots",
                build_setting_default = "",
            )

            determinism_probe(
                name = "untokened_defaults_probe",
            )

            determinism_probe(
                name = "tokened_defaults_probe",
                native_build_token = "build-native-fixture",
            )

            determinism_probe(
                name = "tokened_explicit_probe",
                native_build_token = "build-native-fixture",
            )
            """
        ),
    )
    module_text = textwrap.dedent(
        """\
        module(name = "determinism_analysis_fixture", version = "0.0.0")

        bazel_dep(name = "rules_cc", version = "0.2.17")
        bazel_dep(name = "rules_license", version = "1.0.0")
        """
    )
    write(root / "MODULE.bazel", module_text + "\n" + local_override_block(overrides) + "\n")
    write(root / "BUILD.bazel", 'package(default_visibility = ["//visibility:public"])\n')


def write_toolchain_descriptor_fixture(root: Path, *, cuda_prefix: str) -> None:
    overrides = root / "module-overrides"
    write_module_override_stubs(overrides)
    write(
        overrides / "rules_cc" / "cc" / "find_cc_toolchain.bzl",
        textwrap.dedent(
            """\
            CC_TOOLCHAIN_ATTRS = {}

            def find_cpp_toolchain(_ctx):
                return struct(
                    all_files = depset([]),
                    toolchain_id = "vaso-rootfs-gcc-13.3",
                )

            def use_cc_toolchain():
                return []
            """
        ),
    )
    write(
        overrides / "rules_cc" / "cc" / "common" / "cc_common.bzl",
        textwrap.dedent(
            """\
            def _configure_features(**_kwargs):
                return struct()

            def _get_tool_for_action(feature_configuration, action_name):
                if action_name == "c-compile":
                    return "/usr/bin/gcc"
                if action_name == "c++-compile":
                    return "/usr/bin/g++"
                return "/usr/bin/false"

            cc_common = struct(
                configure_features = _configure_features,
                get_tool_for_action = _get_tool_for_action,
            )
            """
        ),
    )
    write(root / "native" / "common" / "build_settings.bzl", BUILD_SETTINGS_BZL.read_text(encoding="utf-8"))
    write(root / "native" / "common" / "native_action_toolchain.bzl", HELPER_BZL.read_text(encoding="utf-8"))
    write(
        root / "native" / "common" / "descriptor_probe.bzl",
        textwrap.dedent(
            """\
            load(":native_action_toolchain.bzl", "native_action_toolchain_attrs", "native_action_toolchains", "resolve_native_action_toolchain")

            def _descriptor_probe_impl(ctx):
                native_toolchain = resolve_native_action_toolchain(ctx, output_basename = ctx.label.name)
                return [DefaultInfo(files = depset([native_toolchain.toolchain_json]))]

            descriptor_probe = rule(
                implementation = _descriptor_probe_impl,
                attrs = {
                    "cuda_prefix_file": attr.label(allow_single_file = True, mandatory = True),
                } | native_action_toolchain_attrs(),
                toolchains = native_action_toolchains(),
            )
            """
        ),
    )
    write(
        root / "native" / "common" / "BUILD.bazel",
        textwrap.dedent(
            """\
            load(":build_settings.bzl", "native_action_string_flag")
            load(":descriptor_probe.bzl", "descriptor_probe")

            native_action_string_flag(
                name = "source_date_epoch",
                build_setting_default = "0",
            )

            native_action_string_flag(
                name = "deterministic_seed",
                build_setting_default = "unknown",
            )

            native_action_string_flag(
                name = "deterministic_prefix_map_roots",
                build_setting_default = "",
            )

            native_action_string_flag(
                name = "native_action_cuda_home",
                build_setting_default = "",
            )

            descriptor_probe(
                name = "descriptor_probe",
                cuda_prefix_file = "@cuda_native//:prefix_path.txt",
            )
            """
        ),
    )
    write(
        root / "toolchains" / "cuda_toolchain.bzl",
        textwrap.dedent(
            """\
            CudaRootfsToolchainInfo = provider(
                fields = ["cuda_arch_list", "cuda_home", "cuda_prefix_file", "nvcc"],
            )

            def _cuda_toolchain_info_impl(ctx):
                info = CudaRootfsToolchainInfo(
                    cuda_arch_list = ctx.attr.cuda_arch_list,
                    cuda_home = ctx.attr.cuda_home,
                    cuda_prefix_file = ctx.attr.cuda_prefix_file,
                    nvcc = ctx.attr.nvcc,
                )
                return [info, platform_common.ToolchainInfo(cuda = info)]

            cuda_toolchain_info = rule(
                implementation = _cuda_toolchain_info_impl,
                attrs = {
                    "cuda_arch_list": attr.string(mandatory = True),
                    "cuda_home": attr.string(mandatory = True),
                    "cuda_prefix_file": attr.string(mandatory = True),
                    "nvcc": attr.string(mandatory = True),
                },
            )
            """
        ),
    )
    write(
        root / "toolchains" / "python_toolchain.bzl",
        textwrap.dedent(
            """\
            PythonRootfsToolchainInfo = provider(fields = ["python_prefix_file", "version"])

            def _python_toolchain_info_impl(ctx):
                info = PythonRootfsToolchainInfo(
                    python_prefix_file = ctx.attr.python_prefix_file,
                    version = ctx.attr.version,
                )
                return [info, platform_common.ToolchainInfo(python = info)]

            python_toolchain_info = rule(
                implementation = _python_toolchain_info_impl,
                attrs = {
                    "python_prefix_file": attr.string(mandatory = True),
                    "version": attr.string(mandatory = True),
                },
            )
            """
        ),
    )
    write(
        root / "toolchains" / "BUILD.bazel",
        textwrap.dedent(
            """\
            load(":cuda_toolchain.bzl", "cuda_toolchain_info")
            load(":python_toolchain.bzl", "python_toolchain_info")

            toolchain_type(name = "python_toolchain_info_type")

            python_toolchain_info(
                name = "python_313_info",
                python_prefix_file = "@python_313_native//:prefix_path.txt",
                version = "3.13.13",
            )

            toolchain(
                name = "python_313_info_toolchain",
                toolchain = ":python_313_info",
                toolchain_type = ":python_toolchain_info_type",
            )

            toolchain_type(name = "cuda_toolchain_type")

            cuda_toolchain_info(
                name = "cuda_rootfs_info",
                cuda_home = "/usr/local/cuda",
                cuda_arch_list = "10.0+PTX",
                cuda_prefix_file = "@cuda_native//:prefix_path.txt",
                nvcc = "/usr/local/cuda/bin/nvcc",
            )

            toolchain(
                name = "cuda_rootfs_toolchain",
                toolchain = ":cuda_rootfs_info",
                toolchain_type = ":cuda_toolchain_type",
            )
            """
        ),
    )
    write_prefix_repo(root / "native" / "python_313_native", "/fixture/python\n")
    write_prefix_repo(root / "native" / "cuda_native", cuda_prefix + "\n")
    write(root / "BUILD.bazel", 'package(default_visibility = ["//visibility:public"])\n')
    module_text = (
        textwrap.dedent(
            """\
            module(name = "toolchain_descriptor_fixture", version = "0.0.0")

            bazel_dep(name = "rules_cc", version = "0.2.17")
            bazel_dep(name = "rules_python", version = "1.9.0")
            bazel_dep(name = "rules_java", version = "9.3.0", dev_dependency = True)
            bazel_dep(name = "rules_shell", version = "0.6.1", dev_dependency = True)
            """
        )
        + "\n"
        + local_override_block(overrides)
        + "\n\n"
        + textwrap.dedent(
            """\
            local_repository = use_repo_rule("@bazel_tools//tools/build_defs/repo:local.bzl", "local_repository")

            local_repository(
                name = "python_313_native",
                path = "native/python_313_native",
            )

            local_repository(
                name = "cuda_native",
                path = "native/cuda_native",
            )

            register_toolchains("//toolchains:python_313_info_toolchain")
            register_toolchains("//toolchains:cuda_rootfs_toolchain")
            """
        )
    )
    write(root / "MODULE.bazel", module_text)


class NativeActionToolchainTest(unittest.TestCase):
    def test_runtime_helper_uses_only_toolchain_owned_policy_data(self) -> None:
        build = Path(__file__).with_name("BUILD.bazel").read_text(encoding="utf-8")
        self.assertIn("//toolchains:native_action_consumer_families", build)
        self.assertNotIn("//tools:compiler_pathways.json", build)

    def test_runtime_helper_uses_narrow_cc_metadata_not_cpp_toolchain_config(self) -> None:
        build = Path(__file__).with_name("BUILD.bazel").read_text(encoding="utf-8")
        helper = Path(native_action_toolchain.__file__).read_text(encoding="utf-8")

        self.assertIn("//toolchains:native_action_cc_toolchains", build)
        self.assertNotIn("//toolchains:toolchain_files", build)
        self.assertIn('toolchains/native_action_cc_toolchains.json', helper)
        self.assertNotIn('toolchains/BUILD.bazel', helper)

    def test_cuda_home_comes_from_literal_toolchain_field(self) -> None:
        toolchain = {
            "schema_version": 1,
            "cc": {
                "cc": "/usr/bin/gcc",
                "cxx": "/usr/bin/g++",
                "toolchain_identifier": "vaso-rootfs-gcc-13.3",
            },
            "cuda": {
                "home": "/usr/local/cuda",
                "home_env": "VASO_CUDA_HOME",
                "nvcc": "/usr/local/cuda/bin/nvcc",
                "prefix_file": "@cuda_native//:prefix_path.txt",
                "providers": [
                    {"package": "cuda", "label": "@cuda_native//:lib"},
                ],
            },
        }
        env = native_action_toolchain.native_toolchain_environment(toolchain)

        self.assertEqual(env["CUDA_HOME"], "/usr/local/cuda")
        self.assertEqual(env["CUDA_PATH"], "/usr/local/cuda")
        self.assertNotIn("CUDA_TOOLKIT_ROOT_DIR", env)

    def test_missing_literal_cuda_home_fails_loudly(self) -> None:
        toolchain = {
            "schema_version": 1,
            "cc": {
                "cc": "/usr/bin/gcc",
                "cxx": "/usr/bin/g++",
                "toolchain_identifier": "vaso-rootfs-gcc-13.3",
            },
            "cuda": {
                "home_env": "VASO_CUDA_HOME",
                "nvcc": "/usr/local/cuda/bin/nvcc",
                "prefix_file": "@cuda_native//:prefix_path.txt",
                "providers": [
                    {"package": "cuda", "label": "@cuda_native//:lib"},
                ],
            },
        }

        with self.assertRaises(SystemExit) as cm:
            native_action_toolchain.native_toolchain_environment(toolchain)

        self.assertIn("cuda.home", str(cm.exception))

    def test_torch_consumer_rejects_clang_family_descriptor(self) -> None:
        toolchain = {
            "schema_version": 1,
            "cc": {
                "cc": "/usr/lib/llvm-23/bin/clang",
                "cxx": "/usr/lib/llvm-23/bin/clang++",
                "toolchain_identifier": "vaso-rootfs-clang-23",
            },
            "cuda": {
                "home": "/usr/local/cuda",
                "nvcc": "/usr/local/cuda/bin/nvcc",
                "prefix_file": "@cuda_native//:prefix_path.txt",
                "providers": [
                    {"package": "cuda", "label": "@cuda_native//:lib"},
                ],
            },
        }

        with self.assertRaises(SystemExit) as cm:
            native_action_toolchain.validate_native_toolchain(toolchain, consumer="torch")

        self.assertIn("torch requires compiler family gcc", str(cm.exception))

    def test_jax_consumer_rejects_gcc_family_descriptor(self) -> None:
        toolchain = {
            "schema_version": 1,
            "cc": {
                "cc": "/usr/bin/gcc",
                "cxx": "/usr/bin/g++",
                "toolchain_identifier": "vaso-rootfs-gcc-13.3",
            },
            "cuda": {
                "home": "/usr/local/cuda",
                "nvcc": "/usr/local/cuda/bin/nvcc",
                "prefix_file": "@cuda_native//:prefix_path.txt",
                "providers": [
                    {"package": "cuda", "label": "@cuda_native//:lib"},
                ],
            },
        }

        with self.assertRaises(SystemExit) as cm:
            native_action_toolchain.validate_native_toolchain(toolchain, consumer="jaxlib")

        self.assertIn("jaxlib requires compiler family clang", str(cm.exception))

    def test_unknown_family_consumer_fails_loudly(self) -> None:
        toolchain = {
            "schema_version": 1,
            "cc": {
                "cc": "/usr/bin/gcc",
                "cxx": "/usr/bin/g++",
                "toolchain_identifier": "vaso-rootfs-gcc-13.3",
            },
            "cuda": {
                "home": "/usr/local/cuda",
                "nvcc": "/usr/local/cuda/bin/nvcc",
                "prefix_file": "@cuda_native//:prefix_path.txt",
                "providers": [
                    {"package": "cuda", "label": "@cuda_native//:lib"},
                ],
            },
        }

        with self.assertRaises(SystemExit) as cm:
            native_action_toolchain.validate_native_toolchain(toolchain, consumer="new-consumer")

        self.assertIn("consumer is unknown: new-consumer", str(cm.exception))
        self.assertIn("cc=/usr/bin/gcc", str(cm.exception))

    def test_cuda_home_must_match_selected_cuda_prefix(self) -> None:
        toolchain = {
            "schema_version": 1,
            "cc": {
                "cc": "/usr/bin/gcc",
                "cxx": "/usr/bin/g++",
                "toolchain_identifier": "vaso-rootfs-gcc-13.3",
            },
            "cuda": {
                "home": "/descriptor/cuda",
                "nvcc": "/descriptor/cuda/bin/nvcc",
                "prefix_file": "@cuda_native//:prefix_path.txt",
                "providers": [
                    {"package": "cuda", "label": "@cuda_native//:lib"},
                ],
            },
        }

        with self.assertRaises(SystemExit) as cm:
            native_action_toolchain.validate_native_toolchain_cuda_prefix(
                toolchain,
                "/selected/cuda",
            )

        self.assertIn("cuda.home", str(cm.exception))
        self.assertIn("/selected/cuda", str(cm.exception))

    def _cuda_toolchain(self, home: str) -> dict:
        return {
            "schema_version": 1,
            "cc": {"cc": "/usr/bin/gcc", "cxx": "/usr/bin/g++", "toolchain_identifier": "vaso-rootfs-gcc-13.3"},
            "cuda": {
                "home": home,
                "nvcc": home + "/bin/nvcc",
                "prefix_file": "@cuda_native//:prefix_path.txt",
                "providers": [{"package": "cuda", "label": "@cuda_native//:lib"}],
            },
        }

    def test_fixed_mount_cuda_home_matches_execroot_relative_selected_prefix(self) -> None:
        # Post-N1-2b gate red: descriptor is absolute under /vaso-bazel-ob, argv prefix is execroot-relative.
        toolchain = self._cuda_toolchain("/vaso-bazel-ob/external/+cuda_native+cuda_native/prefix")
        native_action_toolchain.validate_native_toolchain_cuda_prefix(
            toolchain, "external/+cuda_native+cuda_native/prefix"
        )

    def test_relative_selected_prefix_for_a_different_repo_still_fails(self) -> None:
        toolchain = self._cuda_toolchain("/vaso-bazel-ob/external/+cuda_native+cuda_native/prefix")
        with self.assertRaises(SystemExit):
            native_action_toolchain.validate_native_toolchain_cuda_prefix(
                toolchain, "external/+other_cuda+other_cuda/prefix"
            )

    def test_generated_descriptor_uses_selected_cuda_prefix_under_output_base(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp_text:
            root = Path(tmp_text)
            output_base = root / "bazel-ob"
            expected_cuda_prefix = str(output_base / "external" / "+cuda_native+cuda_native" / "prefix")
            write_toolchain_descriptor_fixture(root, cuda_prefix=expected_cuda_prefix)
            result = subprocess.run(
                [
                    str(bazel_binary()),
                    f"--output_base={output_base}",
                    "--nohome_rc",
                    "--nosystem_rc",
                    "build",
                    "--lockfile_mode=off",
                    "--registry=file:///nonexistent",
                    "--noshow_progress",
                    f"--//native/common:native_action_cuda_home={expected_cuda_prefix}",
                    "//native/common:descriptor_probe",
                ],
                cwd=root,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            descriptors = sorted(output_base.glob("execroot/_main/bazel-out/**/descriptor_probe_native_toolchain.json"))
            self.assertEqual(len(descriptors), 1, descriptors)
            descriptor = json.loads(descriptors[0].read_text(encoding="utf-8"))

            self.assertEqual(descriptor["cuda"]["home"], expected_cuda_prefix)
            self.assertEqual(descriptor["cuda"]["nvcc"], str(Path(expected_cuda_prefix) / "bin" / "nvcc"))
            self.assertEqual(descriptor["cuda"]["prefix_file"], "@cuda_native//:prefix_path.txt")

    def test_duplicate_cuda_family_provider_fails_loudly(self) -> None:
        toolchain = {
            "schema_version": 1,
            "cc": {
                "cc": "/usr/bin/gcc",
                "cxx": "/usr/bin/g++",
                "toolchain_identifier": "vaso-rootfs-gcc-13.3",
            },
            "cuda": {
                "home": "/usr/local/cuda",
                "nvcc": "/usr/local/cuda/bin/nvcc",
                "prefix_file": "@cuda_native//:prefix_path.txt",
                "providers": [
                    {"package": "cuda", "label": "@cuda_native//:lib"},
                    {"package": "cuda", "label": "@cuda_13_3_native//:lib"},
                ],
            },
        }

        with self.assertRaises(SystemExit) as cm:
            native_action_toolchain.native_toolchain_environment(toolchain)

        self.assertIn("CUDA-family package cuda appears more than once", str(cm.exception))

    def test_tokened_determinism_defaults_fail_at_analysis(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp_text:
            root = Path(tmp_text)
            write_determinism_analysis_fixture(root)
            common = [
                str(bazel_binary()),
                f"--output_base={root / 'bazel-ob'}",
                "--nohome_rc",
                "--nosystem_rc",
                "--batch",
                "build",
                "--lockfile_mode=off",
                "--registry=file:///nonexistent",
                "--noshow_progress",
            ]

            without_token = subprocess.run(
                common + ["//native/common:untokened_defaults_probe"],
                cwd=root,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
            )
            self.assertEqual(without_token.returncode, 0, without_token.stderr)

            missing_inputs = subprocess.run(
                common + ["//native/common:tokened_defaults_probe"],
                cwd=root,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
            )
            self.assertNotEqual(missing_inputs.returncode, 0, missing_inputs.stderr)
            self.assertIn("determinism prefix-map roots", missing_inputs.stderr)
            self.assertIn("deterministic seed", missing_inputs.stderr)
            self.assertIn("SOURCE_DATE_EPOCH", missing_inputs.stderr)

            with_inputs = subprocess.run(
                common
                + [
                    "--//native/common:source_date_epoch=1700000000",
                    "--//native/common:deterministic_seed=abcdef12",
                    "--//native/common:deterministic_prefix_map_roots=/workspace",
                    "//native/common:tokened_explicit_probe",
                ],
                cwd=root,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
            )
            self.assertEqual(with_inputs.returncode, 0, with_inputs.stderr)


if __name__ == "__main__":
    unittest.main()
