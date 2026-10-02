#!/usr/bin/env python3
"""Plan and preflight the native py-llvmlite build without running it."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


REQUIRED_TOKEN = "build-native-py-llvmlite"
SUPPORTED_LLVM_MAJOR = "20"
SELECTED_PYTHON_ABI = "3.13"
PIP_FLAGS = [
    "-vvv",
    "--no-input",
    "--no-cache-dir",
    "--disable-pip-version-check",
    "install",
    "--no-deps",
    "--ignore-installed",
    "--no-build-isolation",
    "--no-warn-script-location",
    "--no-index",
]

REQUIRED_PREFIXES = (
    "binutils",
    "cmake",
    "llvm",
    "python",
    "python_venv",
    "py_pip",
    "py_setuptools",
    "py_wheel",
)

PATH_PREFIXES = ("cmake", "llvm", "binutils", "python", "python_venv")
PYTHONPATH_PREFIXES = ("py_pip", "py_setuptools", "py_wheel", "python_venv")


def _python_abi_segment(python_abi: str) -> str:
    return f"python{python_abi}"


def _site(prefix: str, python_abi: str) -> str:
    return str(Path(prefix) / "lib" / _python_abi_segment(python_abi) / "site-packages")


def _path(prefix: str, rel: str) -> str:
    return str(Path(prefix) / rel)


def _llvm_version(llvm_config: Path) -> str | None:
    if not llvm_config.exists():
        return None
    try:
        result = subprocess.run(
            [str(llvm_config), "--version"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip().splitlines()[0] if result.stdout.strip() else None


def _python_abi(python_prefix: str) -> str | None:
    python = Path(python_prefix) / "bin" / "python3"
    if not python.exists():
        return None
    try:
        result = subprocess.run(
            [str(python), "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            env={"PYTHONHOME": "", "PYTHONPATH": ""},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    abi = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    parts = abi.split(".")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    return abi


def _selected_python_abi(prefixes: dict[str, str]) -> str:
    abi = _python_abi(prefixes["python"])
    if not abi:
        raise SystemExit("could not derive Python ABI from python prefix")
    if abi != SELECTED_PYTHON_ABI:
        raise SystemExit(f"py-llvmlite is pinned to Python ABI {SELECTED_PYTHON_ABI}, got {abi}")
    return abi


def build_env(prefixes: dict[str, str]) -> dict[str, str]:
    for key in REQUIRED_PREFIXES:
        if key not in prefixes:
            raise SystemExit(f"missing required --prefix {key}=... input")

    python_abi = _selected_python_abi(prefixes)
    llvm_dir = _path(prefixes["llvm"], "lib/cmake/llvm")
    cmake_args = [
        f"-DLLVM_DIR:PATH={llvm_dir}",
        f"-DCMAKE_PREFIX_PATH:STRING={prefixes['llvm']}",
        "-DCMAKE_FIND_PACKAGE_PREFER_CONFIG:BOOL=ON",
        "-DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY:BOOL=OFF",
        "-DCMAKE_FIND_USE_PACKAGE_REGISTRY:BOOL=OFF",
        "-DCMAKE_FIND_USE_SYSTEM_PATH:BOOL=OFF",
        "-DCMAKE_C_COMPILER:FILEPATH=/usr/bin/gcc",
        "-DCMAKE_CXX_COMPILER:FILEPATH=/usr/bin/g++",
        "-DLLVMLITE_PACKAGE_FORMAT:STRING=wheel",
        "-DLLVMLITE_LTO:BOOL=ON",
        "-DLLVMLITE_SHARED:BOOL=OFF",
    ]
    return {
        "PATH": ":".join(_path(prefixes[key], "bin") for key in PATH_PREFIXES) + ":/usr/bin:/bin",
        "PYTHON_ABI": python_abi,
        "PYTHONHOME": "",
        "PYTHONPATH": ":".join(_site(prefixes[key], python_abi) for key in PYTHONPATH_PREFIXES),
        "LD_LIBRARY_PATH": _path(prefixes["python"], "lib") + ":" + _path(prefixes["llvm"], "lib"),
        "LLVM_CONFIG": _path(prefixes["llvm"], "bin/llvm-config"),
        "LLVM_DIR": llvm_dir,
        "CMAKE": _path(prefixes["cmake"], "bin/cmake"),
        "CMAKE_PREFIX_PATH": prefixes["llvm"],
        "CMAKE_ARGS": " ".join(cmake_args),
        "CXX_FLTO_FLAGS": "-flto -fPIC",
        "LD_FLTO_FLAGS": "-Wl,--exclude-libs=ALL",
        "CFLAGS": f"-I{_path(prefixes['python'], 'include/' + _python_abi_segment(python_abi))}",
        "CXXFLAGS": f"-fPIC -I{_path(prefixes['python'], 'include/' + _python_abi_segment(python_abi))}",
        "LDFLAGS": (
            f"-L{_path(prefixes['python'], 'lib')} "
            f"-L{_path(prefixes['llvm'], 'lib')} "
            f"-Wl,-rpath,{_path(prefixes['python'], 'lib')} "
            f"-Wl,-rpath,{_path(prefixes['llvm'], 'lib')}"
        ),
    }


def preflight(prefixes: dict[str, str]) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    for key in REQUIRED_PREFIXES:
        p = prefixes.get(key, "")
        check(f"prefix:{key}", bool(p) and Path(p).is_dir(), p or "<unset>")

    derived_python_abi = _python_abi(prefixes.get("python", ""))
    check(
        "python:abi",
        derived_python_abi == SELECTED_PYTHON_ABI,
        derived_python_abi or "<unavailable>",
    )
    python_abi = derived_python_abi or SELECTED_PYTHON_ABI
    python_abi_segment = _python_abi_segment(python_abi)
    file_checks = {
        "binutils:ld": ("binutils", "bin/ld"),
        "cmake:binary": ("cmake", "bin/cmake"),
        "llvm:config": ("llvm", "bin/llvm-config"),
        "llvm:cmake-config": ("llvm", "lib/cmake/llvm/LLVMConfig.cmake"),
        "llvm:header": ("llvm", "include/llvm/Config/llvm-config.h"),
        "python:interpreter": ("python", f"bin/{python_abi_segment}"),
        "python:headers": ("python", f"include/{python_abi_segment}/Python.h"),
        "python-venv:interpreter": ("python_venv", "bin/python3"),
        "py-pip:module": ("py_pip", f"lib/{python_abi_segment}/site-packages/pip"),
        "py-setuptools:module": ("py_setuptools", f"lib/{python_abi_segment}/site-packages/setuptools"),
        "py-wheel:module": ("py_wheel", f"lib/{python_abi_segment}/site-packages/wheel"),
    }
    for name, (key, rel) in file_checks.items():
        root = prefixes.get(key, "")
        target = Path(root) / rel if root else Path(rel)
        check(name, bool(root) and target.exists(), str(target) if root else "<unset>")

    version = _llvm_version(Path(prefixes.get("llvm", "")) / "bin" / "llvm-config")
    check(
        "llvm:version-major",
        bool(version) and version.split(".", 1)[0] == SUPPORTED_LLVM_MAJOR,
        version or "<unavailable>",
    )

    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", action="append", default=[], metavar="KEY=PATH")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--token", default=os.environ.get("VASO_NATIVE_PY_LLVMLITE_TOKEN", ""))
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)

    prefixes: dict[str, str] = {}
    for item in args.prefix:
        if "=" not in item:
            raise SystemExit(f"--prefix must be KEY=PATH, got {item!r}")
        key, value = item.split("=", 1)
        prefixes[key] = value

    checks = preflight(prefixes)
    python_abi = _python_abi(prefixes.get("python", "")) if prefixes else None
    try:
        env = build_env(prefixes) if prefixes else {}
        env_error = None
    except SystemExit as exc:
        env = {}
        env_error = str(exc)

    token_ok = args.token == REQUIRED_TOKEN
    preflight_ok = all(item["ok"] for item in checks) and env_error is None
    plan = {
        "schema_version": 1,
        "package": "py-llvmlite",
        "version": "0.47.0",
        "source": {
            "url": "https://files.pythonhosted.org/packages/source/l/llvmlite/llvmlite-0.47.0.tar.gz",
            "sha256": "62031ce968ec74e95092184d4b0e857e444f8fdff0b8f9213707699570c33ccc",
            "strip_prefix": "llvmlite-0.47.0",
        },
        "dependency_contract": {
            "llvm": {
                "required": "20.x",
                "source": "hermetic Spack py-llvmlite@0.47.0 recipe depends_on('llvm@20')",
                "provider_status": "token-gated; do not flip py-llvmlite before LLVM is native or explicitly accepted as a Spack external",
            },
            "python": {
                "required": "3.10:3.14",
                "selected": python_abi or "<unavailable>",
            },
        },
        "python_abi": python_abi,
        "input_prefixes": prefixes,
        "build_env": env,
        "env_error": env_error,
        "install": {
            "entrypoint": [str(Path(prefixes.get("python_venv", "")) / "bin" / "python3"), "-m", "pip"]
            if prefixes.get("python_venv")
            else ["<python-venv-prefix>/bin/python3", "-m", "pip"],
            "pip_flags": PIP_FLAGS,
            "prefix_arg": "--prefix=<prefix>",
            "source_arg": ".",
        },
        "emitted_prefix_layout": {
            "site_packages": f"lib/{_python_abi_segment(python_abi or SELECTED_PYTHON_ABI)}/site-packages/llvmlite",
            "native_library": f"lib/{_python_abi_segment(python_abi or SELECTED_PYTHON_ABI)}/site-packages/llvmlite/binding/libllvmlite.so",
            "metadata": f"lib/{_python_abi_segment(python_abi or SELECTED_PYTHON_ABI)}/site-packages/llvmlite-0.47.0.dist-info",
        },
        "preflight": checks,
        "preflight_ok": preflight_ok,
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
        print(
            f"REFUSED: full native py-llvmlite build requires token {REQUIRED_TOKEN!r}",
            file=sys.stderr,
        )
        return 2
    if not preflight_ok:
        print("REFUSED: py-llvmlite preflight failed; not building.", file=sys.stderr)
        return 3
    print("REFUSED: py-llvmlite execute path is not implemented in this checkpoint.", file=sys.stderr)
    return 4


if __name__ == "__main__":
    sys.exit(main())
