#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("plan.py")
ACTION_RULE = Path(__file__).with_name("triton_action.bzl")
ACTION_DRIVER = Path(__file__).with_name("action_driver.py")
PINS = Path(__file__).with_name("upstream_pins.json")
EXPERIMENT_ROOT = SCRIPT.parents[2]
MODULE = EXPERIMENT_ROOT / "MODULE.bazel"
NATIVE_OVERRIDES = EXPERIMENT_ROOT / "native_overrides.json"
BUILD_FILE = SCRIPT.with_name("BUILD.bazel")
ONE_LLVM_COMMIT = "35901313800ea6e6cbeb9226e51c7c4b29bfc40e"
FIXTURE_PYTHON_MAJOR = "3"
FIXTURE_PYTHON_MINOR = "13"
FIXTURE_PYTHON_VERSION = FIXTURE_PYTHON_MAJOR + "." + FIXTURE_PYTHON_MINOR
FIXTURE_PYTHON_TAG = "cp" + FIXTURE_PYTHON_MAJOR + FIXTURE_PYTHON_MINOR
FIXTURE_PYTHON_SEGMENT = "python" + FIXTURE_PYTHON_VERSION
FIXTURE_SITE_PACKAGES = Path("lib") / FIXTURE_PYTHON_SEGMENT / "site-packages"
L4_REQUIRED_PREFIXES = (
    "python",
    "python-venv",
    "py-pip",
    "py-setuptools",
    "py-wheel",
    "py-filelock",
    "py-lit",
    "py-pybind11",
    "cmake",
    "ninja",
    "llvm",
    "nlohmann_json",
    "cuda",
    "zlib_ng",
)
L4_PREFIX_ATTRS = {
    key: key.replace("-", "_") + "_prefix_file"
    for key in L4_REQUIRED_PREFIXES
}
SPEC = importlib.util.spec_from_file_location("triton_plan", SCRIPT)
assert SPEC is not None
plan = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = plan
SPEC.loader.exec_module(plan)


def temporary_directory():
    base = os.environ.get("TEST_TMPDIR") or os.environ.get("VASO_AGENT_IO_ROOT")
    return tempfile.TemporaryDirectory(dir=base)


def build_target_block(name: str) -> str:
    build = BUILD_FILE.read_text(encoding="utf-8")
    marker = f'    name = "{name}",'
    name_index = build.index(marker)
    call_start = build.rfind("triton_action_prefix(", 0, name_index)
    call_end = build.index("\n)", name_index) + 2
    return build[call_start:call_end]


def module_repo_rule_block(rule_name: str, name: str) -> str:
    module = MODULE.read_text(encoding="utf-8")
    marker = f'    name = "{name}",'
    name_index = module.index(marker)
    call_start = module.rfind(f"{rule_name}(", 0, name_index)
    call_end = module.index("\n)", name_index) + 2
    return module[call_start:call_end]


