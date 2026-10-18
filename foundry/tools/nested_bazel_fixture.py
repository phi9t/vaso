"""Helpers for nested Bazel tests that must stay offline."""

from __future__ import annotations

import textwrap
from pathlib import Path


MODULE_OVERRIDE_VERSIONS = {
    "apple_support": "1.24.2",
    "buildozer": "8.5.1",
    "platforms": "1.0.0",
    "protobuf": "33.4",
    "rules_cc": "0.2.17",
    "rules_java": "9.3.0",
    "rules_license": "1.0.0",
    "rules_python": "1.9.0",
    "rules_shell": "0.6.1",
    "zlib": "1.3.1.bcr.5",
}


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_module_override(path: Path, module_name: str) -> None:
    version = MODULE_OVERRIDE_VERSIONS[module_name]
    write(path / "MODULE.bazel", f'module(name = "{module_name}", version = "{version}")\n')


def write_rules_cc_override(path: Path) -> None:
    write_module_override(path, "rules_cc")
    write(
        path / "cc" / "BUILD.bazel",
        textwrap.dedent(
            """\
            load("@rules_cc//cc:defs.bzl", "cc_binary", "cc_library")

            package(default_visibility = ["//visibility:public"])

            cc_library(
                name = "current_cc_toolchain",
            )

            alias(
                name = "toolchain_type",
                actual = "@bazel_tools//tools/cpp:toolchain_type",
            )
            """
        ),
    )
    write(
        path / "cc" / "defs.bzl",
        textwrap.dedent(
            """\
            CcInfo = provider()

            def _fixture_rule_impl(ctx):
                return [platform_common.ToolchainInfo()]

            _cc_library = rule(implementation = _fixture_rule_impl)

            _cc_toolchain = rule(
                implementation = _fixture_rule_impl,
                attrs = {
                    "all_files": attr.label(),
                    "ar_files": attr.label(),
                    "compiler_files": attr.label(),
                    "dwp_files": attr.label(),
                    "linker_files": attr.label(),
                    "objcopy_files": attr.label(),
                    "strip_files": attr.label(),
                    "supports_param_files": attr.bool(),
                    "toolchain_config": attr.label(),
                    "toolchain_identifier": attr.string(),
                },
            )

            def cc_library(name, **_kwargs):
                _cc_library(name = name)

            def cc_binary(name, **_kwargs):
                _cc_library(name = name)

            def cc_toolchain(name, **kwargs):
                _cc_toolchain(
                    name = name,
                    all_files = kwargs.get("all_files"),
                    ar_files = kwargs.get("ar_files"),
                    compiler_files = kwargs.get("compiler_files"),
                    dwp_files = kwargs.get("dwp_files"),
                    linker_files = kwargs.get("linker_files"),
                    objcopy_files = kwargs.get("objcopy_files"),
                    strip_files = kwargs.get("strip_files"),
                    supports_param_files = kwargs.get("supports_param_files", False),
                    toolchain_config = kwargs.get("toolchain_config"),
                    toolchain_identifier = kwargs.get("toolchain_identifier", ""),
                )
            """
        ),
    )
    write(
        path / "cc" / "cc_library.bzl",
        textwrap.dedent(
            """\
            def _cc_library_impl(ctx):
                return [DefaultInfo()]

            _cc_library = rule(implementation = _cc_library_impl)

            def cc_library(name, **_kwargs):
                _cc_library(name = name)
            """
        ),
    )
    write(
        path / "cc" / "action_names.bzl",
        textwrap.dedent(
            """\
            ACTION_NAMES = struct(
                assemble = "assemble",
                clif_match = "clif-match",
                c_compile = "c-compile",
                cpp_compile = "c++-compile",
                cpp_header_parsing = "c++-header-parsing",
                cpp_link_dynamic_library = "c++-link-dynamic-library",
                cpp_link_executable = "c++-link-executable",
                cpp_link_nodeps_dynamic_library = "c++-link-nodeps-dynamic-library",
                cpp_link_static_library = "c++-link-static-library",
                cpp_module_codegen = "c++-module-codegen",
                cpp_module_compile = "c++-module-compile",
                linkstamp_compile = "linkstamp-compile",
                lto_backend = "lto-backend",
                preprocess_assemble = "preprocess-assemble",
            )
            """
        ),
    )
    write(
        path / "cc" / "cc_toolchain_config_lib.bzl",
        textwrap.dedent(
            """\
            def action_config(**kwargs):
                return struct(**kwargs)

            def env_entry(**kwargs):
                return struct(**kwargs)

            def env_set(**kwargs):
                return struct(**kwargs)

            def feature(**kwargs):
                return struct(**kwargs)

            def flag_group(**kwargs):
                return struct(**kwargs)

            def flag_set(**kwargs):
                return struct(**kwargs)

            def tool(**kwargs):
                return struct(**kwargs)

            def tool_path(**kwargs):
                return struct(**kwargs)

            def variable_with_value(**kwargs):
                return struct(**kwargs)
            """
        ),
    )
    write(
        path / "cc" / "find_cc_toolchain.bzl",
        textwrap.dedent(
            """\
            CC_TOOLCHAIN_ATTRS = {}

            def find_cpp_toolchain(ctx):
                fail("fixture rules_cc.find_cpp_toolchain is not used")

            def use_cc_toolchain():
                return ["@bazel_tools//tools/cpp:toolchain_type"]
            """
        ),
    )
    write(path / "cc" / "common" / "BUILD.bazel", "package(default_visibility = [\"//visibility:public\"])\n")
    write(path / "cc" / "toolchains" / "BUILD.bazel", "package(default_visibility = [\"//visibility:public\"])\n")
    write(
        path / "cc" / "toolchains" / "cc_toolchain_config_info.bzl",
        "CcToolchainConfigInfo = provider()\n",
    )
    write(
        path / "cc" / "toolchains" / "toolchain_config_utils.bzl",
        textwrap.dedent(
            """\
            MSVC_ENVVARS = []

            def find_vc_path(_repository_ctx):
                return None

            def setup_vc_env_vars(_repository_ctx, _vc, **_kwargs):
                return {}

            def escape_string(value):
                return value
            """
        ),
    )
    write(path / "cc" / "private" / "toolchain" / "BUILD.bazel", "package(default_visibility = [\"//visibility:public\"])\n")
    write(
        path / "cc" / "private" / "toolchain" / "cc_configure.bzl",
        textwrap.dedent(
            """\
            def _cc_configure_impl(repository_ctx):
                repository_ctx.file("BUILD.bazel", "")

            cc_configure = repository_rule(implementation = _cc_configure_impl)
            """
        ),
    )
    write(
        path / "cc" / "common" / "cc_common.bzl",
        textwrap.dedent(
            """\
            load("@rules_cc//cc/toolchains:cc_toolchain_config_info.bzl", "CcToolchainConfigInfo")

            def _create_cc_toolchain_config_info(**kwargs):
                return CcToolchainConfigInfo(**kwargs)

            cc_common = struct(create_cc_toolchain_config_info = _create_cc_toolchain_config_info)
            """
        ),
    )


