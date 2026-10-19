#!/usr/bin/env python3
"""Tests for the native torchvision source/action plan."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("plan.py")
ACTION_RULE = Path(__file__).with_name("torchvision_action.bzl")
ACTION_DRIVER = Path(__file__).with_name("action_driver.py")
PINS = Path(__file__).with_name("upstream_pins.json")
EXPERIMENT_ROOT = SCRIPT.parents[2]
MODULE = EXPERIMENT_ROOT / "MODULE.bazel"
RUN_SH = EXPERIMENT_ROOT / "run.sh"
BUILD_FILE = SCRIPT.with_name("BUILD.bazel")
PYTHON_ABI = "derived"
PYTHON_VERSION = "3.13"
SITE_PACKAGES = Path("lib") / f"python{PYTHON_VERSION}" / "site-packages"
EXPECTED_REQUIRED_PREFIXES = (
    "torch",
    "python",
    "python-venv",
    "py-pip",
    "py-setuptools",
    "py-wheel",
    "py-numpy",
    "py-pillow",
    "py-filelock",
    "cuda",
    "ninja",
    "libjpeg-turbo",
    "libpng",
    "zlib-ng",
)

SPEC = importlib.util.spec_from_file_location("torchvision_plan", SCRIPT)
assert SPEC is not None
plan = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = plan
SPEC.loader.exec_module(plan)


def temporary_directory():
    base = os.environ.get("TEST_TMPDIR") or os.environ.get("VASO_AGENT_IO_ROOT")
    return tempfile.TemporaryDirectory(dir=base)


def module_text_with_includes() -> str:
    module = MODULE.read_text(encoding="utf-8")
    parts = [module]
    for include_label in re.findall(r'^[ \t]*include\("([^"]+)"\)', module, re.M):
        if include_label.startswith("//:"):
            include_path = EXPERIMENT_ROOT / include_label.removeprefix("//:")
        elif include_label.startswith(":"):
            include_path = EXPERIMENT_ROOT / include_label.removeprefix(":")
        else:
            continue
        if include_path.is_file():
            parts.append(include_path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def build_target_block(name: str) -> str:
    build = BUILD_FILE.read_text(encoding="utf-8")
    marker = f'    name = "{name}",'
    name_index = build.index(marker)
    call_start = build.rfind("torchvision_action_prefix(", 0, name_index)
    call_end = build.index("\n)", name_index) + 2
    return build[call_start:call_end]


def native_toolchain_fixture(cuda_home: str | Path = "/usr/local/cuda") -> dict[str, object]:
    cuda_home_text = str(cuda_home)
    return {
        "schema_version": 1,
        "cc": {
            "cc": "/usr/bin/gcc",
            "cxx": "/usr/bin/g++",
            "toolchain_identifier": "vaso-rootfs-gcc-13.3",
        },
        "cuda": {
            "arch_list": "10.0",
            "home": cuda_home_text,
            "home_env": "VASO_CUDA_HOME",
            "nvcc": str(Path(cuda_home_text) / "bin" / "nvcc"),
            "prefix_file": "@cuda_native//:prefix_path.txt",
            "providers": [
                {"package": "cuda", "label": "@cuda_native//:lib"},
            ],
        },
        "determinism": {
            "env": {
                "SOURCE_DATE_EPOCH": "1700000000",
                "PYTHONHASHSEED": "0",
                "CFLAGS": "-ffile-prefix-map=/vaso-ob/execroot/_main=.",
                "CXXFLAGS": "-fdebug-prefix-map=/vaso-ob/execroot/_main=.",
                "NVCC_FLAGS": "--frandom-seed=abcdef12 --objdir-as-tempdir",
            },
            "prefix_map_flags": "-ffile-prefix-map=/vaso-ob/execroot/_main=.",
            "prefix_map_roots": "/vaso-ob/execroot/_main",
        },
        "python": {
            "prefix_file": "@python_313_native//:prefix_path.txt",
            "version": "3.13.13",
        },
    }


def write_native_toolchain(path: Path, *, cuda_home: str | Path = "/usr/local/cuda", **overrides: object) -> Path:
    toolchain = native_toolchain_fixture(cuda_home)
    toolchain.update(overrides)
    path.write_text(json.dumps(toolchain, sort_keys=True) + "\n", encoding="utf-8")
    return path


class TorchvisionPlanTest(unittest.TestCase):
    def make_prefixes(self, root: Path, complete: bool = True) -> dict[str, str]:
        prefixes = {name: root / name for name in plan.REQUIRED_PREFIXES}
        for path in prefixes.values():
            path.mkdir(parents=True)
        if complete:
            fake_python = (
                "#!/bin/sh\n"
                'case "$2" in\n'
                f"  *purelib*) echo lib/python{PYTHON_VERSION}/site-packages ;;\n"
                f"  *include*) echo include/python{PYTHON_VERSION} ;;\n"
                "esac\n"
            )
            files = {
                "torch": (
                    str(SITE_PACKAGES / "torch/__init__.py"),
                    str(SITE_PACKAGES / "torch/lib/libtorch_cuda.so"),
                ),
                "python": ("bin/python3", f"include/python{PYTHON_VERSION}/Python.h"),
                "python-venv": ("bin/python3", f"bin/python{PYTHON_VERSION}", "pyvenv.cfg", str(SITE_PACKAGES / ".keep")),
                "py-pip": ("bin/pip", str(SITE_PACKAGES / "pip/__init__.py")),
                "py-setuptools": (str(SITE_PACKAGES / "setuptools/__init__.py"),),
                "py-wheel": ("bin/wheel", str(SITE_PACKAGES / "wheel/__init__.py")),
                "py-numpy": (str(SITE_PACKAGES / "numpy/__init__.py"),),
                "py-pillow": (str(SITE_PACKAGES / "PIL/Image.py"), str(SITE_PACKAGES / "PIL/__init__.py")),
                "py-filelock": (str(SITE_PACKAGES / "filelock/__init__.py"),),
                "cuda": ("bin/nvcc", "include/cuda.h", "include/nvjpeg.h", "lib64/libcudart.so", "lib64/libnvjpeg.so"),
                "ninja": ("bin/ninja",),
                "libjpeg-turbo": ("include/jpeglib.h", "lib/libjpeg.so"),
                "libpng": ("bin/libpng-config", "include/png.h", "lib/libpng16.so"),
                "zlib-ng": ("include/zlib.h", "lib/libz.so"),
            }
            for key, rels in files.items():
                for rel in rels:
                    p = prefixes[key] / rel
                    p.parent.mkdir(parents=True, exist_ok=True)
                    if key in {"python", "python-venv"} and rel.startswith("bin/python"):
                        p.write_text(fake_python, encoding="utf-8")
                    else:
                        p.write_text("#!/bin/sh\n" if "/bin/" in rel else "", encoding="utf-8")
                    if rel.startswith("bin/"):
                        p.chmod(0o755)
        return {key: str(value) for key, value in prefixes.items()}

    def run_plan(self, prefixes: dict[str, str], *extra: str) -> tuple[int, dict]:
        with temporary_directory() as tmp:
            out = Path(tmp) / "plan.json"
            argv = ["--pins", str(PINS), "--out", str(out), "--python-abi", PYTHON_ABI]
            for key, value in prefixes.items():
                argv.extend(["--prefix", f"{key}={value}"])
            if "--native-toolchain" not in extra:
                native_toolchain = write_native_toolchain(Path(tmp) / "native_toolchain.json", cuda_home=prefixes["cuda"])
                argv.extend(["--native-toolchain", str(native_toolchain)])
            argv.extend(extra)
            rc = plan.main(argv)
            return rc, json.loads(out.read_text(encoding="utf-8"))

    def test_manifest_pins_match_torchvision_for_torch_214(self) -> None:
        pins = plan.load_pins(PINS)

        self.assertEqual(pins["pytorch"]["version"], "2.14.0")
        self.assertEqual(pins["torchvision"]["version"], "0.29.0")
        self.assertEqual(pins["torchvision"]["requires_python"], "!=3.14.1,>=3.10")
        self.assertEqual(pins["torchvision"]["requires_torch"], "==2.14.0")
        self.assertEqual(pins["torchvision"]["source_url"], "https://github.com/pytorch/vision/archive/refs/tags/v0.29.0.tar.gz")
        self.assertEqual(pins["torchvision"]["archive_sha256"], "24be57d922927d8a2ac2e8f076f07c3447ddf8f1d25ddbb7b65578f36c9ab8e3")
        self.assertEqual(pins["pillow"]["version"], "12.2.0")
        self.assertEqual(pins["pillow"]["archive_sha256"], "a830b1a40919539d07806aa58e1b114df53ddd43213d9c8b75847eee6c0182b5")

    def test_plan_env_wires_cuda_nms_and_image_codec_dependencies(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(Path(tmp))
            rc, doc = self.run_plan(prefixes, "--max-jobs", "7")

        self.assertEqual(rc, 0)
        self.assertTrue(doc["preflight_ok"])
        self.assertFalse(doc["will_build"])
        self.assertEqual(doc["package"], "py-torchvision")
        self.assertEqual(doc["version"], "0.29.0")
        self.assertEqual(doc["pytorch_version"], "2.14.0")
        self.assertEqual(tuple(doc["required_prefixes"]), EXPECTED_REQUIRED_PREFIXES)
        env = doc["build_env"]
        self.assertEqual(env["BUILD_VERSION"], "0.29.0")
        self.assertEqual(env["PYTORCH_VERSION"], "2.14.0")
        self.assertEqual(env["FORCE_CUDA"], "1")
        self.assertEqual(env["TORCH_CUDA_ARCH_LIST"], "10.0")
        self.assertEqual(env["TORCHVISION_USE_PNG"], "1")
        self.assertEqual(env["TORCHVISION_USE_JPEG"], "1")
        self.assertEqual(env["TORCHVISION_USE_WEBP"], "0")
        self.assertEqual(env["TORCHVISION_USE_NVJPEG"], "1")
        self.assertEqual(env["MAX_JOBS"], "7")
        self.assertEqual(env["CMAKE_BUILD_PARALLEL_LEVEL"], "7")
        self.assertEqual(env["MAX_CONCURRENCY"], "7")
        self.assertEqual(env["CUDA_HOME"], prefixes["cuda"])
        self.assertEqual(env["CUDA_PATH"], prefixes["cuda"])
        self.assertIn(str(Path(prefixes["libjpeg-turbo"]) / "include"), env["TORCHVISION_INCLUDE"].split(os.pathsep))
        self.assertIn(str(Path(prefixes["libjpeg-turbo"]) / "lib"), env["TORCHVISION_LIBRARY"].split(os.pathsep))
        self.assertIn(str(Path(prefixes["libpng"]) / "bin"), env["PATH"].split(os.pathsep))
        pythonpath = env["PYTHONPATH"].split(os.pathsep)
        self.assertEqual(pythonpath[0], str(Path(prefixes["torch"]) / SITE_PACKAGES))
        self.assertIn(str(Path(prefixes["py-pillow"]) / SITE_PACKAGES), pythonpath)
        self.assertIn(str(Path(prefixes["py-filelock"]) / SITE_PACKAGES), pythonpath)
        self.assertIn(str(Path(prefixes["torch"]) / SITE_PACKAGES / "torch" / "lib"), env["LD_LIBRARY_PATH"].split(os.pathsep))
        decisions = doc["dependency_decisions"]
        self.assertEqual(decisions["libjpeg-turbo"]["status"], "on")
        self.assertEqual(decisions["libpng"]["status"], "on")
        self.assertEqual(decisions["libwebp"]["status"], "off-with-reason")
        self.assertEqual(decisions["pillow"]["status"], "on")

    def test_plan_env_matches_pre_m2b_snapshot_while_requiring_native_toolchain(self) -> None:
        with temporary_directory() as tmp:
            tmp_path = Path(tmp)
            prefixes = self.make_prefixes(tmp_path)
            native_toolchain = write_native_toolchain(tmp_path / "native_toolchain.json", cuda_home=prefixes["cuda"])
            rc, doc = self.run_plan(prefixes, "--native-toolchain", str(native_toolchain), "--max-jobs", "7")

        self.assertEqual(rc, 0)
        env = doc["build_env"]
        self.assertNotIn("CC", env)
        self.assertNotIn("CXX", env)
        self.assertEqual(env["CUDA_HOME"], prefixes["cuda"])
        self.assertEqual(env["CUDA_PATH"], prefixes["cuda"])
        self.assertNotIn("CUDA_TOOLKIT_ROOT_DIR", env)
        for key in ("SOURCE_DATE_EPOCH", "PYTHONHASHSEED", "CFLAGS", "CXXFLAGS", "NVCC_FLAGS"):
            self.assertNotIn(key, env)
        self.assertEqual(doc["native_toolchain"]["cc"]["toolchain_identifier"], "vaso-rootfs-gcc-13.3")

    def assert_plan_exits(self, prefixes: dict[str, str], *extra: str) -> str:
        with temporary_directory() as tmp:
            out = Path(tmp) / "plan.json"
            argv = ["--pins", str(PINS), "--out", str(out), "--python-abi", PYTHON_ABI]
            for key, value in prefixes.items():
                argv.extend(["--prefix", f"{key}={value}"])
            argv.extend(extra)
            with self.assertRaises(SystemExit) as cm:
                plan.main(argv)
            return str(cm.exception)

    def test_torchvision_family_rejects_clang_descriptor(self) -> None:
        with temporary_directory() as tmp:
            tmp_path = Path(tmp)
            prefixes = self.make_prefixes(tmp_path)
            native_toolchain = write_native_toolchain(
                tmp_path / "native_toolchain.json",
                cuda_home=prefixes["cuda"],
                cc={
                    "cc": "/usr/lib/llvm-23/bin/clang",
                    "cxx": "/usr/lib/llvm-23/bin/clang++",
                    "toolchain_identifier": "vaso-rootfs-clang-23",
                },
            )
            error = self.assert_plan_exits(prefixes, "--native-toolchain", str(native_toolchain))

        self.assertIn("torchvision requires compiler family gcc", error)

    def test_torchvision_rejects_cuda_home_that_differs_from_selected_prefix(self) -> None:
        with temporary_directory() as tmp:
            tmp_path = Path(tmp)
            prefixes = self.make_prefixes(tmp_path)
            native_toolchain = write_native_toolchain(
                tmp_path / "native_toolchain.json",
                cuda={
                    "arch_list": "10.0",
                    "home": "/descriptor/cuda",
                    "nvcc": "/descriptor/cuda/bin/nvcc",
                    "prefix_file": "@cuda_native//:prefix_path.txt",
                    "providers": [
                        {"package": "cuda", "label": "@cuda_native//:lib"},
                    ],
                },
            )
            rc, doc = self.run_plan(prefixes, "--native-toolchain", str(native_toolchain))

        self.assertEqual(rc, 1)
        self.assertFalse(doc["preflight_ok"])
        self.assertIn("cuda.home", doc["env_error"])
        self.assertIn(prefixes["cuda"], doc["env_error"])

    def test_python_header_preflight_relativizes_absolute_sysconfig_path(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(Path(tmp))
            python_prefix = Path(prefixes["python"])
            python = python_prefix / "bin" / "python3"
            python.write_text(
                "#!/bin/sh\n"
                'case "$2" in\n'
                f"  *purelib*) echo lib/python{PYTHON_VERSION}/site-packages ;;\n"
                f"  *include*) echo {str(python_prefix).lstrip('/')}/include/python{PYTHON_VERSION} ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            rc, doc = self.run_plan(prefixes)

        header = next(check for check in doc["preflight"] if check["name"] == "python:headers")
        self.assertEqual(rc, 0)
        self.assertTrue(header["ok"])
        self.assertEqual(header["detail"], str(python_prefix / f"include/python{PYTHON_VERSION}/Python.h"))

    def test_execute_without_token_is_refused(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(Path(tmp))
            rc, doc = self.run_plan(prefixes, "--execute")

        self.assertEqual(rc, 2)
        self.assertFalse(doc["will_build"])
        self.assertFalse(doc["authorization"]["token_present"])
        self.assertEqual(doc["authorization"]["required_token"], plan.REQUIRED_TOKEN)

    def test_module_and_prefetch_declare_offline_pinned_source(self) -> None:
        module = module_text_with_includes()
        run_sh = RUN_SH.read_text(encoding="utf-8")

        self.assertIn('name = "torchvision_v0_29_0_source"', module)
        self.assertIn('strip_prefix = "vision-0.29.0"', module)
        self.assertIn('sha256 = "24be57d922927d8a2ac2e8f076f07c3447ddf8f1d25ddbb7b65578f36c9ab8e3"', module)
        self.assertIn("torchvision-v0.29.0.tar.gz", module)
        self.assertIn('"@torchvision_v0_29_0_source"', run_sh)
        self.assertIn("https://github.com/pytorch/vision/archive/refs/tags/v0.29.0.tar.gz", run_sh)
        self.assertIn("24be57d922927d8a2ac2e8f076f07c3447ddf8f1d25ddbb7b65578f36c9ab8e3", run_sh)
        self.assertIn('FETCH_SOURCE_FILENAME="torchvision-v0.29.0.tar.gz"', run_sh)

    def test_action_rule_and_build_file_expose_token_gated_targets(self) -> None:
        rule = ACTION_RULE.read_text(encoding="utf-8")
        build = BUILD_FILE.read_text(encoding="utf-8")

        self.assertIn("TorchvisionNativePrefixInfo = provider(", rule)
        self.assertIn('load("//native/pytorch:pytorch_action.bzl", "PytorchNativePrefixInfo")', rule)
        self.assertIn("torchvision_token_flag = rule(", rule)
        self.assertIn("build_setting = config.string(flag = True)", rule)
        self.assertIn("ctx.attr._token_flag[TorchvisionTokenInfo].value", rule)
        self.assertIn("ctx.actions.run(", rule)
        self.assertNotIn("ctx.actions.run_shell(", rule)
        self.assertIn('"torch_prefix": attr.label(', rule)
        self.assertIn('"plan": attr.label(', rule)
        self.assertIn('"source_archive": attr.label(', rule)
        self.assertIn('"rootfs_identity": attr.label(', rule)
        self.assertIn('"py_filelock_prefix_file": attr.label(', rule)
        self.assertIn("providers = [PytorchNativePrefixInfo]", rule)
        self.assertNotIn('"token": attr.string', rule)
        self.assertNotIn("--max-jobs", rule)
        self.assertIn("use_default_shell_env = False", rule)
        self.assertIn("ctx.file._executor.path", rule)
        self.assertIn("plan_executor.py", rule)
        self.assertIn("prefix_manifest.py", rule)
        self.assertNotIn("resolve_native_action_toolchain(", rule)
        self.assertNotIn("native_action_toolchain_attrs()", rule)
        self.assertNotIn("native_action_toolchains()", rule)
        self.assertIn('attrs = rootfs_platform_forward_attrs("torch")', rule)
        self.assertNotIn("native_toolchain.toolchain_json", rule)
        self.assertNotIn('"--native-toolchain"', rule)
        self.assertNotIn("VASO_TORCHVISION_MAX_JOBS", rule)

        self.assertIn('load(":torchvision_action.bzl", "torchvision_action_prefix", "torchvision_token_flag")', build)
        self.assertIn('name = "token"', build)
        self.assertIn('name = "action_driver"', build)
        self.assertNotIn('name = "max_jobs"', build)
        self.assertNotIn('name = "torchvision_action_dry_run"', build)
        self.assertNotIn('name = "action_smoke_test"', build)
        self.assertIn('name = "torchvision_action"', build)
        self.assertIn('plan = select({', build)
        self.assertIn('"//plans:torch/cu129/torchvision.plan.json"', build)
        self.assertIn('"//plans:torch/cu130/torchvision.plan.json"', build)
        self.assertIn('source_archive = "@torchvision_v0_29_0_source_archive//file"', build)
        self.assertNotIn('source_anchor = "@torchvision_v0_29_0_source//:setup.py"', build)
        self.assertNotIn('source_files = "@torchvision_v0_29_0_source//:all_srcs"', build)

        action = build_target_block("torchvision_action")
        self.assertIn('torch_prefix = "//native/pytorch:pytorch_action"', action)
        self.assertIn('inputs = {', action)
        self.assertIn('":upstream_pins.json": "native/torchvision/upstream_pins.json"', action)
        self.assertIn('py_filelock_prefix_file = "@py_filelock_native//:prefix_path.txt"', action)
        self.assertIn('py_pillow_prefix_file = "@py_pillow_native//:prefix_path.txt"', action)
        self.assertIn('libjpeg_turbo_prefix_file = "@libjpeg_turbo_native//:prefix_path.txt"', action)
        self.assertIn('libpng_prefix_file = "@libpng_native//:prefix_path.txt"', action)
        self.assertNotIn('synthetic_prefixes_for_dry_run = True', action)

    def test_action_driver_writes_only_cheap_metadata_outputs(self) -> None:
        driver = ACTION_DRIVER.read_text(encoding="utf-8")

        self.assertIn('parser.add_argument("--plan", type=Path, required=True)', driver)
        self.assertIn('parser.add_argument("--keys", type=Path, required=True)', driver)
        self.assertIn('parser.add_argument("--wheel", type=Path, required=True)', driver)
        self.assertIn("plan_merkle_key", driver)
        self.assertIn('"mechanism": "committed-plan"', driver)
        self.assertIn('"pytorch_version": pytorch_version_from_plan(plan)', driver)
        self.assertNotIn('parser.add_argument("--source-anchor"', driver)
        self.assertNotIn('parser.add_argument("--build-work-root"', driver)
        self.assertNotIn("VASO_IN_INSULA", driver)
        self.assertNotIn("TMPDIR", driver)
        self.assertNotIn("PIP_CACHE_DIR", driver)
        self.assertNotIn('parser.add_argument("--native-toolchain"', driver)
        self.assertNotIn("read_native_toolchain", driver)
        self.assertNotIn("native_toolchain_environment", driver)
        self.assertNotIn("DETERMINISTIC_ACTION_ENV_KEYS", driver)
        self.assertNotIn("def _deterministic_action_env(", driver)
        self.assertNotIn('"pip",', driver)


if __name__ == "__main__":
    unittest.main()
