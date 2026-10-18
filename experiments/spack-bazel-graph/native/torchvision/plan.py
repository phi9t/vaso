#!/usr/bin/env python3
"""Plan and preflight the native torchvision build without running it."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


REQUIRED_TOKEN = "build-native-torchvision"
MAX_JOBS_ENV = "VASO_TORCHVISION_MAX_JOBS"
MAX_JOBS_CAP = 96
REQUIRED_PREFIXES = (
    "torch",
    "python",
    "python-venv",
    "py-pip",
    "py-setuptools",
    "py-wheel",
    "py-numpy",
    "py-pillow",
    "py-filelock",
    "cuda",
    "ninja",
    "libjpeg-turbo",
    "libpng",
    "zlib-ng",
)
PYTHONPATH_PREFIXES = (
    "torch",
    "py-pip",
    "py-setuptools",
    "py-wheel",
    "py-numpy",
    "py-pillow",
    "py-filelock",
    "python-venv",
)
PATH_PREFIXES = (
    "python-venv",
    "python",
    "py-pip",
    "py-wheel",
    "ninja",
    "cuda",
    "libpng",
)
WHEEL_OUTPUT_DIR = "artifacts/wheels"
WHEEL_SOURCE_PLACEHOLDER = "<source>"


DEPENDENCY_DECISIONS = {
    "libjpeg-turbo": {
        "status": "on",
        "provider": "@libjpeg_turbo_native//:prefix_path.txt",
        "reason": "torchvision image extension enables JPEG support and native libjpeg-turbo already exists.",
    },
    "libpng": {
        "status": "on",
        "provider": "@libpng_native//:prefix_path.txt",
        "reason": "torchvision image extension enables PNG support and native libpng already exists.",
    },
    "pillow": {
        "status": "on",
        "provider": "@py_pillow_native//:prefix_path.txt",
        "reason": "plain import torchvision imports PIL.Image through torchvision.transforms.functional.",
    },
    "libwebp": {
        "status": "off-with-reason",
        "provider": None,
        "reason": "no native libwebp provider exists and the NP-16 gates do not exercise WebP decode.",
    },
    "nvjpeg": {
        "status": "rootfs-cuda",
        "provider": "@cuda_native//:prefix_path.txt",
        "reason": "NVJPEG is part of the selected /usr/local/cuda rootfs boundary when headers and libraries are present.",
    },
}


def load_pins(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _path(prefix: str, rel: str) -> str:
    return str(Path(prefix) / rel)


def _libdirs(prefix: str) -> list[str]:
    return [
        str(Path(prefix) / libdir)
        for libdir in ("lib", "lib64")
        if (Path(prefix) / libdir).is_dir()
    ]


def _first_libdir(prefix: str) -> str:
    dirs = _libdirs(prefix)
    return dirs[0] if dirs else str(Path(prefix) / "lib")


def python_version_from_abi(python_abi: str) -> str | None:
    if python_abi.startswith("cp") and python_abi[2:].isdigit():
        digits = python_abi[2:]
        if len(digits) >= 2:
            return digits[0] + "." + digits[1:]
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


def _relative_to_python_prefix(python_prefix: str, path: str) -> Path:
    candidate = Path(path)
    prefix = Path(python_prefix)
    try:
        return candidate.relative_to(prefix)
    except ValueError:
        pass
    if prefix.is_absolute() and not candidate.is_absolute():
        try:
            return candidate.relative_to(Path(str(prefix).lstrip("/")))
        except ValueError:
            pass
    return candidate


def python_header_path(prefixes: dict[str, str], python_abi: str) -> Path:
    version = python_version_from_abi(python_abi)
    if version:
        return Path("include") / ("python" + version) / "Python.h"

    python_prefix = prefixes.get("python", "")
    python = Path(python_prefix) / "bin" / "python3" if python_prefix else None
    if python is not None and python.is_file() and os.access(python, os.X_OK):
        probe = (
            "import sysconfig\n"
            "path = sysconfig.get_path('include', vars={'base': '', 'platbase': ''})\n"
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
            include_dir = result.stdout.strip()
            if result.returncode == 0 and include_dir:
                return _relative_to_python_prefix(python_prefix, include_dir) / "Python.h"

    return Path("include")


def _pythonpath(prefixes: dict[str, str], python_abi: str) -> list[str]:
    site_packages = python_site_packages_dir(prefixes, python_abi)
    return [str(Path(prefixes[key]) / site_packages) for key in PYTHONPATH_PREFIXES]


def _path_entries(prefixes: dict[str, str]) -> list[str]:
    entries = [str(Path(prefixes[key]) / "bin") for key in PATH_PREFIXES]
    return entries + ["/usr/bin", "/bin"]


def _ld_library_path(prefixes: dict[str, str], python_abi: str) -> list[str]:
    site_packages = python_site_packages_dir(prefixes, python_abi)
    entries = [
        str(Path(prefixes["torch"]) / "lib"),
        str(Path(prefixes["torch"]) / site_packages / "torch" / "lib"),
    ]
    for key in ("cuda", "libjpeg-turbo", "libpng", "zlib-ng"):
        entries.extend(_libdirs(prefixes[key]))
    deduped: list[str] = []
    for entry in entries:
        if entry and entry not in deduped:
            deduped.append(entry)
    return deduped


def normalise_max_jobs(value: str | None, source: str = "--max-jobs") -> str:
    if value is None or not value.strip():
        raise SystemExit(f"{source} must be a positive integer, got empty input")
    try:
        jobs = int(value.strip(), 10)
    except ValueError:
        raise SystemExit(f"{source} must be a positive integer, got {value!r}")
    if jobs < 1:
        raise SystemExit(f"{source} must be a positive integer, got {value!r}")
    if jobs > MAX_JOBS_CAP:
        raise SystemExit(f"{source} must be <= {MAX_JOBS_CAP}, got {value!r}")
    return str(jobs)


def detect_nproc() -> int:
    try:
        result = subprocess.run(["nproc"], check=False, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is not None and result.returncode == 0:
        try:
            jobs = int(result.stdout.strip(), 10)
        except ValueError:
            jobs = 0
        if jobs > 0:
            return jobs
    return max(os.cpu_count() or 1, 1)


def resolve_max_jobs(value: str | None, environ: dict[str, str] | None = None) -> tuple[str, str]:
    if value is not None and value.strip():
        return normalise_max_jobs(value), "explicit --max-jobs input"
    env = os.environ if environ is None else environ
    env_value = env.get(MAX_JOBS_ENV, "")
    if env_value.strip():
        return normalise_max_jobs(env_value, MAX_JOBS_ENV), MAX_JOBS_ENV
    return str(min(detect_nproc(), MAX_JOBS_CAP)), f"nproc capped at {MAX_JOBS_CAP}"


def validate_torch_cuda_arch_list(value: str) -> str:
    entries = [item.strip() for item in value.split(";") if item.strip()]
    if entries != ["10.0"]:
        raise SystemExit("torchvision native build only accepts TORCH_CUDA_ARCH_LIST=10.0 for NP-16")
    return "10.0"


def build_env(prefixes: dict[str, str], torch_cuda_arch_list: str, max_jobs: str | None, python_abi: str) -> dict[str, str]:
    for key in REQUIRED_PREFIXES:
        if key not in prefixes:
            raise SystemExit(f"missing required --prefix {key}=... input")
    torch_arch = validate_torch_cuda_arch_list(torch_cuda_arch_list)
    env = {
        "BUILD_VERSION": "0.29.0",
        "PYTORCH_VERSION": "2.14.0",
        "FORCE_CUDA": "1",
        "CUDA_HOME": prefixes["cuda"],
        "CUDA_PATH": prefixes["cuda"],
        "TORCH_CUDA_ARCH_LIST": torch_arch,
        "TORCHVISION_USE_PNG": "1",
        "TORCHVISION_USE_JPEG": "1",
        "TORCHVISION_USE_WEBP": "0",
        "TORCHVISION_USE_NVJPEG": "1",
        "PYTHONHOME": "",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": os.pathsep.join(_pythonpath(prefixes, python_abi)),
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INDEX": "1",
        "PIP_NO_INPUT": "1",
        "PATH": os.pathsep.join(_path_entries(prefixes)),
        "LD_LIBRARY_PATH": os.pathsep.join(_ld_library_path(prefixes, python_abi)),
        "LIBRARY_PATH": os.pathsep.join(_libdirs(prefixes["cuda"]) + _libdirs(prefixes["libjpeg-turbo"]) + _libdirs(prefixes["libpng"]) + _libdirs(prefixes["zlib-ng"])),
        "TORCHVISION_INCLUDE": os.pathsep.join([
            _path(prefixes["libjpeg-turbo"], "include"),
            _path(prefixes["libpng"], "include"),
            _path(prefixes["zlib-ng"], "include"),
        ]),
        "TORCHVISION_LIBRARY": os.pathsep.join([
            _first_libdir(prefixes["libjpeg-turbo"]),
            _first_libdir(prefixes["libpng"]),
            _first_libdir(prefixes["zlib-ng"]),
        ]),
        "CMAKE_PREFIX_PATH": os.pathsep.join([
            prefixes["torch"],
            prefixes["python"],
            prefixes["cuda"],
            prefixes["ninja"],
            prefixes["libjpeg-turbo"],
            prefixes["libpng"],
            prefixes["zlib-ng"],
        ]),
    }
    if max_jobs is not None:
        env["MAX_JOBS"] = max_jobs
        env["CMAKE_BUILD_PARALLEL_LEVEL"] = max_jobs
        env["MAX_CONCURRENCY"] = max_jobs
    return env


def preflight(prefixes: dict[str, str], python_abi: str = "derived") -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    for key in REQUIRED_PREFIXES:
        p = prefixes.get(key, "")
        check(f"prefix:{key}", bool(p) and Path(p).is_dir(), p or "<unset>")

    site_packages = python_site_packages_dir(prefixes, python_abi) if prefixes else Path("lib/python/site-packages")
    file_checks = {
        "torch:package": ("torch", str(site_packages / "torch")),
        "torch:libdir": ("torch", str(site_packages / "torch/lib")),
        "python:binary": ("python", "bin/python3"),
        "python:headers": ("python", str(python_header_path(prefixes, python_abi))),
        "python-venv:binary": ("python-venv", "bin/python3"),
        "python-venv:config": ("python-venv", "pyvenv.cfg"),
        "py-pip:package": ("py-pip", str(site_packages / "pip")),
        "py-setuptools:package": ("py-setuptools", str(site_packages / "setuptools")),
        "py-wheel:package": ("py-wheel", str(site_packages / "wheel")),
        "py-numpy:package": ("py-numpy", str(site_packages / "numpy")),
        "py-pillow:package": ("py-pillow", str(site_packages / "PIL")),
        "py-pillow:image-module": ("py-pillow", str(site_packages / "PIL/Image.py")),
        "cuda:nvcc": ("cuda", "bin/nvcc"),
        "cuda:header": ("cuda", "include/cuda.h"),
        "cuda:nvjpeg": ("cuda", "include/nvjpeg.h"),
        "ninja:binary": ("ninja", "bin/ninja"),
        "libjpeg-turbo:header": ("libjpeg-turbo", "include/jpeglib.h"),
        "libpng:config": ("libpng", "bin/libpng-config"),
        "libpng:header": ("libpng", "include/png.h"),
        "zlib-ng:header": ("zlib-ng", "include/zlib.h"),
    }
    for name, (key, rel) in file_checks.items():
        root = prefixes.get(key, "")
        target = Path(root) / rel if root else Path(rel)
        check(name, bool(root) and target.exists(), str(target) if root else "<unset>")

    for key, names in {
        "cuda": ("libcudart.so",),
        "libjpeg-turbo": ("libjpeg.so",),
        "libpng": ("libpng16.so", "libpng.so"),
        "zlib-ng": ("libz.so",),
    }.items():
        root = prefixes.get(key, "")
        found = any((Path(root) / libdir / name).exists() for libdir in ("lib", "lib64") for name in names) if root else False
        check(f"{key}:library", found, root or "<unset>")

    return checks


def wheel_entrypoint(source: str | None) -> list[str]:
    return [
        "python",
        "-m",
        "pip",
        "wheel",
        "--no-build-isolation",
        "--no-deps",
        "-w",
        WHEEL_OUTPUT_DIR,
        source or WHEEL_SOURCE_PLACEHOLDER,
    ]


def plan_document(
    prefixes: dict[str, str],
    pins: dict[str, object],
    execute: bool,
    token: str,
    source: str | None,
    max_jobs_input: str | None,
    python_abi: str,
    torch_cuda_arch_list: str,
) -> dict[str, object]:
    checks = preflight(prefixes, python_abi)
    try:
        max_jobs, max_jobs_source = resolve_max_jobs(max_jobs_input)
        env = build_env(prefixes, torch_cuda_arch_list, max_jobs, python_abi) if prefixes else {}
        env_error = None
    except SystemExit as exc:
        max_jobs = None
        max_jobs_source = None
        env = {}
        env_error = str(exc)

    token_ok = token == REQUIRED_TOKEN
    preflight_ok = all(item["ok"] for item in checks) and env_error is None
    package_pins = pins["torchvision"]
    return {
        "schema_version": 1,
        "package": "py-torchvision",
        "version": package_pins["version"],
        "pytorch_version": pins["pytorch"]["version"],
        "python_abi": python_abi,
        "required_prefixes": list(REQUIRED_PREFIXES),
        "source": {
            "repo": "@torchvision_v0_29_0_source",
            "tag": package_pins["tag"],
            "url": package_pins["source_url"],
            "archive_sha256": package_pins["archive_sha256"],
        },
        "entrypoint": wheel_entrypoint(source),
        "input_prefixes": prefixes,
        "build_env": env,
        "env_error": env_error,
        "dependency_decisions": DEPENDENCY_DECISIONS,
        "resources": {
            "max_jobs": max_jobs,
            "source": max_jobs_source,
        },
        "emitted_prefix_layout": {
            "wheels": f"{WHEEL_OUTPUT_DIR}/torchvision-*.whl",
            "site_packages": str(python_site_packages_dir(prefixes, python_abi) / "torchvision"),
            "install_cmd": ["python", "-m", "pip", "install", "--no-deps", "--prefix", "<prefix>", "<wheel>"],
        },
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
    parser.add_argument("--source")
    parser.add_argument("--token", default=os.environ.get("VASO_NATIVE_TORCHVISION_TOKEN", ""))
    parser.add_argument("--python-abi", default="derived")
    parser.add_argument("--torch-cuda-arch-list", default="10.0")
    parser.add_argument("--max-jobs")
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
        args.source,
        args.max_jobs,
        args.python_abi,
        args.torch_cuda_arch_list,
    )
    text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    if args.out is not None:
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")

    if not args.execute:
        return 0 if doc["preflight_ok"] else 1
    if not doc["authorization"]["token_present"]:
        print(
            f"REFUSED: full native torchvision build requires token {REQUIRED_TOKEN!r}; staying dry-run.",
            file=sys.stderr,
        )
        return 2
    if not doc["preflight_ok"]:
        print("REFUSED: torchvision preflight failed; not building.", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
