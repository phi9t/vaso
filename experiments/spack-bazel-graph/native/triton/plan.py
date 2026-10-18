#!/usr/bin/env python3
"""Plan and preflight the native Triton build without running it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


REQUIRED_TOKEN = "build-native-triton"
ONE_LLVM_COMMIT = "35901313800ea6e6cbeb9226e51c7c4b29bfc40e"
REQUIRED_PREFIXES = (
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
PATH_PREFIXES = (
    "python",
    "python-venv",
    "py-pip",
    "py-wheel",
    "py-lit",
    "cmake",
    "ninja",
    "llvm",
    "cuda",
)
PYTHON_BUILD_PREFIXES = (
    "py-pip",
    "py-setuptools",
    "py-wheel",
    "py-filelock",
    "py-lit",
    "py-pybind11",
    "python-venv",
)
PATCH_CHECK_PREFIX = "patch"
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def load_pins(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _path(prefix: str, rel: str) -> str:
    return str(Path(prefix) / rel)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_patch_pairs(pins: dict[str, object], workspace_root: Path = WORKSPACE_ROOT) -> list[tuple[Path, str]]:
    llvm = pins.get("llvm", {})
    if not isinstance(llvm, dict):
        return []
    drift_patch = llvm.get("drift_patch", {})
    if not isinstance(drift_patch, dict):
        return []
    path_text = drift_patch.get("path")
    sha256 = drift_patch.get("sha256")
    if not isinstance(path_text, str) or not isinstance(sha256, str):
        return []
    patch = Path(path_text)
    if not patch.is_absolute():
        patch = workspace_root / patch
    return [(patch, sha256)]


def python_version_from_abi(python_abi: str) -> str | None:
    if python_abi.startswith("cp"):
        digits = python_abi[2:]
        if len(digits) >= 2 and digits.isdigit():
            return digits[0] + "." + digits[1:]
        return None

    parts = python_abi.split(".", 1)
    if len(parts) == 2 and all(part.isdigit() for part in parts):
        return python_abi
    return None


def python_site_packages_dir(prefixes: dict[str, str], python_abi: str) -> Path:
    version = python_version_from_abi(python_abi)
    if version:
        return Path("lib") / ("python" + version) / "site-packages"

    python_prefix = prefixes.get("python", "")
    python = Path(python_prefix) / "bin" / "python3" if python_prefix else None
    if python is not None and python.is_file() and os.access(python, os.X_OK):
        probe = (
            "import sysconfig\n"
            "path = sysconfig.get_path('purelib', vars={'base': '', 'platbase': ''})\n"
            "print(path.lstrip('/'))\n"
        )
        try:
            result = subprocess.run(
                [str(python), "-c", probe],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        else:
            site_packages = result.stdout.strip()
            if result.returncode == 0 and site_packages:
                return Path(site_packages)

    return Path("lib") / "python" / "site-packages"


def python_build_pythonpath(prefixes: dict[str, str], python_abi: str) -> list[str]:
    site_packages = python_site_packages_dir(prefixes, python_abi)
    return [str(Path(prefixes[key]) / site_packages) for key in PYTHON_BUILD_PREFIXES]


def build_env(prefixes: dict[str, str], python_abi: str = "derived") -> dict[str, str]:
    for key in REQUIRED_PREFIXES:
        if key not in prefixes:
            raise SystemExit(f"missing required --prefix {key}=... input")

    return {
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INDEX": "1",
        "PIP_NO_INPUT": "1",
        "PYTHONHOME": "",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": os.pathsep.join(python_build_pythonpath(prefixes, python_abi)),
        "TRITON_OFFLINE_BUILD": "1",
        "CC": "/usr/bin/gcc",
        "CXX": "/usr/bin/g++",
        "CMAKE_C_COMPILER": "/usr/bin/gcc",
        "CMAKE_CXX_COMPILER": "/usr/bin/g++",
        "LLVM_SYSPATH": prefixes["llvm"],
        "JSON_SYSPATH": prefixes["nlohmann_json"],
        "PYBIND11_SYSPATH": prefixes["py-pybind11"],
        "LIBRARY_PATH": _path(prefixes["zlib_ng"], "lib"),
        "LDFLAGS": "-L" + _path(prefixes["zlib_ng"], "lib"),
        "TRITON_PTXAS_PATH": _path(prefixes["cuda"], "bin/ptxas"),
        "TRITON_PTXAS_BLACKWELL_PATH": _path(prefixes["cuda"], "bin/ptxas"),
        "TRITON_CUOBJDUMP_PATH": _path(prefixes["cuda"], "bin/cuobjdump"),
        "TRITON_NVDISASM_PATH": _path(prefixes["cuda"], "bin/nvdisasm"),
        "TRITON_CUDACRT_PATH": _path(prefixes["cuda"], "include"),
        "TRITON_CUDART_PATH": _path(prefixes["cuda"], "include"),
        "TRITON_CUPTI_PATH": prefixes["cuda"],
        "TRITON_CUPTI_INCLUDE_PATH": _path(prefixes["cuda"], "include"),
        "TRITON_CUPTI_LIB_PATH": _path(prefixes["cuda"], "lib64"),
        "TRITON_CUPTI_LIB_BLACKWELL_PATH": _path(prefixes["cuda"], "lib64"),
        "TRITON_LIBDEVICE_PATH": _path(prefixes["cuda"], "nvvm/libdevice/libdevice.10.bc"),
        "CUDA_HOME": prefixes["cuda"],
        "CUDA_PATH": prefixes["cuda"],
        "CMAKE_PREFIX_PATH": os.pathsep.join(
            [
                prefixes["python"],
                prefixes["python-venv"],
                prefixes["cmake"],
                prefixes["ninja"],
                prefixes["llvm"],
                prefixes["nlohmann_json"],
                prefixes["py-pybind11"],
                prefixes["zlib_ng"],
                prefixes["cuda"],
            ]
        ),
        "PATH": os.pathsep.join(
            [_path(prefixes[key], "bin") for key in PATH_PREFIXES]
            + ["/usr/bin", "/bin"]
        ),
    }


def preflight(
    prefixes: dict[str, str],
    python_abi: str = "derived",
    patches: list[tuple[Path, str]] | None = None,
) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    for key in REQUIRED_PREFIXES:
        p = prefixes.get(key, "")
        check(f"prefix:{key}", bool(p) and Path(p).is_dir(), p or "<unset>")

    python_version = python_version_from_abi(python_abi)
    python_headers = f"include/python{python_version}/Python.h" if python_version else "include"
    site_packages = python_site_packages_dir(prefixes, python_abi) if prefixes else Path("lib/python/site-packages")
    file_checks = {
        "python:binary": ("python", "bin/python3"),
        "python:headers": ("python", python_headers),
        "python-venv:config": ("python-venv", "pyvenv.cfg"),
        "python-venv:site-packages": ("python-venv", str(site_packages)),
        "py-pip:package": ("py-pip", str(site_packages / "pip")),
        "py-setuptools:package": ("py-setuptools", str(site_packages / "setuptools")),
        "py-wheel:package": ("py-wheel", str(site_packages / "wheel")),
        "py-filelock:package": ("py-filelock", str(site_packages / "filelock")),
        "py-lit:package": ("py-lit", str(site_packages / "lit")),
        "py-pybind11:package": ("py-pybind11", str(site_packages / "pybind11")),
        "cmake:binary": ("cmake", "bin/cmake"),
        "ninja:binary": ("ninja", "bin/ninja"),
        "llvm:llvm-config": ("llvm", "bin/llvm-config"),
        "llvm:config-header": ("llvm", "include/llvm/Config/llvm-config.h"),
        "llvm:clang": ("llvm", "bin/clang"),
        "llvm:ld.lld": ("llvm", "bin/ld.lld"),
        "llvm:filecheck": ("llvm", "bin/FileCheck"),
        "llvm:cmake-config": ("llvm", "lib/cmake/llvm/LLVMConfig.cmake"),
        "llvm:mlir-cmake-config": ("llvm", "lib/cmake/mlir/MLIRConfig.cmake"),
        "llvm:lld-cmake-config": ("llvm", "lib/cmake/lld/LLDConfig.cmake"),
        "nlohmann-json:header": ("nlohmann_json", "include/nlohmann/json.hpp"),
        "cuda:ptxas": ("cuda", "bin/ptxas"),
        "cuda:nvdisasm": ("cuda", "bin/nvdisasm"),
        "cuda:cuobjdump": ("cuda", "bin/cuobjdump"),
        "cuda:header": ("cuda", "include/cuda.h"),
        "cuda:cupti-lib": ("cuda", "lib64/libcupti.so"),
        "cuda:libdevice": ("cuda", "nvvm/libdevice/libdevice.10.bc"),
        "zlib-ng:header": ("zlib_ng", "include/zlib.h"),
        "zlib-ng:lib": ("zlib_ng", "lib/libz.so"),
    }
    for name, (key, rel) in file_checks.items():
        root = prefixes.get(key, "")
        target = Path(root) / rel if root else Path(rel)
        check(name, bool(root) and target.exists(), str(target) if root else "<unset>")

    for patch, expected_sha256 in patches or []:
        check_name = f"{PATCH_CHECK_PREFIX}:{patch.name}"
        if not patch.is_file():
            check(check_name, False, f"Triton patch file is missing: {patch}")
            continue
        actual_sha256 = file_sha256(patch)
        check(
            check_name,
            actual_sha256 == expected_sha256,
            f"{patch} sha256={actual_sha256}; expected={expected_sha256}",
        )

    return checks


def plan_document(
    prefixes: dict[str, str],
    pins: dict[str, object],
    execute: bool,
    token: str,
    python_abi: str = "derived",
    patches: list[tuple[Path, str]] | None = None,
) -> dict[str, object]:
    effective_patches = default_patch_pairs(pins) if patches is None else patches
    checks = preflight(prefixes, python_abi, effective_patches)
    try:
        env = build_env(prefixes, python_abi) if prefixes else {}
        env_error = None
    except SystemExit as exc:
        env = {}
        env_error = str(exc)

    token_ok = token == REQUIRED_TOKEN
    preflight_ok = all(item["ok"] for item in checks) and env_error is None
    llvm = pins["llvm"]
    triton = pins["triton"]
    return {
        "schema_version": 1,
        "package": "py-triton",
        "version": triton["version"],
        "pytorch_version": pins["pytorch"]["version"],
        "python_abi": python_abi,
        "required_prefixes": list(REQUIRED_PREFIXES),
        "source": {
            "repo": "@triton_v2_14_0_source",
            "commit": triton["commit"],
            "archive_sha256": triton["archive_sha256"],
        },
        "llvm": {
            "provider": "llvm",
            "prefix_key": "llvm",
            "rootfs_prefix": llvm["selected_prefix"],
            "required_source_commit": llvm["selected_source_commit"],
            "source_archive_sha256": llvm["selected_source_archive_sha256"],
            "version": llvm["selected_version"],
            "upstream_triton_source_commit": llvm["source_commit"],
            "upstream_triton_source_archive_sha256": llvm["source_archive_sha256"],
            "published_build_info_commit": llvm["published_build_info_commit"],
            "build_info_repository": llvm["build_info_repository"],
            "build_number": llvm["build_number"],
            "reuse_spack_llvm_20_1_8": llvm["reuse_spack_llvm_20_1_8"],
            "drift_patch": llvm["drift_patch"],
            "audit_note": (
                "Triton v2.14 records its original LLVM source in "
                "cmake/llvm-info.json, but the native provider builds against "
                "the shared rootfs llvm-project 35901313 with the checked-in "
                "LLVM-drift patch."
            ),
        },
        "nlohmann_json": {
            "provider": "nlohmann_json",
            "version": pins["nlohmann_json"]["version"],
            "provider_status": pins["nlohmann_json"]["provider_status"],
        },
        "input_prefixes": prefixes,
        "patches": [
            {"path": str(path), "sha256": sha256}
            for path, sha256 in effective_patches
        ],
        "build_env": env,
        "env_error": env_error,
        "preflight": checks,
        "preflight_ok": preflight_ok,
        "authorization": {
            "required_token": REQUIRED_TOKEN,
            "token_present": token_ok,
            "execute_requested": bool(execute),
        },
        "mode": "execute" if execute and token_ok else "dry-run",
        "will_build": bool(execute and token_ok and preflight_ok),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", action="append", default=[], metavar="KEY=PATH")
    parser.add_argument("--pins", type=Path, default=Path(__file__).with_name("upstream_pins.json"))
    parser.add_argument("--out", type=Path)
    parser.add_argument("--token", default=os.environ.get("VASO_NATIVE_TRITON_TOKEN", ""))
    parser.add_argument("--python-abi", default="derived")
    parser.add_argument("--patch-file", action="append", default=[])
    parser.add_argument("--patch-sha256", action="append", default=[])
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)

    prefixes: dict[str, str] = {}
    for item in args.prefix:
        if "=" not in item:
            raise SystemExit(f"--prefix must be KEY=PATH, got {item!r}")
        key, value = item.split("=", 1)
        prefixes[key] = value
    if len(args.patch_file) != len(args.patch_sha256):
        raise SystemExit("--patch-file and --patch-sha256 must be supplied the same number of times")
    patches = None
    if args.patch_file or args.patch_sha256:
        patches = [
            (Path(patch), sha256)
            for patch, sha256 in zip(args.patch_file, args.patch_sha256)
        ]

    doc = plan_document(prefixes, load_pins(args.pins), args.execute, args.token, args.python_abi, patches)
    text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    if args.out is not None:
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")

    if not args.execute:
        return 0 if doc["preflight_ok"] else 1
    if not doc["authorization"]["token_present"]:
        print(
            f"REFUSED: full native Triton build requires token {REQUIRED_TOKEN!r}; staying dry-run.",
            file=sys.stderr,
        )
        return 2
    if not doc["preflight_ok"]:
        print("REFUSED: Triton preflight failed; not building.", file=sys.stderr)
        return 3

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
