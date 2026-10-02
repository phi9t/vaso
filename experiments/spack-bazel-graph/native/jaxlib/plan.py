#!/usr/bin/env python3
"""Plan and preflight the native jaxlib build without running it."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


REQUIRED_TOKEN = "build-native-llvm"
ONE_LLVM_COMMIT = "35901313800ea6e6cbeb9226e51c7c4b29bfc40e"
REQUIRED_PREFIXES = (
    "python",
    "python-venv",
    "py-pip",
    "py-setuptools",
    "py-wheel",
    "py-numpy",
    "bazel",
    "llvm",
    "cuda",
    "cudnn",
    "nccl",
    "nvshmem",
    "xxd-standalone",
)
PATH_PREFIXES = (
    "python",
    "python-venv",
    "py-pip",
    "py-wheel",
    "bazel",
    "llvm",
    "cuda",
    "xxd-standalone",
)
PYTHON_BUILD_PREFIXES = (
    "py-pip",
    "py-setuptools",
    "py-wheel",
    "py-numpy",
    "python-venv",
)
CUDA_LINES = {
    "cu129": {"major": "12", "version": "12.9.1"},
    "cu130": {"major": "13", "version": "13.0.3"},
}
JAX_BAZEL_DISTDIR = "/vaso/sources/jax/distdir"
JAX_BAZEL_REPOSITORY_CACHE = "/vaso/cache/bazel/repository-cache"
JAXLIB_MAX_JOBS_CAP = 96


def load_pins(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _path(prefix: str, rel: str) -> str:
    return str(Path(prefix) / rel)


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


def cuda_line_config(cuda_line: str | None) -> dict[str, str]:
    line = cuda_line or os.environ.get("VASO_CUDA_LINE", "cu130")
    if line not in CUDA_LINES:
        raise SystemExit(f"unsupported VASO_CUDA_LINE for jaxlib native build: {line!r}")
    config = dict(CUDA_LINES[line])
    config["line"] = line
    return config


def jaxlib_max_jobs() -> int:
    requested = os.environ.get("VASO_JAXLIB_MAX_JOBS") or os.environ.get("MAKE_JOBS") or ""
    if requested:
        if not requested.isdigit() or int(requested) < 1:
            raise SystemExit(f"VASO_JAXLIB_MAX_JOBS must be a positive integer, got: {requested}")
        jobs = int(requested)
    else:
        jobs = os.cpu_count() or 1
    return min(jobs, JAXLIB_MAX_JOBS_CAP)


def nested_bazel_prefetch_targets(cuda_line: str | None = None) -> list[str]:
    line = cuda_line_config(cuda_line)
    major = line["major"]
    return [
        "//jaxlib/tools:jaxlib_wheel",
        f"//jaxlib/tools:jax_cuda{major}_plugin_wheel",
        f"//jaxlib/tools:jax_cuda{major}_pjrt_wheel",
    ]


def build_env(prefixes: dict[str, str], python_abi: str = "derived", cuda_line: str | None = None) -> dict[str, str]:
    for key in REQUIRED_PREFIXES:
        if key not in prefixes:
            raise SystemExit(f"missing required --prefix {key}=... input")

    line = cuda_line_config(cuda_line)
    return {
        "BAZELISK_SKIP_WRAPPER": "1",
        "CC": _path(prefixes["llvm"], "bin/clang"),
        "CXX": _path(prefixes["llvm"], "bin/clang++"),
        "CUDA_HOME": prefixes["cuda"],
        "CUDA_PATH": prefixes["cuda"],
        "HERMETIC_CUDA_VERSION": line["version"],
        "JAX_RELEASE": "1",
        "LOCAL_CUDA_PATH": prefixes["cuda"],
        "LOCAL_CUDNN_PATH": prefixes["cudnn"],
        "LOCAL_NCCL_PATH": prefixes["nccl"],
        "LOCAL_NVSHMEM_PATH": prefixes["nvshmem"],
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INDEX": "1",
        "PIP_NO_INPUT": "1",
        "PYTHONHOME": "",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": os.pathsep.join(python_build_pythonpath(prefixes, python_abi)),
        "XLA_PYTHON_CLIENT_PREALLOCATE": "false",
        "PATH": os.pathsep.join(
            [_path(prefixes[key], "bin") for key in PATH_PREFIXES]
            + ["/usr/bin", "/bin"]
        ),
    }


def build_py_args(prefixes: dict[str, str], cuda_line: str | None = None) -> list[str]:
    line = cuda_line_config(cuda_line)
    jobs = jaxlib_max_jobs()
    return [
        "build/build.py",
        "build",
        "--wheels=jaxlib,jax-cuda-plugin,jax-cuda-pjrt",
        "--python_version=",
        "--bazel_path=" + _path(prefixes["bazel"], "bin/bazel"),
        "--clang_path=" + _path(prefixes["llvm"], "bin/clang"),
        "--build_cuda_with_clang",
        "--bazel_options=--config=build_cuda_with_clang",
        "--bazel_options=--repo_env=USE_HERMETIC_CC_TOOLCHAIN=0",
        "--bazel_options=--@rules_ml_toolchain//common:enable_hermetic_cc=False",
        "--bazel_options=--repo_env=CLANG_CUDA_COMPILER_PATH=" + _path(prefixes["llvm"], "bin/clang"),
        "--bazel_options=--cxxopt=-std=gnu++17",
        "--bazel_options=--host_cxxopt=-std=gnu++17",
        "--cuda_major_version=" + line["major"],
        "--cuda_compute_capabilities=sm_100",
        "--bazel_options=--repo_env=LOCAL_CUDA_PATH=" + prefixes["cuda"],
        "--bazel_options=--repo_env=LOCAL_CUDNN_PATH=" + prefixes["cudnn"],
        "--bazel_options=--repo_env=LOCAL_NCCL_PATH=" + prefixes["nccl"],
        "--bazel_options=--repo_env=LOCAL_NVSHMEM_PATH=" + prefixes["nvshmem"],
        "--bazel_options=--repo_env=HERMETIC_CUDA_VERSION=" + line["version"],
        "--bazel_options=--config=cuda_libraries_from_stubs",
        "--bazel_options=--repository_cache=" + JAX_BAZEL_REPOSITORY_CACHE,
        "--bazel_options=--distdir=" + JAX_BAZEL_DISTDIR,
        f"--bazel_options=--jobs={jobs}",
        f"--bazel_options=--local_resources=cpu={jobs}",
        "--bazel_options=--repository_disable_download",
        "--bazel_startup_options=--nohome_rc",
        "--bazel_startup_options=--nosystem_rc",
    ]


def preflight(prefixes: dict[str, str], python_abi: str = "derived") -> list[dict[str, object]]:
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
        "py-numpy:package": ("py-numpy", str(site_packages / "numpy")),
        "bazel:binary": ("bazel", "bin/bazel"),
        "llvm:clang": ("llvm", "bin/clang"),
        "llvm:clang++": ("llvm", "bin/clang++"),
        "llvm:ld.lld": ("llvm", "bin/ld.lld"),
        "llvm:llvm-config": ("llvm", "bin/llvm-config"),
        "cuda:nvcc": ("cuda", "bin/nvcc"),
        "cuda:ptxas": ("cuda", "bin/ptxas"),
        "cuda:header": ("cuda", "include/cuda.h"),
        "cuda:cudart": ("cuda", "lib64/libcudart.so"),
        "cudnn:header": ("cudnn", "include/cudnn.h"),
        "cudnn:lib": ("cudnn", "lib64/libcudnn.so"),
        "nccl:header": ("nccl", "include/nccl.h"),
        "nccl:lib": ("nccl", "lib/libnccl.so"),
        "nvshmem:header": ("nvshmem", "include/nvshmem.h"),
        "nvshmem:lib": ("nvshmem", "lib/libnvshmem_host.so"),
        "xxd-standalone:binary": ("xxd-standalone", "bin/xxd"),
    }
    for name, (key, rel) in file_checks.items():
        root = prefixes.get(key, "")
        target = Path(root) / rel if root else Path(rel)
        check(name, bool(root) and target.exists(), str(target) if root else "<unset>")

    return checks


def plan_document(
    prefixes: dict[str, str],
    pins: dict[str, object],
    execute: bool,
    token: str,
    python_abi: str = "derived",
    cuda_line: str | None = None,
) -> dict[str, object]:
    line = cuda_line_config(cuda_line)
    checks = preflight(prefixes, python_abi)
    try:
        env = build_env(prefixes, python_abi, line["line"]) if prefixes else {}
        args = build_py_args(prefixes, line["line"]) if prefixes else []
        env_error = None
    except SystemExit as exc:
        env = {}
        args = []
        env_error = str(exc)

    token_ok = token == REQUIRED_TOKEN
    preflight_ok = all(item["ok"] for item in checks) and env_error is None
    llvm = pins["llvm"]
    jax = pins["jax"]
    jaxlib = pins["jaxlib"]
    return {
        "schema_version": 1,
        "package": "py-jaxlib",
        "version": jaxlib["version"],
        "jax_version": jax["version"],
        "python_abi": python_abi,
        "required_prefixes": list(REQUIRED_PREFIXES),
        "source": {
            "repo": "@jax_v0_10_2_source",
            "tag": jaxlib["source_tag"],
            "archive_sha256": jaxlib["source_archive_sha256"],
            "xla_commit": jaxlib["xla_commit"],
            "bazel_version": jaxlib["bazel_version"],
        },
        "llvm": {
            "provider": "llvm",
            "prefix_key": "llvm",
            "rootfs_prefix": llvm["selected_prefix"],
            "required_source_commit": llvm["selected_source_commit"],
            "source_archive_sha256": llvm["selected_source_archive_sha256"],
            "version": llvm["selected_version"],
            "xla_patch_sha256": llvm["xla_patch_sha256"],
            "audit_note": (
                "JAX/XLA builds against the shared rootfs llvm-project 35901313 "
                "clang/lld prefix. XLA's llvm-raw repository must be satisfied "
                "from the matching prefetched source archive and patches."
            ),
        },
        "cuda": {
            "line": line["line"],
            "major_version": line["major"],
            "hermetic_cuda_version": line["version"],
            "compute_capabilities": ["sm_100"],
            "uses_clang_cuda": True,
            "uses_nvcc": False,
        },
        "wheels": ["jaxlib", "jax-cuda-plugin", "jax-cuda-pjrt"],
        "nested_bazel_prefetch_targets": nested_bazel_prefetch_targets(line["line"]),
        "build_py_args": args,
        "input_prefixes": prefixes,
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


def default_token() -> str:
    return os.environ.get("VASO_NATIVE_JAXLIB_TOKEN", os.environ.get("VASO_NATIVE_LLVM_TOKEN", ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", action="append", default=[], metavar="KEY=PATH")
    parser.add_argument("--pins", type=Path, default=Path(__file__).with_name("upstream_pins.json"))
    parser.add_argument("--out", type=Path)
    parser.add_argument("--token", default=default_token())
    parser.add_argument("--python-abi", default="derived")
    parser.add_argument("--cuda-line", default=os.environ.get("VASO_CUDA_LINE", "cu130"))
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)

    prefixes: dict[str, str] = {}
    for item in args.prefix:
        if "=" not in item:
            raise SystemExit(f"--prefix must be KEY=PATH, got {item!r}")
        key, value = item.split("=", 1)
        prefixes[key] = value

    doc = plan_document(
        prefixes,
        load_pins(args.pins),
        args.execute,
        args.token,
        args.python_abi,
        args.cuda_line,
    )
    text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    if args.out is not None:
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")

    if not args.execute:
        return 0 if doc["preflight_ok"] else 1
    if not doc["authorization"]["token_present"]:
        print(
            f"REFUSED: full native jaxlib build requires token {REQUIRED_TOKEN!r}; staying dry-run.",
            file=sys.stderr,
        )
        return 2
    if not doc["preflight_ok"]:
        print("REFUSED: jaxlib preflight failed; not building.", file=sys.stderr)
        return 3

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
