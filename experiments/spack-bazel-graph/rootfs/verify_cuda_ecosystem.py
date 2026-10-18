#!/usr/bin/env python3
"""Verify a CUDA ecosystem rootfs against cuda_ecosystem.lock.json."""

from __future__ import annotations

import argparse
import ctypes
import fnmatch
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


DEFINE_RE = re.compile(r"^\s*#\s*define\s+([A-Za-z0-9_]+)\s+([A-Za-z0-9_]+)(?:[uUlL]*)\b")
FORBIDDEN_DRIVER_NAMES = {
    "libcuda.so",
    "libcuda.so.1",
    "libnvidia-ml.so",
    "libnvidia-ml.so.1",
}
FORBIDDEN_PIP_NAMES = {"torch", "triton", "jax", "jaxlib"}
CUDA_LIBRARY_STEMS = {
    "libcudart",
    "libcublas",
    "libcublasLt",
    "libcufft",
    "libcurand",
    "libcusolver",
    "libcusparse",
    "libcusparseLt",
    "libcudss",
    "libnccl",
    "libnvJitLink",
    "libnvrtc",
    "libnvToolsExt",
    "libnvshmem_host",
    "libnvinfer",
    "libnvinfer_dispatch",
    "libnvinfer_lean",
    "libnvinfer_plugin",
    "libnvinfer_vc_plugin",
    "libnvonnxparser",
    "libnvonnxparsers",
}
LLVM_ARTIFACT_PATTERNS = ("libLLVM*", "libMLIR*")
LLVM_REQUIRED_ARTIFACTS = (
    "bin/clang",
    "bin/clang++",
    "bin/ld.lld",
    "bin/mlir-tblgen",
    "lib/cmake/llvm/LLVMConfig.cmake",
    "lib/cmake/mlir/MLIRConfig.cmake",
)
ROOTFS_SCAN_EXCLUDED_DIRS = (
    ("dev",),
    ("home", "kvothe"),
    ("proc",),
    ("run",),
    ("sys",),
    ("tmp",),
    ("var", "tmp"),
    ("vaso",),
    ("workspace",),
    ("opt", "vaso"),
)
MERGED_USR_ALIAS_TOPLEVELS = {"bin", "lib", "lib64", "sbin"}


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    component: str | None = None
    expected: Any | None = None
    actual: Any | None = None
    path: str | None = None

    def to_json(self) -> dict[str, Any]:
        data = {
            "code": self.code,
            "message": self.message,
        }
        for key in ("component", "expected", "actual", "path"):
            value = getattr(self, key)
            if value is not None:
                data[key] = value
        return data


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def read_macros(*paths: Path) -> dict[str, int]:
    raw: dict[str, str] = {}
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            match = DEFINE_RE.match(line)
            if match:
                raw[match.group(1)] = match.group(2)

    def resolve(name: str, stack: set[str] | None = None) -> int | None:
        stack = stack or set()
        if name in stack:
            return None
        value = raw.get(name)
        if value is None:
            return None
        if value.isdigit():
            return int(value)
        stack.add(name)
        return resolve(value, stack)

    macros: dict[str, int] = {}
    for name in raw:
        value = resolve(name)
        if value is not None:
            macros[name] = value
    return macros


def dotted(parts: list[int | None]) -> str | None:
    if any(part is None for part in parts):
        return None
    return ".".join(str(part) for part in parts)


def parse_version(version: str) -> list[int]:
    return [int(part) for part in version.split(".")]


def version_matches(actual: str | None, expected: str) -> bool:
    if actual is None:
        return False
    if actual == expected:
        return True
    # Some NVIDIA headers expose only major.minor.patch, while package pins
    # carry an extra build component.
    return expected.startswith(actual + ".")


def decode_cuda_version(value: int | None) -> str | None:
    if value is None:
        return None
    major = value // 1000
    minor = (value % 1000) // 10
    return f"{major}.{minor}"