def write_rules_python_override(path: Path) -> None:
    write_module_override(path, "rules_python")
    rule_shim = textwrap.dedent(
        """\
        def _py_rule_impl(ctx):
            return [DefaultInfo(files = depset(ctx.files.srcs))]

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

        def _filtered(kwargs):
            return {
                key: value
                for key, value in kwargs.items()
                if key not in ("size", "timeout")
            }

        def py_binary(name, **kwargs):
            _py_rule(name = name, **_filtered(kwargs))

        def py_library(name, **kwargs):
            _py_rule(name = name, **_filtered(kwargs))

        def py_test(name, **kwargs):
            _py_rule(name = name, **_filtered(kwargs))
        """
    )
    write(
        path / "python" / "BUILD.bazel",
        textwrap.dedent(
            """\
            package(default_visibility = ["//visibility:public"])

            toolchain_type(name = "toolchain_type")
            """
        ),
    )
    write(path / "python" / "defs.bzl", rule_shim)
    write(
        path / "python" / "py_runtime.bzl",
        textwrap.dedent(
            """\
            def _py_runtime_impl(ctx):
                files = []
                for target in ctx.attr.files:
                    files.extend(target[DefaultInfo].files.to_list())
                if ctx.file.interpreter:
                    files.append(ctx.file.interpreter)
                return [DefaultInfo(files = depset(files))]

            py_runtime = rule(
                implementation = _py_runtime_impl,
                attrs = {
                    "files": attr.label_list(allow_files = True),
                    "interpreter": attr.label(allow_single_file = True),
                    "interpreter_version_info": attr.string_dict(),
                    "python_version": attr.string(),
                },
            )
            """
        ),
    )
    write(
        path / "python" / "py_runtime_pair.bzl",
        textwrap.dedent(
            """\
            def _py_runtime_pair_impl(ctx):
                return [platform_common.ToolchainInfo(py3_runtime = ctx.attr.py3_runtime)]

            py_runtime_pair = rule(
                implementation = _py_runtime_pair_impl,
                attrs = {"py3_runtime": attr.label()},
            )
            """
        ),
    )


