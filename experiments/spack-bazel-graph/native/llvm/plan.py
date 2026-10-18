#!/usr/bin/env python3
"""Plan and preflight the native LLVM CMake build without running it."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


REQUIRED_TOKEN = "build-native-llvm"

REQUIRED_PREFIXES = (
    "cmake",
    "ninja",
    "python",
    "pkgconf",
    "perl_data_dumper",
    "hwloc",
    "libedit",
    "libffi",
    "libxml2",
    "lua",
    "ncurses",
    "swig",
    "xz",
    "zlib_ng",
)

LLVM_PROJECTS = ("lldb", "clang", "clang-tools-extra", "lld", "polly")
LLVM_RUNTIMES = ("openmp", "offload", "compiler-rt", "libcxx", "libcxxabi", "libunwind")
LLVM_TARGETS = ("AArch64", "AMDGPU", "NVPTX", "X86")
PATH_PREFIXES = ("cmake", "ninja", "python", "pkgconf", "lua", "swig")
PKG_CONFIG_PREFIXES = ("hwloc", "libedit", "libffi", "libxml2", "lua", "ncurses", "xz", "zlib_ng")
SPACK_TARGET_FLAGS = "-march=icelake-client -mtune=icelake-client"


def _bool(value: bool) -> str:
    return "ON" if value else "OFF"


def cmake_defines(prefixes: dict[str, str]) -> dict[str, str]:
    """Return the concrete Spack-derived CMake cache contract for LLVM 20.1.8."""
    for key in REQUIRED_PREFIXES:
        if key not in prefixes:
            raise SystemExit(f"missing required --prefix {key}=... input")

    return {
        "BUILD_SHARED_LIBS": _bool(False),
        "CLANG_DEFAULT_OPENMP_RUNTIME": "libomp",
        "CLANG_OPENMP_NVPTX_DEFAULT_ARCH": "sm_100",
        "CUDA_NVCC_EXECUTABLE": "IGNORE",
        "CUDA_SDK_ROOT_DIR": "IGNORE",
        "CUDA_TOOLKIT_ROOT_DIR": "IGNORE",
        "CMAKE_BUILD_TYPE": "Release",
        "CMAKE_C_COMPILER": "/usr/bin/gcc",
        "CMAKE_CXX_COMPILER": "/usr/bin/g++",
        "CMAKE_C_FLAGS": SPACK_TARGET_FLAGS,
        "CMAKE_CXX_FLAGS": SPACK_TARGET_FLAGS,
        "CMAKE_EXPORT_COMPILE_COMMANDS": _bool(True),
        "CMAKE_FIND_PACKAGE_PREFER_CONFIG": _bool(True),
        "CMAKE_FIND_USE_PACKAGE_REGISTRY": _bool(False),
        "CMAKE_FIND_USE_PACKAGE_ROOT_PATH": _bool(False),
        "CMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY": _bool(False),
        "CMAKE_FIND_USE_SYSTEM_PATH": _bool(False),
        "CMAKE_INSTALL_RPATH_USE_LINK_PATH": _bool(True),
        "CMAKE_MAKE_PROGRAM": str(Path(prefixes["ninja"]) / "bin" / "ninja-build"),
        "CMAKE_POLICY_DEFAULT_CMP0090": "NEW",
        "CMAKE_PREFIX_PATH": ";".join(prefixes[key] for key in REQUIRED_PREFIXES),
        "LIBCXXABI_USE_LLVM_UNWINDER": _bool(True),
        "LIBCXX_ENABLE_STATIC_ABI_LIBRARY": _bool(True),
        "LIBOMP_HWLOC_INSTALL_DIR": prefixes["hwloc"],
        "LIBOMP_TSAN_SUPPORT": _bool(False),
        "LIBOMP_USE_HWLOC": _bool(True),
        "LIBOMPTARGET_BUILD_AMDGPU_PLUGIN": _bool(False),
        "LIBOMPTARGET_DEP_CUDA_DRIVER_LIBRARIES": "IGNORE",
        "LIBOMPTARGET_ENABLE_DEBUG": _bool(False),
        "LLDB_CURSES_LIBS": "-lncursesw",
        "LLDB_ENABLE_CURSES": _bool(True),
        "LLDB_ENABLE_LIBEDIT": _bool(True),
        "LLDB_ENABLE_LIBXML2": _bool(True),
        "LLDB_ENABLE_LUA": _bool(True),
        "LLDB_ENABLE_LZMA": _bool(True),
        "LLDB_ENABLE_PYTHON": _bool(False),
        "LLVM_BUILD_LLVM_DYLIB": _bool(True),
        "LLVM_ENABLE_EH": _bool(True),
        "LLVM_ENABLE_LIBXML2": _bool(False),
        "LLVM_ENABLE_PROJECTS": ";".join(LLVM_PROJECTS),
        "LLVM_ENABLE_RTTI": _bool(True),
        "LLVM_ENABLE_RUNTIMES": ";".join(LLVM_RUNTIMES),
        "LLVM_ENABLE_TERMINFO": _bool(True),
        "LLVM_ENABLE_Z3_SOLVER": _bool(False),
        "LLVM_ENABLE_ZSTD": _bool(False),
        "LLVM_LINK_LLVM_DYLIB": _bool(False),
        "LLVM_REQUIRES_RTTI": _bool(True),
        "LLVM_TARGETS_TO_BUILD": ";".join(LLVM_TARGETS),
        "LLVM_USE_SPLIT_DWARF": _bool(False),
        "OPENMP_ENABLE_LIBOMPTARGET": _bool(True),
        "RUNTIMES_CMAKE_ARGS": "-DCMAKE_INSTALL_RPATH_USE_LINK_PATH=ON",
        "python_executable": str(Path(prefixes["python"]) / "bin" / "python3.14"),
    }


def execution_contract(prefixes: dict[str, str], defines: dict[str, str]) -> dict[str, object]:
    """Return the hermetic shell channels used by the eventual CMake action."""
    return {
        "source_subdir": "llvm",
        "generator": "Ninja",
        "cmake": str(Path(prefixes["cmake"]) / "bin" / "cmake"),
        "build_tool": str(Path(prefixes["ninja"]) / "bin" / "ninja-build"),
        "target_flags": SPACK_TARGET_FLAGS,
        "path_prefixes": [str(Path(prefixes[key]) / "bin") for key in PATH_PREFIXES],
        "env": {
            "CMAKE_PREFIX_PATH": defines["CMAKE_PREFIX_PATH"],
            "PKG_CONFIG": str(Path(prefixes["pkgconf"]) / "bin" / "pkgconf"),
            "PKG_CONFIG_PATH": ":".join(str(Path(prefixes[key]) / "lib" / "pkgconfig") for key in PKG_CONFIG_PREFIXES),
        },
    }


def preflight(prefixes: dict[str, str]) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    for key in REQUIRED_PREFIXES:
        p = prefixes.get(key, "")
        check(f"prefix:{key}", bool(p) and Path(p).is_dir(), p or "<unset>")

    binary_checks = {
        "cmake:binary": ("cmake", "bin/cmake"),
        "ninja:binary": ("ninja", "bin/ninja-build"),
        "python:interpreter": ("python", "bin/python3.14"),
        "pkgconf:binary": ("pkgconf", "bin/pkgconf"),
        "perl-data-dumper:module-probe": ("perl_data_dumper", "lib"),
        "lua:binary": ("lua", "bin/lua"),
        "swig:binary": ("swig", "bin/swig"),
    }
    for name, (key, rel) in binary_checks.items():
        root = prefixes.get(key, "")
        check(name, bool(root) and (Path(root) / rel).exists(), str(Path(root) / rel) if root else "<unset>")

    header_checks = {
        "hwloc:header": ("hwloc", "include/hwloc.h"),
        "libedit:header": ("libedit", "include/editline/readline.h"),
        "libffi:header": ("libffi", "include/ffi.h"),
        "libxml2:header": ("libxml2", "include/libxml2/libxml/parser.h"),
        "ncurses:header": ("ncurses", "include/ncursesw/curses.h"),
        "xz:lzma-header": ("xz", "include/lzma.h"),
        "zlib-ng:zlib-header": ("zlib_ng", "include/zlib.h"),
    }
    for name, (key, rel) in header_checks.items():
        root = prefixes.get(key, "")
        check(name, bool(root) and (Path(root) / rel).exists(), str(Path(root) / rel) if root else "<unset>")

    return checks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prefix", action="append", default=[], metavar="KEY=PATH")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--token", default=os.environ.get("VASO_NATIVE_LLVM_TOKEN", ""))
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args(argv)

    prefixes: dict[str, str] = {}
    for item in args.prefix:
        if "=" not in item:
            raise SystemExit(f"--prefix must be KEY=PATH, got {item!r}")
        key, value = item.split("=", 1)
        prefixes[key] = value

    checks = preflight(prefixes)
    try:
        defines = cmake_defines(prefixes) if prefixes else {}
        execution = execution_contract(prefixes, defines) if prefixes else {}
        env_error = None
    except SystemExit as exc:
        defines = {}
        execution = {}
        env_error = str(exc)

    token_ok = args.token == REQUIRED_TOKEN
    preflight_ok = all(item["ok"] for item in checks) and env_error is None
    plan = {
        "schema_version": 1,
        "package": "llvm",
        "version": "20.1.8",
        "source": {
            "url": "https://github.com/llvm/llvm-project/archive/refs/tags/llvmorg-20.1.8.tar.gz",
            "sha256": "a6cbad9b2243b17e87795817cfff2107d113543a12486586f8a055a2bb044963",
            "strip_prefix": "llvm-project-llvmorg-20.1.8",
            "root_cmakelists_dir": "llvm",
        },
        "generator": "Ninja",
        "cmake_defines": defines,
        "execution": execution,
        "enabled_projects": list(LLVM_PROJECTS),
        "enabled_runtimes": list(LLVM_RUNTIMES),
        "targets": list(LLVM_TARGETS),
        "input_prefixes": prefixes,
        "preflight": checks,
        "preflight_ok": preflight_ok,
        "env_error": env_error,
        "authorization": {
            "required_token": REQUIRED_TOKEN,
            "token_present": token_ok,
            "execute_requested": bool(args.execute),
        },
        "mode": "execute" if args.execute and token_ok else "dry-run",
        "will_build": bool(args.execute and token_ok and preflight_ok),
    }
    text = json.dumps(plan, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")

    if not args.execute:
        return 0 if preflight_ok else 1
    if not token_ok:
        print(f"REFUSED: full native LLVM build requires token {REQUIRED_TOKEN!r}", file=sys.stderr)
        return 2
    if not preflight_ok:
        print("REFUSED: LLVM preflight failed; not building.", file=sys.stderr)
        return 3
    print("REFUSED: LLVM execute path is not implemented in this checkpoint.", file=sys.stderr)
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
