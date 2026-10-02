#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("plan.py")
RULE = Path(__file__).with_name("llvm.bzl")
SPEC = importlib.util.spec_from_file_location("llvm_plan", SCRIPT)
assert SPEC is not None
plan = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = plan
SPEC.loader.exec_module(plan)


class LlvmPlanTest(unittest.TestCase):
    def make_prefixes(self, root: Path, complete: bool = True) -> dict[str, str]:
        prefixes = {name: root / name for name in plan.REQUIRED_PREFIXES}
        for path in prefixes.values():
            path.mkdir(parents=True)
        if complete:
            files = {
                "cmake": ("bin/cmake",),
                "ninja": ("bin/ninja-build",),
                "python": ("bin/python3.14",),
                "pkgconf": ("bin/pkgconf",),
                "perl_data_dumper": ("lib/perl5/Data/Dumper.pm",),
                "lua": ("bin/lua",),
                "swig": ("bin/swig",),
                "hwloc": ("include/hwloc.h",),
                "libedit": ("include/editline/readline.h",),
                "libffi": ("include/ffi.h",),
                "libxml2": ("include/libxml2/libxml/parser.h",),
                "ncurses": ("include/ncursesw/curses.h",),
                "xz": ("include/lzma.h",),
                "zlib_ng": ("include/zlib.h",),
            }
            for key, rels in files.items():
                for rel in rels:
                    p = prefixes[key] / rel
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text("#!/bin/sh\n" if "/bin/" in rel else "", encoding="utf-8")
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

    def test_complete_dry_run_emits_spack_llvm_interface(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefixes = self.make_prefixes(Path(tmp))
            rc, doc = self.run_plan(prefixes)

        self.assertEqual(rc, 0)
        self.assertTrue(doc["preflight_ok"])
        self.assertEqual(doc["mode"], "dry-run")
        self.assertFalse(doc["will_build"])
        self.assertEqual(doc["enabled_projects"], ["lldb", "clang", "clang-tools-extra", "lld", "polly"])
        self.assertEqual(
            doc["enabled_runtimes"],
            ["openmp", "offload", "compiler-rt", "libcxx", "libcxxabi", "libunwind"],
        )
        self.assertEqual(doc["targets"], ["AArch64", "AMDGPU", "NVPTX", "X86"])
        defines = doc["cmake_defines"]
        self.assertEqual(defines["LLVM_ENABLE_PROJECTS"], "lldb;clang;clang-tools-extra;lld;polly")
        self.assertEqual(defines["LLVM_ENABLE_RUNTIMES"], "openmp;offload;compiler-rt;libcxx;libcxxabi;libunwind")
        self.assertEqual(defines["LLVM_TARGETS_TO_BUILD"], "AArch64;AMDGPU;NVPTX;X86")
        self.assertEqual(defines["LLVM_ENABLE_ZSTD"], "OFF")
        self.assertEqual(defines["LLDB_ENABLE_LUA"], "ON")
        self.assertEqual(defines["LLDB_ENABLE_PYTHON"], "OFF")
        self.assertEqual(defines["CUDA_TOOLKIT_ROOT_DIR"], "IGNORE")
        self.assertEqual(defines["LIBOMPTARGET_BUILD_AMDGPU_PLUGIN"], "OFF")
        self.assertEqual(defines["CMAKE_C_COMPILER"], "/usr/bin/gcc")
        self.assertEqual(defines["CMAKE_CXX_COMPILER"], "/usr/bin/g++")
        self.assertEqual(defines["CMAKE_C_FLAGS"], "-march=icelake-client -mtune=icelake-client")
        self.assertEqual(defines["CMAKE_CXX_FLAGS"], "-march=icelake-client -mtune=icelake-client")
        execution = doc["execution"]
        self.assertEqual(execution["source_subdir"], "llvm")
        self.assertEqual(execution["build_tool"], str(Path(prefixes["ninja"]) / "bin" / "ninja-build"))
        self.assertEqual(execution["cmake"], str(Path(prefixes["cmake"]) / "bin" / "cmake"))
        self.assertEqual(execution["generator"], "Ninja")
        self.assertEqual(execution["target_flags"], "-march=icelake-client -mtune=icelake-client")
        self.assertEqual(execution["env"]["PKG_CONFIG"], str(Path(prefixes["pkgconf"]) / "bin" / "pkgconf"))
        self.assertEqual(execution["env"]["CMAKE_PREFIX_PATH"], defines["CMAKE_PREFIX_PATH"])
        self.assertEqual(
            execution["env"]["PKG_CONFIG_PATH"],
            ":".join(
                str(Path(prefixes[key]) / "lib" / "pkgconfig")
                for key in ("hwloc", "libedit", "libffi", "libxml2", "lua", "ncurses", "xz", "zlib_ng")
            ),
        )
        self.assertEqual(
            execution["path_prefixes"],
            [
                str(Path(prefixes[key]) / "bin")
                for key in ("cmake", "ninja", "python", "pkgconf", "lua", "swig")
            ],
        )

    def test_build_script_carries_every_planned_cmake_define(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefixes = self.make_prefixes(Path(tmp))
            defines = plan.cmake_defines(prefixes)

        script = RULE.read_text(encoding="utf-8")
        shell_values = {
            "CMAKE_MAKE_PROGRAM": "$NINJA",
            "CMAKE_PREFIX_PATH": "${prefix_path}",
            "CMAKE_C_FLAGS": "${spack_target_flags}",
            "CMAKE_CXX_FLAGS": "${spack_target_flags}",
            "LIBOMP_HWLOC_INSTALL_DIR": "${HWLOC_PREFIX}",
            "python_executable": "${PYTHON_PREFIX}/bin/python3.14",
        }
        for key, value in defines.items():
            expected = shell_values.get(key, value)
            pattern = r"-D{}(?::[A-Za-z0-9_]+)?=\"?{}\"?".format(re.escape(key), re.escape(expected))
            self.assertRegex(script, pattern, key)

    def test_repository_dry_run_exports_reviewable_build_script(self) -> None:
        script = RULE.read_text(encoding="utf-8")

        self.assertIn('exports_files(["build_plan.json", "build.sh"])', script)
        self.assertIn('repository_ctx.file("build.sh", _BUILD_SH, executable = True)', script)
        self.assertLess(
            script.index('repository_ctx.file("build.sh", _BUILD_SH, executable = True)'),
            script.index('if not authorized:'),
        )

    def test_preflight_fails_when_prefix_surfaces_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefixes = self.make_prefixes(Path(tmp), complete=False)
            rc, doc = self.run_plan(prefixes)

        checks = {item["name"]: item for item in doc["preflight"]}
        self.assertEqual(rc, 1)
        self.assertFalse(doc["preflight_ok"])
        for name in ("cmake:binary", "swig:binary", "hwloc:header", "zlib-ng:zlib-header"):
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


if __name__ == "__main__":
    unittest.main()