def normalize_project_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def dist_name(path: Path) -> str:
    metadata = path / "METADATA"
    if metadata.exists():
        for line in metadata.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.lower().startswith("name:"):
                return normalize_project_name(line.split(":", 1)[1].strip())
    name = path.name
    for suffix in (".dist-info", ".egg-info"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return normalize_project_name(name)


def resolve_rootfs_path(rootfs: Path, path: Path | str) -> Path:
    """Resolve symlinks as they would resolve from inside rootfs."""
    rootfs = rootfs.resolve()
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            parts = list(candidate.relative_to(rootfs).parts)
        except ValueError:
            parts = list(candidate.parts[1:])
    else:
        parts = list(candidate.parts)

    current = rootfs
    seen = 0
    while parts:
        part = parts.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if current != rootfs:
                current = current.parent
            continue

        current = current / part
        while current.is_symlink():
            seen += 1
            if seen > 64:
                raise RuntimeError(f"too many symlinks while resolving {path}")
            target = Path(os.readlink(current))
            if target.is_absolute():
                current = rootfs
                parts = list(target.parts[1:]) + parts
            else:
                current = current.parent
                parts = list(target.parts) + parts
    return current


def run_command(argv: list[str], *, env: dict[str, str] | None = None) -> str | None:
    try:
        return subprocess.check_output(argv, text=True, stderr=subprocess.STDOUT, env=env).strip()
    except Exception:
        return None


def _inner_rootfs_argv(rootfs: Path, argv: list[str]) -> list[str] | None:
    if not argv:
        return None
    rootfs = rootfs.resolve()
    inner = list(argv)
    executable = Path(inner[0])
    if not executable.is_absolute():
        return inner
    try:
        inner[0] = "/" + executable.resolve().relative_to(rootfs).as_posix()
    except ValueError:
        return None
    return inner


def run_rootfs_command(rootfs: Path, argv: list[str]) -> str | None:
    direct = run_command(argv)
    if direct is not None:
        return direct

    bwrap = shutil_which("bwrap")
    inner = _inner_rootfs_argv(rootfs, argv)
    if not bwrap or inner is None:
        return None

    return run_command([
        bwrap,
        "--die-with-parent",
        "--unshare-user",
        "--uid",
        str(os.getuid()),
        "--gid",
        str(os.getgid()),
        "--unshare-uts",
        "--clearenv",
        "--ro-bind",
        str(rootfs),
        "/",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/vaso/tmp",
        "--tmpfs",
        "/run",
        "--setenv",
        "TMPDIR",
        "/vaso/tmp",
        "--setenv",
        "PATH",
        "/usr/lib/llvm-23/bin:/usr/local/cuda/bin:/usr/bin:/bin",
        "--setenv",
        "LD_LIBRARY_PATH",
        "/usr/lib/llvm-23/lib:/usr/local/cuda/lib64:/usr/lib/x86_64-linux-gnu",
        "--",
        *inner,
    ])


def read_llvm_build_version_output(prefix: Path) -> str | None:
    metadata = prefix / ".vaso-llvm-build.json"
    if not metadata.exists():
        return None
    try:
        data = load_json(metadata)
    except Exception:
        return None
    output = data.get("clang_version_output")
    return output if isinstance(output, str) and output else None


def parse_dpkg_status(rootfs: Path) -> dict[str, str]:
    status = rootfs / "var/lib/dpkg/status"
    if not status.exists():
        return {}
    packages: dict[str, str] = {}
    current: dict[str, str] = {}
    for line in status.read_text(encoding="utf-8", errors="ignore").splitlines() + [""]:
        if not line:
            name = current.get("Package")
            version = current.get("Version")
            status_value = current.get("Status", "")
            if name and version and status_value.endswith("ok installed"):
                packages[name] = version
            current = {}
            continue
        if line[0].isspace() or ": " not in line:
            continue
        key, value = line.split(": ", 1)
        current[key] = value
    return packages


def is_excluded_rootfs_scan_path(rootfs: Path, path: Path) -> bool:
    try:
        rel = path.relative_to(rootfs)
    except ValueError:
        return False
    parts = rel.parts
    return any(parts[: len(excluded)] == excluded for excluded in ROOTFS_SCAN_EXCLUDED_DIRS)


def file_identity(path: Path) -> tuple[int, int] | None:
    try:
        stat_result = path.stat()
    except OSError:
        return None
    return (stat_result.st_dev, stat_result.st_ino)


class StaticVerifier:
    def __init__(self, rootfs: Path, lock: dict[str, Any], line: str) -> None:
        self.rootfs = rootfs
        self.lock = lock
        self.line = line
        self.line_lock = lock["lines"][line]
        self.components_lock = self.line_lock["components"]
        self.global_components_lock = lock.get("components", {})
        self.cuda = resolve_rootfs_path(rootfs, "/usr/local/cuda")
        self.include = resolve_rootfs_path(rootfs, self.cuda / "include")
        self.lib64 = resolve_rootfs_path(rootfs, self.cuda / "lib64")
        self.components: dict[str, Any] = {}
        self.issues: list[Issue] = []

    def verify(self) -> tuple[dict[str, Any], list[Issue]]:
        self.check_cuda_toolkit()
        self.check_cudnn()
        self.check_cusparselt()
        self.check_cudss()
        self.check_nvshmem()
        self.check_nccl()
        self.check_tensorrt()
        self.check_llvm()
        self.check_single_copy()
        self.check_driver_userspace()
        self.check_pip_distributions()
        return self.components, self.issues

    def add_issue(
        self,
        code: str,
        message: str,
        *,
        component: str | None = None,
        expected: Any | None = None,
        actual: Any | None = None,
        path: Path | str | None = None,
    ) -> None:
        self.issues.append(
            Issue(
                code=code,
                message=message,
                component=component,
                expected=expected,
                actual=actual,
                path=str(path) if path is not None else None,
            )
        )

    def record_version(self, component: str, actual: str | None, expected: str, detail: dict[str, Any]) -> None:
        ok = version_matches(actual, expected)
        detail.update({"expected": expected, "actual": actual, "ok": ok})
        self.components.setdefault(component, {})["static_version"] = detail
        if not ok:
            self.add_issue(
                f"{component}.static_version",
                f"{component} static version mismatch",
                component=component,
                expected=expected,
                actual=actual,
            )

    def check_cuda_toolkit(self) -> None:
        component = self.components_lock["cuda_toolkit"]
        expected = component["version"]
        macros = read_macros(self.include / "cuda.h")
        cuda_header = decode_cuda_version(macros.get("CUDA_VERSION"))
        expected_major_minor = ".".join(expected.split(".")[:2])
        nvcc_path = resolve_rootfs_path(self.rootfs, "/usr/local/cuda/bin/nvcc")
        nvcc_out = run_rootfs_command(self.rootfs, [str(nvcc_path), "--version"]) if nvcc_path.exists() else None
        actual_nvcc = None
        if nvcc_out:
            match = re.search(r"release\s+[^,]+,\s+V([0-9.]+)", nvcc_out)
            if match:
                actual_nvcc = match.group(1)
        if actual_nvcc is None:
            versions_path = resolve_rootfs_path(self.rootfs, "/usr/local/cuda/vaso-cuda-versions.json")
            if versions_path.exists():
                versions = load_json(versions_path)
                actual_nvcc = (
                    versions.get("components", {})
                    .get("cuda_toolkit", {})
                    .get("nvcc")
                )
        expected_nvcc = component["verify"]["expect"]["nvcc"]
        ok = cuda_header == expected_major_minor and actual_nvcc == expected_nvcc
        self.components["cuda_toolkit"] = {
            "static_version": {
                "expected": expected,
                "expected_nvcc": expected_nvcc,
                "cuda_header": cuda_header,
                "nvcc": actual_nvcc,
                "ok": ok,
            }
        }
        if not ok:
            self.add_issue(
                "cuda_toolkit.static_version",
                "CUDA toolkit static version mismatch",
                component="cuda_toolkit",
                expected={"cuda": expected_major_minor, "nvcc": expected_nvcc},
                actual={"cuda": cuda_header, "nvcc": actual_nvcc},
            )

    def check_cudnn(self) -> None:
        macros = read_macros(self.include / "cudnn_version.h")
        actual = dotted([
            macros.get("CUDNN_MAJOR"),
            macros.get("CUDNN_MINOR"),
            macros.get("CUDNN_PATCHLEVEL"),
            macros.get("CUDNN_BUILD_VERSION"),
        ])
        if actual is None:
            actual = dotted([
                macros.get("CUDNN_MAJOR"),
                macros.get("CUDNN_MINOR"),
                macros.get("CUDNN_PATCHLEVEL"),
            ])
        self.record_version("cudnn", actual, self.components_lock["cudnn"]["version"], {"source": "cudnn_version.h"})

    def check_cusparselt(self) -> None:
        macros = read_macros(self.include / "cusparseLt.h")
        actual = dotted([
            macros.get("CUSPARSELT_VER_MAJOR"),
            macros.get("CUSPARSELT_VER_MINOR"),
            macros.get("CUSPARSELT_VER_PATCH"),
            macros.get("CUSPARSELT_VER_BUILD"),
        ])
        if actual is None and "CUSPARSELT_VERSION" in macros:
            actual = str(macros["CUSPARSELT_VERSION"])
        self.record_version("cusparselt", actual, self.components_lock["cusparselt"]["version"], {"source": "cusparseLt.h"})

    def check_cudss(self) -> None:
        macros = read_macros(self.include / "cudss.h")
        actual = dotted([
            macros.get("CUDSS_VERSION_MAJOR", macros.get("CUDSS_VER_MAJOR")),
            macros.get("CUDSS_VERSION_MINOR", macros.get("CUDSS_VER_MINOR")),
            macros.get("CUDSS_VERSION_PATCH", macros.get("CUDSS_VER_PATCH")),
            macros.get("CUDSS_VERSION_BUILD", macros.get("CUDSS_VER_BUILD")),
        ])
        if actual is None:
            actual = dotted([
                macros.get("CUDSS_VERSION_MAJOR", macros.get("CUDSS_VER_MAJOR")),
                macros.get("CUDSS_VERSION_MINOR", macros.get("CUDSS_VER_MINOR")),
                macros.get("CUDSS_VERSION_PATCH", macros.get("CUDSS_VER_PATCH")),
            ])
        self.record_version("cudss", actual, self.components_lock["cudss"]["version"], {"source": "cudss.h"})

    def check_nvshmem(self) -> None:
        macros = read_macros(
            self.include / "non_abi/nvshmem_version.h",
            self.include / "nvshmem_version.h",
            self.include / "nvshmem.h",
        )
        actual = dotted([
            macros.get("NVSHMEM_VENDOR_MAJOR_VERSION", macros.get("NVSHMEM_MAJOR_VERSION")),
            macros.get("NVSHMEM_VENDOR_MINOR_VERSION", macros.get("NVSHMEM_MINOR_VERSION")),
            macros.get("NVSHMEM_VENDOR_PATCH_VERSION", macros.get("NVSHMEM_PATCH_VERSION")),
        ])
        self.record_version("nvshmem", actual, self.components_lock["nvshmem"]["version"], {"source": "nvshmem_version.h"})

    def check_nccl(self) -> None:
        macros = read_macros(self.include / "nccl.h")
        actual = dotted([
            macros.get("NCCL_MAJOR"),
            macros.get("NCCL_MINOR"),
            macros.get("NCCL_PATCH"),
        ])
        self.record_version("nccl", actual, self.components_lock["nccl"]["version"], {"source": "nccl.h"})

    def check_tensorrt(self) -> None:
        component = self.components_lock["tensorrt"]
        expected = component["version"]
        macros = read_macros(
            self.rootfs / "usr/include/x86_64-linux-gnu/NvInferVersion.h",
            self.rootfs / "usr/include/NvInferVersion.h",
        )
        header = dotted([
            macros.get("NV_TENSORRT_MAJOR"),
            macros.get("NV_TENSORRT_MINOR"),
            macros.get("NV_TENSORRT_PATCH"),
            macros.get("NV_TENSORRT_BUILD"),
        ])
        packages = parse_dpkg_status(self.rootfs)
        expected_packages = {
            package["package"].split("=", 1)[0]: package["package"].split("=", 1)[1]
            for package in component["source"].get("packages", [])
        }
        package_mismatches = {
            name: {"expected": version, "actual": packages.get(name)}
            for name, version in expected_packages.items()
            if packages.get(name) != version
        }
        ok = version_matches(header, expected) and not package_mismatches
        self.components["tensorrt"] = {
            "static_version": {
                "expected": expected,
                "actual": header,
                "packages_checked": len(expected_packages),
                "ok": ok,
            }
        }
        if not version_matches(header, expected):
            self.add_issue(
                "tensorrt.static_version",
                "TensorRT header version mismatch",
                component="tensorrt",
                expected=expected,
                actual=header,
            )
        if package_mismatches:
            self.add_issue(
                "tensorrt.dpkg_version",
                "TensorRT package version mismatch",
                component="tensorrt",
                expected=expected_packages,
                actual={name: packages.get(name) for name in expected_packages},
            )

    def check_llvm(self) -> None:
        component = self.global_components_lock.get("llvm")
        if not isinstance(component, dict):
            self.add_issue("llvm.lock_missing", "global llvm component is missing from the lock", component="llvm")
            return

        verify = component.get("verify", {})
        expect = verify.get("expect", {}) if isinstance(verify, dict) else {}
        if not isinstance(expect, dict):
            expect = {}
        expected_prefix = str(expect.get("prefix") or component.get("install_layout", {}).get("prefix") or "/usr/lib/llvm-23")
        expected_version = str(expect.get("clang_version") or component.get("version") or "")
        expected_commit = str(expect.get("commit") or component.get("commit") or "")
        prefix = resolve_rootfs_path(self.rootfs, expected_prefix)

        required = component.get("build", {}).get("install_artifacts", LLVM_REQUIRED_ARTIFACTS)
        if not isinstance(required, list):
            required = list(LLVM_REQUIRED_ARTIFACTS)
        missing_artifacts = [
            rel
            for rel in required
            if not (prefix / str(rel)).exists()
        ]

        clang = prefix / "bin/clang"
        clang_out = run_rootfs_command(self.rootfs, [str(clang), "--version"]) if clang.exists() else None
        if clang_out is None:
            clang_out = read_llvm_build_version_output(prefix)
        actual_version = None
        if clang_out:
            match = re.search(r"clang version\s+([^\s]+)", clang_out)
            if match:
                actual_version = match.group(1)
        version_ok = bool(
            clang_out
            and actual_version == expected_version
            and expected_commit
            and expected_commit in clang_out
        )

        install_dirs = sorted((self.rootfs / "usr/lib").glob("llvm-*")) if (self.rootfs / "usr/lib").exists() else []
        expected_rel = Path(expected_prefix).relative_to("/")
        unexpected_installs = [
            path
            for path in install_dirs
            if path.relative_to(self.rootfs).as_posix() != expected_rel.as_posix()
        ]
        missing_install = not prefix.exists()

        artifact_violations: list[Path] = []
        nvidia_exceptions: list[Path] = []
        for path in self._llvm_artifact_scan():
            if self._is_under(path, prefix):
                continue
            if self._same_merged_usr_artifact_as(path, prefix):
                continue
            if self._is_nvidia_llvm_exception(path):
                nvidia_exceptions.append(path)
                continue
            artifact_violations.append(path)

        default_compilers = self._check_default_compilers(component)
        self.components["llvm"] = {
            "static_version": {
                "expected": expected_version,
                "actual": actual_version,
                "commit": expected_commit if clang_out and expected_commit in clang_out else None,
                "version_output": clang_out,
                "ok": version_ok,
            },
            "install_prefix": expected_prefix,
            "required_artifacts": {
                "checked": [str(item) for item in required],
                "missing": [str(item) for item in missing_artifacts],
                "ok": not missing_artifacts,
            },
            "default_compilers": default_compilers,
            "single_install_scan": {
                "install_dirs": [str(path.relative_to(self.rootfs)) for path in install_dirs],
                "unexpected_install_dirs": [str(path.relative_to(self.rootfs)) for path in unexpected_installs],
                "artifact_violations": [str(path.relative_to(self.rootfs)) for path in artifact_violations],
                "nvidia_exceptions": [str(path.relative_to(self.rootfs)) for path in sorted(nvidia_exceptions)],
                "ok": not missing_install and not unexpected_installs and not artifact_violations,
            },
        }

        if missing_install or unexpected_installs or artifact_violations:
            self.add_issue(
                "llvm.single_install",
                "rootfs must contain exactly one non-NVIDIA LLVM install",
                component="llvm",
                expected=expected_prefix,
                actual={
                    "install_dirs": [str(path.relative_to(self.rootfs)) for path in install_dirs],
                    "artifact_violations": [str(path.relative_to(self.rootfs)) for path in artifact_violations],
                },
            )
        if missing_artifacts:
            self.add_issue(
                "llvm.required_artifact",
                "LLVM install is missing required tools or CMake exports",
                component="llvm",
                expected=[str(item) for item in required],
                actual=[str(item) for item in missing_artifacts],
            )
        if not version_ok:
            self.add_issue(
                "llvm.clang_version",
                "clang version output does not match the locked LLVM commit",
                component="llvm",
                expected={"version": expected_version, "commit": expected_commit},
                actual=clang_out,
            )
        if not all(item["ok"] for item in default_compilers.values()):
            self.add_issue(
                "llvm.default_compiler",
                "cc and c++ must remain gcc 13 defaults",
                component="llvm",
                expected="gcc 13.3",
                actual=default_compilers,
            )

    def _llvm_artifact_scan(self) -> list[Path]:
        paths: set[Path] = set()
        for pattern in LLVM_ARTIFACT_PATTERNS:
            for path in self._rootfs_scan(pattern):
                if path.is_dir():
                    continue
                paths.add(path)
        for path in self._rootfs_scan("clang*"):
            if path.is_dir():
                continue
            rel_parts = path.relative_to(self.rootfs).parts
            if "bin" in rel_parts or path.is_symlink() or os.access(path, os.X_OK):
                paths.add(path)
        return sorted(paths)

    def _rootfs_scan(self, pattern: str) -> Iterable[Path]:
        def onerror(_exc: OSError) -> None:
            return None

        for dirpath, dirnames, filenames in os.walk(self.rootfs, topdown=True, followlinks=False, onerror=onerror):
            directory = Path(dirpath)
            dirnames[:] = [
                dirname
                for dirname in dirnames
                if not is_excluded_rootfs_scan_path(self.rootfs, directory / dirname)
            ]
            for name in [*dirnames, *filenames]:
                path = directory / name
                if is_excluded_rootfs_scan_path(self.rootfs, path):
                    continue
                if fnmatch.fnmatch(name, pattern):
                    yield path

    def _same_merged_usr_artifact_as(self, path: Path, expected_prefix: Path) -> bool:
        try:
            rel = path.relative_to(self.rootfs)
        except ValueError:
            return False
        if not rel.parts or rel.parts[0] not in MERGED_USR_ALIAS_TOPLEVELS:
            return False
        canonical = self.rootfs / "usr" / Path(*rel.parts)
        if not self._is_under(canonical, expected_prefix):
            return False
        identity = file_identity(path)
        return identity is not None and identity == file_identity(canonical)

    def _is_under(self, path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False

    def _is_nvidia_llvm_exception(self, path: Path) -> bool:
        rel = path.relative_to(self.rootfs).as_posix()
        if not (rel.startswith("usr/local/cuda/") or self._is_under(path, self.cuda)):
            return False
        return "/nvvm/" in "/" + rel or "/nvrtc/" in "/" + rel or "nvrtc" in path.name.lower()

    def _check_default_compilers(self, component: dict[str, Any]) -> dict[str, dict[str, Any]]:
        compiler = component.get("build", {}).get("compiler", {})
        expected_version = str(compiler.get("version", "13.3")) if isinstance(compiler, dict) else "13.3"
        packages = parse_dpkg_status(self.rootfs)
        result: dict[str, dict[str, Any]] = {}
        for name, spec in {
            "cc": {
                "names": ("gcc",),
                "packages": ("gcc-13", "gcc-13-x86-64-linux-gnu"),
            },
            "c++": {
                "names": ("g++", "gcc"),
                "packages": ("g++-13", "g++-13-x86-64-linux-gnu"),
            },
        }.items():
            expected_names = spec["names"]
            expected_packages = spec["packages"]
            path = resolve_rootfs_path(self.rootfs, f"/usr/bin/{name}")
            output = run_rootfs_command(self.rootfs, [str(path), "--version"]) if path.exists() else None
            lowered = " ".join([path.name, output or ""]).lower()
            is_gcc = "clang" not in lowered and any(expected in lowered for expected in expected_names)
            has_version = bool(output and re.search(rf"\b{re.escape(expected_version)}(?:\.|\b)", output))
            package_versions = {
                package: version
                for package in expected_packages
                if (version := packages.get(package)) is not None
            }
            if not has_version:
                has_version = any(
                    version.split(":", 1)[-1].startswith(expected_version)
                    for version in package_versions.values()
                )
            result[name] = {
                "path": str(path.relative_to(self.rootfs)) if self._is_under(path, self.rootfs) else str(path),
                "version_output": output,
                "package_versions": package_versions,
                "ok": bool(path.exists() and is_gcc and has_version and "clang" not in lowered and "clang" not in path.name),
            }
        return result

    def check_single_copy(self) -> None:
        copies: dict[str, list[Path]] = {}
        identities_by_stem: dict[str, set[tuple[int, int]]] = {}
        for path in self._rootfs_scan("lib*.so*"):
            rel = path.relative_to(self.rootfs).as_posix()
            if "/stubs/" in "/" + rel:
                continue
            if path.is_symlink() or not path.is_file():
                continue
            name = path.name
            if ".so" not in name:
                continue
            stem = name.split(".so", 1)[0]
            if stem in CUDA_LIBRARY_STEMS or stem.startswith("libcudnn"):
                identity = file_identity(path)
                if identity is not None:
                    identities = identities_by_stem.setdefault(stem, set())
                    if identity in identities:
                        continue
                    identities.add(identity)
                copies.setdefault(stem, []).append(path)
        summary = {stem: [str(p.relative_to(self.rootfs)) for p in paths] for stem, paths in sorted(copies.items())}
        self.components["single_copy_scan"] = {"families": summary, "ok": True}
        for stem, paths in sorted(copies.items()):
            if len(paths) > 1:
                self.components["single_copy_scan"]["ok"] = False
                self.add_issue(
                    f"single_copy.{stem}",
                    f"{stem} has more than one real shared-library file",
                    component=stem,
                    actual=[str(p.relative_to(self.rootfs)) for p in paths],
                )

    def check_driver_userspace(self) -> None:
        bad: list[Path] = []
        for path in self._rootfs_scan("lib*.so*"):
            if path.name not in FORBIDDEN_DRIVER_NAMES:
                continue
            rel = path.relative_to(self.rootfs).as_posix()
            if "/stubs/" not in "/" + rel:
                bad.append(path)
        self.components["driver_userspace_scan"] = {
            "forbidden": [str(p.relative_to(self.rootfs)) for p in bad],
            "ok": not bad,
        }
        for path in bad:
            self.add_issue(
                "driver_userspace.forbidden",
                "driver userspace library is present outside stubs",
                path=path.relative_to(self.rootfs),
            )

    def check_pip_distributions(self) -> None:
        bad: list[tuple[str, Path]] = []
        for path in list(self._rootfs_scan("*.dist-info")) + list(self._rootfs_scan("*.egg-info")):
            if not path.is_dir():
                continue
            name = dist_name(path)
            if name.startswith("nvidia-") or name in FORBIDDEN_PIP_NAMES:
                bad.append((name, path))
        self.components["pip_distribution_scan"] = {
            "forbidden": [{"name": name, "path": str(path.relative_to(self.rootfs))} for name, path in bad],
            "ok": not bad,
        }
        for name, path in bad:
            self.add_issue(
                "pip.forbidden_distribution",
                "forbidden CUDA consumer or NVIDIA wheel distribution is installed",
                actual=name,
                path=path.relative_to(self.rootfs),
            )


def find_host_driver_library(soname: str) -> list[tuple[Path, str]]:
    candidates: list[Path] = []
    for directory in (Path("/usr/lib/x86_64-linux-gnu"), Path("/usr/lib64"), Path("/lib/x86_64-linux-gnu")):
        path = directory / soname
        if path.exists():
            candidates.append(path)
    ldconfig = run_command(["ldconfig", "-p"])
    if ldconfig:
        for line in ldconfig.splitlines():
            if soname in line and "=>" in line:
                path = Path(line.split("=>", 1)[1].strip())
                if path.exists():
                    candidates.append(path)
    if not candidates:
        return []
    path = candidates[0]
    binds = [(path, f"/run/nvidia-driver/lib/{soname}")]
    real = path.resolve()
    if real != path:
        binds.append((real, f"/run/nvidia-driver/lib/{real.name}"))
    return binds


GPU_PROBE = r'''
import ctypes
import json

report = {"components": {}, "issues": []}

def issue(component, message, actual=None):
    report["issues"].append({"component": component, "message": message, "actual": actual})
    report["components"].setdefault(component, {})["ok"] = False

def ok(component, version):
    report["components"].setdefault(component, {})["ok"] = True
    report["components"][component]["runtime_version"] = version

def load(component, names):
    last = None
    for name in names:
        try:
            return ctypes.CDLL(name)
        except OSError as exc:
            last = str(exc)
    issue(component, "library load failed", last)
    return None

def rc(component, call, value):
    if value != 0:
        issue(component, call + " failed", value)
        return False
    return True

cudart = load("cudart", ["libcudart.so", "libcudart.so.13", "libcudart.so.12"])
if cudart:
    v = ctypes.c_int()
    if rc("cudart", "cudaRuntimeGetVersion", cudart.cudaRuntimeGetVersion(ctypes.byref(v))):
        ok("cudart", v.value)
    count = ctypes.c_int()
    if rc("cuda_devices", "cudaGetDeviceCount", cudart.cudaGetDeviceCount(ctypes.byref(count))):
        report["components"]["cuda_devices"] = {"ok": count.value == 8, "count": count.value}
        if count.value != 8:
            issue("cuda_devices", "cudaGetDeviceCount mismatch", count.value)

cublas = load("cublas", ["libcublas.so", "libcublas.so.13", "libcublas.so.12"])
if cublas:
    handle = ctypes.c_void_p()
    if rc("cublas", "cublasCreate_v2", cublas.cublasCreate_v2(ctypes.byref(handle))):
        v = ctypes.c_int()
        if rc("cublas", "cublasGetVersion_v2", cublas.cublasGetVersion_v2(handle, ctypes.byref(v))):
            ok("cublas", v.value)
        cublas.cublasDestroy_v2(handle)

cudnn = load("cudnn", ["libcudnn.so", "libcudnn.so.9"])
if cudnn:
    cudnn.cudnnGetVersion.restype = ctypes.c_size_t
    ok("cudnn", int(cudnn.cudnnGetVersion()))

nccl = load("nccl", ["libnccl.so", "libnccl.so.2"])
if nccl:
    v = ctypes.c_int()
    if rc("nccl", "ncclGetVersion", nccl.ncclGetVersion(ctypes.byref(v))):
        ok("nccl", v.value)

cusparselt = load("cusparselt", ["libcusparseLt.so", "libcusparseLt.so.0"])
if cusparselt:
    try:
        handle = ctypes.c_byte * 8192
        storage = handle()
        ptr = ctypes.cast(ctypes.byref(storage), ctypes.c_void_p)
        if rc("cusparselt", "cusparseLtInit", cusparselt.cusparseLtInit(ptr)):
            v = ctypes.c_int()
            if rc("cusparselt", "cusparseLtGetVersion", cusparselt.cusparseLtGetVersion(ptr, ctypes.byref(v))):
                ok("cusparselt", v.value)
            cusparselt.cusparseLtDestroy(ptr)
    except Exception as exc:
        issue("cusparselt", "cusparseLtGetVersion failed", str(exc))

cudss = load("cudss", ["libcudss.so", "libcudss.so.0"])
if cudss:
    try:
        values = []
        for prop in (0, 1, 2):
            v = ctypes.c_int()
            if not rc("cudss", "cudssGetProperty", cudss.cudssGetProperty(prop, ctypes.byref(v))):
                break
            values.append(v.value)
        if len(values) == 3:
            ok("cudss", values[0] * 10000 + values[1] * 100 + values[2])
    except Exception as exc:
        issue("cudss", "cudssGetProperty failed", str(exc))

nvshmem = load("nvshmem", ["libnvshmem_host.so.3", "libnvshmem_host.so", "libnvshmem.so"])
if nvshmem:
    try:
        major, minor, patch = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
        fn = nvshmem.nvshmemx_vendor_get_version_info
        fn.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
        if rc("nvshmem", "nvshmemx_vendor_get_version_info", fn(ctypes.byref(major), ctypes.byref(minor), ctypes.byref(patch))):
            ok("nvshmem", f"{major.value}.{minor.value}.{patch.value}")
    except Exception as exc:
        issue("nvshmem", "nvshmem version query failed", str(exc))

tensorrt = load("tensorrt", ["libnvinfer.so", "libnvinfer.so.11"])
if tensorrt:
    try:
        tensorrt.getInferLibMajorVersion.restype = ctypes.c_int
        tensorrt.getInferLibMinorVersion.restype = ctypes.c_int
        tensorrt.getInferLibPatchVersion.restype = ctypes.c_int
        ok("tensorrt", [
            int(tensorrt.getInferLibMajorVersion()),
            int(tensorrt.getInferLibMinorVersion()),
            int(tensorrt.getInferLibPatchVersion()),
        ])
    except Exception as exc:
        issue("tensorrt", "TensorRT version query failed", str(exc))

print(json.dumps(report, sort_keys=True))
'''


def run_gpu_probe(rootfs: Path, line_lock: dict[str, Any]) -> tuple[dict[str, Any], list[Issue]]:
    bwrap = shutil_which("bwrap")
    if not bwrap:
        if os.environ.get("VASO_IN_INSULA") == "1" and rootfs.resolve() == Path("/"):
            env = dict(os.environ)
            env.update(
                {
                    "PATH": "/usr/local/cuda/bin:/usr/bin:/bin",
                    "LD_LIBRARY_PATH": "/run/nvidia-driver/lib:/usr/local/cuda/lib64:/usr/lib/x86_64-linux-gnu",
                }
            )
            result = subprocess.run(
                ["/usr/bin/python3", "-c", GPU_PROBE],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
        else:
            return {}, [Issue("gpu.bwrap_missing", "bwrap is not available")]
    else:
        scratch_dir = "/" + "tmp"
        binds: list[str] = []
        for soname in ("libcuda.so.1", "libnvidia-ml.so.1"):
            found = find_host_driver_library(soname)
            if not found:
                return {}, [Issue("gpu.driver_missing", f"host {soname} was not found")]
            for host, inner in found:
                binds.extend(["--ro-bind", str(host), inner])
        dev_binds: list[str] = []
        for path in sorted(Path("/dev").glob("nvidia*")):
            dev_binds.extend(["--dev-bind-try", str(path), str(path)])
        for directory in (Path("/dev/nvidia-caps"),):
            if directory.exists():
                dev_binds.extend(["--dev-bind-try", str(directory), str(directory)])
        cmd = [
            bwrap,
            "--die-with-parent",
            "--unshare-user",
            "--uid",
            str(os.getuid()),
            "--gid",
            str(os.getgid()),
            "--unshare-uts",
            "--clearenv",
            "--ro-bind",
            str(rootfs),
            "/",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            *dev_binds,
            "--tmpfs",
            scratch_dir,
            "--tmpfs",
            "/run",
            "--dir",
            "/run/nvidia-driver",
            "--dir",
            "/run/nvidia-driver/lib",
            *binds,
            "--ro-bind-try",
            "/proc/driver/nvidia",
            "/proc/driver/nvidia",
            "--ro-bind-try",
            "/sys",
            "/sys",
            "--setenv",
            "PATH",
            "/usr/local/cuda/bin:/usr/bin:/bin",
            "--setenv",
            "LD_LIBRARY_PATH",
            "/usr/local/cuda/lib64:/usr/lib/x86_64-linux-gnu:/run/nvidia-driver/lib",
            "--",
            "/usr/bin/python3",
            "-c",
            GPU_PROBE,
        ]
        result = subprocess.run(cmd, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        return {}, [
            Issue(
                "gpu.probe_failed",
                "GPU bwrap probe failed",
                actual={
                    "returncode": result.returncode,
                    "stderr": result.stderr.strip(),
                    "stdout": result.stdout.strip(),
                },
            )
        ]
    try:
        probe = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {}, [Issue("gpu.invalid_json", "GPU probe did not emit JSON", actual=result.stdout, path=str(exc))]
    components = probe.get("components", {})
    issues = [
        Issue(
            f"gpu.{entry.get('component', 'unknown')}",
            entry.get("message", "GPU component check failed"),
            component=entry.get("component"),
            actual=entry.get("actual"),
        )
        for entry in probe.get("issues", [])
    ]
    issues.extend(compare_gpu_versions(components, line_lock))
    return components, issues


def shutil_which(name: str) -> str | None:
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def compare_gpu_versions(components: dict[str, Any], line_lock: dict[str, Any]) -> list[Issue]:
    issues: list[Issue] = []
    lock_components = line_lock["components"]

    def expect_int(component: str, actual_name: str, expected: int) -> None:
        actual = components.get(actual_name, {}).get("runtime_version")
        if actual != expected:
            issues.append(
                Issue(
                    f"gpu.{component}.runtime_version",
                    f"{component} runtime version mismatch",
                    component=component,
                    expected=expected,
                    actual=actual,
                )
            )

    toolkit = lock_components["cuda_toolkit"]["verify"]["expect"]
    major_minor = toolkit["cuda_api"].split(".")
    expect_int("cudart", "cudart", int(major_minor[0]) * 1000 + int(major_minor[1]) * 10)
    cublas_parts = parse_version(toolkit["components"]["libcublas"])
    expect_int("cublas", "cublas", cublas_parts[0] * 10000 + cublas_parts[1] * 100 + cublas_parts[2])
    expect_int("cudnn", "cudnn", lock_components["cudnn"]["verify"]["expect"]["runtime_version"])
    expect_int("nccl", "nccl", lock_components["nccl"]["verify"]["expect"]["runtime_version"])
    expect_int("cusparselt", "cusparselt", lock_components["cusparselt"]["verify"]["expect"]["runtime_version"])
    expect_int("cudss", "cudss", lock_components["cudss"]["verify"]["expect"]["runtime_version"])
    nvshmem_actual = components.get("nvshmem", {}).get("runtime_version")
    if nvshmem_actual != lock_components["nvshmem"]["version"]:
        issues.append(
            Issue(
                "gpu.nvshmem.runtime_version",
                "NVSHMEM runtime version mismatch",
                component="nvshmem",
                expected=lock_components["nvshmem"]["version"],
                actual=nvshmem_actual,
            )
        )
    trt_actual = components.get("tensorrt", {}).get("runtime_version")
    trt_expected = lock_components["tensorrt"]["verify"]["expect"]["runtime_version"]
    if isinstance(trt_actual, str):
        trt_ok = trt_actual.startswith(".".join(str(part) for part in trt_expected))
    else:
        trt_ok = trt_actual == trt_expected
    if not trt_ok:
        issues.append(
            Issue(
                "gpu.tensorrt.runtime_version",
                "TensorRT runtime version mismatch",
                component="tensorrt",
                expected=trt_expected,
                actual=trt_actual,
            )
        )
    device_count = components.get("cuda_devices", {}).get("count")
    if device_count != 8:
        issues.append(
            Issue(
                "gpu.cuda_devices.count",
                "CUDA device count mismatch",
                component="cuda_devices",
                expected=8,
                actual=device_count,
            )
        )
    return issues


def build_report(rootfs: Path, lock: dict[str, Any], line: str, *, gpu: bool) -> dict[str, Any]:
    verifier = StaticVerifier(rootfs, lock, line)
    components, issues = verifier.verify()
    report: dict[str, Any] = {
        "schema_version": 1,
        "rootfs": str(rootfs),
        "line": line,
        "mode": "gpu" if gpu else "static",
        "components": components,
        "issues": [],
    }
    if gpu:
        gpu_components, gpu_issues = run_gpu_probe(rootfs, lock["lines"][line])
        report["gpu_components"] = gpu_components
        issues.extend(gpu_issues)
    report["issues"] = [issue.to_json() for issue in issues]
    report["ok"] = not issues
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rootfs_dir", type=Path)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--line", choices=("cu129", "cu130"), required=True)
    parser.add_argument("--gpu", action="store_true", help="also run runtime ctypes checks inside bwrap")
    args = parser.parse_args(argv)

    lock = load_json(args.lock)
    rootfs = args.rootfs_dir.resolve()
    if args.line not in lock.get("lines", {}):
        raise SystemExit(f"line {args.line!r} not found in {args.lock}")
    report = build_report(rootfs, lock, args.line, gpu=args.gpu)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
