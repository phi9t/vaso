#!/usr/bin/env python3
"""Guard the explicit rootfs toolchain registrations."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nested_bazel_fixture import local_override_block, write, write_module_override_stubs

MODULE = Path("MODULE.bazel")
TOOLCHAINS_BUILD = Path("toolchains/BUILD.bazel")
CC_CONFIG = Path("toolchains/cc_toolchain_config.bzl")
CUDA_TOOLCHAIN = Path("toolchains/cuda_toolchain.bzl")
PYTHON_TOOLCHAIN = Path("toolchains/python_toolchain.bzl")
RESOLUTION_PROBE = Path("toolchains/resolution_probe.bzl")
CONSUMER_FAMILIES = Path("toolchains/native_action_consumer_families.json")
ROOTFS_BUILTIN_INCLUDES = Path("toolchains/rootfs_cc_builtin_includes.bzl")
NATIVE_ACTION_CC_TOOLCHAINS = Path("toolchains/native_action_cc_toolchains.json")
NATIVE_ACTION_TOOLCHAIN_BZL = Path("native/common/native_action_toolchain.bzl")
NATIVE_COMMON_BUILD_SETTINGS = Path("native/common/build_settings.bzl")
ROOTFS_CC_COMPILE_PROBE = Path("toolchains/rootfs_cc_compile_probe.cc")
ROOTFS_CC_INCLUDE_PROBE = Path("toolchains/rootfs_cc_builtin_include_probe.py")
ROOTFS_CC_INCLUDE_PROBE_TEST = Path("toolchains/rootfs_cc_builtin_include_probe_test.py")
ROOTFS_CC_DRIVERS = (
    Path("toolchains/clang_23_cc_driver"),
    Path("toolchains/clang_23_cxx_driver"),
    Path("toolchains/gcc_13_3_cc_driver"),
    Path("toolchains/gcc_13_3_cxx_driver"),
)
COMPILER_POLICY = Path("tools/compiler_pathways.json")


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


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def scratch_parent() -> Path:
    test_tmpdir = os.environ.get("TEST_TMPDIR")
    if test_tmpdir:
        return Path(test_tmpdir)
    tmpdir = os.environ.get("TMPDIR")
    if tmpdir:
        return Path(tmpdir)
    return Path.cwd()


def _copy_toolchain_package(source: Path, root: Path) -> None:
    for rel in (
        TOOLCHAINS_BUILD,
        CC_CONFIG,
        CUDA_TOOLCHAIN,
        PYTHON_TOOLCHAIN,
        RESOLUTION_PROBE,
        CONSUMER_FAMILIES,
        ROOTFS_BUILTIN_INCLUDES,
        NATIVE_ACTION_CC_TOOLCHAINS,
        ROOTFS_CC_COMPILE_PROBE,
        ROOTFS_CC_INCLUDE_PROBE,
        ROOTFS_CC_INCLUDE_PROBE_TEST,
        *ROOTFS_CC_DRIVERS,
    ):
        source_path = source / rel
        if source_path.exists():
            write(root / rel, _read(source_path))


def _write_native_common_build_settings(root: Path, source_repo_root: Path) -> None:
    write(root / NATIVE_COMMON_BUILD_SETTINGS, _read(source_repo_root / NATIVE_COMMON_BUILD_SETTINGS))
    write(
        root / "native" / "common" / "BUILD.bazel",
        textwrap.dedent(
            """\
            load(":build_settings.bzl", "native_action_string_flag", "release_cuda_line")

            package(default_visibility = ["//visibility:public"])

            release_cuda_line(
                name = "release_line",
                build_setting_default = "cu130",
            )

            native_action_string_flag(
                name = "source_date_epoch",
                build_setting_default = "0",
            )
            """
        ),
    )


def _write_prefix_repo(path: Path, prefix_text: str) -> None:
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


def _write_nested_module(root: Path, source_repo_root: Path) -> None:
    overrides = root / "module-overrides"
    write_module_override_stubs(overrides)
    _copy_toolchain_package(source_repo_root, root)
    _write_native_common_build_settings(root, source_repo_root)
    _write_prefix_repo(root / "native" / "python_313_native", "/fixture/python\n")
    _write_prefix_repo(root / "native" / "cuda_native", "/fixture/cuda\n")
    write(root / "BUILD.bazel", 'package(default_visibility = ["//visibility:public"])\n')
    module_text = (
        textwrap.dedent(
            """\
            module(name = "toolchain_registration_fixture", version = "0.0.0")

            bazel_dep(name = "rules_cc", version = "0.2.17")
            bazel_dep(name = "platforms", version = "1.0.0")
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

            register_toolchains("//toolchains:gcc_13_3_toolchain")
            register_toolchains("//toolchains:clang_23_toolchain")
            register_toolchains("//toolchains:python_313_toolchain")
            register_toolchains("//toolchains:python_313_info_toolchain")
            register_toolchains("//toolchains:cuda_rootfs_toolchain")
            """
        )
    )
    write(
        root / "MODULE.bazel",
        module_text,
    )


def _write_command_line_fixture(root: Path, source_repo_root: Path) -> None:
    overrides = root / "module-overrides"
    write_module_override_stubs(overrides)
    write(
        root / "compile_action.bzl",
        textwrap.dedent(
            """\
            load("@rules_cc//cc/toolchains:cc_toolchain_config_info.bzl", "CcToolchainConfigInfo")

            _CPP_COMPILE = "c++-compile"

            def _field(value, name, default):
                return getattr(value, name, default) if hasattr(value, name) else default

            def _enabled_for_mode(feature, mode):
                return _field(feature, "enabled", False) or _field(feature, "name", "") == mode

            def _substitute(flag, source, output):
                return flag.replace("%{source_file}", source.path).replace("%{output_file}", output.path)

            def _flatten_group(group, *, source, output, user_compile_flags):
                if _field(group, "iterate_over", "") == "user_compile_flags":
                    expanded = []
                    for user_flag in user_compile_flags:
                        for flag in _field(group, "flags", []):
                            expanded.append(flag.replace("%{user_compile_flags}", user_flag))
                    return expanded
                flags = []
                for nested in _field(group, "flag_groups", []):
                    flags.extend(_flatten_group(
                        nested,
                        source = source,
                        output = output,
                        user_compile_flags = user_compile_flags,
                    ))
                for flag in _field(group, "flags", []):
                    flags.append(_substitute(flag, source, output))
                return flags

            def _compile_flags(config, *, source, output, mode, user_compile_flags):
                flags = []
                for feature in config.features:
                    if not _enabled_for_mode(feature, mode):
                        continue
                    for flag_set in _field(feature, "flag_sets", []):
                        if _CPP_COMPILE not in _field(flag_set, "actions", []):
                            continue
                        for group in _field(flag_set, "flag_groups", []):
                            flags.extend(_flatten_group(
                                group,
                                source = source,
                                output = output,
                                user_compile_flags = user_compile_flags,
                            ))
                return flags

            def _compile_driver(config):
                for action_config in config.action_configs:
                    if _field(action_config, "action_name", "") != _CPP_COMPILE:
                        continue
                    for selected_tool in _field(action_config, "tools", []):
                        if hasattr(selected_tool, "tool"):
                            return selected_tool.tool.path
                        if hasattr(selected_tool, "path"):
                            return selected_tool.path
                fail("missing c++ compile tool")

            def _compile_command_probe_impl(ctx):
                config = ctx.attr.config[CcToolchainConfigInfo]
                source = ctx.file.src
                output = ctx.actions.declare_file(ctx.label.name + ".o")
                flags = _compile_flags(
                    config,
                    source = source,
                    output = output,
                    mode = ctx.var.get("COMPILATION_MODE", "fastbuild"),
                    user_compile_flags = ctx.attr.copts,
                )
                command = " ".join([_compile_driver(config)] + flags)
                ctx.actions.run_shell(
                    inputs = [source],
                    outputs = [output],
                    command = command,
                    mnemonic = "CppCompile",
                )
                return [DefaultInfo(files = depset([output]))]

            compile_command_probe = rule(
                implementation = _compile_command_probe_impl,
                attrs = {
                    "config": attr.label(mandatory = True, providers = [CcToolchainConfigInfo]),
                    "copts": attr.string_list(),
                    "src": attr.label(allow_single_file = [".cc"], mandatory = True),
                },
            )
            """
        ),
    )
    _copy_toolchain_package(source_repo_root, root)
    _write_native_common_build_settings(root, source_repo_root)
    _write_prefix_repo(root / "native" / "python_313_native", "/fixture/python\n")
    _write_prefix_repo(root / "native" / "cuda_native", "/fixture/cuda\n")
    write(root / "hello.cc", "int meaning() { return 42; }\n")
    write(
        root / "BUILD.bazel",
        textwrap.dedent(
            """\
            load(":compile_action.bzl", "compile_command_probe")

            compile_command_probe(
                name = "hello",
                config = "//toolchains:gcc_13_3_config",
                src = "hello.cc",
                copts = ["-O9", "-funroll-loops"],
            )
            """
        ),
    )
    module_text = (
        textwrap.dedent(
            """\
            module(name = "toolchain_command_line_fixture", version = "0.0.0")

            bazel_dep(name = "rules_cc", version = "0.2.17")
            bazel_dep(name = "platforms", version = "1.0.0")
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

            register_toolchains("//toolchains:gcc_13_3_toolchain")
            register_toolchains("//toolchains:python_313_toolchain")
            register_toolchains("//toolchains:python_313_info_toolchain")
            register_toolchains("//toolchains:cuda_rootfs_toolchain")
            """
        )
    )
    write(root / "MODULE.bazel", module_text)


def _driver_exec_path(driver: Path) -> str:
    for raw in driver.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if stripped.startswith("exec "):
            return stripped.split()[1]
    raise AssertionError(f"{driver} has no exec line")


def _attr_value(block: str, attr: str) -> str:
    match = re.search(rf"^\s*{re.escape(attr)}\s*=\s*\"([^\"]+)\"", block, re.MULTILINE)
    if not match:
        raise AssertionError(f"missing {attr} in block:\n{block}")
    return match.group(1)


class ToolchainRegistrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        parser = argparse.ArgumentParser()
        parser.add_argument("--repo-root", required=True)
        args, _ = parser.parse_known_args()
        cls.source_repo_root = Path(args.repo_root).resolve().parent
        cls.bazel = bazel_binary()
        cls._tmp = tempfile.TemporaryDirectory(dir=scratch_parent())
        cls.repo_root = Path(cls._tmp.name) / "repo"
        _write_nested_module(cls.repo_root, cls.source_repo_root)
        cls.nested_output_base = str(Path(cls._tmp.name) / "bazel-ob")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def run_bazel(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(self.bazel),
                f"--output_base={self.nested_output_base}",
                "--nohome_rc",
                "--nosystem_rc",
                "--batch",
                *args,
            ],
            cwd=self.repo_root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )

    def test_root_module_registers_only_opt_in_rootfs_toolchains(self) -> None:
        module = _read(self.source_repo_root / MODULE)
        build = _read(self.source_repo_root / TOOLCHAINS_BUILD)

        for label in (
            "//toolchains:gcc_13_3_toolchain",
            "//toolchains:clang_23_toolchain",
        ):
            self.assertIn(f'register_toolchains("{label}")', module)

        for label in (
            "//toolchains:python_313_toolchain",
            "//toolchains:python_313_info_toolchain",
            "//toolchains:cuda_rootfs_toolchain",
        ):
            self.assertIn(f'register_toolchains("{label}", dev_dependency = True)', module)

        self.assertIn('default_constraint_value = ":cc_family_unselected"', build)
        self.assertIn('default_constraint_value = ":python_runtime_unselected"', build)
        self.assertIn('default_constraint_value = ":cuda_sdk_unselected"', build)

    def test_declared_toolchain_paths_match_rootfs_policy(self) -> None:
        build = _read(self.source_repo_root / TOOLCHAINS_BUILD)
        config = _read(self.source_repo_root / CC_CONFIG)
        cuda = _read(self.source_repo_root / CUDA_TOOLCHAIN)
        python = _read(self.source_repo_root / PYTHON_TOOLCHAIN)
        policy = json.loads((self.source_repo_root / COMPILER_POLICY).read_text(encoding="utf-8"))

        self.assertEqual(policy["rootfs"]["gcc"]["cc"], "/usr/bin/gcc")
        self.assertEqual(policy["rootfs"]["gcc"]["cxx"], "/usr/bin/g++")
        self.assertEqual(policy["llvm"]["prefix"], "/usr/lib/llvm-23")
        self.assertEqual(policy["llvm"]["clang"], "/usr/lib/llvm-23/bin/clang")
        self.assertEqual(policy["llvm"]["clangxx"], "/usr/lib/llvm-23/bin/clang++")

        for expected in (
            'cc = "/usr/bin/gcc"',
            'cpp = "/usr/bin/cpp"',
            'cxx = "/usr/bin/g++"',
            'ld = "/usr/bin/ld"',
            'cc = "/usr/lib/llvm-23/bin/clang"',
            'cpp = "/usr/lib/llvm-23/bin/clang-cpp"',
            'cxx = "/usr/lib/llvm-23/bin/clang++"',
            'ld = "/usr/lib/llvm-23/bin/ld.lld"',
            'cuda_arch_list = "10.0+PTX"',
            'cuda_home = "/usr/local/cuda"',
            'cuda_prefix_file = "@cuda_native//:prefix_path.txt"',
            'nvcc = "/usr/local/cuda/bin/nvcc"',
            'python_prefix_file(',
            'name = "python_313_interpreter"',
            'path = "prefix/bin/python3.13"',
            'prefix = "@python_313_native//:prefix"',
            'name = "python_313_runtime"',
            'interpreter = ":python_313_interpreter"',
            'files = ["@python_313_native//:prefix"]',
            'python_prefix_file = "@python_313_native//:prefix_path.txt"',
            'version = "3.13.13"',
            '"micro": "13"',
        ):
            self.assertIn(expected, build)
        self.assertNotIn('interpreter = "@python_313_native//:prefix/bin/python3.13"', build)
        self.assertNotRegex(build + config + cuda + python, re.escape(str(Path.home())))
        for forbidden in (
            Path("/", "tmp").as_posix() + "/",
            Path("/", "var", "tmp").as_posix() + "/",
        ):
            self.assertNotIn(forbidden, build + config + cuda + python)

    def test_builtin_include_directories_are_checked_in_with_rootfs_line_evidence(self) -> None:
        data_path = self.source_repo_root / ROOTFS_BUILTIN_INCLUDES
        self.assertTrue(data_path.is_file(), f"missing {data_path}")
        data = data_path.read_text(encoding="utf-8")
        build = _read(self.source_repo_root / TOOLCHAINS_BUILD)
        config = _read(self.source_repo_root / CC_CONFIG)

        for line in ("cu129", "cu130"):
            self.assertIn(f'"{line}"', data)
            self.assertIn("/usr/include/c++/13", data)
            self.assertIn("/usr/include/x86_64-linux-gnu/c++/13", data)
            self.assertIn("/usr/lib/gcc/x86_64-linux-gnu/13/include", data)
            self.assertIn("/usr/lib/llvm-23/lib/clang/23/include", data)
            self.assertIn("/usr/include/x86_64-linux-gnu", data)
            self.assertIn("/usr/include", data)

        self.assertIn("builtin_include_directories_by_line = {", build)
        self.assertIn('ROOTFS_CC_BUILTIN_INCLUDE_DIRECTORIES_BY_LINE[line]["gcc_13_3"]', build)
        self.assertIn('ROOTFS_CC_BUILTIN_INCLUDE_DIRECTORIES_BY_LINE[line]["clang_23"]', build)
        self.assertIn('"//native/common:release_line"', config)
        self.assertIn("ROOTFS_CC_BUILTIN_INCLUDE_DIRECTORIES_BY_LINE", data)
        self.assertNotIn("ROOTFS_CC_BUILTIN_INCLUDES =", data)
        self.assertTrue((self.source_repo_root / ROOTFS_CC_INCLUDE_PROBE).is_file())
        self.assertTrue((self.source_repo_root / ROOTFS_CC_INCLUDE_PROBE_TEST).is_file())

    def test_link_and_compile_features_cover_downstream_cpp_builds(self) -> None:
        config = _read(self.source_repo_root / CC_CONFIG)
        build = _read(self.source_repo_root / TOOLCHAINS_BUILD)

        for feature_name in (
            "dependency_file",
            "user_compile_flags",
            "preprocessor_defines",
            "include_paths",
            "pic",
            "shared_flag",
            "runtime_library_search_directories",
            "libraries_to_link",
            "user_link_flags",
            "default_link_libs",
            "deterministic_compile_flags",
            "dbg",
            "opt",
            "fastbuild",
        ):
            self.assertRegex(config, r'feature\(\s*name = "{}"'.format(re.escape(feature_name)))
        self.assertIn('flags = ["-pthread"]', config)
        self.assertIn("-fdebug-compilation-dir=.", config)
        self.assertIn("-fno-canonical-system-headers", config)
        self.assertIn("-frandom-seed=%{output_file}", config)
        self.assertNotIn("-ffile-prefix-map=/workspace=.", config)
        self.assertNotIn("-fdebug-prefix-map=/workspace=.", config)
        self.assertNotIn("-fmacro-prefix-map=/workspace=.", config)
        self.assertIn("SOURCE_DATE_EPOCH", config)
        self.assertIn("ctx.attr._source_date_epoch", config)
        self.assertIn('flags = ["-lstdc++", "-lm"]', config)
        self.assertIn("ACTION_NAMES.cpp_link_executable", config)
        self.assertIn("ACTION_NAMES.cpp_link_dynamic_library", config)
        self.assertIn("ACTION_NAMES.cpp_link_nodeps_dynamic_library", config)
        self.assertIn("tool = ctx.executable.cc_driver", config)
        self.assertIn("tool = ctx.executable.cxx_driver", config)
        self.assertNotIn("tools = [tool(path = ctx.attr.ld)]", config)
        for driver in ROOTFS_CC_DRIVERS:
            driver_text = _read(self.source_repo_root / driver)
            self.assertIn("-ffile-prefix-map=$PWD=.", driver_text)
            self.assertIn("-fdebug-prefix-map=$PWD=.", driver_text)
            self.assertIn("-fmacro-prefix-map=$PWD=.", driver_text)

        self.assertIn('cc_driver = ":gcc_13_3_cc_driver"', build)
        self.assertIn('cxx_driver = ":gcc_13_3_cxx_driver"', build)
        self.assertIn('cc_driver = ":clang_23_cc_driver"', build)
        self.assertIn('cxx_driver = ":clang_23_cxx_driver"', build)
        self.assertIsNotNone(
            re.search(
                r'cc_binary\(\n\s*name = "rootfs_cc_compile_probe"(?P<body>.*?)\n\)',
                build,
                re.DOTALL,
            )
        )
        self.assertIn('tags = ["manual"]', build)

    def test_compile_mode_flags_precede_user_copts_on_real_cpp_action(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp_text:
            fixture = Path(tmp_text) / "command-line-fixture"
            _write_command_line_fixture(fixture, self.source_repo_root)
            result = subprocess.run(
                [
                    str(self.bazel),
                    f"--output_base={Path(tmp_text) / 'aquery-ob'}",
                    "--nohome_rc",
                    "--nosystem_rc",
                    "--batch",
                    "aquery",
                    "--lockfile_mode=off",
                    "--registry=file:///nonexistent",
                    "--noshow_progress",
                    "--output=text",
                    "--compilation_mode=opt",
                    "--platforms=//toolchains:torch_rootfs_platform",
                    "mnemonic(\"CppCompile\", //:hello)",
                ],
                cwd=fixture,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            command = result.stdout
            self.assertIn("-O2", command)
            self.assertIn("-DNDEBUG", command)
            self.assertIn("-O9", command)
            self.assertLess(command.index("-O2"), command.index("-O9"))
            self.assertLess(command.index("-DNDEBUG"), command.index("-funroll-loops"))

    def test_rootfs_cc_driver_scripts_reject_host_invocation_directly(self) -> None:
        for driver in ROOTFS_CC_DRIVERS:
            result = subprocess.run(
                ["env", "-u", "VASO_IN_INSULA", str(self.source_repo_root / driver), "--version"],
                cwd=self.source_repo_root,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )
            self.assertEqual(result.returncode, 125, driver)
            self.assertIn("vaso rootfs C++ toolchain selected outside the insula", result.stderr)

    def test_rootfs_cc_driver_scripts_require_insula_rootfs_manifest_marker(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp_text:
            wrong_manifest = Path(tmp_text) / "rootfs-bundle.json"
            wrong_manifest.write_text("{}", encoding="utf-8")
            cases = (
                ["env", "-u", "VASO_ROOTFS_BUNDLE_MANIFEST", "VASO_IN_INSULA=1"],
                ["env", f"VASO_ROOTFS_BUNDLE_MANIFEST={wrong_manifest}", "VASO_IN_INSULA=1"],
            )
            for driver in ROOTFS_CC_DRIVERS:
                for env_prefix in cases:
                    result = subprocess.run(
                        [
                            *env_prefix,
                            str(self.source_repo_root / driver),
                            "--version",
                        ],
                        cwd=self.source_repo_root,
                        check=False,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=30,
                    )
                    self.assertEqual(result.returncode, 125, driver)
                    self.assertIn(
                        "VASO_ROOTFS_BUNDLE_MANIFEST must point at /run/vaso/rootfs-bundle.json",
                        result.stderr,
                    )

    def test_toolchain_path_metadata_agrees_across_build_json_and_wrappers(self) -> None:
        build = _read(self.source_repo_root / TOOLCHAINS_BUILD)
        helper = _read(self.source_repo_root / NATIVE_ACTION_TOOLCHAIN_BZL)
        data = json.loads((self.source_repo_root / NATIVE_ACTION_CC_TOOLCHAINS).read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)

        for rule_name, identifier, cc_driver, cxx_driver in (
            ("gcc_13_3_config", "vaso-rootfs-gcc-13.3", "gcc_13_3_cc_driver", "gcc_13_3_cxx_driver"),
            ("clang_23_config", "vaso-rootfs-clang-23", "clang_23_cc_driver", "clang_23_cxx_driver"),
        ):
            match = re.search(rf"rootfs_cc_toolchain_config\(\n\s*name = \"{rule_name}\"(?P<body>.*?)\n\)", build, re.DOTALL)
            self.assertIsNotNone(match, rule_name)
            body = match.group("body")
            expected = data["toolchains"][identifier]
            self.assertEqual(_attr_value(body, "cc"), expected["cc"])
            self.assertEqual(_attr_value(body, "cxx"), expected["cxx"])
            self.assertEqual(_attr_value(body, "cc_driver"), f":{cc_driver}")
            self.assertEqual(_attr_value(body, "cxx_driver"), f":{cxx_driver}")
            self.assertEqual(_driver_exec_path(self.source_repo_root / "toolchains" / cc_driver), expected["cc"])
            self.assertEqual(_driver_exec_path(self.source_repo_root / "toolchains" / cxx_driver), expected["cxx"])
            self.assertIn('"{}": {{'.format(identifier), helper)
            self.assertIn('"cc": "{}",'.format(expected["cc"]), helper)
            self.assertIn('"cxx": "{}",'.format(expected["cxx"]), helper)

    def test_registered_cc_toolchain_all_files_stay_empty(self) -> None:
        build = _read(self.source_repo_root / TOOLCHAINS_BUILD)
        for rule_name in ("gcc_13_3_cc_toolchain", "clang_23_cc_toolchain"):
            match = re.search(rf"cc_toolchain\(\n\s*name = \"{rule_name}\"(?P<body>.*?)\n\)", build, re.DOTALL)
            self.assertIsNotNone(match, rule_name)
            self.assertIn('all_files = ":empty_toolchain_files"', match.group("body"))

    def test_registered_toolchains_and_profile_platforms_query_under_offline_module(self) -> None:
        result = self.run_bazel(
            "query",
            "--lockfile_mode=off",
            "--noshow_progress",
            "set(//toolchains:gcc_13_3_toolchain //toolchains:clang_23_toolchain //toolchains:torch_rootfs_platform //toolchains:jax_rootfs_platform)",
            "--output=label",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("//toolchains:gcc_13_3_toolchain", result.stdout)
        self.assertIn("//toolchains:clang_23_toolchain", result.stdout)
        self.assertIn("//toolchains:torch_rootfs_platform", result.stdout)
        self.assertIn("//toolchains:jax_rootfs_platform", result.stdout)

    def test_python_and_cuda_registration_targets_analyze_without_second_cuda_provider(self) -> None:
        result = self.run_bazel(
            "query",
            "--lockfile_mode=off",
            "--noshow_progress",
            "set(//toolchains:python_313_toolchain //toolchains:python_313_info_toolchain //toolchains:cuda_rootfs_toolchain)",
            "--output=label",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("//toolchains:python_313_toolchain", result.stdout)
        self.assertIn("//toolchains:python_313_info_toolchain", result.stdout)
        self.assertIn("//toolchains:cuda_rootfs_toolchain", result.stdout)

        build = _read(self.source_repo_root / TOOLCHAINS_BUILD)
        self.assertEqual(build.count("@cuda_native//:prefix_path.txt"), 1)
        self.assertNotIn("http_archive", build)
        self.assertNotIn("local_repository", build)


if __name__ == "__main__":
    unittest.main(argv=[__file__])
