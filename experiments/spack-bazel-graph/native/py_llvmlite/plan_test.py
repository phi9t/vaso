#!/usr/bin/env python3
"""Tests for the native py-llvmlite dry-run planner."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("plan.py")
RULE = Path(__file__).with_name("py_llvmlite.bzl")
SPEC = importlib.util.spec_from_file_location("py_llvmlite_plan", SCRIPT)
assert SPEC is not None
plan = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = plan
SPEC.loader.exec_module(plan)


class PyLlvmLitePlanTest(unittest.TestCase):
    SELECTED_ABI = "3.13"

    def make_prefixes(self, root: Path, complete: bool = True) -> dict[str, str]:
        prefixes = {name: root / name for name in plan.REQUIRED_PREFIXES}
        for path in prefixes.values():
            path.mkdir(parents=True)
        if complete:
            files = {
                "binutils": ("bin/ld",),
                "cmake": ("bin/cmake",),
                "llvm": ("bin/llvm-config", "lib/cmake/llvm/LLVMConfig.cmake", "include/llvm/Config/llvm-config.h"),
                "python": ("bin/python3", f"bin/python{self.SELECTED_ABI}", f"include/python{self.SELECTED_ABI}/Python.h"),
                "python_venv": ("bin/python3", f"bin/python{self.SELECTED_ABI}"),
                "py_pip": (f"lib/python{self.SELECTED_ABI}/site-packages/pip/__init__.py",),
                "py_setuptools": (f"lib/python{self.SELECTED_ABI}/site-packages/setuptools/__init__.py",),
                "py_wheel": (f"lib/python{self.SELECTED_ABI}/site-packages/wheel/__init__.py",),
            }
            for key, rels in files.items():
                for rel in rels:
                    p = prefixes[key] / rel
                    p.parent.mkdir(parents=True, exist_ok=True)
                    if rel.startswith("bin/python"):
                        p.write_text(f"#!/bin/sh\necho {self.SELECTED_ABI}\n", encoding="utf-8")
                    else:
                        p.write_text("#!/bin/sh\n" if "/bin/" in rel else "", encoding="utf-8")
                    if rel.startswith("bin/"):
                        os.chmod(p, 0o755)
            llvm_config = prefixes["llvm"] / "bin" / "llvm-config"
            llvm_config.write_text("#!/bin/sh\necho 20.1.8\n", encoding="utf-8")
            os.chmod(llvm_config, 0o755)
        return {key: str(value) for key, value in prefixes.items()}

    def run_plan(self, prefixes: dict[str, str], *extra: str) -> tuple[int, dict]:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "plan.json"
            argv = ["--out", str(out)]
            for key, value in prefixes.items():
                argv.extend(["--prefix", f"{key}={value}"])
            argv.extend(extra)
            rc = plan.main(argv)
            return rc, json.loads(out.read_text(encoding="utf-8"))

    def test_complete_dry_run_records_spack_pip_and_llvm20_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefixes = self.make_prefixes(Path(tmp))
            rc, doc = self.run_plan(prefixes)

        self.assertEqual(rc, 0)
        self.assertTrue(doc["preflight_ok"])
        self.assertEqual(doc["package"], "py-llvmlite")
        self.assertEqual(doc["version"], "0.47.0")
        self.assertEqual(doc["mode"], "dry-run")
        self.assertFalse(doc["will_build"])
        self.assertEqual(doc["source"]["sha256"], "62031ce968ec74e95092184d4b0e857e444f8fdff0b8f9213707699570c33ccc")
        self.assertEqual(doc["install"]["entrypoint"], [str(Path(prefixes["python_venv"]) / "bin" / "python3"), "-m", "pip"])
        self.assertEqual(doc["install"]["pip_flags"], plan.PIP_FLAGS)
        self.assertEqual(doc["python_abi"], self.SELECTED_ABI)
        env = doc["build_env"]
        self.assertEqual(env["PYTHON_ABI"], self.SELECTED_ABI)
        self.assertEqual(env["LLVM_CONFIG"], str(Path(prefixes["llvm"]) / "bin" / "llvm-config"))
        self.assertEqual(env["LLVM_DIR"], str(Path(prefixes["llvm"]) / "lib" / "cmake" / "llvm"))
        self.assertEqual(env["CMAKE_PREFIX_PATH"], prefixes["llvm"])
        self.assertEqual(env["CMAKE"], str(Path(prefixes["cmake"]) / "bin" / "cmake"))
        self.assertIn(str(Path(prefixes["cmake"]) / "bin"), env["PATH"].split(":"))
        self.assertIn(str(Path(prefixes["llvm"]) / "bin"), env["PATH"].split(":"))
        self.assertEqual(env["CXX_FLTO_FLAGS"], "-flto -fPIC")
        self.assertEqual(env["LD_FLTO_FLAGS"], "-Wl,--exclude-libs=ALL")
        self.assertEqual(env["PYTHONHOME"], "")
        self.assertIn(str(Path(prefixes["py_pip"]) / "lib" / f"python{self.SELECTED_ABI}" / "site-packages"), env["PYTHONPATH"].split(":"))
        self.assertEqual(doc["emitted_prefix_layout"]["site_packages"], f"lib/python{self.SELECTED_ABI}/site-packages/llvmlite")
        checks = {item["name"]: item for item in doc["preflight"]}
        self.assertTrue(checks["python:abi"]["ok"])
        self.assertEqual(checks["python:abi"]["detail"], self.SELECTED_ABI)
        self.assertTrue(checks["llvm:version-major"]["ok"])
        self.assertEqual(checks["llvm:version-major"]["detail"], "20.1.8")

    def test_preflight_fails_when_llvm_or_python_surfaces_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefixes = self.make_prefixes(Path(tmp), complete=False)
            rc, doc = self.run_plan(prefixes)

        checks = {item["name"]: item for item in doc["preflight"]}
        self.assertEqual(rc, 1)
        self.assertFalse(doc["preflight_ok"])
        for name in ("llvm:config", "llvm:cmake-config", "python:headers", "py-pip:module"):
            self.assertIn(name, checks)
            self.assertFalse(checks[name]["ok"])

    def test_execute_without_token_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefixes = self.make_prefixes(Path(tmp))
            rc, doc = self.run_plan(prefixes, "--execute")

        self.assertEqual(rc, 2)
        self.assertTrue(doc["preflight_ok"])
        self.assertEqual(doc["authorization"]["required_token"], plan.REQUIRED_TOKEN)
        self.assertFalse(doc["authorization"]["token_present"])
        self.assertEqual(doc["mode"], "dry-run")
        self.assertFalse(doc["will_build"])

    def test_repository_rule_is_token_gated_and_reviewable(self) -> None:
        script = RULE.read_text(encoding="utf-8")

        self.assertIn('exports_files(["build_plan.json", "build.sh"])', script)
        self.assertIn('repository_ctx.file("build.sh", _BUILD_SH, executable = True)', script)
        self.assertIn('VASO_NATIVE_PY_LLVM LITE_TOKEN'.replace(" ", ""), script)
        self.assertIn("build-native-py-llvmlite", script)
        self.assertIn("PYTHON_ABI", script)
        self.assertIn("$PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI}", script)
        self.assertIn("$LLVM_PREFIX/bin/llvm-config", script)
        self.assertIn("--no-build-isolation", script)
        self.assertNotIn("python" + "3.14", script)
        self.assertNotIn("cpython-" + "314", script)


if __name__ == "__main__":
    unittest.main()
