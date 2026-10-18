#!/usr/bin/env python3
"""Action driver for the token-gated native PyTorch prefix build."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from native.common.native_action_toolchain import (
        append_unique_cmake_args,
        deterministic_cmake_flag_args,
        deterministic_prefix_map_fragments,
        native_toolchain_environment,
        read_native_toolchain,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
    from native_action_toolchain import (  # type: ignore[no-redef]
        append_unique_cmake_args,
        deterministic_cmake_flag_args,
        deterministic_prefix_map_fragments,
        native_toolchain_environment,
        read_native_toolchain,
    )

REQUIRED_TOKEN = "build-native-pytorch"
SOURCE_NORMALIZATION_VERSION = "2026-10-05-cudss-rowend-null"
NATIVE_HELPER_PREFIX_KEYS = (
    "cpuinfo",
    "fp16",
    "fxdiv",
    "psimd",
    "pthreadpool",
)
PREFIX_KEYS = (
    "cuda",
    "cudnn",
    "nccl",
    "python",
    "cmake",
    "ninja",
    "openblas",
    "protobuf",
    "cusparselt",
    "cudss",
    "nvshmem",
    "openmpi",
    "libevent",
    "hwloc",
    "libpciaccess",
    "libxml2",
    "libiconv",
    "pmix",
    "numactl",
    *NATIVE_HELPER_PREFIX_KEYS,
    "py-pip",
    "py-setuptools",
    "py-wheel",
    "py-scikit-build-core",
    "py-numpy",
    "py-pyyaml",
    "py-typing-extensions",
    "py-six",
    "py-packaging",
    "py-pathspec",
    "py-protobuf",
)
PYTHON_BUILD_PREFIX_KEYS = (
    "py-pip",
    "py-setuptools",
    "py-wheel",
    "py-scikit-build-core",
    "py-numpy",
    "py-pyyaml",
    "py-typing-extensions",
    "py-six",
    "py-packaging",
    "py-pathspec",
    "py-protobuf",
)


def _check_insula() -> None:
    if os.environ.get("VASO_IN_INSULA") != "1":
        raise SystemExit(
            "native PyTorch actions must run inside the hermetic insula "
            "(VASO_IN_INSULA=1)"
        )
    manifest = os.environ.get("VASO_ROOTFS_BUNDLE_MANIFEST", "")
    if not manifest or not Path(manifest).is_file():
        raise SystemExit(
            "native PyTorch actions require VASO_ROOTFS_BUNDLE_MANIFEST "
            "inside the hermetic CUDA rootfs"
        )


def _required_tmpdir() -> Path:
    value = os.environ.get("TMPDIR", "")
    if not value:
        raise SystemExit("native PyTorch actions require TMPDIR from the insula action environment")
    path = Path(value)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_prefix_files(items: list[str]) -> dict[str, str]:
    prefixes: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--prefix-file must be KEY=PATH, got {item!r}")
        key, path_text = item.split("=", 1)
        if key not in PREFIX_KEYS:
            raise SystemExit(f"unsupported PyTorch native prefix key {key!r}")
        value = path_text.strip()
        if not value:
            raise SystemExit(f"{key} prefix path is empty")
        # N1-2 passes dependency prefixes execroot-relative (keeps output-base paths out of the key);
        # resolve them once here, at run time, because builds later change directory.
        prefixes[key] = os.path.realpath(value)
    missing = [key for key in PREFIX_KEYS if key not in prefixes]
    if missing:
        raise SystemExit("missing PyTorch native prefix file(s): " + ", ".join(missing))
    return prefixes


def _read_native_toolchain(path: str | Path | None) -> dict[str, object]:
    return read_native_toolchain(path, consumer="torch")


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _materialize_synthetic_prefixes(root: Path) -> dict[str, str]:
    prefixes = {key: root / key for key in PREFIX_KEYS}
    for path in prefixes.values():
        path.mkdir(parents=True, exist_ok=True)

    (prefixes["cuda"] / "bin").mkdir()
    _write_executable(prefixes["cuda"] / "bin" / "nvcc", "#!/bin/sh\n")

    (prefixes["cudnn"] / "include").mkdir()
    (prefixes["cudnn"] / "include" / "cudnn.h").write_text("", encoding="utf-8")
    (prefixes["cudnn"] / "include" / "cudnn_version.h").write_text("", encoding="utf-8")
    (prefixes["cudnn"] / "lib64").mkdir()
    (prefixes["cudnn"] / "lib64" / "libcudnn.so").write_text("", encoding="utf-8")

    (prefixes["nccl"] / "include").mkdir()
    (prefixes["nccl"] / "include" / "nccl.h").write_text("", encoding="utf-8")
    (prefixes["nccl"] / "lib").mkdir()
    (prefixes["nccl"] / "lib" / "libnccl.so").write_text("", encoding="utf-8")

    (prefixes["python"] / "bin").mkdir()
    _write_executable(
        prefixes["python"] / "bin" / "python3",
        "#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n',
    )

    (prefixes["cmake"] / "bin").mkdir()
    _write_executable(prefixes["cmake"] / "bin" / "cmake", "#!/bin/sh\n")
    (prefixes["ninja"] / "bin").mkdir()
    _write_executable(prefixes["ninja"] / "bin" / "ninja", "#!/bin/sh\n")

    (prefixes["openblas"] / "include").mkdir()
    (prefixes["openblas"] / "include" / "cblas.h").write_text("", encoding="utf-8")
    (prefixes["openblas"] / "lib").mkdir()
    (prefixes["openblas"] / "lib" / "libopenblas.so").write_text("", encoding="utf-8")

    (prefixes["protobuf"] / "include" / "google" / "protobuf").mkdir(parents=True)
    (prefixes["protobuf"] / "include" / "google" / "protobuf" / "message.h").write_text("", encoding="utf-8")
    (prefixes["protobuf"] / "bin").mkdir()
    _write_executable(
        prefixes["protobuf"] / "bin" / "protoc",
        "#!/bin/sh\necho 'libprotoc 3.21.12'\n",
    )
    (prefixes["protobuf"] / "lib").mkdir()
    (prefixes["protobuf"] / "lib" / "libprotobuf.so").write_text("", encoding="utf-8")

    (prefixes["cusparselt"] / "include").mkdir()
    (prefixes["cusparselt"] / "include" / "cusparseLt.h").write_text("", encoding="utf-8")
    (prefixes["cusparselt"] / "lib64").mkdir()
    (prefixes["cusparselt"] / "lib64" / "libcusparseLt.so").write_text("", encoding="utf-8")

    (prefixes["cudss"] / "include").mkdir()
    (prefixes["cudss"] / "include" / "cudss.h").write_text("", encoding="utf-8")
    (prefixes["cudss"] / "lib64").mkdir()
    (prefixes["cudss"] / "lib64" / "libcudss.so").write_text("", encoding="utf-8")

    (prefixes["nvshmem"] / "include").mkdir()
    (prefixes["nvshmem"] / "include" / "nvshmem.h").write_text("", encoding="utf-8")
    (prefixes["nvshmem"] / "include" / "non_abi").mkdir()
    (prefixes["nvshmem"] / "include" / "non_abi" / "nvshmem_version.h").write_text("", encoding="utf-8")
    (prefixes["nvshmem"] / "lib64").mkdir()
    (prefixes["nvshmem"] / "lib64" / "libnvshmem_host.so").write_text("", encoding="utf-8")

    (prefixes["openmpi"] / "include").mkdir()
    (prefixes["openmpi"] / "include" / "mpi.h").write_text("", encoding="utf-8")
    (prefixes["openmpi"] / "lib").mkdir()
    (prefixes["openmpi"] / "lib" / "libmpi.so").write_text("", encoding="utf-8")

    (prefixes["libevent"] / "lib").mkdir()
    (prefixes["libevent"] / "lib" / "libevent_core-2.1.so.7").write_text("", encoding="utf-8")
    (prefixes["libevent"] / "lib" / "libevent_pthreads-2.1.so.7").write_text("", encoding="utf-8")

    (prefixes["hwloc"] / "lib").mkdir()
    (prefixes["hwloc"] / "lib" / "libhwloc.so.15").write_text("", encoding="utf-8")

    (prefixes["libpciaccess"] / "lib").mkdir()
    (prefixes["libpciaccess"] / "lib" / "libpciaccess.so.0").write_text("", encoding="utf-8")

    (prefixes["libxml2"] / "lib").mkdir()
    (prefixes["libxml2"] / "lib" / "libxml2.so.16").write_text("", encoding="utf-8")

    (prefixes["libiconv"] / "lib").mkdir()
    (prefixes["libiconv"] / "lib" / "libiconv.so.2").write_text("", encoding="utf-8")

    (prefixes["pmix"] / "lib").mkdir()
    (prefixes["pmix"] / "lib" / "libpmix.so.2").write_text("", encoding="utf-8")

    (prefixes["numactl"] / "include").mkdir()
    (prefixes["numactl"] / "include" / "numa.h").write_text("", encoding="utf-8")
    (prefixes["numactl"] / "lib").mkdir()
    (prefixes["numactl"] / "lib" / "libnuma.so").write_text("", encoding="utf-8")

    (prefixes["cpuinfo"] / "include").mkdir()
    (prefixes["cpuinfo"] / "include" / "cpuinfo.h").write_text("", encoding="utf-8")
    (prefixes["cpuinfo"] / "lib").mkdir()
    (prefixes["cpuinfo"] / "lib" / "libcpuinfo.so").write_text("", encoding="utf-8")

    (prefixes["fp16"] / "include").mkdir()
    (prefixes["fp16"] / "include" / "fp16.h").write_text("", encoding="utf-8")

    (prefixes["fxdiv"] / "include").mkdir()
    (prefixes["fxdiv"] / "include" / "fxdiv.h").write_text("", encoding="utf-8")

    (prefixes["psimd"] / "include").mkdir()
    (prefixes["psimd"] / "include" / "psimd.h").write_text("", encoding="utf-8")

    (prefixes["pthreadpool"] / "include").mkdir()
    (prefixes["pthreadpool"] / "include" / "pthreadpool.h").write_text("", encoding="utf-8")
    (prefixes["pthreadpool"] / "lib").mkdir()
    (prefixes["pthreadpool"] / "lib" / "libpthreadpool.a").write_text("", encoding="utf-8")

    site_packages = _python_site_packages_dir(str(prefixes["python"]), "derived")

    def python_package_prefix(name: str, package_path: str) -> None:
        package = prefixes[name] / site_packages / package_path
        if package.suffix == ".py":
            package.parent.mkdir(parents=True, exist_ok=True)
            package.write_text("", encoding="utf-8")
        else:
            package.mkdir(parents=True, exist_ok=True)
        (prefixes[name] / "bin").mkdir(exist_ok=True)

    python_package_prefix("py-pip", "pip")
    _write_executable(prefixes["py-pip"] / "bin" / "pip", "#!/bin/sh\n")
    python_package_prefix("py-setuptools", "setuptools")
    python_package_prefix("py-wheel", "wheel")
    _write_executable(prefixes["py-wheel"] / "bin" / "wheel", "#!/bin/sh\n")
    python_package_prefix("py-scikit-build-core", "scikit_build_core")
    scikit_dist = prefixes["py-scikit-build-core"] / site_packages / "scikit_build_core-1.0.0.dist-info"
    scikit_dist.mkdir(parents=True)
    (scikit_dist / "METADATA").write_text(
        "Name: scikit-build-core\nVersion: 1.0.0\n",
        encoding="utf-8",
    )
    python_package_prefix("py-numpy", "numpy")
    python_package_prefix("py-pyyaml", "yaml")
    python_package_prefix("py-typing-extensions", "typing_extensions.py")
    python_package_prefix("py-six", "six.py")
    python_package_prefix("py-packaging", "packaging")
    python_package_prefix("py-pathspec", "pathspec")
    python_package_prefix("py-protobuf", "google/protobuf")

    return {key: str(path) for key, path in prefixes.items()}


def _run_plan(args: argparse.Namespace, prefixes: dict[str, str], source_dir: Path) -> tuple[int, dict]:
    plan_argv = [
        sys.executable,
        args.plan,
        "--out",
        args.build_plan_out,
        "--source",
        str(source_dir),
        "--source-manifest",
        args.source_manifest,
        "--build-version",
        args.build_version,
        "--torch-cuda-arch-list",
        args.torch_cuda_arch_list,
        "--python-abi",
        args.python_abi,
        "--token",
        args.token,
    ]
    if args.execute:
        plan_argv.append("--execute")
    if args.rootfs_cuda_bundle:
        plan_argv.append("--rootfs-cuda-bundle")
    if getattr(args, "cuda_arch_ptx_supported", False):
        plan_argv.append("--cuda-arch-ptx-supported")
    if native_toolchain_path := getattr(args, "native_toolchain", ""):
        plan_argv.extend(["--native-toolchain", native_toolchain_path])
    if args.max_jobs:
        plan_argv.extend(["--max-jobs", args.max_jobs])
    for key in PREFIX_KEYS:
        plan_argv.extend(["--prefix", f"{key}={prefixes[key]}"])

    result = subprocess.run(
        plan_argv,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    try:
        plan_doc = json.loads(Path(args.build_plan_out).read_text(encoding="utf-8"))
    except FileNotFoundError:
        plan_doc = {}
    return result.returncode, plan_doc


def _read_prefix_path(value: str, name: str) -> Path:
    if not value:
        raise SystemExit(f"{name} prefix path is empty")
    return Path(value)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _action_input_digest(args: argparse.Namespace, prefixes: dict[str, str]) -> str:
    files = {
        "plan": args.plan,
        "source_anchor": args.source_anchor,
        "source_archive": args.source_archive,
        "source_manifest": args.source_manifest,
    }
    if zstd_manifest := getattr(args, "zstd_prefix_manifest", ""):
        files["zstd_prefix_manifest"] = zstd_manifest
    else:
        files["zstd_prefix_file"] = args.zstd_prefix_file
    if native_toolchain_path := getattr(args, "native_toolchain", ""):
        files["native_toolchain"] = native_toolchain_path
    rootfs_manifest = os.environ.get("VASO_ROOTFS_BUNDLE_MANIFEST", "")
    if rootfs_manifest:
        files["rootfs_manifest"] = rootfs_manifest
    payload = {
        "build_version": args.build_version,
        "execute": bool(args.execute),
        "files": {},
        "prefixes": {
            key: prefixes[key]
            for key in PREFIX_KEYS
        },
        "python_abi": args.python_abi,
        "rootfs_cuda_bundle": bool(args.rootfs_cuda_bundle),
        "source_normalization_version": SOURCE_NORMALIZATION_VERSION,
        "cuda_arch_ptx_supported": bool(getattr(args, "cuda_arch_ptx_supported", False)),
        "torch_cuda_arch_list": args.torch_cuda_arch_list,
        "vaso_cuda_line": os.environ.get("VASO_CUDA_LINE", ""),
    }
    for key, value in sorted(files.items()):
        path = Path(value)
        if not path.is_file():
            raise SystemExit(f"native PyTorch action digest input is missing: {key}={path}")
        payload["files"][key] = _file_sha256(path)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:32]


def _estate_build_work_dir(args: argparse.Namespace, prefixes: dict[str, str]) -> Path:
    root_arg = getattr(args, "build_work_root", "")
    if root_arg:
        root = Path(root_arg)
    else:
        line = os.environ.get("VASO_CUDA_LINE", "")
        if not line:
            raise SystemExit("native PyTorch actions require VASO_CUDA_LINE for build work reuse")
        vaso_home = os.environ.get("VASO_HOME", "/vaso")
        root = Path(vaso_home) / "lines" / line / "work" / "pytorch"
    if not root.is_absolute():
        raise SystemExit(f"native PyTorch build work root must be absolute: {root}")
    work_dir = root.resolve(strict=False) / _action_input_digest(args, prefixes)
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def _extract_source_archive(source_archive: Path, build_work: Path, zstd_prefix: Path) -> Path:
    if not source_archive.is_file():
        raise SystemExit(f"PyTorch source archive is missing: {source_archive}")
    zstd = zstd_prefix / "bin" / "zstd"
    if not zstd.is_file():
        raise SystemExit(f"native zstd executable is missing: {zstd}")

    source_dir = build_work / "pytorch-v2.14.0"
    if (source_dir / "setup.py").is_file():
        return source_dir

    subprocess.run(
        [
            "tar",
            "--use-compress-program",
            str(zstd),
            "-xf",
            str(source_archive),
            "-C",
            str(build_work),
        ],
        check=True,
    )
    if not (source_dir / "setup.py").is_file():
        setup_files = sorted(build_work.glob("*/setup.py"))
        if len(setup_files) != 1:
            raise SystemExit(
                "PyTorch source archive did not unpack to one setup.py under "
                f"{build_work}"
            )
        source_dir = setup_files[0].parent
    return source_dir


def _source_dir_for_action(args: argparse.Namespace, prefixes: dict[str, str]) -> Path:
    source_anchor = Path(args.source_anchor)
    if not source_anchor.is_file():
        raise SystemExit(f"PyTorch source anchor is missing: {source_anchor}")
    source_archive = Path(args.source_archive)
    if not source_archive.is_file():
        raise SystemExit(f"PyTorch source archive is missing: {source_archive}")

    if not args.execute:
        return source_anchor.parent

    build_work = _estate_build_work_dir(args, prefixes)
    zstd_prefix = _read_prefix_path(args.zstd_prefix_file, "zstd")
    return _extract_source_archive(source_archive, build_work, zstd_prefix)


def _read_cmake_home_directory(cache: Path) -> Path | None:
    for line in cache.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("CMAKE_HOME_DIRECTORY:INTERNAL="):
            value = line.split("=", 1)[1].strip()
            if value:
                return Path(value)
    return None


def _make_tree_owner_writable(root: Path) -> None:
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        for path in [current_path] + [current_path / name for name in dirs] + [current_path / name for name in files]:
            if path.is_symlink():
                continue
            try:
                mode = path.stat().st_mode
                extra = stat.S_IWUSR
                if path.is_dir():
                    extra |= stat.S_IXUSR
                path.chmod(mode | extra)
            except OSError:
                pass


def _reset_stale_cmake_build_dir(
    source_dir: Path,
    *,
    required_cache_fragments: tuple[str, ...] = (),
) -> list[str]:
    build_dir = source_dir / "build"
    cache = build_dir / "CMakeCache.txt"
    if not cache.is_file():
        return []
    cache_text = cache.read_text(encoding="utf-8", errors="replace")
    cached_home = _read_cmake_home_directory(cache)
    if cached_home is None:
        return []
    current_home = source_dir.resolve(strict=False)
    if cached_home.resolve(strict=False) == current_home:
        if required_cache_fragments and not all(fragment in cache_text for fragment in required_cache_fragments):
            _make_tree_owner_writable(build_dir)
            shutil.rmtree(build_dir)
            return ["build:CMakeCache-missing-deterministic-prefix-map"]
        return []
    _make_tree_owner_writable(build_dir)
    shutil.rmtree(build_dir)
    return ["build:CMakeCache-home-mismatch"]


_PREBUILD_SENTINELS = (
    "CMakeLists.txt",
    "Makefile",
    "setup.py",
    "LICENSE",
    "LICENSE.md",
    "LICENSE.txt",
)


def _cmake_string(value: Path) -> str:
    return str(value).replace("\\", "/").replace('"', '\\"')


def _normalize_externalized_psimd_source(source_dir: Path, prefixes: dict[str, str]) -> list[str]:
    psimd_dir = source_dir / "third_party" / "psimd"
    if any((psimd_dir / name).exists() for name in _PREBUILD_SENTINELS):
        return []

    if not psimd_dir.exists():
        psimd_dir.mkdir(parents=True)
    unexpected_entries = sorted(
        path.name for path in psimd_dir.iterdir()
        if path.name != ".git"
    )
    if unexpected_entries:
        raise SystemExit(
            "third_party/psimd has no PyTorch prebuild sentinel but is not an "
            "empty externalized submodule: " + ", ".join(unexpected_entries)
        )

    include_dir = Path(prefixes["psimd"]) / "include"
    header = include_dir / "psimd.h"
    if not header.is_file():
        raise SystemExit(f"native psimd header is missing: {header}")

    (psimd_dir / "CMakeLists.txt").write_text(
        "\n".join([
            "cmake_minimum_required(VERSION 3.5)",
            "project(psimd_native_prefix NONE)",
            "add_library(psimd INTERFACE)",
            'target_include_directories(psimd INTERFACE "{}")'.format(_cmake_string(include_dir)),
            "",
        ]),
        encoding="utf-8",
    )
    return ["third_party/psimd"]


def _normalize_qnnpack_helper_imports(source_dir: Path, prefixes: dict[str, str]) -> list[str]:
    qnnpack_cmake = (
        source_dir /
        "aten" /
        "src" /
        "ATen" /
        "native" /
        "quantized" /
        "cpu" /
        "qnnpack" /
        "CMakeLists.txt"
    )
    if not qnnpack_cmake.is_file():
        return []

    text = qnnpack_cmake.read_text(encoding="utf-8")
    changed = False

    psimd_include_dir = Path(prefixes["psimd"]) / "include"
    psimd_header = psimd_include_dir / "psimd.h"
    psimd_property = (
        '  set_property(TARGET psimd PROPERTY INTERFACE_INCLUDE_DIRECTORIES "{}")'
        .format(_cmake_string(psimd_include_dir))
    )
    if psimd_property not in text:
        psimd_needle = '  set_property(TARGET psimd PROPERTY LINKER_LANGUAGE C)'
        if psimd_needle in text:
            if not psimd_header.is_file():
                raise SystemExit(f"native psimd header is missing: {psimd_header}")
            text = text.replace(psimd_needle, psimd_needle + "\n" + psimd_property, 1)
            changed = True

    pthreadpool_include_dir = Path(prefixes["pthreadpool"]) / "include"
    pthreadpool_header = pthreadpool_include_dir / "pthreadpool.h"
    if not pthreadpool_header.is_file():
        raise SystemExit(f"native pthreadpool header is missing: {pthreadpool_header}")

    pthreadpool_needle = (
        '  set_target_properties(pthreadpool PROPERTIES\n'
        '    IMPORTED_LOCATION "${PTHREADPOOL_LIBRARY}")'
    )
    pthreadpool_property = 'INTERFACE_INCLUDE_DIRECTORIES "{}"'.format(
        _cmake_string(pthreadpool_include_dir)
    )
    if pthreadpool_property in text:
        if changed:
            qnnpack_cmake.write_text(text, encoding="utf-8")
            return ["aten/src/ATen/native/quantized/cpu/qnnpack"]
        return []
    if pthreadpool_needle not in text:
        raise SystemExit(
            "cannot patch qnnpack pthreadpool import; expected PyTorch v2.14.0 "
            "CMake snippet is missing"
        )

    pthreadpool_replacement = (
        '  set_target_properties(pthreadpool PROPERTIES\n'
        '    IMPORTED_LOCATION "${{PTHREADPOOL_LIBRARY}}"\n'
        '    INTERFACE_INCLUDE_DIRECTORIES "{}")'.format(_cmake_string(pthreadpool_include_dir))
    )
    text = text.replace(pthreadpool_needle, pthreadpool_replacement, 1)
    qnnpack_cmake.write_text(text, encoding="utf-8")
    return ["aten/src/ATen/native/quantized/cpu/qnnpack"]


def _normalize_nnpack_peachpy_launcher(source_dir: Path, prefixes: dict[str, str]) -> list[str]:
    nnpack_cmake = source_dir / "third_party" / "NNPACK" / "CMakeLists.txt"
    if not nnpack_cmake.is_file():
        return []

    text = nnpack_cmake.read_text(encoding="utf-8")
    fixed = 'COMMAND "${CMAKE_COMMAND}" -E env "PYTHONPATH=${PEACHPY_PYTHONPATH}"'
    if fixed in text:
        return []
    broken = 'COMMAND "PYTHONPATH=${PEACHPY_PYTHONPATH}"'
    if broken not in text:
        return []

    text = text.replace(broken, fixed, 1)
    nnpack_cmake.write_text(text, encoding="utf-8")
    return ["third_party/NNPACK/CMakeLists.txt"]


def _normalize_top_level_helper_imports(source_dir: Path, prefixes: dict[str, str]) -> list[str]:
    dependencies_cmake = source_dir / "cmake" / "Dependencies.cmake"
    if not dependencies_cmake.is_file():
        return []

    text = dependencies_cmake.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    changed = False
    for target, prefix_key, library_var, source_var, header_name in (
        ("cpuinfo", "cpuinfo", "CPUINFO_LIBRARY", "CPUINFO_SOURCE_DIR", "cpuinfo.h"),
        ("pthreadpool", "pthreadpool", "PTHREADPOOL_LIBRARY", "PTHREADPOOL_SOURCE_DIR", "pthreadpool.h"),
    ):
        property_line = (
            f'set_property(TARGET {target} PROPERTY INTERFACE_INCLUDE_DIRECTORIES '
            f'"${{{source_var}}}/include")'
        )
        if property_line in text or property_line in "".join(lines):
            continue

        location_lines = (
            f'set_property(TARGET {target} PROPERTY IMPORTED_LOCATION "${{{library_var}}}")',
            f'set_target_properties({target} PROPERTIES IMPORTED_LOCATION "${{{library_var}}}")',
        )
        patch_index = next(
            (
                index
                for index, line in enumerate(lines)
                if line.strip() in location_lines
            ),
            None,
        )
        if patch_index is None:
            continue

        header = Path(prefixes[prefix_key]) / "include" / header_name
        if not header.is_file():
            raise SystemExit(f"native {prefix_key} header is missing: {header}")
        indent = lines[patch_index][:len(lines[patch_index]) - len(lines[patch_index].lstrip())]
        lines.insert(patch_index + 1, f"{indent}{property_line}\n")
        changed = True

    if changed:
        dependencies_cmake.write_text("".join(lines), encoding="utf-8")
        return ["cmake/Dependencies.cmake"]
    return []


def _normalize_cudss_csr_row_end(source_dir: Path) -> list[str]:
    sparse_impl = source_dir / "aten" / "src" / "ATen" / "native" / "sparse" / "cuda" / "SparseCsrTensorMath.cu"
    if not sparse_impl.is_file():
        return []

    text = sparse_impl.read_text(encoding="utf-8")
    broken = "rowOffsets, rowOffsets + crow.size(0), colIndices, values_ptr"
    fixed = "rowOffsets, nullptr, colIndices, values_ptr"
    if fixed in text:
        return []
    if broken not in text:
        if "cudssMatrixCreateCsr" in text:
            raise SystemExit(
                "cannot patch cuDSS CSR rowEnd; expected PyTorch v2.14.0 "
                "cudssMatrixCreateCsr argument snippet is missing"
            )
        return []

    sparse_impl.write_text(text.replace(broken, fixed, 1), encoding="utf-8")
    return ["aten/src/ATen/native/sparse/cuda/SparseCsrTensorMath.cu"]


def _normalize_externalized_helper_sources(source_dir: Path, prefixes: dict[str, str]) -> list[str]:
    normalized: list[str] = []
    normalized.extend(_normalize_cudss_csr_row_end(source_dir))
    normalized.extend(_normalize_externalized_psimd_source(source_dir, prefixes))
    normalized.extend(_normalize_top_level_helper_imports(source_dir, prefixes))
    normalized.extend(_normalize_qnnpack_helper_imports(source_dir, prefixes))
    normalized.extend(_normalize_nnpack_peachpy_launcher(source_dir, prefixes))
    return normalized


def _resolve_source_placeholders(value: str, source_dir: Path) -> str:
    return value.replace("${PROJECT_SOURCE_DIR}", str(source_dir))


def _minimal_build_env(plan_doc: dict, prefixes: dict[str, str], source_dir: Path) -> dict[str, str]:
    python_abi = str(plan_doc.get("python_abi") or "derived")
    site_packages = _python_site_packages_dir(prefixes["python"], python_abi)
    pythonpath = [
        str(Path(prefixes[key]) / site_packages)
        for key in PYTHON_BUILD_PREFIX_KEYS
    ]
    path_entries = [
        str(Path(prefixes["python"]) / "bin"),
        str(Path(prefixes["cmake"]) / "bin"),
        str(Path(prefixes["ninja"]) / "bin"),
        str(Path(prefixes["cuda"]) / "bin"),
        str(Path(prefixes["py-pip"]) / "bin"),
        str(Path(prefixes["py-wheel"]) / "bin"),
        "/usr/bin",
        "/bin",
    ]
    tmpdir = _required_tmpdir()
    action_home = tmpdir / "pytorch-home"
    pip_cache = tmpdir / "pip-cache"
    action_home.mkdir(parents=True, exist_ok=True)
    pip_cache.mkdir(parents=True, exist_ok=True)
    env = {
        "HOME": str(action_home),
        "TMPDIR": str(tmpdir),
        "VASO_IN_INSULA": os.environ.get("VASO_IN_INSULA", ""),
        "VASO_ROOTFS_BUNDLE_MANIFEST": os.environ.get("VASO_ROOTFS_BUNDLE_MANIFEST", ""),
    }
    for key, value in plan_doc.get("build_env", {}).items():
        env[str(key)] = _resolve_source_placeholders(str(value), source_dir)
    native_toolchain = plan_doc.get("native_toolchain")
    env.update(
        native_toolchain_environment(
            native_toolchain if isinstance(native_toolchain, dict) else None,
            compilers=False,
            cuda_home=False,
        )
    )
    env["CMAKE_ARGS"] = append_unique_cmake_args(
        env.get("CMAKE_ARGS", ""),
        deterministic_cmake_flag_args(native_toolchain if isinstance(native_toolchain, dict) else None),
    )
    env["PATH"] = os.pathsep.join(path_entries)
    env["PYTHONHOME"] = ""
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)
    env["PIP_CACHE_DIR"] = str(pip_cache)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INDEX"] = "1"
    env["PIP_NO_INPUT"] = "1"
    return env


def _execute_build(args: argparse.Namespace, prefixes: dict[str, str], plan_doc: dict, source_dir: Path) -> None:
    prefix = Path(args.prefix_out).resolve(strict=False)
    source_dir = source_dir.resolve(strict=False)
    wheel_dir = prefix / "artifacts" / "wheels"
    wheel_dir.mkdir(parents=True, exist_ok=True)
    python = Path(prefixes["python"]) / "bin" / "python3"
    env = _minimal_build_env(plan_doc, prefixes, source_dir)

    wheel_cmd = [
        str(python),
        "-m",
        "pip",
        "wheel",
        "--no-build-isolation",
        "--no-deps",
        "--no-index",
        "-w",
        str(wheel_dir),
        str(source_dir),
    ]
    subprocess.run(wheel_cmd, check=True, env=env, cwd=str(prefix))
    wheels = sorted(wheel_dir.glob("torch-*.whl"))
    if not wheels:
        raise SystemExit(f"native PyTorch build produced no torch wheel in {wheel_dir}")
    wheel_out_arg = getattr(args, "wheel_out", "")
    if wheel_out_arg:
        wheel_out = Path(wheel_out_arg)
        wheel_out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(wheels[-1], wheel_out)
    wheel_manifest = wheel_dir / "wheel_manifest.json"
    manifest_wheels = [
        {
            "name": wheels[-1].name,
            "path": str(wheels[-1]),
            "sha256": _file_sha256(wheels[-1]),
        }
    ]
    wheel_manifest.write_text(
        json.dumps({"schema_version": 1, "wheels": manifest_wheels}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    install_cmd = [
        str(python),
        "-m",
        "pip",
        "install",
        "--no-deps",
        "--no-index",
        "--prefix",
        str(prefix),
        str(wheel_dir / manifest_wheels[0]["name"]),
    ]
    subprocess.run(install_cmd, check=True, env=env, cwd=str(prefix))


def _python_version_from_abi(python_abi: str) -> str | None:
    if python_abi.startswith("cp") and python_abi[2:].isdigit():
        digits = python_abi[2:]
        return digits[0] + "." + digits[1:]
    return None


def _python_site_packages_dir(python_prefix: str, python_abi: str) -> Path:
    version = _python_version_from_abi(python_abi)
    if version:
        return Path("lib") / ("python" + version) / "site-packages"

    python = Path(python_prefix) / "bin" / "python3"
    if python.is_file() and os.access(python, os.X_OK):
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


def _write_dry_run_prefix(prefix: Path, python_prefix: str, python_abi: str) -> None:
    if prefix.exists():
        shutil.rmtree(prefix)
    site_packages = _python_site_packages_dir(python_prefix, python_abi)
    (prefix / "artifacts" / "wheels").mkdir(parents=True)
    (prefix / site_packages / "torch" / "lib").mkdir(parents=True)
    (prefix / site_packages / "torch" / "include").mkdir(parents=True)
    (prefix / "DRY_RUN_DO_NOT_USE_AS_TORCH_PREFIX").write_text(
        "Token-safe native PyTorch dry run. No wheel was built.\n",
        encoding="utf-8",
    )
    return None


def _write_dry_run_wheel(args: argparse.Namespace) -> None:
    wheel_out_arg = getattr(args, "wheel_out", "")
    if not wheel_out_arg:
        return
    wheel_out = Path(wheel_out_arg)
    wheel_out.parent.mkdir(parents=True, exist_ok=True)
    wheel_out.write_text(
        "Token-safe native PyTorch dry run. No wheel was built.\n",
        encoding="utf-8",
    )


def _write_metadata(
    args: argparse.Namespace,
    plan_doc: dict,
    prefixes: dict[str, str],
    source_dir: Path,
    normalized_source_submodules: list[str],
    work_tree_cleanups: list[str],
) -> None:
    metadata = {
        "schema_version": 1,
        "package": "py-torch",
        "version": "2.14.0",
        "action_input_digest": _action_input_digest(args, prefixes),
        "build_version": args.build_version,
        "python_abi": args.python_abi,
        "cuda_arch_ptx_supported": bool(getattr(args, "cuda_arch_ptx_supported", False)),
        "torch_cuda_arch_list": args.torch_cuda_arch_list,
        "mechanism": "python-wheel-action",
        "resources": plan_doc.get("resources", {}),
        "source": str(source_dir),
        "source_anchor": args.source_anchor,
        "source_manifest": args.source_manifest,
        "build_work": str(source_dir.parent) if plan_doc.get("will_build") else "",
        "wheel": getattr(args, "wheel_out", ""),
        "execute_requested": bool(args.execute),
        "token_present": args.token == REQUIRED_TOKEN,
        "will_build": bool(plan_doc.get("will_build")),
        "mode": plan_doc.get("mode", "dry-run"),
        "input_prefixes": prefixes,
        "normalized_source_submodules": normalized_source_submodules,
        "work_tree_cleanups": work_tree_cleanups,
        "rootfs_manifest": os.environ.get("VASO_ROOTFS_BUNDLE_MANIFEST", ""),
    }
    Path(args.provider_metadata_out).write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_result_marker(args: argparse.Namespace, plan_doc: dict) -> None:
    if plan_doc.get("will_build"):
        text = "Native PyTorch wheel build requested and prefix installation completed.\n"
    else:
        text = "Token-safe native PyTorch dry run. No wheel was built.\n"
    Path(args.result_marker_out).write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--prefix-out", required=True)
    parser.add_argument("--build-work-root", default="")
    parser.add_argument("--build-plan-out", required=True)
    parser.add_argument("--provider-metadata-out", required=True)
    parser.add_argument("--result-marker-out", required=True)
    parser.add_argument("--wheel-out", default="")
    parser.add_argument("--source-anchor", required=True)
    parser.add_argument("--source-archive", required=True)
    parser.add_argument("--source-manifest", required=True)
    parser.add_argument("--zstd-prefix-file", required=True)
    parser.add_argument("--zstd-prefix-manifest", default="")
    parser.add_argument("--native-toolchain", required=True)
    parser.add_argument("--prefix-file", action="append", default=[])
    parser.add_argument("--build-version", default="2.14.0")
    parser.add_argument("--torch-cuda-arch-list", default="10.0")
    parser.add_argument("--cuda-arch-ptx-supported", action="store_true")
    parser.add_argument("--python-abi", default="derived")
    parser.add_argument("--token", default="")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--rootfs-cuda-bundle", action="store_true")
    parser.add_argument("--synthetic-prefixes-for-dry-run", action="store_true")
    parser.add_argument("--max-jobs", default="")
    args = parser.parse_args(argv)

    _check_insula()
    native_toolchain = _read_native_toolchain(args.native_toolchain)
    prefixes = _read_prefix_files(args.prefix_file)
    synthetic_root: tempfile.TemporaryDirectory[str] | None = None
    if args.synthetic_prefixes_for_dry_run:
        if args.execute:
            raise SystemExit("synthetic prefixes are allowed only for token-safe dry-run actions")
        synthetic_root = tempfile.TemporaryDirectory(prefix="vaso-pytorch-prefixes-", dir=str(_required_tmpdir()))
        prefixes = _materialize_synthetic_prefixes(Path(synthetic_root.name))
    source_dir = _source_dir_for_action(args, prefixes)

    prefix = Path(args.prefix_out)
    if not args.execute:
        _write_dry_run_prefix(prefix, prefixes["python"], args.python_abi)
        _write_dry_run_wheel(args)
    rc, plan_doc = _run_plan(args, prefixes, source_dir)
    if rc != 0:
        return rc
    if native_toolchain:
        plan_doc["native_toolchain"] = native_toolchain
    normalized_source_submodules: list[str] = []
    work_tree_cleanups: list[str] = []
    if plan_doc.get("will_build"):
        work_tree_cleanups = _reset_stale_cmake_build_dir(
            source_dir,
            required_cache_fragments=deterministic_prefix_map_fragments(native_toolchain),
        )
        normalized_source_submodules = _normalize_externalized_helper_sources(source_dir, prefixes)
        _execute_build(args, prefixes, plan_doc, source_dir)
    _write_metadata(args, plan_doc, prefixes, source_dir, normalized_source_submodules, work_tree_cleanups)
    _write_result_marker(args, plan_doc)
    if synthetic_root is not None:
        synthetic_root.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
