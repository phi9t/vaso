#!/usr/bin/env python3
"""Generate committed native build plans."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

NATIVE_COMMON = Path(__file__).resolve().parents[1] / "native" / "common"
if str(NATIVE_COMMON) not in sys.path:
    sys.path.insert(0, str(NATIVE_COMMON))

from plan_schema import LINES, schema_v1, stable_json, validate_plan


ROOTFS_IDENTITY_PROFILES = ["jax", "torch"]

PACKAGE_GENERATORS: dict[str, dict[str, Any]] = {
    "py-jaxlib": {
        "profiles": ["jax"],
        "lines": list(LINES),
        "source_date_epoch": 1793577600,
        "deps": {
            "python": "@python_313_native//:prefix_path.txt",
            "python-venv": "@python_venv_native//:prefix_path.txt",
            "py-pip": "@py_pip_native//:prefix_path.txt",
            "py-setuptools": "@py_setuptools_82_native//:prefix_path.txt",
            "py-wheel": "@py_wheel_native//:prefix_path.txt",
            "py-numpy": "@py_numpy_native//:prefix_path.txt",
            "bazel": "@rootfs_bazel_native//:prefix_path.txt",
            "llvm": "@rootfs_llvm_23_native//:prefix_path.txt",
            "cuda": "@cuda_native//:prefix_path.txt",
            "cudnn": "@cudnn_native//:prefix_path.txt",
            "nccl": "@nccl_native//:prefix_path.txt",
            "nvshmem": "@nvshmem_native//:prefix_path.txt",
            "xxd-standalone": "@xxd_standalone_native//:prefix_path.txt",
        },
        "outputs": [
            {"kind": "prefix", "path": "{out}"},
            {"kind": "wheel", "path": "{wheel}"},
        ],
        "gpu_archs": ["sm_100"],
    },
    "py-torch": {
        "profiles": ["torch"],
        "lines": list(LINES),
        "version": "2.14.0",
        "torch_cuda_arch_list": "10.0+PTX",
        "source": {
            "name": "main",
            "archive": "{source_archive}",
            "patches": [],
            "sha256": "62dedab65c9d7dda7cbb6edaad07d325ad193014496e5da4540a5c074cd8a50a",
            "strip_prefix": "pytorch-v2.14.0",
            "urls": ["https://example.invalid/vaso/pytorch-v2.14.0-2b3ec348-recursive.tar.zst"],
        },
        "source_date_epoch": 0,
        "python_site_packages": "lib/python3.13/site-packages",
        "deps": {
            "cuda": "@cuda_native//:prefix_path.txt",
            "cudnn": "@cudnn_native//:prefix_path.txt",
            "nccl": "@nccl_native//:prefix_path.txt",
            "python": "@python_313_native//:prefix_path.txt",
            "cmake": "@cmake_native//:prefix_path.txt",
            "ninja": "@ninja_native//:prefix_path.txt",
            "openblas": "@openblas_native//:prefix_path.txt",
            "protobuf": "@protobuf_native//:prefix_path.txt",
            "cusparselt": "@cusparselt_native//:prefix_path.txt",
            "cudss": "@cudss_native//:prefix_path.txt",
            "nvshmem": "@nvshmem_native//:prefix_path.txt",
            "openmpi": "@openmpi_native//:prefix_path.txt",
            "libevent": "@libevent_native//:prefix_path.txt",
            "hwloc": "@hwloc_native//:prefix_path.txt",
            "libpciaccess": "@libpciaccess_native//:prefix_path.txt",
            "libxml2": "@libxml2_215_native//:prefix_path.txt",
            "libiconv": "@libiconv_native//:prefix_path.txt",
            "pmix": "@pmix_native//:prefix_path.txt",
            "numactl": "@numactl_native//:prefix_path.txt",
            "cpuinfo": "@cpuinfo_native//:prefix_path.txt",
            "fp16": "@fp16_native//:prefix_path.txt",
            "fxdiv": "@fxdiv_native//:prefix_path.txt",
            "psimd": "@psimd_native//:prefix_path.txt",
            "pthreadpool": "@pthreadpool_native//:prefix_path.txt",
            "py-pip": "@py_pip_native//:prefix_path.txt",
            "py-setuptools": "@py_setuptools_native//:prefix_path.txt",
            "py-wheel": "@py_wheel_native//:prefix_path.txt",
            "py-scikit-build-core": "@py_scikit_build_core_native//:prefix_path.txt",
            "py-numpy": "@py_numpy_native//:prefix_path.txt",
            "py-pyyaml": "@py_pyyaml_native//:prefix_path.txt",
            "py-typing-extensions": "@py_typing_extensions_native//:prefix_path.txt",
            "py-six": "@py_six_native//:prefix_path.txt",
            "py-packaging": "@py_packaging_native//:prefix_path.txt",
            "py-pathspec": "@py_pathspec_native//:prefix_path.txt",
            "py-protobuf": "@py_protobuf_native//:prefix_path.txt",
            "zstd": "//native/zstd:zstd_action_prefix",
        },
        "build_deps": [
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
            "cpuinfo",
            "fp16",
            "fxdiv",
            "psimd",
            "pthreadpool",
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
        ],
        "outputs": [
            {"kind": "prefix", "path": "{out}"},
            {"kind": "wheel", "path": "{wheel}"},
        ],
        "gpu_archs": ["sm_100+PTX"],
    },
    "py-triton": {
        "profiles": ["torch"],
        "lines": list(LINES),
        "version": "3.8.0",
        "source": {
            "name": "main",
            "archive": "{source_archive}",
            "patches": [
                {
                    "path": "native/triton/patches/0001-llvm-35901313.patch",
                    "sha256": "771dbaa52dbcaebff26f47684cacdb1edf237d900c8a418b11f9c2793397ce7f",
                }
            ],
            "sha256": "61a11952362a0d54e67fb11fd29f25747da7339fe4ed39b2cff730c21ebe8825",
            "strip_prefix": "triton-675c59878aa2280b31f722aaf42b825fcee21de8",
            "urls": ["https://github.com/triton-lang/triton/archive/675c59878aa2280b31f722aaf42b825fcee21de8.tar.gz"],
        },
        "source_date_epoch": 1772203844,
        "python_site_packages": "lib/python3.13/site-packages",
        "deps": {
            "python": "@python_313_native//:prefix_path.txt",
            "python-venv": "@python_venv_native//:prefix_path.txt",
            "py-pip": "@py_pip_native//:prefix_path.txt",
            "py-setuptools": "@py_setuptools_native//:prefix_path.txt",
            "py-wheel": "@py_wheel_native//:prefix_path.txt",
            "py-filelock": "@py_filelock_native//:prefix_path.txt",
            "py-lit": "@py_lit_native//:prefix_path.txt",
            "py-pybind11": "@py_pybind11_native//:prefix_path.txt",
            "cmake": "@cmake_native//:prefix_path.txt",
            "ninja": "@ninja_native//:prefix_path.txt",
            "llvm": "@rootfs_llvm_23_native//:prefix_path.txt",
            "nlohmann_json": "@nlohmann_json_native//:prefix_path.txt",
            "cuda": "@cuda_native//:prefix_path.txt",
            "zlib_ng": "@zlib_ng_native//:prefix_path.txt",
        },
        "outputs": [
            {"kind": "prefix", "path": "{out}"},
            {"kind": "wheel", "path": "{wheel}"},
        ],
        "gpu_archs": ["sm_100"],
    },
    "py-torchvision": {
        "profiles": ["torch"],
        "lines": list(LINES),
        "version": "0.29.0",
        "pytorch_version": "2.14.0",
        "source": {
            "name": "main",
            "archive": "{source_archive}",
            "patches": [],
            "sha256": "24be57d922927d8a2ac2e8f076f07c3447ddf8f1d25ddbb7b65578f36c9ab8e3",
            "strip_prefix": "vision-0.29.0",
            "urls": ["https://github.com/pytorch/vision/archive/refs/tags/v0.29.0.tar.gz"],
        },
        "source_date_epoch": 1793577600,
        "python_site_packages": "lib/python3.13/site-packages",
        "deps": {
            "torch": "//native/pytorch:pytorch_action",
            "python": "@python_313_native//:prefix_path.txt",
            "python-venv": "@python_venv_native//:prefix_path.txt",
            "py-pip": "@py_pip_native//:prefix_path.txt",
            "py-setuptools": "@py_setuptools_native//:prefix_path.txt",
            "py-wheel": "@py_wheel_native//:prefix_path.txt",
            "py-numpy": "@py_numpy_native//:prefix_path.txt",
            "py-pillow": "@py_pillow_native//:prefix_path.txt",
            "py-filelock": "@py_filelock_native//:prefix_path.txt",
            "cuda": "@cuda_native//:prefix_path.txt",
            "ninja": "@ninja_native//:prefix_path.txt",
            "libjpeg-turbo": "@libjpeg_turbo_native//:prefix_path.txt",
            "libpng": "@libpng_native//:prefix_path.txt",
            "zlib-ng": "@zlib_ng_native//:prefix_path.txt",
        },
        "outputs": [
            {"kind": "prefix", "path": "{out}"},
            {"kind": "wheel", "path": "{wheel}"},
        ],
        "gpu_archs": ["sm_100"],
    },
    "py-torchaudio": {
        "profiles": ["torch"],
        "lines": list(LINES),
        "version": "2.11.0",
        "pytorch_version": "2.14.0",
        "source": {
            "name": "main",
            "archive": "{source_archive}",
            "patches": [],
            "sha256": "599ec24e7e1eef476ef21f0178e33da00e2434f930ba42e9cc20bf4002220486",
            "strip_prefix": "audio-2.11.0",
            "urls": ["https://github.com/pytorch/audio/archive/refs/tags/v2.11.0.tar.gz"],
        },
        "source_date_epoch": 1793577600,
        "python_site_packages": "lib/python3.13/site-packages",
        "deps": {
            "torch": "//native/pytorch:pytorch_action",
            "python": "@python_313_native//:prefix_path.txt",
            "python-venv": "@python_venv_native//:prefix_path.txt",
            "py-pip": "@py_pip_native//:prefix_path.txt",
            "py-setuptools": "@py_setuptools_native//:prefix_path.txt",
            "py-wheel": "@py_wheel_native//:prefix_path.txt",
            "py-filelock": "@py_filelock_native//:prefix_path.txt",
            "cuda": "@cuda_native//:prefix_path.txt",
            "ninja": "@ninja_native//:prefix_path.txt",
        },
        "outputs": [
            {"kind": "prefix", "path": "{out}"},
            {"kind": "wheel", "path": "{wheel}"},
        ],
        "gpu_archs": ["sm_100"],
    },
    "zlib-ng": {
        "profiles": ["torch"],
        "lines": list(LINES),
        "version": "2.3.3",
        "source": {
            "name": "main",
            "archive": "{source_archive}",
            "patches": [],
            "sha256": "f9c65aa9c852eb8255b636fd9f07ce1c406f061ec19a2e7d508b318ca0c907d1",
            "strip_prefix": "zlib-ng-2.3.3",
            "urls": ["https://github.com/zlib-ng/zlib-ng/archive/2.3.3.tar.gz"],
        },
        "source_date_epoch": 1770153003,
        "configure": {
            "cwd": "{src}",
            "argv": ["{src}/configure", "--prefix=/", "--zlib-compat"],
            "env": {},
        },
        "build": {
            "cwd": "{src}",
            "argv": ["make", "-j{jobs}"],
            "env": {},
        },
        "install": {
            "cwd": "{src}",
            "argv": ["make", "DESTDIR={out}", "install"],
            "env": {},
        },
        "outputs": [{"kind": "prefix", "path": "{out}"}],
    },
    "bzip2": {
        "profiles": ["torch"],
        "lines": list(LINES),
        "version": "1.0.8",
        "source": {
            "name": "main",
            "archive": "{source_archive}",
            "patches": [],
            "sha256": "ab5a03176ee106d3f0fa90e381da478ddae405918153cca248e682cd0c4a2269",
            "strip_prefix": "bzip2-1.0.8",
            "urls": ["https://sourceware.org/pub/bzip2/bzip2-1.0.8.tar.gz"],
        },
        "source_date_epoch": 1595891929,
        "configure": {
            "cwd": "{src}",
            "argv": [
                "bash",
                "-c",
                "\n".join(
                    [
                        "set -euo pipefail",
                        r"sed -i 's/^CC=gcc$/CC ?= gcc/' Makefile",
                        r"sed -i 's/^CC=gcc$/CC ?= gcc/' Makefile-libbz2_so",
                    ]
                ),
            ],
            "env": {},
        },
        "build": {
            "cwd": "{src}",
            "argv": [
                "bash",
                "-c",
                "\n".join(
                    [
                        "set -euo pipefail",
                        "make -f Makefile-libbz2_so -j{jobs} CFLAGS=\"-fpic -fPIC -Wall -Winline -O2 -D_FILE_OFFSET_BITS=64\"",
                        "make -j{jobs} CFLAGS=\"-Wall -Winline -O2 -D_FILE_OFFSET_BITS=64\"",
                    ]
                ),
            ],
            "env": {},
        },
        "install": {
            "cwd": "{src}",
            "argv": [
                "bash",
                "-c",
                "\n".join(
                    [
                        "set -euo pipefail",
                        "make install PREFIX=\"{out}\"",
                        "install -m 0755 bzip2-shared \"{out}/bin/bzip2\"",
                        "install -m 0644 libbz2.so.1.0.8 \"{out}/lib/libbz2.so.1.0.8\"",
                        "cd \"{out}/lib\"",
                        "ln -sfn libbz2.so.1.0.8 libbz2.so",
                        "ln -sfn libbz2.so.1.0.8 libbz2.so.1",
                        "ln -sfn libbz2.so.1.0.8 libbz2.so.1.0",
                        "cd \"{out}/bin\"",
                        "ln -sfn bzip2 bunzip2",
                        "ln -sfn bzip2 bzcat",
                        "ln -sfn bzgrep bzegrep",
                        "ln -sfn bzgrep bzfgrep",
                        "ln -sfn bzmore bzless",
                        "ln -sfn bzdiff bzcmp",
                        "mkdir -p \"{out}/lib/pkgconfig\"",
                        "{",
                        "  printf 'prefix={out}\\n'",
                        "  printf 'exec_prefix=$\\173prefix\\175\\n'",
                        "  printf 'libdir=$\\173exec_prefix\\175/lib\\n'",
                        "  printf 'includedir=$\\173prefix\\175/include\\n'",
                        "  printf '\\n'",
                        "  printf 'Name: bzip2\\n'",
                        "  printf 'Description: Lossless, block-sorting data compression\\n'",
                        "  printf 'Version: 1.0.8\\n'",
                        "  printf 'Libs: -L$\\173libdir\\175 -lbz2\\n'",
                        "  printf 'Cflags: -I$\\173includedir\\175\\n'",
                        "} > \"{out}/lib/pkgconfig/bzip2.pc\"",
                    ]
                ),
            ],
            "env": {},
        },
        "outputs": [{"kind": "prefix", "path": "{out}"}],
    },
    "xz": {
        "profiles": ["torch"],
        "lines": list(LINES),
        "version": "5.8.3",
        "source": {
            "name": "main",
            "archive": "{source_archive}",
            "patches": [],
            "sha256": "3d3a1b973af218114f4f889bbaa2f4c037deaae0c8e815eec381c3d546b974a0",
            "strip_prefix": "xz-5.8.3",
            "urls": ["https://github.com/tukaani-project/xz/releases/download/v5.8.3/xz-5.8.3.tar.gz"],
        },
        "source_date_epoch": 1765899264,
        "configure": {
            "cwd": "{src}",
            "argv": ["{src}/configure", "--prefix={out}", "--enable-shared", "--enable-static", "--disable-nls"],
            "env": {},
        },
        "build": {
            "cwd": "{src}",
            "argv": ["make", "-j{jobs}"],
            "env": {},
        },
        "install": {
            "cwd": "{src}",
            "argv": [
                "bash",
                "-c",
                "\n".join(
                    [
                        "set -euo pipefail",
                        "make install",
                        "find \"{out}\" -name '*.la' -delete",
                        "python3 - \"{out}/lib/pkgconfig/liblzma.pc\" <<'PY'",
                        "from pathlib import Path",
                        "import sys",
                        "",
                        "path = Path(sys.argv[1])",
                        "rewritten = []",
                        "for line in path.read_text(encoding='utf-8').splitlines(keepends=True):",
                        "    newline = '\\n' if line.endswith('\\n') else ''",
                        "    body = line[:-1] if newline else line",
                        "    if body.startswith('exec_prefix='):",
                        "        line = 'exec_prefix=$\\173prefix\\175' + newline",
                        "    elif body.startswith('libdir='):",
                        "        line = 'libdir=$\\173prefix\\175/lib' + newline",
                        "    elif body.startswith('includedir='):",
                        "        line = 'includedir=$\\173prefix\\175/include' + newline",
                        "    rewritten.append(line)",
                        "path.write_text(''.join(rewritten), encoding='utf-8')",
                        "PY",
                        "find \"{out}/bin\" \"{out}/lib\" -type f -print0 | LC_ALL=C sort -z | while IFS= read -r -d '' f; do",
                        "  description=\"$(file -b -- \"$f\")\"",
                        "  case \"$description\" in",
                        "    *ELF*)",
                        "      case \"$f\" in",
                        "        \"{out}/bin/\"*)",
                        "          if [[ -n \"$(patchelf --print-rpath \"$f\" 2>/dev/null || true)\" ]]; then",
                        "            patchelf --set-rpath '$ORIGIN/../lib' \"$f\"",
                        "          fi",
                        "          strip --strip-all \"$f\"",
                        "          ;;",
                        "        \"{out}/lib/\"*)",
                        "          if [[ -n \"$(patchelf --print-rpath \"$f\" 2>/dev/null || true)\" ]]; then",
                        "            patchelf --set-rpath '$ORIGIN' \"$f\"",
                        "          fi",
                        "          strip --strip-debug \"$f\"",
                        "          ;;",
                        "        *) strip --strip-debug \"$f\" ;;",
                        "      esac",
                        "      ;;",
                        "    *ar\\ archive*) strip --strip-debug \"$f\" ;;",
                        "  esac",
                        "done",
                    ]
                ),
            ],
            "env": {},
        },
        "outputs": [{"kind": "prefix", "path": "{out}"}],
    },
    "zstd": {
        "profiles": ["torch"],
        "lines": list(LINES),
        "version": "1.5.7",
        "source": {
            "name": "main",
            "archive": "{source_archive}",
            "patches": [],
            "sha256": "37d7284556b20954e56e1ca85b80226768902e2edabd3b649e9e72c0c9012ee3",
            "strip_prefix": "zstd-1.5.7",
            "urls": ["https://github.com/facebook/zstd/archive/v1.5.7.tar.gz"],
        },
        "source_date_epoch": 1751906416,
        "configure": {
            "cwd": "{src}",
            "argv": [
                "bash",
                "-c",
                "\n".join(
                    [
                        "set -euo pipefail",
                        "make -C \"{src}/lib\" -j{jobs} VERBOSE=1 PREFIX=\"{out}\" install-pc install-includes install-shared install-static",
                    ]
                ),
            ],
            "env": {},
        },
        "build": {
            "cwd": "{src}",
            "argv": [
                "bash",
                "-c",
                "\n".join(
                    [
                        "set -euo pipefail",
                        "make -C \"{src}/programs\" -j{jobs} VERBOSE=1 PREFIX=\"{out}\" HAVE_ZLIB=0 HAVE_LZMA=0 HAVE_LZ4=0",
                    ]
                ),
            ],
            "env": {},
        },
        "install": {
            "cwd": "{src}",
            "argv": ["make", "-C", "{src}/programs", "PREFIX={out}", "HAVE_ZLIB=0", "HAVE_LZMA=0", "HAVE_LZ4=0", "install"],
            "env": {},
        },
        "outputs": [{"kind": "prefix", "path": "{out}"}],
    },
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def rootfs_identity(lock: dict[str, Any], line: str) -> dict[str, Any]:
    lines = lock.get("lines")
    if not isinstance(lines, dict) or line not in lines:
        raise ValueError(f"rootfs lock does not contain lines.{line}")
    identity = copy.deepcopy(lines[line])
    if isinstance(identity, dict):
        identity.pop("generated", None)
        runtime_driver = identity.get("runtime_driver")
        if isinstance(runtime_driver, dict):
            runtime_driver.pop("host_version", None)
        identity["live_checks"] = live_rootfs_checks(identity)
    return identity


def live_rootfs_checks(identity: dict[str, Any]) -> dict[str, Any]:
    packages = identity.get("apt", {}).get("packages", {})
    if not isinstance(packages, dict):
        packages = {}
    cuda_version = identity["components"]["cuda_toolkit"]["version"]
    cuda_major, cuda_minor, *_ = cuda_version.split(".")
    cuda_cudart = f"cuda-cudart-{cuda_major}-{cuda_minor}"
    gcc_version = package_upstream_version(packages["gcc-13"])
    commands = [
        {
            "name": "gcc-dumpfullversion",
            "argv": ["gcc", "-dumpfullversion"],
            "expect_stdout": gcc_version,
        },
        {
            "name": "dpkg-gcc-13",
            "argv": ["dpkg-query", "-W", "-f=${Version}", "gcc-13"],
            "expect_stdout": packages["gcc-13"],
        },
        {
            "name": f"dpkg-{cuda_cudart}",
            "argv": ["dpkg-query", "-W", "-f=${Version}", cuda_cudart],
            "expect_stdout": packages[cuda_cudart],
        },
    ]
    return {"commands": commands}


def package_upstream_version(version: str) -> str:
    return version.split(":", 1)[-1].split("-", 1)[0]


def gcc_major_minor(identity: dict[str, Any]) -> str:
    packages = identity.get("apt", {}).get("packages", {})
    if not isinstance(packages, dict) or not isinstance(packages.get("gcc-13"), str):
        raise ValueError("rootfs identity does not contain apt.packages.gcc-13")
    upstream = package_upstream_version(packages["gcc-13"])
    major, minor, *_ = upstream.split(".")
    return f"{major}.{minor}"


def toolchain_ref(identifier: str) -> str:
    stem = identifier.removeprefix("vaso-rootfs-").replace(".", "_").replace("-", "_")
    return f"toolchain:{stem}"


def registered_toolchain(repo_root: Path, profile: str, rootfs: dict[str, Any]) -> tuple[str, dict[str, str]]:
    toolchains = read_json(repo_root / "toolchains" / "native_action_cc_toolchains.json")
    families = read_json(repo_root / "toolchains" / "native_action_consumer_families.json")
    family = families.get("families", {}).get(profile)
    if not isinstance(family, str):
        raise ValueError(f"profile {profile} is missing from native action consumer family registry")
    expected_suffix = gcc_major_minor(rootfs) if family == "gcc" else None
    matches: list[tuple[str, dict[str, Any]]] = []
    for identifier, data in sorted(toolchains.get("toolchains", {}).items()):
        if not isinstance(data, dict) or data.get("family") != family:
            continue
        if expected_suffix is not None and not identifier.endswith(expected_suffix):
            continue
        matches.append((identifier, data))
    if len(matches) != 1:
        raise ValueError(f"profile {profile} must resolve to one {family} toolchain, got {[name for name, _ in matches]!r}")
    identifier, data = matches[0]
    env = {
        "CC": str(data["cc"]),
        "CXX": str(data["cxx"]),
        "CFLAGS": str(data.get("cflags", "")),
        "CXXFLAGS": str(data.get("cxxflags", "")),
        "CUDAFLAGS": str(data.get("cudaflags", "")),
        "NVCC_FLAGS": str(data.get("nvcc_flags", "")),
    }
    return identifier, env


def plan_seed(plan: dict[str, Any]) -> str:
    seedless = copy.deepcopy(plan)
    seedless["determinism"]["seed"] = "derive:sha256"
    return sha256_bytes(stable_json(seedless).encode("utf-8"))


def leaf_prefix_plan(
    repo_root: Path,
    package_name: str,
    profile: str,
    line: str,
    rootfs_sha256: str,
    rootfs: dict[str, Any],
) -> dict[str, Any]:
    package = PACKAGE_GENERATORS[package_name]
    cuda_version = rootfs["components"]["cuda_toolkit"]["version"]
    toolchain_identifier, toolchain_env = registered_toolchain(repo_root, profile, rootfs)
    cc_ref = toolchain_ref(toolchain_identifier)
    plan = {
        "schema": 1,
        "profile": profile,
        "line": line,
        "package": {"name": package_name, "version": package["version"]},
        "sources": [copy.deepcopy(package["source"])],
        "inputs": [],
        "toolchain": {
            "cc": cc_ref,
            "cxx": cc_ref,
            "cuda": f"rootfs:cuda@{cuda_version}",
            "env": toolchain_env,
            "python": "python_313_native",
            "rootfs_identity": {
                "label": f"//plans:{profile}/{line}/rootfs.identity.json",
                "sha256": rootfs_sha256,
            },
        },
        "deps": {},
        "configure": copy.deepcopy(package["configure"]),
        "build": copy.deepcopy(package["build"]),
        "install": copy.deepcopy(package["install"]),
        "outputs": copy.deepcopy(package["outputs"]),
        "gpu_archs": [],
        "determinism": {
            "prefix_map": {
                "{out}": ".",
                "{work}": ".",
            },
            "seed": "derive:sha256",
            "source_date_epoch": package["source_date_epoch"],
        },
        "live_checks": copy.deepcopy(rootfs["live_checks"]),
    }
    plan["determinism"]["seed"] = "sha256:" + plan_seed(plan)
    return plan


def zlib_ng_plan(repo_root: Path, profile: str, line: str, rootfs_sha256: str, rootfs: dict[str, Any]) -> dict[str, Any]:
    return leaf_prefix_plan(repo_root, "zlib-ng", profile, line, rootfs_sha256, rootfs)


def manifest_label(prefix_file_label: str) -> str:
    suffix = "//:prefix_path.txt"
    if prefix_file_label.startswith("@") and prefix_file_label.endswith(suffix):
        return prefix_file_label[: -len("prefix_path.txt")] + "prefix_manifest.json"
    raise ValueError(f"unsupported dependency prefix label {prefix_file_label!r}")


def dep_plan(deps: dict[str, str]) -> dict[str, Any]:
    planned: dict[str, Any] = {}
    for key, label in sorted(deps.items()):
        if label == "//native/pytorch:pytorch_action":
            planned[key] = {
                "label": label,
                "manifest": "action://native/pytorch:pytorch_action_prefix_manifest",
                "key": "action://native/pytorch:pytorch_action_prefix_manifest",
            }
        elif label.startswith("//native/") and label.endswith("_action_prefix"):
            package_path = label[2:].split(":", 1)[0]
            target_name = label.rsplit(":", 1)[1]
            planned[key] = {
                "label": label,
                "manifest": "action://" + package_path + ":" + target_name + "_manifest",
                "key": "action://" + package_path + ":" + target_name + "_manifest",
            }
        else:
            manifest = manifest_label(label)
            planned[key] = {
                "label": label,
                "manifest": manifest,
                "key": "manifest:" + manifest,
            }
    return planned


def triton_dep_plan(deps: dict[str, str]) -> dict[str, Any]:
    return dep_plan(deps)


def triton_env_script(package: dict[str, Any]) -> str:
    site = package["python_site_packages"]
    prefix_keys = [
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
    ]
    assignments = "\n".join(
        f'{key.replace("-", "_")}="$(abs_prefix "$1")"\nshift'
        for key in prefix_keys
    )
    pythonpath_parts = [
        f"${key.replace('-', '_')}/{site}"
        for key in ("py-pip", "py-setuptools", "py-wheel", "py-filelock", "py-lit", "py-pybind11", "python-venv")
    ]
    cmake_prefix_parts = [
        "$python",
        "$python_venv",
        "$cmake",
        "$ninja",
        "$llvm",
        "$nlohmann_json",
        "$py_pybind11",
        "$zlib_ng",
        "$cuda",
    ]
    path_parts = [
        "$python_venv/bin",
        "$python/bin",
        "$py_pip/bin",
        "$py_wheel/bin",
        "$py_lit/bin",
        "$cmake/bin",
        "$ninja/bin",
        "$llvm/bin",
        "$cuda/bin",
        "/usr/bin",
        "/bin",
    ]
    return "\n".join(
        [
            "set -euo pipefail",
            'env_file="$1"',
            "shift",
            'root="$PWD"',
            'abs_prefix() ( case "$1" in /*) printf "%s\\n" "$1" ;; *) printf "%s/%s\\n" "$root" "$1" ;; esac )',
            assignments,
            'compiler_prefix_map_flags=""',
            'cuda_prefix_map_flags=""',
            'for logical_map_root in '
            + " ".join("$" + key.replace("-", "_") for key in prefix_keys)
            + '; do',
            '  resolved_map_root="$(readlink -f "$logical_map_root" 2>/dev/null || printf "%s" "$logical_map_root")"',
            '  for map_root in "$logical_map_root" "$resolved_map_root"; do',
            '    compiler_prefix_map_flags="$compiler_prefix_map_flags -ffile-prefix-map=$map_root=. -fdebug-prefix-map=$map_root=. -fmacro-prefix-map=$map_root=."',
            '    cuda_prefix_map_flags="$cuda_prefix_map_flags -Xcompiler=-ffile-prefix-map=$map_root=. -Xcompiler=-fdebug-prefix-map=$map_root=. -Xcompiler=-fmacro-prefix-map=$map_root=."',
            "  done",
            "done",
            'mkdir -p "$(dirname "$env_file")" "{work}/home" "{work}/pip-cache"',
            'cat > "$env_file" <<EOF',
            "set -euo pipefail",
            "export PIP_DISABLE_PIP_VERSION_CHECK=1",
            "export PIP_NO_INDEX=1",
            "export PIP_NO_INPUT=1",
            "export PYTHONHOME=",
            "export PYTHONNOUSERSITE=1",
            "export TRITON_OFFLINE_BUILD=1",
            "export CFLAGS=\"$CFLAGS $compiler_prefix_map_flags\"",
            "export CXXFLAGS=\"$CXXFLAGS $compiler_prefix_map_flags\"",
            "export CUDAFLAGS=\"$CUDAFLAGS $cuda_prefix_map_flags\"",
            "export NVCC_FLAGS=\"$NVCC_FLAGS $cuda_prefix_map_flags\"",
            "export TRITON_APPEND_CMAKE_ARGS=\"-DCMAKE_C_FLAGS='$CFLAGS $compiler_prefix_map_flags' -DCMAKE_CXX_FLAGS='$CXXFLAGS $compiler_prefix_map_flags' -DCMAKE_CUDA_FLAGS='$CUDAFLAGS $cuda_prefix_map_flags'\"",
            "export CMAKE_C_COMPILER=\"$CC\"",
            "export CMAKE_CXX_COMPILER=\"$CXX\"",
            "export LLVM_SYSPATH=\"$llvm\"",
            "export JSON_SYSPATH=\"$nlohmann_json\"",
            "export PYBIND11_SYSPATH=\"$py_pybind11\"",
            "export LIBRARY_PATH=\"$zlib_ng/lib\"",
            "export LDFLAGS=\"-L$zlib_ng/lib\"",
            "export TRITON_PTXAS_PATH=\"$cuda/bin/ptxas\"",
            "export TRITON_PTXAS_BLACKWELL_PATH=\"$cuda/bin/ptxas\"",
            "export TRITON_CUOBJDUMP_PATH=\"$cuda/bin/cuobjdump\"",
            "export TRITON_NVDISASM_PATH=\"$cuda/bin/nvdisasm\"",
            "export TRITON_CUDACRT_PATH=\"$cuda/include\"",
            "export TRITON_CUDART_PATH=\"$cuda/include\"",
            "export TRITON_CUPTI_PATH=\"$cuda\"",
            "export TRITON_CUPTI_INCLUDE_PATH=\"$cuda/include\"",
            "export TRITON_CUPTI_LIB_PATH=\"$cuda/lib64\"",
            "export TRITON_CUPTI_LIB_BLACKWELL_PATH=\"$cuda/lib64\"",
            "export TRITON_LIBDEVICE_PATH=\"$cuda/nvvm/libdevice/libdevice.10.bc\"",
            "export CUDA_HOME=\"$cuda\"",
            "export CUDA_PATH=\"$cuda\"",
            f"export PYTHONPATH=\"{':'.join(pythonpath_parts)}\"",
            f"export CMAKE_PREFIX_PATH=\"{':'.join(cmake_prefix_parts)}\"",
            f"export PATH=\"{':'.join(path_parts)}\"",
            "export HOME=\"{work}/home\"",
            "export PIP_CACHE_DIR=\"{work}/pip-cache\"",
            "EOF",
        ]
    )


def triton_prefix_placeholders(package: dict[str, Any]) -> list[str]:
    return ["{prefix:" + key + "}" for key in package["deps"]]


def triton_plan(repo_root: Path, profile: str, line: str, rootfs_sha256: str, rootfs: dict[str, Any]) -> dict[str, Any]:
    package = PACKAGE_GENERATORS["py-triton"]
    cuda_version = rootfs["components"]["cuda_toolkit"]["version"]
    toolchain_identifier, toolchain_env = registered_toolchain(repo_root, profile, rootfs)
    cc_ref = toolchain_ref(toolchain_identifier)
    env_script = "{work}/triton-build-env.sh"
    plan = {
        "schema": 1,
        "profile": profile,
        "line": line,
        "package": {"name": "py-triton", "version": package["version"]},
        "sources": [copy.deepcopy(package["source"])],
        "inputs": [{"path": "native/triton/upstream_pins.json", "sha256": sha256_file(repo_root / "native" / "triton" / "upstream_pins.json")}],
        "toolchain": {
            "cc": cc_ref,
            "cxx": cc_ref,
            "cuda": f"rootfs:cuda@{cuda_version}",
            "env": toolchain_env,
            "python": "python_313_native",
            "rootfs_identity": {
                "label": f"//plans:{profile}/{line}/rootfs.identity.json",
                "sha256": rootfs_sha256,
            },
        },
        "deps": triton_dep_plan(package["deps"]),
        "configure": {
            "cwd": ".",
            "argv": [
                "bash",
                "-c",
                triton_env_script(package),
                "configure",
                env_script,
                *triton_prefix_placeholders(package),
            ],
            "env": {},
        },
        "build": {
            "cwd": ".",
            "argv": [
                "bash",
                "-c",
                (
                    "set -euo pipefail; "
                    "source \"$1\"; "
                    "rm -rf \"{work}/wheelhouse\"; "
                    "mkdir -p \"{work}/wheelhouse\"; "
                    "python3 -m pip wheel --no-deps --no-build-isolation --no-index --no-cache-dir "
                    "-w \"{work}/wheelhouse\" \"{src}\"; "
                    "python3 -c 'import os, pathlib, sys, zipfile\n"
                    "wheelhouse = pathlib.Path(sys.argv[1])\n"
                    "source_root = str(pathlib.Path(sys.argv[2]).resolve()).encode(\"utf-8\")\n"
                    "wheels = sorted(wheelhouse.glob(\"triton-*.whl\"))\n"
                    "if len(wheels) != 1:\n"
                    "    raise SystemExit(\"expected exactly one Triton wheel\")\n"
                    "wheel = wheels[0]\n"
                    "tmp = wheel.with_name(wheel.name + \".tmp\")\n"
                    "with zipfile.ZipFile(wheel, \"r\") as src_zip, zipfile.ZipFile(tmp, \"w\") as dst_zip:\n"
                    "    for info in src_zip.infolist():\n"
                    "        data = src_zip.read(info.filename)\n"
                    "        if info.filename.endswith(\".ll\"):\n"
                    "            data = data.replace(source_root, b\".\")\n"
                    "        dst_zip.writestr(info, data)\n"
                    "os.replace(tmp, wheel)\n"
                    "' \"{work}/wheelhouse\" \"{src}\""
                ),
                "build",
                env_script,
            ],
            "env": {},
        },
        "install": {
            "cwd": ".",
            "argv": [
                "bash",
                "-c",
                (
                    "set -euo pipefail; "
                    "source \"$1\"; "
                    "set -- \"{work}/wheelhouse\"/triton-*.whl; "
                    "if [ \"$#\" -ne 1 ] || [ ! -f \"$1\" ]; then "
                    "echo 'expected exactly one Triton wheel' >&2; exit 2; "
                    "fi; "
                    "wheel=\"$1\"; "
                    "python3 -m pip install --no-deps --ignore-installed --no-build-isolation --no-compile "
                    "--no-warn-script-location --no-index --no-cache-dir --prefix \"{out}\" \"$wheel\"; "
                    "python3 -c 'import pathlib, shutil, sys\n"
                    "prefix = pathlib.Path(sys.argv[1])\n"
                    "for pyc in prefix.rglob(\"*.pyc\"):\n"
                    "    if not pyc.is_symlink():\n"
                    "        pyc.unlink()\n"
                    "for pycache in sorted(prefix.rglob(\"__pycache__\"), reverse=True):\n"
                    "    if pycache.is_dir() and not pycache.is_symlink():\n"
                    "        shutil.rmtree(pycache)\n"
                    "for direct_url in prefix.rglob(\"direct_url.json\"):\n"
                    "    if direct_url.parent.name.endswith(\".dist-info\") and direct_url.is_file() and not direct_url.is_symlink():\n"
                    "        direct_url.unlink()\n"
                    "        record = direct_url.parent / \"RECORD\"\n"
                    "        if record.is_file():\n"
                    "            record.write_text(\"\".join(line for line in record.read_text(encoding=\"utf-8\").splitlines(True) if not line.split(\",\", 1)[0].endswith(\"/direct_url.json\")), encoding=\"utf-8\")\n"
                    "bin_dir = prefix / \"bin\"\n"
                    "if bin_dir.is_dir():\n"
                    "    for script in sorted(bin_dir.iterdir()):\n"
                    "        if script.is_symlink() or not script.is_file():\n"
                    "            continue\n"
                    "        data = script.read_bytes()\n"
                    "        first, sep, rest = data.partition(b\"\\n\")\n"
                    "        if not sep or not first.startswith(b\"#!\"):\n"
                    "            continue\n"
                    "        interp = first[2:].strip().split(maxsplit=1)[0]\n"
                    "        name = pathlib.PurePosixPath(interp.decode(\"utf-8\", \"ignore\")).name\n"
                    "        if name.startswith(\"python\"):\n"
                    "            script.write_bytes(b\"#!/usr/bin/env python3\\n\" + rest)\n"
                    "' \"{out}\"; "
                    "mkdir -p \"$(dirname \"{wheel}\")\"; "
                    "cp \"$wheel\" \"{wheel}\""
                ),
                "install",
                env_script,
            ],
            "env": {},
        },
        "outputs": copy.deepcopy(package["outputs"]),
        "gpu_archs": copy.deepcopy(package["gpu_archs"]),
        "determinism": {
            "prefix_map": {
                "{out}": ".",
                "{work}": ".",
            },
            "seed": "derive:sha256",
            "source_date_epoch": package["source_date_epoch"],
        },
        "live_checks": copy.deepcopy(rootfs["live_checks"]),
    }
    plan["determinism"]["seed"] = "sha256:" + plan_seed(plan)
    return plan


def shell_var(prefix_key: str) -> str:
    return prefix_key.replace("-", "_")


def pytorch_env_script(package: dict[str, Any]) -> str:
    site = package["python_site_packages"]
    prefix_keys = list(package["build_deps"])
    assignments = "\n".join(
        f'{shell_var(key)}="$(abs_prefix "$1")"\nshift'
        for key in prefix_keys
    )
    pythonpath_parts = [
        f"${shell_var(key)}/{site}"
        for key in (
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
    ]
    path_parts = [
        "$python/bin",
        "$cmake/bin",
        "$ninja/bin",
        "$cuda/bin",
        "$py_pip/bin",
        "$py_wheel/bin",
        "/usr/bin",
        "/bin",
    ]
    cmake_prefix_parts = [
        "$python",
        "$cmake",
        "$protobuf",
        "$openblas",
        "$cusparselt",
        "$cudss",
        "$nvshmem",
        "$openmpi",
        "$numactl",
        "$cpuinfo",
        "$fp16",
        "$fxdiv",
        "$psimd",
        "$pthreadpool",
    ]
    return "\n".join(
        [
            "set -euo pipefail",
            'env_file="$1"',
            "shift",
            'root="$PWD"',
            'abs_prefix() ( case "$1" in /*) printf "%s\\n" "$1" ;; *) printf "%s/%s\\n" "$root" "$1" ;; esac )',
            assignments,
            'first_provider_libdir() (',
            '  local prefix="$1"',
            '  shift',
            '  local libdir name',
            '  for libdir in lib lib64 lib/x86_64-linux-gnu; do',
            '    for name in "$@"; do',
            '      if [ -e "$prefix/$libdir/$name" ]; then',
            '        printf "%s\\n" "$prefix/$libdir"',
            '        return 0',
            '      fi',
            '    done',
            '  done',
            '  printf "%s/lib\\n" "$prefix"',
            ')',
            'first_existing_library() (',
            '  local prefix="$1"',
            '  shift',
            '  local libdir name',
            '  for libdir in lib lib64 lib/x86_64-linux-gnu; do',
            '    for name in "$@"; do',
            '      if [ -e "$prefix/$libdir/$name" ]; then',
            '        printf "%s\\n" "$prefix/$libdir/$name"',
            '        return 0',
            '      fi',
            '    done',
            '  done',
            '  printf "%s/lib/%s\\n" "$prefix" "$1"',
            ')',
            'normalize_aten_config_include_dir() (',
            '  local template="{src}/aten/src/ATen/ATenConfig.cmake.in"',
            '  local lists="{src}/aten/src/ATen/CMakeLists.txt"',
            '  [ -f "$template" ] || return 0',
            '  python3 - "$template" "$lists" <<\'PY\'',
            'import pathlib',
            'import sys',
            '# ATEN_INCLUDE_DIR is the wheel build\'s temporary install prefix; point it at the',
            '# installed config\'s own location (torch/share/cmake/ATen -> torch/include).',
            'template = pathlib.Path(sys.argv[1])',
            'lists = pathlib.Path(sys.argv[2])',
            'lb = chr(123)',
            'rb = chr(125)',
            'broken = \'set(ATEN_INCLUDE_DIR "@ATEN_INCLUDE_DIR@")\'',
            'fixed = \'get_filename_component(ATEN_INCLUDE_DIR "$\' + lb + \'CMAKE_CURRENT_LIST_DIR\' + rb + \'/../../../include" ABSOLUTE)\'',
            'text = template.read_text(encoding="utf-8")',
            'if fixed not in text:',
            '    if text.count(broken) != 1:',
            '        raise SystemExit("cannot make ATenConfig.cmake relocatable; ATEN_INCLUDE_DIR template line is missing")',
            '    template.write_text(text.replace(broken, fixed, 1), encoding="utf-8")',
            'configure = \'configure_file(ATenConfig.cmake.in "$\' + lb + \'CMAKE_CURRENT_BINARY_DIR\' + rb + \'/cmake-exports/ATenConfig.cmake")\'',
            'only = configure[:-1] + " @ONLY)"',
            'text = lists.read_text(encoding="utf-8")',
            'if only not in text:',
            '    if text.count(configure) != 1:',
            '        raise SystemExit("cannot make ATenConfig.cmake relocatable; configure_file line is missing")',
            '    lists.write_text(text.replace(configure, only, 1), encoding="utf-8")',
            'PY',
            ')',
            'normalize_macros_build_flags() (',
            '  local path="{src}/cmake/Codegen.cmake"',
            '  [ -f "$path" ] || return 0',
            '  python3 - "$path" <<\'PY\'',
            'import pathlib',
            'import sys',
            '# caffe2/core/macros.h embeds CMAKE_CXX_FLAGS and CMAKE_CUDA_FLAGS as build strings.',
            '# Our -f*-prefix-map flags name action-local paths, so strip them for that one header.',
            'path = pathlib.Path(sys.argv[1])',
            'text = path.read_text(encoding="utf-8")',
            'marker = "# vaso: strip prefix-map flags from macros.h build strings"',
            'if marker in text:',
            '    raise SystemExit(0)',
            'anchor = "# ---[ Write the macros file\\n"',
            'if text.count(anchor) != 1:',
            '    raise SystemExit("cannot strip prefix-map flags from macros.h; Codegen.cmake anchor is missing")',
            'lb = chr(123)',
            'rb = chr(125)',
            'regex = "(-Xcompiler=)?-f(file|debug|macro)-prefix-map=[^ ]*"',
            'block = [marker]',
            'for var in ("CMAKE_CXX_FLAGS", "CMAKE_CUDA_FLAGS"):',
            '    block.append("set(VASO_SAVED_" + var + " \\"$" + lb + var + rb + "\\")")',
            '    block.append("string(REGEX REPLACE \\"" + regex + "\\" \\"\\" " + var + " \\"$" + lb + var + rb + "\\")")',
            'path.write_text(text.replace(anchor, "\\n".join(block) + "\\n" + anchor, 1), encoding="utf-8")',
            'text = path.read_text(encoding="utf-8")',
            'end = "    $" + lb + "CMAKE_BINARY_DIR" + rb + "/caffe2/core/macros.h)\\n"',
            'if text.count(end) != 1:',
            '    raise SystemExit("cannot strip prefix-map flags from macros.h; configure_file anchor is missing")',
            'restore = "".join("set(" + var + " \\"$" + lb + "VASO_SAVED_" + var + rb + "\\")\\n" for var in ("CMAKE_CXX_FLAGS", "CMAKE_CUDA_FLAGS"))',
            'path.write_text(text.replace(end, end + restore, 1), encoding="utf-8")',
            'PY',
            ')',
            'normalize_cudss_csr_row_end() (',
            '  local path="{src}/aten/src/ATen/native/sparse/cuda/SparseCsrTensorMath.cu"',
            '  [ -f "$path" ] || return 0',
            '  python3 - "$path" <<\'PY\'',
            'import pathlib',
            'import sys',
            'path = pathlib.Path(sys.argv[1])',
            'text = path.read_text(encoding="utf-8")',
            'broken = "rowOffsets, rowOffsets + crow.size(0), colIndices, values_ptr"',
            'fixed = "rowOffsets, nullptr, colIndices, values_ptr"',
            'if fixed in text:',
            '    raise SystemExit(0)',
            'if broken not in text:',
            '    if "cudssMatrixCreateCsr" in text:',
            '        raise SystemExit("cannot patch cuDSS CSR rowEnd; expected PyTorch v2.14.0 cudssMatrixCreateCsr argument snippet is missing")',
            '    raise SystemExit(0)',
            'path.write_text(text.replace(broken, fixed, 1), encoding="utf-8")',
            'PY',
            ')',
            'normalize_externalized_psimd_source() (',
            '  local dir="{src}/third_party/psimd"',
            '  local sentinel',
            '  for sentinel in CMakeLists.txt Makefile setup.py LICENSE LICENSE.md LICENSE.txt; do',
            '    [ -e "$dir/$sentinel" ] && return 0',
            '  done',
            '  mkdir -p "$dir"',
            '  local unexpected',
            '  unexpected="$(find "$dir" -mindepth 1 -maxdepth 1 ! -name .git -printf "%f\\n" | LC_ALL=C sort | paste -sd, -)"',
            '  if [ -n "$unexpected" ]; then',
            '    echo "third_party/psimd has no PyTorch prebuild sentinel but is not an empty externalized submodule: $unexpected" >&2',
            '    exit 2',
            '  fi',
            '  if [ ! -f "$psimd/include/psimd.h" ]; then',
            '    echo "native psimd header is missing: $psimd/include/psimd.h" >&2',
            '    exit 2',
            '  fi',
            '  python3 - "$dir/CMakeLists.txt" "$psimd/include" <<\'PY\'',
            'import pathlib',
            'import sys',
            'path = pathlib.Path(sys.argv[1])',
            'include = str(pathlib.Path(sys.argv[2])).replace("\\\\", "/").replace(\'"\', \'\\\\"\')',
            'path.write_text("\\n".join([',
            '    "cmake_minimum_required(VERSION 3.5)",',
            '    "project(psimd_native_prefix NONE)",',
            '    "add_library(psimd INTERFACE)",',
            '    \'target_include_directories(psimd INTERFACE "\' + include + \'")\',',
            '    "",',
            ']) + "\\n", encoding="utf-8")',
            'PY',
            ')',
            'normalize_top_level_helper_imports() (',
            '  local path="{src}/cmake/Dependencies.cmake"',
            '  [ -f "$path" ] || return 0',
            '  python3 - "$path" "$cpuinfo/include/cpuinfo.h" "$pthreadpool/include/pthreadpool.h" <<\'PY\'',
            'import pathlib',
            'import sys',
            'path = pathlib.Path(sys.argv[1])',
            'cpuinfo_header = pathlib.Path(sys.argv[2])',
            'pthreadpool_header = pathlib.Path(sys.argv[3])',
            'text = path.read_text(encoding="utf-8")',
            'lines = text.splitlines(keepends=True)',
            'changed = False',
            'lbrace = chr(123)',
            'rbrace = chr(125)',
            'for target, library_var, source_var, header in (',
            '    ("cpuinfo", "CPUINFO_LIBRARY", "CPUINFO_SOURCE_DIR", cpuinfo_header),',
            '    ("pthreadpool", "PTHREADPOOL_LIBRARY", "PTHREADPOOL_SOURCE_DIR", pthreadpool_header),',
            '):',
            '    source_ref = "$" + lbrace + source_var + rbrace',
            '    library_ref = "$" + lbrace + library_var + rbrace',
            '    property_line = \'set_property(TARGET \' + target + \' PROPERTY INTERFACE_INCLUDE_DIRECTORIES "\' + source_ref + \'/include")\'',
            '    if property_line in "".join(lines):',
            '        continue',
            '    location_lines = (',
            '        \'set_property(TARGET \' + target + \' PROPERTY IMPORTED_LOCATION "\' + library_ref + \'")\',',
            '        \'set_target_properties(\' + target + \' PROPERTIES IMPORTED_LOCATION "\' + library_ref + \'")\',',
            '    )',
            '    patch_index = next((i for i, line in enumerate(lines) if line.strip() in location_lines), None)',
            '    if patch_index is None:',
            '        continue',
            '    if not header.is_file():',
            '        raise SystemExit("native " + target + " header is missing: " + str(header))',
            '    indent = lines[patch_index][:len(lines[patch_index]) - len(lines[patch_index].lstrip())]',
            '    lines.insert(patch_index + 1, indent + property_line + "\\n")',
            '    changed = True',
            'if changed:',
            '    path.write_text("".join(lines), encoding="utf-8")',
            'PY',
            ')',
            'normalize_qnnpack_helper_imports() (',
            '  local path="{src}/aten/src/ATen/native/quantized/cpu/qnnpack/CMakeLists.txt"',
            '  [ -f "$path" ] || return 0',
            '  python3 - "$path" "$psimd/include" "$pthreadpool/include" <<\'PY\'',
            'import pathlib',
            'import sys',
            'path = pathlib.Path(sys.argv[1])',
            'psimd_include = pathlib.Path(sys.argv[2])',
            'pthreadpool_include = pathlib.Path(sys.argv[3])',
            'text = path.read_text(encoding="utf-8")',
            'changed = False',
            'def cmake_escape(value):',
            '    return str(value).replace(chr(92), "/").replace(chr(34), chr(92) + chr(34))',
            'lbrace = chr(123)',
            'rbrace = chr(125)',
            'psimd_property = \'  set_property(TARGET psimd PROPERTY INTERFACE_INCLUDE_DIRECTORIES "\' + cmake_escape(psimd_include) + \'")\'',
            'if psimd_property not in text:',
            '    psimd_needle = "  set_property(TARGET psimd PROPERTY LINKER_LANGUAGE C)"',
            '    if psimd_needle in text:',
            '        header = psimd_include / "psimd.h"',
            '        if not header.is_file():',
            '            raise SystemExit("native psimd header is missing: " + str(header))',
            '        text = text.replace(psimd_needle, psimd_needle + "\\n" + psimd_property, 1)',
            '        changed = True',
            'pthreadpool_header = pthreadpool_include / "pthreadpool.h"',
            'if not pthreadpool_header.is_file():',
            '    raise SystemExit("native pthreadpool header is missing: " + str(pthreadpool_header))',
            'pthreadpool_property = \'INTERFACE_INCLUDE_DIRECTORIES "\' + cmake_escape(pthreadpool_include) + \'"\'',
            'if pthreadpool_property not in text:',
            '    pthreadpool_ref = "$" + lbrace + "PTHREADPOOL_LIBRARY" + rbrace',
            '    needle = \'  set_target_properties(pthreadpool PROPERTIES\\n    IMPORTED_LOCATION "\' + pthreadpool_ref + \'")\'',
            '    if needle not in text:',
            '        raise SystemExit("cannot patch qnnpack pthreadpool import; expected PyTorch v2.14.0 CMake snippet is missing")',
            '    replacement = \'  set_target_properties(pthreadpool PROPERTIES\\n    IMPORTED_LOCATION "\' + pthreadpool_ref + \'"\\n    \' + pthreadpool_property + ")"',
            '    text = text.replace(needle, replacement, 1)',
            '    changed = True',
            'if changed:',
            '    path.write_text(text, encoding="utf-8")',
            'PY',
            ')',
            'normalize_nnpack_peachpy_launcher() (',
            '  local path="{src}/third_party/NNPACK/CMakeLists.txt"',
            '  [ -f "$path" ] || return 0',
            '  python3 - "$path" <<\'PY\'',
            'import pathlib',
            'import sys',
            'path = pathlib.Path(sys.argv[1])',
            'text = path.read_text(encoding="utf-8")',
            'lbrace = chr(123)',
            'rbrace = chr(125)',
            'cmake_command = "$" + lbrace + "CMAKE_COMMAND" + rbrace',
            'peachpy_pythonpath = "$" + lbrace + "PEACHPY_PYTHONPATH" + rbrace',
            'fixed = \'COMMAND "\' + cmake_command + \'" -E env "PYTHONPATH=\' + peachpy_pythonpath + \'"\'',
            'broken = \'COMMAND "PYTHONPATH=\' + peachpy_pythonpath + \'"\'',
            'if fixed not in text and broken in text:',
            '    path.write_text(text.replace(broken, fixed, 1), encoding="utf-8")',
            'PY',
            ')',
            'normalize_peachpy_symbol_order() (',
            '  local path="{src}/third_party/python-peachpy/peachpy/name.py"',
            '  python3 - "$path" <<\'PYTHON\'',
            'import pathlib',
            'import sys',
            'path = pathlib.Path(sys.argv[1])',
            'text = path.read_text(encoding="utf-8")',
            '# Name hashes include object identity. Keep insertion order when allocating',
            '# NNPACK local symbol names, independently of address layout or hash seed.',
            'replacements = (',
            '    ("self.prenames.setdefault(scope_name.prename, set())", "self.prenames.setdefault(scope_name.prename, dict())"),',
            '    ("self.prenames[scope_name.prename].add(scope)", "self.prenames[scope_name.prename][scope] = None"),',
            ')',
            'for old, new in replacements:',
            '    if new not in text:',
            '        if text.count(old) != 1:',
            '            raise SystemExit("cannot stabilize PeachPy names; upstream allocation changed")',
            '        text = text.replace(old, new, 1)',
            'path.write_text(text, encoding="utf-8")',
            'PYTHON',
            ')',
            'normalize_peachpy_symbol_order',
            'normalize_cudss_csr_row_end',
            'normalize_externalized_psimd_source',
            'normalize_macros_build_flags',
            'normalize_aten_config_include_dir',
            'normalize_top_level_helper_imports',
            'normalize_qnnpack_helper_imports',
            'normalize_nnpack_peachpy_launcher',
            'compiler_prefix_map_flags=""',
            'cuda_prefix_map_flags=""',
            'for logical_map_root in '
            + " ".join("$" + shell_var(key) for key in prefix_keys)
            + '; do',
            '  resolved_map_root="$(readlink -f "$logical_map_root" 2>/dev/null || printf "%s" "$logical_map_root")"',
            '  for map_root in "$logical_map_root" "$resolved_map_root"; do',
            '    compiler_prefix_map_flags="$compiler_prefix_map_flags -ffile-prefix-map=$map_root=. -fdebug-prefix-map=$map_root=. -fmacro-prefix-map=$map_root=."',
            '    cuda_prefix_map_flags="$cuda_prefix_map_flags -Xcompiler=-ffile-prefix-map=$map_root=. -Xcompiler=-fdebug-prefix-map=$map_root=. -Xcompiler=-fmacro-prefix-map=$map_root=."',
            "  done",
            "done",
            'mkdir -p "$(dirname "$env_file")" "{work}/home" "{work}/pip-cache"',
            'cudnn_library="$(first_provider_libdir "$cudnn" libcudnn.so libcudnn_static.a)"',
            'nccl_lib_dir="$(first_provider_libdir "$nccl" libnccl.so libnccl_static.a)"',
            'cudss_library="$(first_existing_library "$cudss" libcudss.so libcudss_static.a)"',
            'py_six_source_dir="$py_six/' + site + '"',
            'cat > "$env_file" <<EOF',
            "set -euo pipefail",
            "export USE_GFLAGS=0",
            "export USE_GLOG=0",
            "export USE_MKL=0",
            "export USE_MKLDNN=0",
            "export USE_MPI=1",
            "export USE_NUMA=1",
            "export USE_TENSORPIPE=1",
            "export USE_RPC=1",
            "export USE_TENSORRT=0",
            "export USE_XPU=0",
            "export USE_ROCM=0",
            "export USE_MPS=0",
            "export USE_OPENMP=1",
            "export USE_FBGEMM=1",
            "export USE_QNNPACK=1",
            "export USE_XNNPACK=1",
            "export USE_NNPACK=1",
            "export USE_KINETO=1",
            "export USE_GLOO=1",
            "export USE_FLASH_ATTENTION=1",
            "export USE_MEM_EFF_ATTENTION=1",
            "export USE_MAGMA=0",
            "export TH_BINARY_BUILD=1",
            "export ATEN_STATIC_CUDA=0",
            "export USE_CUDA_STATIC_LINK=0",
            "export USE_STATIC_CUDNN=0",
            "export _GLIBCXX_USE_CXX11_ABI=1",
            "export COLORIZE_OUTPUT=0",
            "export BUILD_TEST=0",
            "export BUILD_BINARY=0",
            "export BUILD_CAFFE2_OPS=0",
            "export BUILD_CUSTOM_PROTOBUF=OFF",
            "export USE_STATIC_DISPATCH=0",
            "export PYTORCH_BUILD_NUMBER=1",
            "export USE_CUDA=1",
            "export USE_CUDNN=1",
            "export USE_NCCL=1",
            "export USE_DISTRIBUTED=1",
            "export USE_SYSTEM_NCCL=1",
            "export USE_STATIC_NCCL=0",
            "export USE_CUSPARSELT=1",
            "export USE_CUDSS=1",
            "export USE_NVSHMEM=1",
            "export USE_SYSTEM_CPUINFO=1",
            "export USE_SYSTEM_FXDIV=1",
            "export USE_SYSTEM_PSIMD=1",
            "export USE_SYSTEM_PTHREADPOOL=1",
            "export ONNX_BUILD_CUSTOM_PROTOBUF=OFF",
            "export ONNX_USE_PROTOBUF_SHARED_LIBS=ON",
            f"export TORCH_CUDA_ARCH_LIST={package['torch_cuda_arch_list']}",
            f"export PYTORCH_BUILD_VERSION={package['version']}",
            "export PYTHONHOME=",
            "export PYTHONNOUSERSITE=1",
            "export PIP_DISABLE_PIP_VERSION_CHECK=1",
            "export PIP_NO_INDEX=1",
            "export PIP_NO_INPUT=1",
            "export CFLAGS=\"$CFLAGS $compiler_prefix_map_flags\"",
            "export CXXFLAGS=\"$CXXFLAGS $compiler_prefix_map_flags\"",
            # Modern CMake CUDA compilation reads CUDAFLAGS; retain the executor's
            # seeded nvcc flags for separable compilation and device linking too.
            "export CUDAFLAGS=\"$CUDAFLAGS $NVCC_FLAGS $cuda_prefix_map_flags\"",
            "export NVCC_FLAGS=\"$NVCC_FLAGS $cuda_prefix_map_flags\"",
            "export CMAKE_C_COMPILER=\"$CC\"",
            "export CMAKE_CXX_COMPILER=\"$CXX\"",
            "export CMAKE_ARGS=\"-DCPUINFO_SOURCE_DIR=$cpuinfo -DPYTHON_SIX_SOURCE_DIR=$py_six_source_dir -DPYTHON_PEACHPY_SOURCE_DIR={src}/third_party/python-peachpy -DPTHREADPOOL_SOURCE_DIR=$pthreadpool\"",
            "export CUDA_HOME=\"$cuda\"",
            "export CUDA_TOOLKIT_ROOT_DIR=\"$cuda\"",
            "export CUDA_PATH=\"$cuda\"",
            "export CUDNN_ROOT=\"$cudnn\"",
            "export CUDNN_INCLUDE_DIR=\"$cudnn/include\"",
            "export CUDNN_LIBRARY=\"$cudnn_library\"",
            "export NCCL_ROOT=\"$nccl\"",
            "export NCCL_INCLUDE_DIR=\"$nccl/include\"",
            "export NCCL_LIB_DIR=\"$nccl_lib_dir\"",
            "export BLAS=OpenBLAS",
            "export OpenBLAS_HOME=\"$openblas\"",
            "export CUSPARSELT_ROOT=\"$cusparselt\"",
            "export CUDSS_ROOT=\"$cudss\"",
            "export CUDSS_INCLUDE_DIR=\"$cudss/include\"",
            "export CUDSS_LIBRARY=\"$cudss_library\"",
            "export NVSHMEM_HOME=\"$nvshmem\"",
            "export MPI_HOME=\"$openmpi\"",
            "export NUMA_ROOT=\"$numactl\"",
            "export PROTOBUF_PROTOC_EXECUTABLE=\"$protobuf/bin/protoc\"",
            f"export PYTHONPATH=\"{':'.join(pythonpath_parts)}\"",
            f"export PATH=\"{':'.join(path_parts)}\"",
            f"export CMAKE_PREFIX_PATH=\"{':'.join(cmake_prefix_parts)}\"",
            "export MAX_JOBS={jobs}",
            "export CMAKE_BUILD_PARALLEL_LEVEL={jobs}",
            "export HOME=\"{work}/home\"",
            "export PIP_CACHE_DIR=\"{work}/pip-cache\"",
            "EOF",
        ]
    )


def torchvision_env_script(package: dict[str, Any]) -> str:
    site = package["python_site_packages"]
    prefix_keys = list(package["deps"])
    assignments = "\n".join(
        f'{shell_var(key)}="$(abs_prefix "$1")"\nshift'
        for key in prefix_keys
    )
    pythonpath_parts = [
        f"${shell_var(key)}/{site}"
        for key in ("torch", "py-pip", "py-setuptools", "py-wheel", "py-numpy", "py-pillow", "py-filelock", "python-venv")
    ]
    path_parts = [
        "$python_venv/bin",
        "$python/bin",
        "$py_pip/bin",
        "$py_wheel/bin",
        "$ninja/bin",
        "$cuda/bin",
        "$libpng/bin",
        "/usr/bin",
        "/bin",
    ]
    library_parts = [
        "$torch/lib",
        f"$torch/{site}/torch/lib",
        "$cuda/lib64",
        "$cuda/lib",
        "$libjpeg_turbo/lib",
        "$libjpeg_turbo/lib64",
        "$libpng/lib",
        "$libpng/lib64",
        "$zlib_ng/lib",
        "$zlib_ng/lib64",
    ]
    cmake_prefix_parts = [
        "$torch",
        "$python",
        "$cuda",
        "$ninja",
        "$libjpeg_turbo",
        "$libpng",
        "$zlib_ng",
    ]
    return "\n".join(
        [
            "set -euo pipefail",
            'env_file="$1"',
            "shift",
            'root="$PWD"',
            'abs_prefix() ( case "$1" in /*) printf "%s\\n" "$1" ;; *) printf "%s/%s\\n" "$root" "$1" ;; esac )',
            assignments,
            'compiler_prefix_map_flags=""',
            'cuda_prefix_map_flags=""',
            'for logical_map_root in '
            + " ".join("$" + shell_var(key) for key in prefix_keys)
            + '; do',
            '  resolved_map_root="$(readlink -f "$logical_map_root" 2>/dev/null || printf "%s" "$logical_map_root")"',
            '  for map_root in "$logical_map_root" "$resolved_map_root"; do',
            '    compiler_prefix_map_flags="$compiler_prefix_map_flags -ffile-prefix-map=$map_root=. -fdebug-prefix-map=$map_root=. -fmacro-prefix-map=$map_root=."',
            '    cuda_prefix_map_flags="$cuda_prefix_map_flags -Xcompiler=-ffile-prefix-map=$map_root=. -Xcompiler=-fdebug-prefix-map=$map_root=. -Xcompiler=-fmacro-prefix-map=$map_root=."',
            "  done",
            "done",
            'mkdir -p "$(dirname "$env_file")" "{work}/home" "{work}/pip-cache"',
            'cat > "$env_file" <<EOF',
            "set -euo pipefail",
            f"export BUILD_VERSION={package['version']}",
            f"export PYTORCH_VERSION={package['pytorch_version']}",
            "export FORCE_CUDA=1",
            "export TORCH_CUDA_ARCH_LIST=10.0",
            "export TORCHVISION_USE_PNG=1",
            "export TORCHVISION_USE_JPEG=1",
            "export TORCHVISION_USE_WEBP=0",
            "export TORCHVISION_USE_NVJPEG=1",
            "export PYTHONHOME=",
            "export PYTHONNOUSERSITE=1",
            "export PYTHONDONTWRITEBYTECODE=1",
            "export PIP_DISABLE_PIP_VERSION_CHECK=1",
            "export PIP_NO_INDEX=1",
            "export PIP_NO_INPUT=1",
            "export CFLAGS=\"$CFLAGS $compiler_prefix_map_flags\"",
            "export CXXFLAGS=\"$CXXFLAGS $compiler_prefix_map_flags\"",
            "export CUDAFLAGS=\"$CUDAFLAGS $cuda_prefix_map_flags\"",
            "export NVCC_FLAGS=\"$NVCC_FLAGS $cuda_prefix_map_flags\"",
            "export CMAKE_C_COMPILER=\"$CC\"",
            "export CMAKE_CXX_COMPILER=\"$CXX\"",
            "export CUDA_HOME=\"$cuda\"",
            "export CUDA_PATH=\"$cuda\"",
            f"export PYTHONPATH=\"{':'.join(pythonpath_parts)}\"",
            f"export PATH=\"{':'.join(path_parts)}\"",
            f"export LD_LIBRARY_PATH=\"{':'.join(library_parts)}\"",
            f"export LIBRARY_PATH=\"{':'.join(library_parts[2:])}\"",
            "export TORCHVISION_INCLUDE=\"$libjpeg_turbo/include:$libpng/include:$zlib_ng/include\"",
            "export TORCHVISION_LIBRARY=\"$libjpeg_turbo/lib:$libjpeg_turbo/lib64:$libpng/lib:$libpng/lib64:$zlib_ng/lib:$zlib_ng/lib64\"",
            f"export CMAKE_PREFIX_PATH=\"{':'.join(cmake_prefix_parts)}\"",
            "export MAX_JOBS={jobs}",
            "export CMAKE_BUILD_PARALLEL_LEVEL={jobs}",
            "export MAX_CONCURRENCY={jobs}",
            "export HOME=\"{work}/home\"",
            "export PIP_CACHE_DIR=\"{work}/pip-cache\"",
            "EOF",
        ]
    )


def python_wheel_build_command(package_glob: str, *, no_cache_dir: bool = True) -> str:
    cache_arg = " --no-cache-dir" if no_cache_dir else ""
    return (
        "set -euo pipefail; "
        "source \"$1\"; "
        "rm -rf \"{work}/wheelhouse\"; "
        "mkdir -p \"{work}/wheelhouse\"; "
        "python3 -m pip wheel --no-deps --no-build-isolation --no-index" + cache_arg + " "
        "-w \"{work}/wheelhouse\" \"{src}\"; "
        "set -- \"{work}/wheelhouse\"/"
        + package_glob
        + "; "
        "if [ \"$#\" -ne 1 ] || [ ! -f \"$1\" ]; then "
        "echo 'expected exactly one wheel matching "
        + package_glob
        + "' >&2; exit 2; "
        "fi"
    )


def python_wheel_install_command(package_glob: str) -> str:
    return (
        "set -euo pipefail; "
        "source \"$1\"; "
        "set -- \"{work}/wheelhouse\"/"
        + package_glob
        + "; "
        "if [ \"$#\" -ne 1 ] || [ ! -f \"$1\" ]; then "
        "echo 'expected exactly one wheel matching "
        + package_glob
        + "' >&2; exit 2; "
        "fi; "
        "wheel=\"$1\"; "
        "python3 -m pip install --no-deps --ignore-installed --no-build-isolation --no-compile "
        "--no-warn-script-location --no-index --no-cache-dir --prefix \"{out}\" \"$wheel\"; "
        "python3 -c 'import pathlib, shutil, sys\n"
        "prefix = pathlib.Path(sys.argv[1])\n"
        "for pyc in prefix.rglob(\"*.pyc\"):\n"
        "    if not pyc.is_symlink():\n"
        "        pyc.unlink()\n"
        "for pycache in sorted(prefix.rglob(\"__pycache__\"), reverse=True):\n"
        "    if pycache.is_dir() and not pycache.is_symlink():\n"
        "        shutil.rmtree(pycache)\n"
        "for direct_url in prefix.rglob(\"direct_url.json\"):\n"
        "    if direct_url.parent.name.endswith(\".dist-info\") and direct_url.is_file() and not direct_url.is_symlink():\n"
        "        direct_url.unlink()\n"
        "        record = direct_url.parent / \"RECORD\"\n"
        "        if record.is_file():\n"
        "            record.write_text(\"\".join(line for line in record.read_text(encoding=\"utf-8\").splitlines(True) if not line.split(\",\", 1)[0].endswith(\"/direct_url.json\")), encoding=\"utf-8\")\n"
        "bin_dir = prefix / \"bin\"\n"
        "if bin_dir.is_dir():\n"
        "    for script in sorted(bin_dir.iterdir()):\n"
        "        if script.is_symlink() or not script.is_file():\n"
        "            continue\n"
        "        data = script.read_bytes()\n"
        "        first, sep, rest = data.partition(b\"\\n\")\n"
        "        if not sep or not first.startswith(b\"#!\"):\n"
        "            continue\n"
        "        interp = first[2:].strip().split(maxsplit=1)[0]\n"
        "        name = pathlib.PurePosixPath(interp.decode(\"utf-8\", \"ignore\")).name\n"
        "        if name.startswith(\"python\"):\n"
        "            script.write_bytes(b\"#!/usr/bin/env python3\\n\" + rest)\n"
        "' \"{out}\"; "
        "mkdir -p \"$(dirname \"{wheel}\")\"; "
        "cp \"$wheel\" \"{wheel}\""
    )


def torchvision_prefix_placeholders(package: dict[str, Any]) -> list[str]:
    return ["{prefix:" + key + "}" for key in package["deps"]]


def torchaudio_env_script(package: dict[str, Any]) -> str:
    site = package["python_site_packages"]
    prefix_keys = list(package["deps"])
    assignments = "\n".join(
        f'{shell_var(key)}="$(abs_prefix "$1")"\nshift'
        for key in prefix_keys
    )
    pythonpath_parts = [
        f"${shell_var(key)}/{site}"
        for key in ("torch", "py-pip", "py-setuptools", "py-wheel", "py-filelock", "python-venv")
    ]
    path_parts = [
        "$python_venv/bin",
        "$python/bin",
        "$py_pip/bin",
        "$py_wheel/bin",
        "$ninja/bin",
        "$cuda/bin",
        "/usr/bin",
        "/bin",
    ]
    library_parts = [
        "$torch/lib",
        f"$torch/{site}/torch/lib",
        "$cuda/lib64",
        "$cuda/lib",
    ]
    cmake_prefix_parts = [
        "$torch",
        "$python",
        "$cuda",
        "$ninja",
    ]
    patch_extension_helper = (
        "python3 - \"{src}/tools/setup_helpers/extension.py\" <<'PY'\n"
        "import pathlib\n"
        "import sys\n"
        "path = pathlib.Path(sys.argv[1])\n"
        "text = path.read_text(encoding=\"utf-8\")\n"
        "if 'shlex.split(os.environ.get(\"NVCC_FLAGS\", \"\"))' not in text:\n"
        "    if \"import shlex\" not in text:\n"
        "        text = text.replace(\"import os\\n\", \"import os\\nimport shlex\\n\", 1)\n"
        "    needle = '        extra_compile_args[\"nvcc\"] = [\"-O2\", \"-DUSE_CUDA\"]\\n'\n"
        "    if needle not in text:\n"
        "        raise SystemExit(\"torchaudio extension helper no longer has the expected nvcc flag stanza\")\n"
        "    text = text.replace(\n"
        "        needle,\n"
        "        needle\n"
        "        + '        nvcc_flags = shlex.split(os.environ.get(\"NVCC_FLAGS\", \"\"))\\n'\n"
        "        + '        extra_compile_args[\"nvcc\"].extend(nvcc_flags)\\n',\n"
        "        1,\n"
        "    )\n"
        "    path.write_text(text, encoding=\"utf-8\")\n"
        "PY"
    )
    return "\n".join(
        [
            "set -euo pipefail",
            'env_file="$1"',
            "shift",
            'root="$PWD"',
            'abs_prefix() ( case "$1" in /*) printf "%s\\n" "$1" ;; *) printf "%s/%s\\n" "$root" "$1" ;; esac )',
            assignments,
            'compiler_prefix_map_flags=""',
            'cuda_prefix_map_flags=""',
            'for logical_map_root in '
            + " ".join("$" + shell_var(key) for key in prefix_keys)
            + '; do',
            '  resolved_map_root="$(readlink -f "$logical_map_root" 2>/dev/null || printf "%s" "$logical_map_root")"',
            '  for map_root in "$logical_map_root" "$resolved_map_root"; do',
            '    compiler_prefix_map_flags="$compiler_prefix_map_flags -ffile-prefix-map=$map_root=. -fdebug-prefix-map=$map_root=. -fmacro-prefix-map=$map_root=."',
            '    cuda_prefix_map_flags="$cuda_prefix_map_flags -Xcompiler=-ffile-prefix-map=$map_root=. -Xcompiler=-fdebug-prefix-map=$map_root=. -Xcompiler=-fmacro-prefix-map=$map_root=."',
            "  done",
            "done",
            patch_extension_helper,
            'mkdir -p "$(dirname "$env_file")" "{work}/home" "{work}/pip-cache"',
            'cat > "$env_file" <<EOF',
            "set -euo pipefail",
            f"export BUILD_VERSION={package['version']}",
            "export USE_CUDA=1",
            "export USE_ROCM=0",
            "export BUILD_RNNT=1",
            "export BUILD_ALIGN=1",
            "export BUILD_CUDA_CTC_DECODER=1",
            "export TORCH_CUDA_ARCH_LIST=10.0",
            "export PYTHONHOME=",
            "export PYTHONNOUSERSITE=1",
            "export PYTHONDONTWRITEBYTECODE=1",
            "export PIP_DISABLE_PIP_VERSION_CHECK=1",
            "export PIP_NO_INDEX=1",
            "export PIP_NO_INPUT=1",
            "export CFLAGS=\"$CFLAGS $compiler_prefix_map_flags\"",
            "export CXXFLAGS=\"$CXXFLAGS $compiler_prefix_map_flags\"",
            "export CUDAFLAGS=\"$CUDAFLAGS $cuda_prefix_map_flags\"",
            "export NVCC_FLAGS=\"$NVCC_FLAGS $cuda_prefix_map_flags\"",
            "export CMAKE_C_COMPILER=\"$CC\"",
            "export CMAKE_CXX_COMPILER=\"$CXX\"",
            "export CUDA_HOME=\"$cuda\"",
            "export CUDA_PATH=\"$cuda\"",
            f"export PYTHONPATH=\"{':'.join(pythonpath_parts)}\"",
            f"export PATH=\"{':'.join(path_parts)}\"",
            f"export LD_LIBRARY_PATH=\"{':'.join(library_parts)}\"",
            f"export CMAKE_PREFIX_PATH=\"{':'.join(cmake_prefix_parts)}\"",
            "export MAX_JOBS={jobs}",
            "export CMAKE_BUILD_PARALLEL_LEVEL={jobs}",
            "export HOME=\"{work}/home\"",
            "export PIP_CACHE_DIR=\"{work}/pip-cache\"",
            "EOF",
        ]
    )


def torchaudio_prefix_placeholders(package: dict[str, Any]) -> list[str]:
    return ["{prefix:" + key + "}" for key in package["deps"]]


def pytorch_prefix_placeholders(package: dict[str, Any]) -> list[str]:
    return ["{prefix:" + key + "}" for key in package["build_deps"]]


def fixed_build_root(package: str, line: str) -> str:
    # nvcc embeds device-side __FILE__ strings (kernel asserts) in the fatbins and has no
    # prefix-map option, so CUDA wheels compile from one constant, line-specific directory
    # instead of the action's execroot work tree. Official wheels do the same (/pytorch).
    return f"/vaso/build/{package}-{line}"


def use_fixed_build_root(plan: dict[str, Any], root: str) -> None:
    """Stage {src} into root/src in configure; build and install from there."""
    src = f"{root}/src"
    claim = (
        f'fixed_root="{root}"\n'
        'if [ "$(cat "$fixed_root/owner" 2>/dev/null)" != "{work}" ]; then\n'
        '  echo "fixed torch build root $fixed_root is owned by another action" >&2\n'
        "  exit 2\n"
        "fi\n"
    )
    stage = (
        f'fixed_root="{root}"\n'
        'rm -rf "$fixed_root"\n'
        'mkdir -p "$fixed_root/src"\n'
        'printf "%s\\n" "{work}" > "$fixed_root/owner"\n'
        'cp -a "{src}/." "$fixed_root/src/"\n'
    )
    for phase in ("configure", "build", "install"):
        script = plan[phase]["argv"][2]
        head, sep, body = script.partition("\n") if script.startswith("set -euo pipefail\n") else ("", "", script)
        prologue = (stage if phase == "configure" else claim)
        body = body.replace("{src}", src)
        if phase == "install":
            body = body.rstrip() + f'\nrm -rf "{root}"\n'
        if sep:
            plan[phase]["argv"][2] = head + sep + prologue + body
        else:
            plan[phase]["argv"][2] = "set -euo pipefail\n" + prologue + body


def pytorch_plan(repo_root: Path, profile: str, line: str, rootfs_sha256: str, rootfs: dict[str, Any]) -> dict[str, Any]:
    package = PACKAGE_GENERATORS["py-torch"]
    cuda_version = rootfs["components"]["cuda_toolkit"]["version"]
    toolchain_identifier, toolchain_env = registered_toolchain(repo_root, profile, rootfs)
    cc_ref = toolchain_ref(toolchain_identifier)
    env_script = "{work}/pytorch-build-env.sh"
    source_manifest = repo_root / "native" / "pytorch" / "pytorch-v2.14.0-2b3ec348-submodules.json"
    plan = {
        "schema": 1,
        "profile": profile,
        "line": line,
        "package": {"name": "py-torch", "version": package["version"]},
        "sources": [copy.deepcopy(package["source"])],
        "inputs": [
            {
                "path": "native/pytorch/pytorch-v2.14.0-2b3ec348-submodules.json",
                "sha256": sha256_file(source_manifest),
            }
        ],
        "toolchain": {
            "cc": cc_ref,
            "cxx": cc_ref,
            "cuda": f"rootfs:cuda@{cuda_version}",
            "env": toolchain_env,
            "python": "python_313_native",
            "rootfs_identity": {
                "label": f"//plans:{profile}/{line}/rootfs.identity.json",
                "sha256": rootfs_sha256,
            },
        },
        "deps": dep_plan(package["deps"]),
        "configure": {
            "cwd": ".",
            "argv": [
                "bash",
                "-c",
                pytorch_env_script(package),
                "configure",
                env_script,
                *pytorch_prefix_placeholders(package),
            ],
            "env": {},
        },
        "build": {
            "cwd": ".",
            "argv": [
                "bash",
                "-c",
                python_wheel_build_command("torch-*.whl", no_cache_dir = False),
                "build",
                env_script,
            ],
            "env": {},
        },
        "install": {
            "cwd": ".",
            "argv": [
                "bash",
                "-c",
                python_wheel_install_command("torch-*.whl"),
                "install",
                env_script,
            ],
            "env": {},
        },
        "outputs": copy.deepcopy(package["outputs"]),
        "gpu_archs": copy.deepcopy(package["gpu_archs"]),
        "determinism": {
            "prefix_map": {
                "{out}": ".",
                "{work}": ".",
            },
            "seed": "derive:sha256",
            "source_date_epoch": package["source_date_epoch"],
        },
        "live_checks": copy.deepcopy(rootfs["live_checks"]),
    }
    use_fixed_build_root(plan, fixed_build_root("pytorch", line))
    plan["determinism"]["seed"] = "sha256:" + plan_seed(plan)
    return plan


def torchvision_plan(repo_root: Path, profile: str, line: str, rootfs_sha256: str, rootfs: dict[str, Any]) -> dict[str, Any]:
    package = PACKAGE_GENERATORS["py-torchvision"]
    cuda_version = rootfs["components"]["cuda_toolkit"]["version"]
    toolchain_identifier, toolchain_env = registered_toolchain(repo_root, profile, rootfs)
    cc_ref = toolchain_ref(toolchain_identifier)
    env_script = "{work}/torchvision-build-env.sh"
    plan = {
        "schema": 1,
        "profile": profile,
        "line": line,
        "package": {"name": "py-torchvision", "version": package["version"]},
        "sources": [copy.deepcopy(package["source"])],
        "inputs": [{"path": "native/torchvision/upstream_pins.json", "sha256": sha256_file(repo_root / "native" / "torchvision" / "upstream_pins.json")}],
        "toolchain": {
            "cc": cc_ref,
            "cxx": cc_ref,
            "cuda": f"rootfs:cuda@{cuda_version}",
            "env": toolchain_env,
            "python": "python_313_native",
            "rootfs_identity": {
                "label": f"//plans:{profile}/{line}/rootfs.identity.json",
                "sha256": rootfs_sha256,
            },
        },
        "deps": dep_plan(package["deps"]),
        "configure": {
            "cwd": ".",
            "argv": [
                "bash",
                "-c",
                torchvision_env_script(package),
                "configure",
                env_script,
                *torchvision_prefix_placeholders(package),
            ],
            "env": {},
        },
        "build": {
            "cwd": ".",
            "argv": [
                "bash",
                "-c",
                python_wheel_build_command("torchvision-*.whl"),
                "build",
                env_script,
            ],
            "env": {},
        },
        "install": {
            "cwd": ".",
            "argv": [
                "bash",
                "-c",
                python_wheel_install_command("torchvision-*.whl"),
                "install",
                env_script,
            ],
            "env": {},
        },
        "outputs": copy.deepcopy(package["outputs"]),
        "gpu_archs": copy.deepcopy(package["gpu_archs"]),
        "determinism": {
            "prefix_map": {
                "{out}": ".",
                "{work}": ".",
            },
            "seed": "derive:sha256",
            "source_date_epoch": package["source_date_epoch"],
        },
        "live_checks": copy.deepcopy(rootfs["live_checks"]),
    }
    use_fixed_build_root(plan, fixed_build_root("torchvision", line))
    plan["determinism"]["seed"] = "sha256:" + plan_seed(plan)
    return plan


def torchaudio_plan(repo_root: Path, profile: str, line: str, rootfs_sha256: str, rootfs: dict[str, Any]) -> dict[str, Any]:
    package = PACKAGE_GENERATORS["py-torchaudio"]
    cuda_version = rootfs["components"]["cuda_toolkit"]["version"]
    toolchain_identifier, toolchain_env = registered_toolchain(repo_root, profile, rootfs)
    cc_ref = toolchain_ref(toolchain_identifier)
    env_script = "{work}/torchaudio-build-env.sh"
    plan = {
        "schema": 1,
        "profile": profile,
        "line": line,
        "package": {"name": "py-torchaudio", "version": package["version"]},
        "sources": [copy.deepcopy(package["source"])],
        "inputs": [{"path": "native/torchaudio/upstream_pins.json", "sha256": sha256_file(repo_root / "native" / "torchaudio" / "upstream_pins.json")}],
        "toolchain": {
            "cc": cc_ref,
            "cxx": cc_ref,
            "cuda": f"rootfs:cuda@{cuda_version}",
            "env": toolchain_env,
            "python": "python_313_native",
            "rootfs_identity": {
                "label": f"//plans:{profile}/{line}/rootfs.identity.json",
                "sha256": rootfs_sha256,
            },
        },
        "deps": dep_plan(package["deps"]),
        "configure": {
            "cwd": ".",
            "argv": [
                "bash",
                "-c",
                torchaudio_env_script(package),
                "configure",
                env_script,
                *torchaudio_prefix_placeholders(package),
            ],
            "env": {},
        },
        "build": {
            "cwd": ".",
            "argv": [
                "bash",
                "-c",
                python_wheel_build_command("torchaudio-*.whl"),
                "build",
                env_script,
            ],
            "env": {},
        },
        "install": {
            "cwd": ".",
            "argv": [
                "bash",
                "-c",
                python_wheel_install_command("torchaudio-*.whl"),
                "install",
                env_script,
            ],
            "env": {},
        },
        "outputs": copy.deepcopy(package["outputs"]),
        "gpu_archs": copy.deepcopy(package["gpu_archs"]),
        "determinism": {
            "prefix_map": {
                "{out}": ".",
                "{work}": ".",
            },
            "seed": "derive:sha256",
            "source_date_epoch": package["source_date_epoch"],
        },
        "live_checks": copy.deepcopy(rootfs["live_checks"]),
    }
    use_fixed_build_root(plan, fixed_build_root("torchaudio", line))
    plan["determinism"]["seed"] = "sha256:" + plan_seed(plan)
    return plan


def jaxlib_nested_manifest_sha256(nested_pins: dict[str, Any]) -> str:
    entries = []
    archives = nested_pins.get("archives", [])
    if not isinstance(archives, list):
        raise ValueError("native/jaxlib/nested_http_archives.json must contain archives")
    for archive in sorted(archives, key=lambda item: (str(item.get("sha256", "")), str(item.get("filename", "")))):
        entries.append(
            {
                "expected_sha256": str(archive.get("sha256", "")),
                "filename": str(archive.get("filename", "")),
                "kind": "archive",
            }
        )
    payload = {
        "entries": entries,
        "jaxlib_version": nested_pins.get("jaxlib_version"),
        "package": nested_pins.get("package", "py-jaxlib"),
        "schema_version": 1,
    }
    payload["tree_sha256"] = sha256_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return sha256_bytes(stable_json(payload).encode("utf-8"))


def jaxlib_nested_archives(nested_pins: dict[str, Any]) -> list[dict[str, Any]]:
    archives = nested_pins.get("archives", [])
    if not isinstance(archives, list):
        raise ValueError("native/jaxlib/nested_http_archives.json must contain archives")
    planned = []
    for archive in archives:
        if not isinstance(archive, dict):
            raise ValueError("native/jaxlib/nested_http_archives.json archives must be objects")
        planned.append(
            {
                "name": str(archive["name"]),
                "sha256": str(archive["sha256"]),
                "urls": list(archive["urls"]),
            }
        )
    return planned


def jaxlib_prefix_placeholders(package: dict[str, Any]) -> list[str]:
    args: list[str] = []
    for key in package["deps"]:
        args.extend(["--dep-prefix", key + "={prefix:" + key + "}"])
    return args


def jaxlib_build_py_args(cuda_major: str, cuda_version: str) -> list[str]:
    clang = "/usr/lib/llvm-23/bin/clang"
    return [
        "build/build.py",
        "build",
        "--wheels=jax,jaxlib,jax-cuda-plugin,jax-cuda-pjrt",
        "--python_version=",
        "--bazel_path={prefix:bazel}/bin/bazel",
        "--clang_path=" + clang,
        "--build_cuda_with_clang",
        "--bazel_options=--config=build_cuda_with_clang",
        "--bazel_options=--repo_env=USE_HERMETIC_CC_TOOLCHAIN=0",
        "--bazel_options=--@rules_ml_toolchain//common:enable_hermetic_cc=False",
        "--bazel_options=--repo_env=CLANG_CUDA_COMPILER_PATH=" + clang,
        "--bazel_options=--cxxopt=-std=gnu++17",
        "--bazel_options=--host_cxxopt=-std=gnu++17",
        # Generated headers (protobuf _virtual_includes) reach __FILE__ as nested-Bazel
        # bazel-out/ paths; ship them as external/ like the rest of the nested sources.
        "--bazel_options=--copt=" + NESTED_BAZEL_OUT_PREFIX_MAP,
        "--cuda_major_version=" + cuda_major,
        "--cuda_compute_capabilities=sm_100",
        "--bazel_options=--repo_env=LOCAL_CUDA_PATH={work}/jax-local-redists/cuda",
        "--bazel_options=--repo_env=LOCAL_CUDNN_PATH={work}/jax-local-redists/cudnn",
        "--bazel_options=--repo_env=LOCAL_NCCL_PATH={work}/jax-local-redists/nccl",
        "--bazel_options=--repo_env=LOCAL_NVSHMEM_PATH={work}/jax-local-redists/nvshmem",
        "--bazel_options=--repo_env=LOCAL_CCCL_PATH={work}/jax-local-redists/cccl",
        "--bazel_options=--repo_env=HERMETIC_CUDA_VERSION=" + cuda_version,
        "--bazel_options=--config=cuda_libraries_from_stubs",
        "--bazel_options=--repository_cache={nested_repository_cache}",
        "--bazel_options=--distdir={nested_distdir}",
        "--bazel_options=--disk_cache={nested_disk_cache}",
        "--bazel_options=--jobs={jobs}",
        "--bazel_options=--local_resources=cpu={jobs}",
        "--bazel_options=--repository_disable_download",
        "--bazel_startup_options=--nohome_rc",
        "--bazel_startup_options=--nosystem_rc",
    ]


NESTED_BAZEL_OUT_PREFIX_MAP = "-ffile-prefix-map=bazel-out/k8-opt/bin/external/=external/"


def jaxlib_install_command() -> str:
    return (
        "set -euo pipefail; "
        "source \"$1\"; "
        "wheelhouse=\"$2\"; "
        "prefix=\"$3\"; "
        "rm -rf \"$wheelhouse\"; "
        "mkdir -p \"$wheelhouse\"; "
        "set -- \"{src}\"/dist/*.whl; "
        "if [ \"$#\" -eq 1 ] && [ ! -f \"$1\" ]; then echo 'expected jaxlib build.py to produce wheels' >&2; exit 2; fi; "
        "cp \"$@\" \"$wheelhouse\"/; "
        "python3 -m pip install --no-deps --ignore-installed --no-build-isolation --no-compile "
        "--no-warn-script-location --no-index --no-cache-dir --prefix \"$prefix\" \"$wheelhouse\"/*.whl; "
        "python3 -c 'import pathlib, shutil, sys\n"
        "prefix = pathlib.Path(sys.argv[1])\n"
        "for pyc in prefix.rglob(\"*.pyc\"):\n"
        "    if not pyc.is_symlink():\n"
        "        pyc.unlink()\n"
        "for pycache in sorted(prefix.rglob(\"__pycache__\"), reverse=True):\n"
        "    if pycache.is_dir() and not pycache.is_symlink():\n"
        "        shutil.rmtree(pycache)\n"
        "for direct_url in prefix.rglob(\"direct_url.json\"):\n"
        "    if direct_url.parent.name.endswith(\".dist-info\") and direct_url.is_file() and not direct_url.is_symlink():\n"
        "        direct_url.unlink()\n"
        "        record = direct_url.parent / \"RECORD\"\n"
        "        if record.is_file():\n"
        "            record.write_text(\"\".join(line for line in record.read_text(encoding=\"utf-8\").splitlines(True) if not line.split(\",\", 1)[0].endswith(\"/direct_url.json\")), encoding=\"utf-8\")\n"
        "' \"$prefix\""
    )


def jaxlib_plan(repo_root: Path, profile: str, line: str, rootfs_sha256: str, rootfs: dict[str, Any]) -> dict[str, Any]:
    package = PACKAGE_GENERATORS["py-jaxlib"]
    pins_path = repo_root / "native" / "jaxlib" / "upstream_pins.json"
    nested_path = repo_root / "native" / "jaxlib" / "nested_http_archives.json"
    support_path = repo_root / "native" / "jaxlib" / "committed_plan_support.py"
    pins = read_json(pins_path)
    nested_pins = read_json(nested_path)
    jaxlib = pins["jaxlib"]
    cuda_line = pins["cuda_lines"][line]
    toolchain_identifier, toolchain_env = registered_toolchain(repo_root, profile, rootfs)
    cc_ref = toolchain_ref(toolchain_identifier)
    env_script = "{work}/jaxlib-build-env.sh"
    manifest_sha = jaxlib_nested_manifest_sha256(nested_pins)
    plan = {
        "schema": 1,
        "profile": profile,
        "line": line,
        "package": {"name": "py-jaxlib", "version": jaxlib["version"]},
        "sources": [
            {
                "name": "main",
                "archive": "{source_archive}",
                "patches": [],
                "sha256": jaxlib["source_archive_sha256"],
                "strip_prefix": "jax-jax-v" + jaxlib["version"],
                "urls": ["https://github.com/jax-ml/jax/archive/refs/tags/" + jaxlib["source_tag"] + ".tar.gz"],
            }
        ],
        "inputs": [
            {"path": "native/jaxlib/upstream_pins.json", "sha256": sha256_file(pins_path)},
            {"path": "native/jaxlib/nested_http_archives.json", "sha256": sha256_file(nested_path)},
            {"path": "native/jaxlib/committed_plan_support.py", "sha256": sha256_file(support_path)},
        ],
        "toolchain": {
            "cc": cc_ref,
            "cxx": cc_ref,
            "cuda": "rootfs:cuda@" + cuda_line["cuda_version"],
            "env": toolchain_env,
            "python": "python_313_native",
            "rootfs_identity": {
                "label": f"//plans:{profile}/{line}/rootfs.identity.json",
                "sha256": rootfs_sha256,
            },
        },
        "deps": dep_plan(package["deps"]),
        "configure": {
            "cwd": ".",
            "argv": [
                "python3",
                "native/jaxlib/committed_plan_support.py",
                "prepare",
                "--source",
                "{src}",
                "--work",
                "{work}",
                "--env-file",
                env_script,
                "--python-abi",
                "derived",
                "--cuda-version",
                cuda_line["cuda_version"],
                "--nested-repository-cache",
                "{nested_repository_cache}",
                *jaxlib_prefix_placeholders(package),
            ],
            "env": {},
        },
        "build": {
            "cwd": "{src}",
            "argv": [
                "bash",
                "-c",
                "set -euo pipefail; source \"$1\"; shift; python3 \"$@\"",
                "build",
                env_script,
                "{nested_build_py_args}",
            ],
            "env": {},
        },
        "install": {
            "cwd": ".",
            "argv": [
                "bash",
                "-c",
                jaxlib_install_command(),
                "install",
                env_script,
                "{wheel}",
                "{out}",
            ],
            "env": {},
        },
        "outputs": copy.deepcopy(package["outputs"]),
        "gpu_archs": copy.deepcopy(package["gpu_archs"]),
        "nested_bazel": {
            "archives": jaxlib_nested_archives(nested_pins),
            "build_py_args": jaxlib_build_py_args(cuda_line["cuda_major"], cuda_line["cuda_version"]),
            "distdir_manifest": {
                "label": "@jaxlib_nested_cache_manifest//:manifest.json",
                "sha256": manifest_sha,
            },
            "repository_cache_manifest": {
                "label": "@jaxlib_nested_cache_manifest//:manifest.json",
                "sha256": manifest_sha,
            },
        },
        "determinism": {
            "prefix_map": {
                "{out}": ".",
                "{work}": ".",
            },
            "seed": "derive:sha256",
            "source_date_epoch": package["source_date_epoch"],
        },
        "live_checks": copy.deepcopy(rootfs["live_checks"]),
    }
    plan["determinism"]["seed"] = "sha256:" + plan_seed(plan)
    return plan


def generated_files(repo_root: Path) -> dict[Path, str]:
    rootfs_lock = read_json(repo_root / "rootfs" / "cuda_ecosystem.lock.json")
    schema = schema_v1()
    files: dict[Path, str] = {Path("schema/v1.json"): stable_json(schema)}
    rootfs_by_line = {line: rootfs_identity(rootfs_lock, line) for line in LINES}
    for profile in ROOTFS_IDENTITY_PROFILES:
        for line in LINES:
            identity = rootfs_by_line[line]
            registered_toolchain(repo_root, profile, identity)
            identity_rel = Path(profile) / line / "rootfs.identity.json"
            files[identity_rel] = stable_json(identity)
    for package_name, package in sorted(PACKAGE_GENERATORS.items()):
        for profile in package["profiles"]:
            for line in package["lines"]:
                identity = rootfs_by_line[line]
                identity_bytes = stable_json(identity).encode("utf-8")
                if package_name in ("bzip2", "xz", "zlib-ng", "zstd"):
                    plan = leaf_prefix_plan(repo_root, package_name, profile, line, sha256_bytes(identity_bytes), identity)
                    filename = package_name.replace("-", "_") + ".plan.json"
                elif package_name == "py-torch":
                    plan = pytorch_plan(repo_root, profile, line, sha256_bytes(identity_bytes), identity)
                    filename = "pytorch.plan.json"
                elif package_name == "py-triton":
                    plan = triton_plan(repo_root, profile, line, sha256_bytes(identity_bytes), identity)
                    filename = "triton.plan.json"
                elif package_name == "py-torchvision":
                    plan = torchvision_plan(repo_root, profile, line, sha256_bytes(identity_bytes), identity)
                    filename = "torchvision.plan.json"
                elif package_name == "py-torchaudio":
                    plan = torchaudio_plan(repo_root, profile, line, sha256_bytes(identity_bytes), identity)
                    filename = "torchaudio.plan.json"
                elif package_name == "py-jaxlib":
                    plan = jaxlib_plan(repo_root, profile, line, sha256_bytes(identity_bytes), identity)
                    filename = "jaxlib.plan.json"
                else:
                    raise ValueError(f"unsupported package generator {package_name}")
                errors = validate_plan(plan, schema)
                if errors:
                    raise ValueError(f"{profile}/{line}/{filename}: " + "; ".join(errors))
                files[Path(profile) / line / filename] = stable_json(plan)
    return files


def write_files(root: Path, files: dict[Path, str]) -> None:
    for relative, content in sorted(files.items()):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def check_files(root: Path, files: dict[Path, str]) -> list[str]:
    errors: list[str] = []
    for relative, expected in sorted(files.items()):
        path = root / relative
        if not path.is_file():
            errors.append(f"missing plans/{relative}")
            continue
        actual = path.read_text(encoding="utf-8")
        if actual != expected:
            errors.append(f"plan drift for plans/{relative}; run tools/gen_plans.py and commit the result")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--check", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    files = generated_files(args.repo_root)
    if args.check:
        errors = check_files(args.repo_root / "plans", files)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        return 0
    out_dir = args.out_dir or args.repo_root / "plans"
    write_files(out_dir, files)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