def write_platforms_override(path: Path) -> None:
    write_module_override(path, "platforms")
    write(
        path / "cpu" / "BUILD.bazel",
        textwrap.dedent(
            """\
            package(default_visibility = ["//visibility:public"])

            constraint_setting(name = "cpu")
            constraint_value(name = "x86_64", constraint_setting = ":cpu")
            """
        ),
    )
    write(
        path / "os" / "BUILD.bazel",
        textwrap.dedent(
            """\
            package(default_visibility = ["//visibility:public"])

            constraint_setting(name = "os")
            constraint_value(name = "linux", constraint_setting = ":os")
            constraint_value(name = "windows", constraint_setting = ":os")
            """
        ),
    )
    write(
        path / "host" / "BUILD.bazel",
        textwrap.dedent(
            """\
            package(default_visibility = ["//visibility:public"])

            platform(
                name = "host",
                constraint_values = [
                    "@platforms//cpu:x86_64",
                    "@platforms//os:linux",
                ],
            )
            """
        ),
    )
    write(
        path / "host" / "constraints.bzl",
        textwrap.dedent(
            """\
            HOST_CONSTRAINTS = [
                Label("@platforms//cpu:x86_64"),
                Label("@platforms//os:linux"),
            ]
            """
        ),
    )


def write_rules_shell_override(path: Path) -> None:
    write_module_override(path, "rules_shell")
    write(path / "shell" / "BUILD.bazel", "package(default_visibility = [\"//visibility:public\"])\n")
    shim = textwrap.dedent(
        """\
        def _sh_rule_impl(ctx):
            out = ctx.actions.declare_file(ctx.label.name + ".sh")
            ctx.actions.write(output = out, content = "#!/usr/bin/env sh\\nexit 0\\n", is_executable = True)
            return [DefaultInfo(executable = out)]

        _sh_rule = rule(
            implementation = _sh_rule_impl,
            executable = True,
        )
        """
    )
    write(path / "shell" / "sh_binary.bzl", shim + "\ndef sh_binary(name, **_kwargs):\n    _sh_rule(name = name)\n")
    write(path / "shell" / "sh_test.bzl", shim + "\ndef sh_test(name, **_kwargs):\n    _sh_rule(name = name)\n")


def write_module_override_stubs(root: Path) -> None:
    write_rules_cc_override(root / "rules_cc")
    write_rules_python_override(root / "rules_python")
    write_rules_shell_override(root / "rules_shell")
    write_platforms_override(root / "platforms")
    for name in sorted(set(MODULE_OVERRIDE_VERSIONS) - {"platforms", "rules_cc", "rules_python", "rules_shell"}):
        write_module_override(root / name, name)


def local_override_block(root: Path, *, include_vaso_graph: Path | None = None) -> str:
    lines = []
    if include_vaso_graph is not None:
        lines.append(f'local_path_override(module_name = "vaso_graph", path = "{include_vaso_graph}")')
    for name in sorted(MODULE_OVERRIDE_VERSIONS):
        lines.append(f'local_path_override(module_name = "{name}", path = "{root / name}")')
    return "\n".join(lines)