class TritonPlanTest(unittest.TestCase):
    def make_prefixes(self, root: Path, complete: bool = True) -> dict[str, str]:
        prefixes = {name: root / name for name in plan.REQUIRED_PREFIXES}
        for path in prefixes.values():
            path.mkdir(parents=True)
        if complete:
            files = {
                "python": ("bin/python3", "include"),
                "cmake": ("bin/cmake",),
                "ninja": ("bin/ninja",),
                "llvm": (
                    "bin/llvm-config",
                    "bin/clang",
                    "bin/ld.lld",
                    "bin/FileCheck",
                    "include/llvm/Config/llvm-config.h",
                    "lib/cmake/llvm/LLVMConfig.cmake",
                    "lib/cmake/mlir/MLIRConfig.cmake",
                    "lib/cmake/lld/LLDConfig.cmake",
                ),
                "nlohmann_json": ("include/nlohmann/json.hpp",),
                "cuda": (
                    "bin/ptxas",
                    "bin/nvdisasm",
                    "bin/cuobjdump",
                    "include/cuda.h",
                    "lib64/libcupti.so",
                    "nvvm/libdevice/libdevice.10.bc",
                ),
                "python-venv": ("pyvenv.cfg", "lib/python/site-packages/.keep"),
                "py-pip": ("lib/python/site-packages/pip/__init__.py",),
                "py-setuptools": ("lib/python/site-packages/setuptools/__init__.py",),
                "py-wheel": ("lib/python/site-packages/wheel/__init__.py",),
                "py-filelock": ("lib/python/site-packages/filelock/__init__.py",),
                "py-lit": ("lib/python/site-packages/lit/__init__.py",),
                "py-pybind11": ("lib/python/site-packages/pybind11/__init__.py",),
                "zlib_ng": ("include/zlib.h", "lib/libz.so"),
            }
            for key, rels in files.items():
                for rel in rels:
                    p = prefixes[key] / rel
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text("#!/bin/sh\n" if "/bin/" in rel else "", encoding="utf-8")
                    if "/bin/" in rel:
                        p.chmod(0o755)
        return {key: str(value) for key, value in prefixes.items()}

    def make_l4_prefixes(self, root: Path, python_abi: str = FIXTURE_PYTHON_TAG) -> dict[str, str]:
        prefixes = {name: root / name for name in L4_REQUIRED_PREFIXES}
        for path in prefixes.values():
            path.mkdir(parents=True)

        python_version = plan.python_version_from_abi(python_abi)
        assert python_version is not None
        site_packages = Path("lib") / f"python{python_version}" / "site-packages"
        files = {
            "python": ("bin/python3", f"include/python{python_version}/Python.h"),
            "python-venv": ("pyvenv.cfg", f"bin/python{python_version}", str(site_packages / ".keep")),
            "py-pip": ("bin/pip", str(site_packages / "pip/__init__.py")),
            "py-setuptools": (str(site_packages / "setuptools/__init__.py"),),
            "py-wheel": ("bin/wheel", str(site_packages / "wheel/__init__.py")),
            "py-filelock": (str(site_packages / "filelock/__init__.py"),),
            "py-lit": ("bin/lit", str(site_packages / "lit/__init__.py")),
            "py-pybind11": (str(site_packages / "pybind11/__init__.py"),),
            "cmake": ("bin/cmake",),
            "ninja": ("bin/ninja",),
            "llvm": (
                "bin/llvm-config",
                "bin/clang",
                "bin/ld.lld",
                "bin/FileCheck",
                "include/llvm/Config/llvm-config.h",
                "lib/cmake/llvm/LLVMConfig.cmake",
                "lib/cmake/mlir/MLIRConfig.cmake",
                "lib/cmake/lld/LLDConfig.cmake",
            ),
            "nlohmann_json": ("include/nlohmann/json.hpp",),
            "cuda": (
                "bin/ptxas",
                "bin/nvdisasm",
                "bin/cuobjdump",
                "include/cuda.h",
                "lib64/libcupti.so",
                "nvvm/libdevice/libdevice.10.bc",
            ),
            "zlib_ng": ("include/zlib.h", "lib/libz.so"),
        }
        for key, rels in files.items():
            for rel in rels:
                p = prefixes[key] / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("#!/bin/sh\n" if "/bin/" in rel else "", encoding="utf-8")
                if "/bin/" in rel:
                    p.chmod(0o755)
        return {key: str(value) for key, value in prefixes.items()}

    def run_plan(self, prefixes: dict[str, str], *extra: str) -> tuple[int, dict]:
        with temporary_directory() as tmp:
            out = Path(tmp) / "plan.json"
            argv = ["--out", str(out)]
            for key, value in prefixes.items():
                argv.extend(["--prefix", f"{key}={value}"])
            argv.extend(extra)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                rc = plan.main(argv)
            return rc, json.loads(out.read_text(encoding="utf-8"))

    def write_patch(self, root: Path, content: bytes = b"patch\n") -> tuple[Path, str]:
        path = root / "llvm-drift.patch"
        path.write_bytes(content)
        return path, hashlib.sha256(content).hexdigest()

    def test_manifest_pins_match_pytorch_v214_triton_release(self) -> None:
        pins = plan.load_pins(PINS)
        self.assertEqual(pins["pytorch"]["version"], "2.14.0")
        self.assertEqual(pins["triton"]["version"], "3.8.0")
        self.assertEqual(pins["triton"]["commit"], "675c59878aa2280b31f722aaf42b825fcee21de8")
        self.assertEqual(pins["triton"]["archive_sha256"], "61a11952362a0d54e67fb11fd29f25747da7339fe4ed39b2cff730c21ebe8825")
        self.assertEqual(pins["llvm"]["source_commit"], "4611156032e1ebac68b2b8f6107b1475a7c60800")
        self.assertEqual(pins["llvm"]["source_archive_sha256"], "3d5f91c1d6eea1ee585af1f9ce4e5acc29cc4968edcfbb95681df51edfad0462")
        self.assertEqual(pins["llvm"]["selected_source_commit"], ONE_LLVM_COMMIT)
        self.assertEqual(pins["llvm"]["selected_version"], "23.0.0git")
        self.assertEqual(pins["llvm"]["selected_prefix"], "/usr/lib/llvm-23")
        self.assertEqual(pins["llvm"]["build_info_commit"], "1f126a6dea50d185c0781743a667390037ae88bd")
        self.assertEqual(pins["nlohmann_json"]["version"], "v3.11.3")

    def test_plan_rejects_reusing_spack_llvm_without_pin_audit(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(Path(tmp))
            rc, doc = self.run_plan(prefixes)

        self.assertEqual(rc, 0)
        self.assertFalse(doc["will_build"])
        self.assertEqual(doc["mode"], "dry-run")
        self.assertTrue(doc["preflight_ok"])
        self.assertFalse(doc["llvm"]["reuse_spack_llvm_20_1_8"])
        self.assertEqual(doc["llvm"]["provider"], "llvm")
        self.assertEqual(doc["llvm"]["required_source_commit"], ONE_LLVM_COMMIT)
        self.assertEqual(doc["llvm"]["upstream_triton_source_commit"], "4611156032e1ebac68b2b8f6107b1475a7c60800")
        self.assertEqual(doc["llvm"]["published_build_info_commit"], "1f126a6dea50d185c0781743a667390037ae88bd")
        self.assertEqual(doc["build_env"]["TRITON_OFFLINE_BUILD"], "1")
        self.assertEqual(doc["build_env"]["LLVM_SYSPATH"], prefixes["llvm"])
        self.assertEqual(doc["build_env"]["JSON_SYSPATH"], prefixes["nlohmann_json"])

    def test_l4_plan_matches_one_llvm_graph_and_overlay_environment(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_l4_prefixes(Path(tmp), python_abi=FIXTURE_PYTHON_TAG)
            rc, doc = self.run_plan(prefixes, "--python-abi", FIXTURE_PYTHON_TAG)

        self.assertEqual(rc, 0)
        self.assertTrue(doc["preflight_ok"])
        self.assertEqual(doc["package"], "py-triton")
        self.assertEqual(tuple(doc["required_prefixes"]), L4_REQUIRED_PREFIXES)
        self.assertEqual(doc["llvm"]["provider"], "llvm")
        self.assertEqual(doc["llvm"]["required_source_commit"], ONE_LLVM_COMMIT)
        self.assertEqual(
            doc["llvm"]["upstream_triton_source_commit"],
            "4611156032e1ebac68b2b8f6107b1475a7c60800",
        )

        env = doc["build_env"]
        self.assertEqual(env["TRITON_OFFLINE_BUILD"], "1")
        self.assertNotIn("TRITON_BUILD_WITH_CLANG_LLD", env)
        self.assertEqual(env["CC"], "/usr/bin/gcc")
        self.assertEqual(env["CXX"], "/usr/bin/g++")
        self.assertEqual(env["CMAKE_C_COMPILER"], "/usr/bin/gcc")
        self.assertEqual(env["CMAKE_CXX_COMPILER"], "/usr/bin/g++")
        self.assertEqual(env["LLVM_SYSPATH"], prefixes["llvm"])
        self.assertEqual(env["JSON_SYSPATH"], prefixes["nlohmann_json"])
        self.assertEqual(env["PYBIND11_SYSPATH"], prefixes["py-pybind11"])
        self.assertEqual(env["LIBRARY_PATH"], str(Path(prefixes["zlib_ng"]) / "lib"))
        self.assertEqual(env["LDFLAGS"], "-L" + str(Path(prefixes["zlib_ng"]) / "lib"))
        self.assertEqual(env["TRITON_PTXAS_PATH"], str(Path(prefixes["cuda"]) / "bin" / "ptxas"))
        self.assertEqual(env["TRITON_PTXAS_BLACKWELL_PATH"], str(Path(prefixes["cuda"]) / "bin" / "ptxas"))
        self.assertEqual(env["TRITON_CUOBJDUMP_PATH"], str(Path(prefixes["cuda"]) / "bin" / "cuobjdump"))
        self.assertEqual(env["TRITON_NVDISASM_PATH"], str(Path(prefixes["cuda"]) / "bin" / "nvdisasm"))
        self.assertEqual(env["TRITON_CUDACRT_PATH"], str(Path(prefixes["cuda"]) / "include"))
        self.assertEqual(env["TRITON_CUDART_PATH"], str(Path(prefixes["cuda"]) / "include"))
        self.assertEqual(env["TRITON_CUPTI_PATH"], prefixes["cuda"])
        self.assertEqual(env["TRITON_CUPTI_INCLUDE_PATH"], str(Path(prefixes["cuda"]) / "include"))
        self.assertEqual(env["TRITON_CUPTI_LIB_PATH"], str(Path(prefixes["cuda"]) / "lib64"))
        self.assertEqual(env["TRITON_CUPTI_LIB_BLACKWELL_PATH"], str(Path(prefixes["cuda"]) / "lib64"))
        self.assertEqual(
            env["TRITON_LIBDEVICE_PATH"],
            str(Path(prefixes["cuda"]) / "nvvm" / "libdevice" / "libdevice.10.bc"),
        )
        pythonpath = env["PYTHONPATH"].split(os.pathsep)
        self.assertIn(str(Path(prefixes["py-filelock"]) / FIXTURE_SITE_PACKAGES), pythonpath)
        self.assertIn(str(Path(prefixes["py-lit"]) / FIXTURE_SITE_PACKAGES), pythonpath)
        self.assertIn(str(Path(prefixes["py-pybind11"]) / FIXTURE_SITE_PACKAGES), pythonpath)
        cmake_prefix_path = env["CMAKE_PREFIX_PATH"].split(os.pathsep)
        for key in ("llvm", "nlohmann_json", "py-pybind11", "zlib_ng", "cuda"):
            self.assertIn(prefixes[key], cmake_prefix_path)
        checks = {item["name"]: item for item in doc["preflight"]}
        self.assertTrue(checks["zlib-ng:lib"]["ok"])

    def test_python_paths_accept_version_or_cp_abi(self) -> None:
        for python_abi in (FIXTURE_PYTHON_TAG, FIXTURE_PYTHON_VERSION):
            with self.subTest(python_abi=python_abi):
                with temporary_directory() as tmp:
                    prefixes = self.make_l4_prefixes(Path(tmp), python_abi=python_abi)
                    rc, doc = self.run_plan(prefixes, "--python-abi", python_abi)

                self.assertEqual(rc, 0)
                self.assertTrue(doc["preflight_ok"])
                pythonpath = doc["build_env"]["PYTHONPATH"].split(os.pathsep)
                self.assertIn(
                    str(Path(prefixes["py-filelock"]) / FIXTURE_SITE_PACKAGES),
                    pythonpath,
                )
                checks = {item["name"]: item for item in doc["preflight"]}
                self.assertEqual(
                    checks["python:headers"]["detail"],
                    str(Path(prefixes["python"]) / "include" / FIXTURE_PYTHON_SEGMENT / "Python.h"),
                )

    def test_module_declares_offline_pinned_triton_and_triton_llvm_sources(self) -> None:
        module = MODULE.read_text(encoding="utf-8")

        self.assertIn('name = "triton_v2_14_0_source"', module)
        self.assertIn('exports_files(["setup.py"])', module)
        self.assertNotIn('exports_files(["python/setup.py"])', module)
        self.assertIn("triton-675c59878aa2280b31f722aaf42b825fcee21de8.tar.gz", module)
        self.assertIn('sha256 = "61a11952362a0d54e67fb11fd29f25747da7339fe4ed39b2cff730c21ebe8825"', module)
        self.assertIn('strip_prefix = "triton-675c59878aa2280b31f722aaf42b825fcee21de8"', module)
        self.assertIn('name = "triton_llvm_v2_14_0_source"', module)
        self.assertIn("llvm-project-4611156032e1ebac68b2b8f6107b1475a7c60800.tar.gz", module)
        self.assertIn('sha256 = "3d5f91c1d6eea1ee585af1f9ce4e5acc29cc4968edcfbb95681df51edfad0462"', module)
        self.assertIn('strip_prefix = "llvm-project-4611156032e1ebac68b2b8f6107b1475a7c60800"', module)

    def test_preflight_fails_when_offline_provider_surfaces_are_missing(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(Path(tmp), complete=False)
            rc, doc = self.run_plan(prefixes)

        checks = {item["name"]: item for item in doc["preflight"]}
        self.assertEqual(rc, 1)
        self.assertFalse(doc["preflight_ok"])
        for name in ("python:binary", "llvm:llvm-config", "nlohmann-json:header", "cuda:ptxas"):
            self.assertIn(name, checks)
            self.assertFalse(checks[name]["ok"])

    def test_preflight_requires_exact_llvm_and_cuda_provider_surfaces(self) -> None:
        cases = (
            ("llvm", "lib/cmake/llvm/LLVMConfig.cmake", "llvm:cmake-config"),
            ("llvm", "lib/cmake/mlir/MLIRConfig.cmake", "llvm:mlir-cmake-config"),
            ("llvm", "lib/cmake/lld/LLDConfig.cmake", "llvm:lld-cmake-config"),
            ("cuda", "bin/ptxas", "cuda:ptxas"),
        )
        for provider, missing_rel, check_name in cases:
            with self.subTest(provider=provider, missing_rel=missing_rel), temporary_directory() as tmp:
                prefixes = self.make_l4_prefixes(Path(tmp), python_abi=FIXTURE_PYTHON_TAG)
                (Path(prefixes[provider]) / missing_rel).unlink()
                rc, doc = self.run_plan(
                    prefixes,
                    "--python-abi",
                    FIXTURE_PYTHON_TAG,
                    "--execute",
                    "--token",
                    plan.REQUIRED_TOKEN,
                )

            checks = {item["name"]: item for item in doc["preflight"]}
            self.assertEqual(rc, 3)
            self.assertFalse(doc["preflight_ok"])
            self.assertFalse(doc["will_build"])
            self.assertIn(check_name, checks)
            self.assertFalse(checks[check_name]["ok"])
            self.assertIn(provider, checks[check_name]["detail"])

    def test_preflight_requires_declared_patch_file_with_expected_sha(self) -> None:
        with temporary_directory() as tmp:
            tmp_path = Path(tmp)
            prefixes = self.make_l4_prefixes(tmp_path, python_abi=FIXTURE_PYTHON_TAG)
            patch, sha256 = self.write_patch(tmp_path)
            patch.unlink()
            rc, doc = self.run_plan(
                prefixes,
                "--python-abi",
                FIXTURE_PYTHON_TAG,
                "--patch-file",
                str(patch),
                "--patch-sha256",
                sha256,
                "--execute",
                "--token",
                plan.REQUIRED_TOKEN,
            )

        checks = {item["name"]: item for item in doc["preflight"]}
        self.assertEqual(rc, 3)
        self.assertFalse(doc["preflight_ok"])
        self.assertFalse(doc["will_build"])
        self.assertIn("patch:llvm-drift.patch", checks)
        self.assertFalse(checks["patch:llvm-drift.patch"]["ok"])
        self.assertIn("llvm-drift.patch", checks["patch:llvm-drift.patch"]["detail"])

        with temporary_directory() as tmp:
            tmp_path = Path(tmp)
            prefixes = self.make_l4_prefixes(tmp_path, python_abi=FIXTURE_PYTHON_TAG)
            patch, _sha256 = self.write_patch(tmp_path, content=b"changed\n")
            rc, doc = self.run_plan(
                prefixes,
                "--python-abi",
                FIXTURE_PYTHON_TAG,
                "--patch-file",
                str(patch),
                "--patch-sha256",
                "0" * 64,
                "--execute",
                "--token",
                plan.REQUIRED_TOKEN,
            )

        checks = {item["name"]: item for item in doc["preflight"]}
        self.assertEqual(rc, 3)
        self.assertFalse(doc["preflight_ok"])
        self.assertFalse(doc["will_build"])
        self.assertIn("patch:llvm-drift.patch", checks)
        self.assertFalse(checks["patch:llvm-drift.patch"]["ok"])
        self.assertIn("expected", checks["patch:llvm-drift.patch"]["detail"])

    def test_preflight_accepts_declared_patch_file_with_matching_sha(self) -> None:
        with temporary_directory() as tmp:
            tmp_path = Path(tmp)
            prefixes = self.make_l4_prefixes(tmp_path, python_abi=FIXTURE_PYTHON_TAG)
            patch, sha256 = self.write_patch(tmp_path)
            rc, doc = self.run_plan(
                prefixes,
                "--python-abi",
                FIXTURE_PYTHON_TAG,
                "--patch-file",
                str(patch),
                "--patch-sha256",
                sha256,
            )

        checks = {item["name"]: item for item in doc["preflight"]}
        self.assertEqual(rc, 0)
        self.assertTrue(doc["preflight_ok"])
        self.assertIn("patch:llvm-drift.patch", checks)
        self.assertTrue(checks["patch:llvm-drift.patch"]["ok"])

    def test_preflight_uses_default_drift_patch_from_pins(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_l4_prefixes(Path(tmp), python_abi=FIXTURE_PYTHON_TAG)
            rc, doc = self.run_plan(prefixes, "--python-abi", FIXTURE_PYTHON_TAG)

        checks = {item["name"]: item for item in doc["preflight"]}
        self.assertEqual(rc, 0)
        self.assertTrue(doc["preflight_ok"])
        self.assertIn("patch:0001-llvm-35901313.patch", checks)
        self.assertTrue(checks["patch:0001-llvm-35901313.patch"]["ok"])
        self.assertIn(
            "8a0405802e0f45fae5aa9bd0a6921d81f16a1f50224a705a4f450fe7c03a90d3",
            checks["patch:0001-llvm-35901313.patch"]["detail"],
        )

    def test_execute_without_token_is_refused(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(Path(tmp))
            rc, doc = self.run_plan(prefixes, "--execute")

        self.assertEqual(rc, 2)
        self.assertEqual(doc["authorization"]["required_token"], plan.REQUIRED_TOKEN)
        self.assertFalse(doc["authorization"]["token_present"])
        self.assertFalse(doc["will_build"])

    def test_execute_with_token_and_complete_preflight_requests_build(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_l4_prefixes(Path(tmp), python_abi=FIXTURE_PYTHON_TAG)
            rc, doc = self.run_plan(
                prefixes,
                "--execute",
                "--token",
                plan.REQUIRED_TOKEN,
                "--python-abi",
                FIXTURE_PYTHON_TAG,
            )

        self.assertEqual(rc, 0)
        self.assertTrue(doc["authorization"]["token_present"])
        self.assertEqual(doc["mode"], "execute")
        self.assertTrue(doc["preflight_ok"])
        self.assertTrue(doc["will_build"])

    def test_action_rule_moves_execute_path_into_configured_target(self) -> None:
        self.assertTrue(
            ACTION_RULE.exists(),
            "L4 requires a configured Triton action rule",
        )
        script = ACTION_RULE.read_text(encoding="utf-8")

        self.assertIn("TritonNativePrefixInfo = provider(", script)
        self.assertIn("ctx.actions.declare_directory(ctx.label.name + \"_prefix\")", script)
        self.assertIn("ctx.actions.declare_file(ctx.label.name + \"_wheel.whl\")", script)
        self.assertIn("ctx.actions.declare_file(ctx.label.name + \"_build_plan.json\")", script)
        self.assertIn("ctx.actions.declare_file(ctx.label.name + \"_provider_metadata.json\")", script)
        self.assertIn("ctx.actions.declare_file(ctx.label.name + \"_result.txt\")", script)
        self.assertNotIn("ctx.actions.declare_directory(ctx.label.name + \"_work\")", script)
        self.assertIn("ctx.actions.run_shell(", script)
        self.assertNotIn("repository_rule(", script)
        self.assertIn("--execute", script)
        self.assertIn("--source-anchor", script)
        self.assertIn("--wheel-out", script)
        self.assertIn("action_driver.py", script)
        self.assertIn("triton_token_flag = rule(", script)
        self.assertIn("build_setting = config.string(flag = True)", script)
        self.assertIn("ctx.build_setting_value", script)
        self.assertIn("ctx.attr._token_flag[TritonTokenInfo].value", script)
        self.assertNotIn("\"token\": attr.string", script)
        self.assertIn("use_default_shell_env = True", script)
        self.assertNotIn("\"VASO_IN_INSULA\":", script)
        self.assertNotIn("\"VASO_ROOTFS_BUNDLE_MANIFEST\":", script)
        self.assertNotIn("\"TMPDIR\":", script)

        for provider, attr_name in L4_PREFIX_ATTRS.items():
            with self.subTest(provider=provider):
                self.assertIn(f"(\"{provider}\", \"{attr_name}\")", script)
                self.assertIn(f"\"{attr_name}\": attr.label", script)

    def test_action_driver_uses_action_owned_source_and_no_declared_workdir(self) -> None:
        self.assertTrue(
            ACTION_DRIVER.exists(),
            "L4 requires a configured action driver",
        )
        driver = ACTION_DRIVER.read_text(encoding="utf-8")

        self.assertIn('parser.add_argument("--source-anchor", required=True)', driver)
        self.assertIn('parser.add_argument("--build-work-root", default="")', driver)
        self.assertIn('parser.add_argument("--wheel-out", required=True)', driver)
        self.assertNotIn('parser.add_argument("--build-work-out", required=True)', driver)
        self.assertIn("VASO_IN_INSULA", driver)
        self.assertIn("VASO_ROOTFS_BUNDLE_MANIFEST", driver)
        self.assertIn("TMPDIR", driver)
        self.assertIn("PIP_CACHE_DIR", driver)
        self.assertIn("TRITON_OFFLINE_BUILD", driver)
        self.assertNotIn("TRITON_BUILD_WITH_CLANG_LLD", driver)

    def test_build_file_exposes_token_safe_action_dry_run_target(self) -> None:
        build = BUILD_FILE.read_text(encoding="utf-8")

        self.assertIn('load(":triton_action.bzl", "triton_action_prefix", "triton_token_flag")', build)
        self.assertIn('name = "token"', build)
        self.assertIn('name = "action_driver"', build)
        self.assertIn('name = "triton_action_dry_run"', build)
        self.assertIn('name = "action_smoke_test"', build)
        self.assertIn('source_anchor = ":synthetic_source_anchor.txt"', build)
        self.assertIn('source_files = ":synthetic_source_anchor.txt"', build)
        self.assertIn('execute = False', build)
        self.assertIn('synthetic_prefixes_for_dry_run = True', build)
        self.assertNotIn('token = "build-native-triton"', build)
        self.assertNotIn("@llvm_native//:prefix_path.txt", build)

        action = build_target_block("triton_action_dry_run")
        for provider, attr_name in L4_PREFIX_ATTRS.items():
            with self.subTest(provider=provider):
                self.assertIn(f"{attr_name} = \":synthetic_prefix_path.txt\"", action)

    def test_build_file_exposes_real_token_gated_action_target(self) -> None:
        build = BUILD_FILE.read_text(encoding="utf-8")
        self.assertIn('name = "triton_action"', build)
        self.assertIn('"patches/0001-llvm-35901313.patch"', build)
        self.assertIn('"patches/SHA256SUMS"', build)
        self.assertNotIn("@llvm_native//:prefix_path.txt", build)

        action = build_target_block("triton_action")
        self.assertIn('source_anchor = "@triton_v2_14_0_source//:setup.py"', action)
        self.assertIn('source_files = "@triton_v2_14_0_source//:all_srcs"', action)
        self.assertIn('execute = True', action)
        self.assertIn('drift_patch = ":patches/0001-llvm-35901313.patch"', action)
        self.assertIn(
            'drift_patch_sha256 = "8a0405802e0f45fae5aa9bd0a6921d81f16a1f50224a705a4f450fe7c03a90d3"',
            action,
        )
        self.assertIn('llvm_prefix_file = "@rootfs_llvm_23_native//:prefix_path.txt"', action)
        self.assertIn('nlohmann_json_prefix_file = "@nlohmann_json_native//:prefix_path.txt"', action)
        self.assertIn('py_lit_prefix_file = "@py_lit_native//:prefix_path.txt"', action)
        self.assertIn('py_pybind11_prefix_file = "@py_pybind11_native//:prefix_path.txt"', action)
        self.assertNotIn('synthetic_prefixes_for_dry_run = True', action)

    def test_native_overrides_flip_triton_helper_nodes(self) -> None:
        overrides = json.loads(NATIVE_OVERRIDES.read_text(encoding="utf-8"))

        self.assertEqual(
            overrides["native"].get("nlohmann-json@3.11.3"),
            "@nlohmann_json_native//:lib",
        )
        self.assertEqual(
            overrides["provided_versions"].get("nlohmann-json@3.11.3"),
            "3.11.3",
        )
        self.assertEqual(
            overrides["native"].get("py-lit@18.1.8"),
            "@py_lit_native//:lib",
        )
        self.assertEqual(
            overrides["provided_versions"].get("py-lit@18.1.8"),
            "18.1.8",
        )

    def test_py_lit_provider_uses_setuptools_from_triton_graph(self) -> None:
        py_lit = module_repo_rule_block("py_lit_native", "py_lit_native")

        self.assertIn(
            'py_setuptools_prefix_file = "@py_setuptools_82_native//:prefix_path.txt"',
            py_lit,
        )


if __name__ == "__main__":
    unittest.main()
