#!/usr/bin/env python3
"""Tests for compiler_pathway_guard.py."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("compiler_pathway_guard.py")
SPEC = importlib.util.spec_from_file_location("compiler_pathway_guard", SCRIPT)
assert SPEC is not None
guard = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = guard
SPEC.loader.exec_module(guard)


LLVM_COMMIT = "35901313800ea6e6cbeb9226e51c7c4b29bfc40e"


def policy() -> dict[str, object]:
    return {
        "schema_version": 1,
        "rootfs": {
            "cuda_prefix": "/usr/local/cuda",
            "gcc": {
                "cc": "/usr/bin/gcc",
                "cxx": "/usr/bin/g++",
            },
            "binutils_ld": "/usr/bin/ld",
        },
        "llvm": {
            "prefix": "/usr/lib/llvm-23",
            "bin": "/usr/lib/llvm-23/bin",
            "source_commit": LLVM_COMMIT,
            "source_commit_prefix": "35901313",
            "clang": "/usr/lib/llvm-23/bin/clang",
            "clangxx": "/usr/lib/llvm-23/bin/clang++",
            "lld": "/usr/lib/llvm-23/bin/ld.lld",
        },
        "consumers": {
            "torch": {
                "allowed_host_cc_paths": ["/usr/bin/gcc"],
                "allowed_host_cxx_paths": ["/usr/bin/g++"],
                "cuda_compiler": {"kind": "nvcc", "path": "/usr/local/cuda/bin/nvcc", "host": "/usr/bin/gcc"},
                "linker": {"kind": "binutils-ld", "path": "/usr/bin/ld"},
                "required": {
                    "env": {"USE_CUDA": "1", "CUDA_HOME": "/usr/local/cuda", "CUDA_PATH": "/usr/local/cuda"},
                    "path_entries": ["/usr/local/cuda/bin", "/usr/bin"],
                },
                "forbidden": {
                    "env": {
                        "CC": ["clang", "/usr/lib/llvm-23/bin/clang"],
                        "CXX": ["clang++", "/usr/lib/llvm-23/bin/clang++"],
                        "CUDAHOSTCXX": ["clang", "clang++"],
                        "CMAKE_C_COMPILER": ["clang"],
                        "CMAKE_CXX_COMPILER": ["clang++"],
                    },
                    "substrings": [
                        "-allow-unsupported-compiler",
                        "TF_NVCC_CLANG",
                        "USE_HERMETIC_CC_TOOLCHAIN=1",
                    ],
                    "regexes": [r"/usr/lib/llvm-(?!23\b)[^\s:]*"],
                },
            },
            "triton": {
                "allowed_host_cc_paths": ["/usr/bin/gcc"],
                "allowed_host_cxx_paths": ["/usr/bin/g++"],
                "cuda_compiler": {"kind": "none", "runtime_assembler": "/usr/local/cuda/bin/ptxas"},
                "linker": {"kind": "binutils-ld", "path": "/usr/bin/ld"},
                "required_module_constants": {"ONE_LLVM_COMMIT": LLVM_COMMIT},
                "required": {
                    "env": {
                        "CC": "/usr/bin/gcc",
                        "CXX": "/usr/bin/g++",
                        "CMAKE_C_COMPILER": "/usr/bin/gcc",
                        "CMAKE_CXX_COMPILER": "/usr/bin/g++",
                        "LLVM_SYSPATH": "/usr/lib/llvm-23",
                    },
                    "path_entries": ["/usr/lib/llvm-23/bin"],
                },
                "forbidden": {
                    "env": {
                        "CC": ["clang", "/usr/lib/llvm-23/bin/clang"],
                        "CXX": ["clang++", "/usr/lib/llvm-23/bin/clang++"],
                        "CMAKE_C_COMPILER": ["clang"],
                        "CMAKE_CXX_COMPILER": ["clang++"],
                    },
                    "substrings": [
                        "TRITON_BUILD_WITH_CLANG_LLD",
                        "-allow-unsupported-compiler",
                        "USE_HERMETIC_CC_TOOLCHAIN=1",
                    ],
                    "regexes": [r"/usr/lib/llvm-(?!23\b)[^\s:]*"],
                },
            },
            "jaxlib": {
                "allowed_host_cc_paths": ["/usr/lib/llvm-23/bin/clang"],
                "allowed_host_cxx_paths": ["/usr/lib/llvm-23/bin/clang++"],
                "cuda_compiler": {"kind": "clang", "path": "/usr/lib/llvm-23/bin/clang"},
                "linker": {"kind": "lld", "path": "/usr/lib/llvm-23/bin/ld.lld"},
                "required_module_constants": {"ONE_LLVM_COMMIT": LLVM_COMMIT},
                "required": {
                    "env": {"CC": "/usr/lib/llvm-23/bin/clang", "CXX": "/usr/lib/llvm-23/bin/clang++"},
                    "args": [
                        "--bazel_options=--config=build_cuda_with_clang",
                        "--clang_path=/usr/lib/llvm-23/bin/clang",
                        "--bazel_options=--repo_env=USE_HERMETIC_CC_TOOLCHAIN=0",
                    ],
                    "path_entries": ["/usr/lib/llvm-23/bin"],
                },
                "forbidden": {
                    "substrings": [
                        "build_cuda_with_nvcc",
                        "TF_NVCC_CLANG",
                        "-allow-unsupported-compiler",
                        "USE_HERMETIC_CC_TOOLCHAIN=1",
                    ],
                    "regexes": [r"/usr/lib/llvm-(?!23\b)[^\s:]*"],
                },
            },
        },
    }


def profiled_policy() -> dict[str, object]:
    base = policy()
    consumers = base.pop("consumers")
    triton = dict(consumers["triton"])
    triton.update(
        {
            "allowed_host_cc_paths": ["/usr/bin/gcc"],
            "allowed_host_cxx_paths": ["/usr/bin/g++"],
            "linker": {"kind": "binutils-ld", "path": "/usr/bin/ld"},
            "required": {
                "env": {
                    "LLVM_SYSPATH": "/usr/lib/llvm-23",
                },
                "path_entries": ["/usr/local/cuda/bin", "/usr/bin"],
            },
            "forbidden": {
                "env": {
                    "CC": ["clang", "/usr/lib/llvm-23/bin/clang"],
                    "CXX": ["clang++", "/usr/lib/llvm-23/bin/clang++"],
                    "TRITON_BUILD_WITH_CLANG_LLD": ["1"],
                },
                "substrings": [
                    "-allow-unsupported-compiler",
                    "TF_NVCC_CLANG",
                    "USE_HERMETIC_CC_TOOLCHAIN=1",
                ],
                "regexes": [r"/usr/lib/llvm-(?!23\b)[^\s:]*"],
            },
            "artifact_comment": {
                "required_substrings": ["GCC: (Ubuntu 13.3.0"],
                "allowed_regexes": [r"GCC: \(Ubuntu 13\.3\.0"],
                "forbidden_regexes": [r"clang version", r"GCC: \((?!Ubuntu 13\.3\.0)"],
            },
        }
    )
    base["default_profile"] = "torch"
    base["profiles"] = {
        "torch": {
            "consumers": {
                "torch": consumers["torch"],
                "torchvision": consumers["torch"],
                "torchaudio": consumers["torch"],
                "triton": triton,
            }
        },
        "jax": {"consumers": {"jaxlib": consumers["jaxlib"]}},
    }
    return base


def plan_module(
    *,
    required_prefixes: tuple[str, ...],
    env: dict[str, str],
    args: list[str] | None = None,
    llvm_commit: str | None = None,
) -> types.SimpleNamespace:
    module = types.SimpleNamespace()
    module.REQUIRED_PREFIXES = required_prefixes
    module.REQUIRED_PREFIX_KEYS = required_prefixes
    module.build_env = lambda *_args, **_kwargs: dict(env)
    if args is not None:
        module.build_py_args = lambda *_args, **_kwargs: list(args)
    if llvm_commit is not None:
        module.ONE_LLVM_COMMIT = llvm_commit
    return module


def rule_fixture(rule_id: str, test_method: str) -> tuple[str, str]:
    assert rule_id in guard.RULE_IDS
    return rule_id, test_method


RULE_FIXTURES = dict(
    (
        rule_fixture("allowed-host-compiler", "test_triton_clang_lld_pathway_is_forbidden"),
        rule_fixture("forbidden-env", "test_torch_clang_host_override_is_forbidden"),
        rule_fixture("forbidden-regex", "test_wrong_llvm_prefix_is_forbidden"),
        rule_fixture("forbidden-substring", "test_jaxlib_nvcc_pathway_is_forbidden"),
        rule_fixture("missing-plan-module", "test_missing_plan_module_is_reported"),
        rule_fixture("plan-snapshot", "test_plan_snapshot_errors_are_reported"),
        rule_fixture("required-args", "test_required_args_are_enforced"),
        rule_fixture("required-env", "test_required_env_is_enforced"),
        rule_fixture("required-module-constant", "test_wrong_llvm_commit_is_forbidden"),
        rule_fixture("required-path-entry", "test_required_path_entry_is_enforced"),
    )
)


class CompilerPathwayGuardTest(unittest.TestCase):
    def test_rule_fixture_manifest_matches_guard_rule_ids(self) -> None:
        self.assertEqual(set(RULE_FIXTURES), guard.RULE_IDS)
        missing = sorted(name for name in RULE_FIXTURES.values() if not hasattr(self, name))
        self.assertEqual(missing, [])

    def test_torch_profile_is_gcc_only_and_excludes_jaxlib(self) -> None:
        configured = guard.consumers_for_profile(profiled_policy(), "torch")

        self.assertCountEqual(configured, ("torch", "torchvision", "torchaudio", "triton"))
        self.assertNotIn("jaxlib", configured)

    def test_torch_profile_accepts_gcc_triton_without_clang_lld_flag(self) -> None:
        modules = {
            "triton": plan_module(
                required_prefixes=("llvm", "cuda"),
                env={
                    "LLVM_SYSPATH": "/usr/lib/llvm-23",
                    "TRITON_PTXAS_PATH": "/usr/local/cuda/bin/ptxas",
                    "TRITON_PTXAS_BLACKWELL_PATH": "/usr/local/cuda/bin/ptxas",
                    "PATH": "/usr/lib/llvm-23/bin:/usr/local/cuda/bin:/usr/bin:/bin",
                },
                llvm_commit=LLVM_COMMIT,
            ),
        }

        errors = guard.check_modules(profiled_policy(), modules, consumers=("triton",), profile="torch")

        self.assertEqual(errors, [])

    def test_torch_profile_masks_only_temporary_triton_clang_lld_flag(self) -> None:
        modules = {
            "triton": plan_module(
                required_prefixes=("llvm", "cuda"),
                env={
                    "TRITON_BUILD_WITH_CLANG_LLD": "1",
                    "LLVM_SYSPATH": "/usr/lib/llvm-23",
                    "TRITON_PTXAS_PATH": "/usr/local/cuda/bin/ptxas",
                    "TRITON_PTXAS_BLACKWELL_PATH": "/usr/local/cuda/bin/ptxas",
                    "PATH": "/usr/lib/llvm-23/bin:/usr/local/cuda/bin:/usr/bin:/bin",
                },
                llvm_commit=LLVM_COMMIT,
            ),
        }

        errors = guard.check_modules(profiled_policy(), modules, consumers=("triton",), profile="torch")
        active, expected = guard.partition_expected_failures(
            errors,
            {
                "triton": [
                    r"forbidden env TRITON_BUILD_WITH_CLANG_LLD contains '1'",
                ]
            },
        )

        self.assertEqual(active, [])
        self.assertEqual(
            expected,
            ["triton: forbidden env TRITON_BUILD_WITH_CLANG_LLD contains '1' (value: 1)"],
        )

    def test_jax_profile_contains_only_jaxlib(self) -> None:
        configured = guard.consumers_for_profile(profiled_policy(), "jax")

        self.assertEqual(tuple(configured), ("jaxlib",))

    def test_compliant_fixture_plans_pass(self) -> None:
        modules = {
            "torch": plan_module(
                required_prefixes=("cuda", "python"),
                env={
                    "USE_CUDA": "1",
                    "CUDA_HOME": "/usr/local/cuda",
                    "CUDA_PATH": "/usr/local/cuda",
                    "PATH": "/usr/local/cuda/bin:/usr/bin:/bin",
                },
            ),
            "triton": plan_module(
                required_prefixes=("llvm", "cuda"),
                env={
                    "CC": "/usr/bin/gcc",
                    "CXX": "/usr/bin/g++",
                    "CMAKE_C_COMPILER": "/usr/bin/gcc",
                    "CMAKE_CXX_COMPILER": "/usr/bin/g++",
                    "LLVM_SYSPATH": "/usr/lib/llvm-23",
                    "PATH": "/usr/lib/llvm-23/bin:/usr/local/cuda/bin:/usr/bin:/bin",
                },
                llvm_commit=LLVM_COMMIT,
            ),
            "jaxlib": plan_module(
                required_prefixes=("llvm", "cuda"),
                env={
                    "CC": "/usr/lib/llvm-23/bin/clang",
                    "CXX": "/usr/lib/llvm-23/bin/clang++",
                    "PATH": "/usr/lib/llvm-23/bin:/usr/local/cuda/bin:/usr/bin:/bin",
                },
                args=[
                    "--bazel_options=--config=build_cuda_with_clang",
                    "--clang_path=/usr/lib/llvm-23/bin/clang",
                    "--bazel_options=--repo_env=USE_HERMETIC_CC_TOOLCHAIN=0",
                ],
                llvm_commit=LLVM_COMMIT,
            ),
        }

        errors = guard.check_modules(policy(), modules, consumers=("torch", "triton", "jaxlib"))

        self.assertEqual(errors, [])

    def test_jaxlib_nvcc_pathway_is_forbidden(self) -> None:
        modules = {
            "jaxlib": plan_module(
                required_prefixes=("llvm", "cuda"),
                env={
                    "CC": "/usr/lib/llvm-23/bin/clang",
                    "CXX": "/usr/lib/llvm-23/bin/clang++",
                    "PATH": "/usr/lib/llvm-23/bin:/usr/local/cuda/bin:/usr/bin:/bin",
                },
                args=[
                    "--bazel_options=--config=build_cuda_with_nvcc",
                    "--clang_path=/usr/lib/llvm-23/bin/clang",
                    "--bazel_options=--repo_env=USE_HERMETIC_CC_TOOLCHAIN=0",
                ],
                llvm_commit=LLVM_COMMIT,
            )
        }

        errors = guard.check_modules(policy(), modules, consumers=("jaxlib",))

        self.assertIn(
            "jaxlib: forbidden substring 'build_cuda_with_nvcc' present in args",
            errors,
        )

    def test_required_env_is_enforced(self) -> None:
        modules = {
            "torch": plan_module(
                required_prefixes=("cuda", "python"),
                env={
                    "CUDA_HOME": "/usr/local/cuda",
                    "CUDA_PATH": "/usr/local/cuda",
                    "PATH": "/usr/local/cuda/bin:/usr/bin:/bin",
                },
            )
        }

        errors = guard.check_modules(policy(), modules, consumers=("torch",))

        self.assertIn("torch: env USE_CUDA=None; expected '1'", errors)

    def test_required_args_are_enforced(self) -> None:
        modules = {
            "jaxlib": plan_module(
                required_prefixes=("llvm", "cuda"),
                env={
                    "CC": "/usr/lib/llvm-23/bin/clang",
                    "CXX": "/usr/lib/llvm-23/bin/clang++",
                    "PATH": "/usr/lib/llvm-23/bin:/usr/local/cuda/bin:/usr/bin:/bin",
                },
                args=[],
                llvm_commit=LLVM_COMMIT,
            )
        }

        errors = guard.check_modules(policy(), modules, consumers=("jaxlib",))

        self.assertIn(
            "jaxlib: missing required args entry '--bazel_options=--config=build_cuda_with_clang'",
            errors,
        )

    def test_required_path_entry_is_enforced(self) -> None:
        modules = {
            "torch": plan_module(
                required_prefixes=("cuda", "python"),
                env={
                    "USE_CUDA": "1",
                    "CUDA_HOME": "/usr/local/cuda",
                    "CUDA_PATH": "/usr/local/cuda",
                    "PATH": "/usr/bin:/bin",
                },
            )
        }

        errors = guard.check_modules(policy(), modules, consumers=("torch",))

        self.assertIn("torch: PATH is missing required entry '/usr/local/cuda/bin'", errors)

    def test_missing_plan_module_is_reported(self) -> None:
        errors = guard.check_modules(policy(), modules={}, consumers=("torch",))

        self.assertEqual(errors, ["torch: missing plan module"])

    def test_plan_snapshot_errors_are_reported(self) -> None:
        module = types.SimpleNamespace()
        module.REQUIRED_PREFIXES = ("cuda",)

        def fail_build_env(*_args: object, **_kwargs: object) -> dict[str, str]:
            raise RuntimeError("boom")

        module.build_env = fail_build_env

        errors = guard.check_modules(policy(), {"torch": module}, consumers=("torch",))

        self.assertEqual(errors, ["torch: could not build fake-prefix plan: boom"])

    def test_torch_clang_host_override_is_forbidden(self) -> None:
        modules = {
            "torch": plan_module(
                required_prefixes=("cuda", "python"),
                env={
                    "CC": "/usr/lib/llvm-23/bin/clang",
                    "CXX": "/usr/lib/llvm-23/bin/clang++",
                    "USE_CUDA": "1",
                    "CUDA_HOME": "/usr/local/cuda",
                    "CUDA_PATH": "/usr/local/cuda",
                    "PATH": "/usr/lib/llvm-23/bin:/usr/local/cuda/bin:/usr/bin:/bin",
                },
            )
        }

        errors = guard.check_modules(policy(), modules, consumers=("torch",))

        self.assertIn(
            "torch: forbidden env CC contains 'clang' (value: /usr/lib/llvm-23/bin/clang)",
            errors,
        )

    def test_wrong_llvm_commit_is_forbidden(self) -> None:
        modules = {
            "triton": plan_module(
                required_prefixes=("llvm", "cuda"),
                env={
                    "CC": "/usr/bin/gcc",
                    "CXX": "/usr/bin/g++",
                    "CMAKE_C_COMPILER": "/usr/bin/gcc",
                    "CMAKE_CXX_COMPILER": "/usr/bin/g++",
                    "LLVM_SYSPATH": "/usr/lib/llvm-23",
                    "PATH": "/usr/lib/llvm-23/bin:/usr/local/cuda/bin:/usr/bin:/bin",
                },
                llvm_commit="deadbeef",
            )
        }

        errors = guard.check_modules(policy(), modules, consumers=("triton",))

        self.assertIn(
            "triton: module constant ONE_LLVM_COMMIT='deadbeef'; expected "
            f"'{LLVM_COMMIT}'",
            errors,
        )

    def test_triton_clang_lld_pathway_is_forbidden(self) -> None:
        modules = {
            "triton": plan_module(
                required_prefixes=("llvm", "cuda"),
                env={
                    "CC": "/usr/lib/llvm-23/bin/clang",
                    "CXX": "/usr/lib/llvm-23/bin/clang++",
                    "TRITON_BUILD_WITH_CLANG_LLD": "1",
                    "LLVM_SYSPATH": "/usr/lib/llvm-23",
                    "PATH": "/usr/lib/llvm-23/bin:/usr/local/cuda/bin:/usr/bin:/bin",
                },
                llvm_commit=LLVM_COMMIT,
            )
        }

        errors = guard.check_modules(policy(), modules, consumers=("triton",))

        self.assertIn(
            "triton: env CC='/usr/lib/llvm-23/bin/clang'; expected one of ['/usr/bin/gcc']",
            errors,
        )
        self.assertIn(
            "triton: forbidden substring 'TRITON_BUILD_WITH_CLANG_LLD' present in env",
            errors,
        )

    def test_wrong_llvm_prefix_is_forbidden(self) -> None:
        modules = {
            "jaxlib": plan_module(
                required_prefixes=("llvm", "cuda"),
                env={
                    "CC": "/usr/lib/llvm-22/bin/clang",
                    "CXX": "/usr/lib/llvm-22/bin/clang++",
                    "PATH": "/usr/lib/llvm-22/bin:/usr/local/cuda/bin:/usr/bin:/bin",
                },
                args=[
                    "--bazel_options=--config=build_cuda_with_clang",
                    "--clang_path=/usr/lib/llvm-22/bin/clang",
                    "--bazel_options=--repo_env=USE_HERMETIC_CC_TOOLCHAIN=0",
                ],
                llvm_commit=LLVM_COMMIT,
            )
        }

        errors = guard.check_modules(policy(), modules, consumers=("jaxlib",))

        self.assertIn(
            "jaxlib: forbidden pattern '/usr/lib/llvm-(?!23\\\\b)[^\\\\s:]*' matched args",
            errors,
        )

    def test_expected_failure_masks_only_matching_error(self) -> None:
        errors = [
            "jaxlib: missing required args entry '--bazel_options=--config=build_cuda_with_clang'",
            "jaxlib: forbidden substring 'TF_NVCC_CLANG' present in env",
        ]

        active, expected = guard.partition_expected_failures(
            errors,
            {"jaxlib": ["missing required args entry '--bazel_options=--config=build_cuda_with_clang'"]},
        )

        self.assertEqual(expected, [errors[0]])
        self.assertEqual(active, [errors[1]])


if __name__ == "__main__":
    unittest.main()
