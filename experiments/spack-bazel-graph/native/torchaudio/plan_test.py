#!/usr/bin/env python3
"""Tests for the native torchaudio source/action plan."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("plan.py")
ACTION_RULE = Path(__file__).with_name("torchaudio_action.bzl")
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
    "py-filelock",
    "cuda",
    "ninja",
)

SPEC = importlib.util.spec_from_file_location("torchaudio_plan", SCRIPT)
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
    call_start = build.rfind("torchaudio_action_prefix(", 0, name_index)
    call_end = build.index("\n)", name_index) + 2
    return build[call_start:call_end]


class TorchaudioPlanTest(unittest.TestCase):
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
                "py-filelock": (str(SITE_PACKAGES / "filelock/__init__.py"),),
                "cuda": ("bin/nvcc", "include/cuda.h", "lib64/libcudart.so"),
                "ninja": ("bin/ninja",),
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
            argv.extend(extra)
            rc = plan.main(argv)
            return rc, json.loads(out.read_text(encoding="utf-8"))

    def test_manifest_pins_torchaudio_compatibility_release_for_torch_214(self) -> None:
        pins = plan.load_pins(PINS)

        self.assertEqual(pins["pytorch"]["version"], "2.14.0")
        self.assertEqual(pins["torchaudio"]["version"], "2.11.0")
        self.assertEqual(pins["torchaudio"]["source_url"], "https://github.com/pytorch/audio/archive/refs/tags/v2.11.0.tar.gz")
        self.assertEqual(pins["torchaudio"]["archive_sha256"], "599ec24e7e1eef476ef21f0178e33da00e2434f930ba42e9cc20bf4002220486")
        self.assertIn("future torch release", pins["torchaudio"]["torch_compatibility_note"])

    def test_plan_env_wires_cuda_extension_without_audio_io_backends(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(Path(tmp))
            rc, doc = self.run_plan(prefixes, "--max-jobs", "5")

        self.assertEqual(rc, 0)
        self.assertTrue(doc["preflight_ok"])
        self.assertFalse(doc["will_build"])
        self.assertEqual(doc["package"], "py-torchaudio")
        self.assertEqual(doc["version"], "2.11.0")
        self.assertEqual(doc["pytorch_version"], "2.14.0")
        self.assertEqual(tuple(doc["required_prefixes"]), EXPECTED_REQUIRED_PREFIXES)
        env = doc["build_env"]
        self.assertEqual(env["BUILD_VERSION"], "2.11.0")
        self.assertEqual(env["USE_CUDA"], "1")
        self.assertEqual(env["BUILD_RNNT"], "1")
        self.assertEqual(env["BUILD_ALIGN"], "1")
        self.assertEqual(env["BUILD_CUDA_CTC_DECODER"], "1")
        self.assertEqual(env["TORCH_CUDA_ARCH_LIST"], "10.0")
        self.assertEqual(env["MAX_JOBS"], "5")
        self.assertEqual(env["CMAKE_BUILD_PARALLEL_LEVEL"], "5")
        self.assertEqual(env["CUDA_HOME"], prefixes["cuda"])
        self.assertIn(str(Path(prefixes["ninja"]) / "bin"), env["PATH"].split(os.pathsep))
        pythonpath = env["PYTHONPATH"].split(os.pathsep)
        self.assertEqual(pythonpath[0], str(Path(prefixes["torch"]) / SITE_PACKAGES))
        self.assertIn(str(Path(prefixes["py-filelock"]) / SITE_PACKAGES), pythonpath)
        decisions = doc["dependency_decisions"]
        self.assertEqual(decisions["ffmpeg"]["status"], "off-with-reason")
        self.assertEqual(decisions["sox"]["status"], "off-with-reason")
        self.assertEqual(decisions["sndfile"]["status"], "off-with-reason")

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
        module = MODULE.read_text(encoding="utf-8")
        run_sh = RUN_SH.read_text(encoding="utf-8")

        self.assertIn('name = "torchaudio_v2_11_0_source"', module)
        self.assertIn('strip_prefix = "audio-2.11.0"', module)
        self.assertIn('sha256 = "599ec24e7e1eef476ef21f0178e33da00e2434f930ba42e9cc20bf4002220486"', module)
        self.assertIn("torchaudio-v2.11.0.tar.gz", module)
        self.assertIn('"@torchaudio_v2_11_0_source"', run_sh)
        self.assertIn("https://github.com/pytorch/audio/archive/refs/tags/v2.11.0.tar.gz", run_sh)
        self.assertIn("599ec24e7e1eef476ef21f0178e33da00e2434f930ba42e9cc20bf4002220486", run_sh)
        self.assertIn('FETCH_SOURCE_FILENAME="torchaudio-v2.11.0.tar.gz"', run_sh)

    def test_action_rule_and_build_file_expose_token_gated_targets(self) -> None:
        rule = ACTION_RULE.read_text(encoding="utf-8")
        build = BUILD_FILE.read_text(encoding="utf-8")

        self.assertIn("TorchaudioNativePrefixInfo = provider(", rule)
        self.assertIn('load("//native/pytorch:pytorch_action.bzl", "PytorchNativePrefixInfo")', rule)
        self.assertIn("torchaudio_token_flag = rule(", rule)
        self.assertIn("torchaudio_max_jobs_flag = rule(", rule)
        self.assertIn("build_setting = config.string(flag = True)", rule)
        self.assertIn("build_setting = config.int(flag = True)", rule)
        self.assertIn("ctx.attr._token_flag[TorchaudioTokenInfo].value", rule)
        self.assertIn("ctx.attr._max_jobs_flag[TorchaudioMaxJobsInfo].value", rule)
        self.assertIn('"torch_prefix": attr.label(', rule)
        self.assertIn('"py_filelock_prefix_file": attr.label(', rule)
        self.assertIn("providers = [PytorchNativePrefixInfo]", rule)
        self.assertNotIn('"token": attr.string', rule)
        self.assertIn("--max-jobs", rule)
        self.assertIn("use_default_shell_env = True", rule)

        self.assertIn('load(":torchaudio_action.bzl", "torchaudio_action_prefix", "torchaudio_max_jobs_flag", "torchaudio_token_flag")', build)
        self.assertIn('name = "token"', build)
        self.assertIn('name = "max_jobs"', build)
        self.assertIn('name = "action_driver"', build)
        self.assertIn('name = "torchaudio_action_dry_run"', build)
        self.assertIn('name = "torchaudio_action"', build)
        self.assertIn('name = "action_smoke_test"', build)

        dry_run = build_target_block("torchaudio_action_dry_run")
        self.assertIn('torch_prefix = "//native/pytorch:pytorch_action_dry_run"', dry_run)
        self.assertIn('execute = False', dry_run)
        self.assertIn('synthetic_prefixes_for_dry_run = True', dry_run)

        action = build_target_block("torchaudio_action")
        self.assertIn('torch_prefix = "//native/pytorch:pytorch_action"', action)
        self.assertIn('source_anchor = "@torchaudio_v2_11_0_source//:setup.py"', action)
        self.assertIn('source_files = "@torchaudio_v2_11_0_source//:all_srcs"', action)
        self.assertIn('py_filelock_prefix_file = "@py_filelock_native//:prefix_path.txt"', action)
        self.assertNotIn('py_pillow_prefix_file', action)
        self.assertNotIn('synthetic_prefixes_for_dry_run = True', action)

    def test_action_driver_uses_resumable_estate_workdir_and_offline_pip(self) -> None:
        driver = ACTION_DRIVER.read_text(encoding="utf-8")

        self.assertIn('parser.add_argument("--build-work-root", default="")', driver)
        self.assertIn('Path(vaso_home) / "lines" / line / "work" / "torchaudio"', driver)
        self.assertIn("VASO_IN_INSULA", driver)
        self.assertIn("VASO_ROOTFS_BUNDLE_MANIFEST", driver)
        self.assertIn("TMPDIR", driver)
        self.assertIn("PIP_NO_INDEX", driver)
        self.assertRegex(driver, r'"pip",\s*"wheel"')
        self.assertRegex(driver, r'"pip",\s*"install"')


if __name__ == "__main__":
    unittest.main()
