#!/usr/bin/env python3
"""Action driver for the token-gated native torchvision prefix build."""

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


REQUIRED_TOKEN = "build-native-torchvision"
PACKAGE = "torchvision"
PACKAGE_GLOB = "torchvision-*.whl"
PREFIX_KEYS = (
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
PREFIX_FILE_KEYS = tuple(key for key in PREFIX_KEYS if key != "torch")


def _normalize_declared_output_paths(args: argparse.Namespace) -> None:
    for name in (
        "prefix_out",
        "build_plan_out",
        "provider_metadata_out",
        "result_marker_out",
        "wheel_out",
    ):
        setattr(args, name, str(Path(getattr(args, name)).resolve(strict=False)))


def _absolute_action_path(path_text: str) -> str:
    path = Path(path_text)
    if path.is_absolute():
        return str(path)
    return str((Path.cwd() / path).resolve(strict=False))


def _check_insula() -> None:
    if os.environ.get("VASO_IN_INSULA") != "1":
        raise SystemExit(
            "native torchvision actions must run inside the hermetic insula "
            "(VASO_IN_INSULA=1)"
        )
    manifest = os.environ.get("VASO_ROOTFS_BUNDLE_MANIFEST", "")
    if not manifest or not Path(manifest).is_file():
        raise SystemExit(
            "native torchvision actions require VASO_ROOTFS_BUNDLE_MANIFEST "
            "inside the hermetic CUDA rootfs"
        )


def _required_tmpdir() -> Path:
    value = os.environ.get("TMPDIR", "")
    if not value:
        raise SystemExit("native torchvision actions require TMPDIR from the insula action environment")
    path = Path(value)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_prefix_files(items: list[str]) -> dict[str, str]:
    prefixes: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--prefix-file must be KEY=PATH, got {item!r}")
        key, path_text = item.split("=", 1)
        if key not in PREFIX_FILE_KEYS:
            raise SystemExit(f"unsupported torchvision native prefix key {key!r}")
        path = Path(path_text)
        if not path.is_file():
            raise SystemExit(f"{key} prefix file is missing: {path}")
        prefixes[key] = path.read_text(encoding="utf-8").strip()
    missing = [key for key in PREFIX_FILE_KEYS if key not in prefixes]
    if missing:
        raise SystemExit("missing torchvision native prefix file(s): " + ", ".join(missing))
    return prefixes


def _write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _python_version_from_abi(python_abi: str) -> str | None:
    if python_abi.startswith("cp") and python_abi[2:].isdigit():
        digits = python_abi[2:]
        return digits[0] + "." + digits[1:]
    parts = python_abi.split(".", 1)
    if len(parts) == 2 and all(part.isdigit() for part in parts):
        return python_abi
    return None


def _site_packages_dir(python_abi: str) -> Path:
    version = _python_version_from_abi(python_abi) or "3.13"
    return Path("lib") / ("python" + version) / "site-packages"


def _materialize_synthetic_prefixes(root: Path, python_abi: str) -> dict[str, str]:
    prefixes = {key: root / key for key in PREFIX_KEYS}
    for path in prefixes.values():
        path.mkdir(parents=True, exist_ok=True)
    site_packages = _site_packages_dir(python_abi)

    _write_executable(
        prefixes["python"] / "bin" / "python3",
        "#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n',
    )
    version = _python_version_from_abi(python_abi) or "3.13"
    (prefixes["python"] / "include" / ("python" + version)).mkdir(parents=True)
    (prefixes["python"] / "include" / ("python" + version) / "Python.h").write_text("", encoding="utf-8")

    _write_executable(
        prefixes["python-venv"] / "bin" / "python3",
        "#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n',
    )
    _write_executable(
        prefixes["python-venv"] / "bin" / ("python" + version),
        "#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n',
    )
    (prefixes["python-venv"] / "pyvenv.cfg").write_text("", encoding="utf-8")
    (prefixes["python-venv"] / site_packages).mkdir(parents=True)

    def python_package_prefix(name: str, package_path: str) -> None:
        package = prefixes[name] / site_packages / package_path
        package.parent.mkdir(parents=True, exist_ok=True)
        if package_path.endswith(".py"):
            package.write_text("", encoding="utf-8")
        else:
            package.mkdir(parents=True, exist_ok=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
        (prefixes[name] / "bin").mkdir(exist_ok=True)

    python_package_prefix("torch", "torch")
    (prefixes["torch"] / site_packages / "torch" / "lib").mkdir(parents=True, exist_ok=True)
    (prefixes["torch"] / site_packages / "torch" / "lib" / "libtorch_cuda.so").write_text("", encoding="utf-8")
    python_package_prefix("py-pip", "pip")
    _write_executable(prefixes["py-pip"] / "bin" / "pip", "#!/bin/sh\n")
    python_package_prefix("py-setuptools", "setuptools")
    python_package_prefix("py-wheel", "wheel")
    _write_executable(prefixes["py-wheel"] / "bin" / "wheel", "#!/bin/sh\n")
    python_package_prefix("py-numpy", "numpy")
    python_package_prefix("py-pillow", "PIL")
    (prefixes["py-pillow"] / site_packages / "PIL" / "Image.py").write_text("", encoding="utf-8")
    python_package_prefix("py-filelock", "filelock")

    for rel in ("bin/nvcc", "include/cuda.h", "include/nvjpeg.h", "lib64/libcudart.so", "lib64/libnvjpeg.so"):
        path = prefixes["cuda"] / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if "/bin/" in rel:
            _write_executable(path, "#!/bin/sh\n")
        else:
            path.write_text("", encoding="utf-8")

    _write_executable(prefixes["ninja"] / "bin" / "ninja", "#!/bin/sh\n")
    (prefixes["libjpeg-turbo"] / "include").mkdir()
    (prefixes["libjpeg-turbo"] / "include" / "jpeglib.h").write_text("", encoding="utf-8")
    (prefixes["libjpeg-turbo"] / "lib").mkdir()
    (prefixes["libjpeg-turbo"] / "lib" / "libjpeg.so").write_text("", encoding="utf-8")
    _write_executable(prefixes["libpng"] / "bin" / "libpng-config", "#!/bin/sh\necho 1.6.58\n")
    (prefixes["libpng"] / "include").mkdir()
    (prefixes["libpng"] / "include" / "png.h").write_text("", encoding="utf-8")
    (prefixes["libpng"] / "lib").mkdir()
    (prefixes["libpng"] / "lib" / "libpng16.so").write_text("", encoding="utf-8")
    (prefixes["zlib-ng"] / "include").mkdir()
    (prefixes["zlib-ng"] / "include" / "zlib.h").write_text("", encoding="utf-8")
    (prefixes["zlib-ng"] / "lib").mkdir()
    (prefixes["zlib-ng"] / "lib" / "libz.so").write_text("", encoding="utf-8")

    return {key: str(path) for key, path in prefixes.items()}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _action_input_digest(args: argparse.Namespace, prefixes: dict[str, str]) -> str:
    files = {
        "plan": args.plan,
        "pins": args.pins,
        "source_anchor": args.source_anchor,
    }
    rootfs_manifest = os.environ.get("VASO_ROOTFS_BUNDLE_MANIFEST", "")
    if rootfs_manifest:
        files["rootfs_manifest"] = rootfs_manifest
    payload = {
        "execute": bool(args.execute),
        "files": {},
        "max_jobs": args.max_jobs,
        "prefixes": {key: prefixes[key] for key in PREFIX_KEYS},
        "python_abi": args.python_abi,
        "torch_cuda_arch_list": args.torch_cuda_arch_list,
        "vaso_cuda_line": os.environ.get("VASO_CUDA_LINE", ""),
    }
    for key, value in sorted(files.items()):
        path = Path(value)
        if not path.is_file():
            raise SystemExit(f"native torchvision action digest input is missing: {key}={path}")
        payload["files"][key] = _file_sha256(path)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:32]


def _estate_build_work_dir(args: argparse.Namespace, prefixes: dict[str, str]) -> Path:
    if args.build_work_root:
        root = Path(args.build_work_root)
    else:
        line = os.environ.get("VASO_CUDA_LINE", "")
        if not line:
            raise SystemExit("native torchvision actions require VASO_CUDA_LINE for build work reuse")
        vaso_home = os.environ.get("VASO_HOME", "/vaso")
        root = Path(vaso_home) / "lines" / line / "work" / "torchvision"
    if not root.is_absolute():
        raise SystemExit(f"native torchvision build work root must be absolute: {root}")
    work_dir = root.resolve(strict=False) / _action_input_digest(args, prefixes)
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def _source_dir_for_action(args: argparse.Namespace) -> Path:
    source_anchor = Path(args.source_anchor)
    if not source_anchor.is_file():
        raise SystemExit(f"torchvision source anchor is missing: {source_anchor}")
    return source_anchor.parent


def _copy_source_for_build(source_dir: Path, build_work: Path) -> Path:
    destination = build_work / "src"
    marker = build_work / "source_ready.json"
    desired = {
        "schema_version": 1,
        "source_anchor_sha256": _file_sha256(source_dir / "setup.py"),
    }
    if destination.is_dir() and marker.is_file():
        try:
            current = json.loads(marker.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            current = {}
        if current == desired:
            return destination
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source_dir, destination, ignore=shutil.ignore_patterns(".git", "__pycache__"), symlinks=False)
    marker.write_text(json.dumps(desired, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def _python_executable(prefixes: dict[str, str], python_abi: str) -> str:
    version = _python_version_from_abi(python_abi)
    candidates: list[Path] = []
    if version:
        candidates.append(Path(prefixes["python-venv"]) / "bin" / ("python" + version))
        candidates.append(Path(prefixes["python"]) / "bin" / ("python" + version))
    candidates.extend([
        Path(prefixes["python-venv"]) / "bin" / "python3",
        Path(prefixes["python"]) / "bin" / "python3",
    ])
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise SystemExit("native torchvision action could not find a Python executable in native prefixes")


def _minimal_build_env(plan_doc: dict) -> dict[str, str]:
    tmpdir = _required_tmpdir()
    action_home = tmpdir / "torchvision-home"
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
        env[str(key)] = str(value)
    env["PIP_CACHE_DIR"] = str(pip_cache)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INDEX"] = "1"
    env["PIP_NO_INPUT"] = "1"
    return env


def _run_checked(argv: list[str], *, cwd: Path, env: dict[str, str], label: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, cwd=cwd, env=env, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        raise SystemExit(f"{label} failed with rc={result.returncode}")
    return result


def _run_plan(args: argparse.Namespace, prefixes: dict[str, str], source_dir: Path) -> tuple[int, dict]:
    Path(args.build_plan_out).parent.mkdir(parents=True, exist_ok=True)
    argv = [
        sys.executable,
        args.plan,
        "--pins",
        args.pins,
        "--out",
        args.build_plan_out,
        "--python-abi",
        args.python_abi,
        "--torch-cuda-arch-list",
        args.torch_cuda_arch_list,
        "--max-jobs",
        args.max_jobs,
        "--source",
        str(source_dir),
        "--token",
        args.token,
    ]
    if args.execute:
        argv.append("--execute")
    for key in PREFIX_KEYS:
        argv.extend(["--prefix", f"{key}={prefixes[key]}"])
    result = subprocess.run(argv, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    try:
        plan_doc = json.loads(Path(args.build_plan_out).read_text(encoding="utf-8"))
    except FileNotFoundError:
        plan_doc = {}
    return result.returncode, plan_doc


def _build_and_install(args: argparse.Namespace, plan_doc: dict, prefixes: dict[str, str], source_dir: Path, build_work: Path) -> Path:
    build_source = _copy_source_for_build(source_dir, build_work)
    env = _minimal_build_env(plan_doc)
    python = _python_executable(prefixes, args.python_abi)
    wheelhouse = build_work / "wheelhouse"
    if wheelhouse.exists():
        shutil.rmtree(wheelhouse)
    wheelhouse.mkdir(parents=True)
    _run_checked(
        [
            python,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--no-index",
            "--no-cache-dir",
            "-w",
            str(wheelhouse),
            ".",
        ],
        cwd=build_source,
        env=env,
        label="native torchvision wheel build",
    )
    wheels = sorted(wheelhouse.glob(PACKAGE_GLOB))
    if len(wheels) != 1:
        raise SystemExit(f"native torchvision wheel build expected one torchvision wheel, found {len(wheels)}")
    prefix_out = Path(args.prefix_out)
    if prefix_out.exists():
        shutil.rmtree(prefix_out)
    prefix_out.parent.mkdir(parents=True, exist_ok=True)
    _run_checked(
        [
            python,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--ignore-installed",
            "--no-build-isolation",
            "--no-warn-script-location",
            "--no-index",
            "--no-cache-dir",
            "--prefix",
            str(prefix_out),
            str(wheels[0]),
        ],
        cwd=build_work,
        env=env,
        label="native torchvision prefix install",
    )
    wheel_out = Path(args.wheel_out)
    wheel_out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(wheels[0], wheel_out)
    return build_source


def _write_dry_run_prefix(prefix: Path, python_abi: str) -> None:
    if prefix.exists():
        shutil.rmtree(prefix)
    site_packages = _site_packages_dir(python_abi)
    (prefix / "artifacts" / "wheels").mkdir(parents=True)
    (prefix / site_packages / PACKAGE).mkdir(parents=True)
    (prefix / site_packages / PACKAGE / "__init__.py").write_text("__version__ = '0.29.0'\n", encoding="utf-8")
    (prefix / "DRY_RUN_DO_NOT_USE_AS_TORCHVISION_PREFIX").write_text(
        "Token-safe native torchvision dry run. No wheel was built.\n",
        encoding="utf-8",
    )


def _write_dry_run_wheel(path_text: str) -> None:
    wheel = Path(path_text)
    wheel.parent.mkdir(parents=True, exist_ok=True)
    wheel.write_text("Token-safe native torchvision dry run. No wheel was built.\n", encoding="utf-8")


def _write_metadata(
    args: argparse.Namespace,
    plan_doc: dict,
    prefixes: dict[str, str],
    source_dir: Path,
    build_work: Path | None,
) -> None:
    metadata = {
        "schema_version": 1,
        "package": "py-torchvision",
        "version": plan_doc.get("version", "0.29.0"),
        "pytorch_version": plan_doc.get("pytorch_version", "2.14.0"),
        "python_abi": args.python_abi,
        "mechanism": "python-wheel-action",
        "source": str(source_dir),
        "source_anchor": args.source_anchor,
        "build_work": str(build_work) if plan_doc.get("will_build") and build_work is not None else "",
        "wheel": args.wheel_out,
        "execute_requested": bool(args.execute),
        "token_present": args.token == REQUIRED_TOKEN,
        "will_build": bool(plan_doc.get("will_build")),
        "mode": plan_doc.get("mode", "dry-run"),
        "input_prefixes": prefixes,
        "rootfs_manifest": os.environ.get("VASO_ROOTFS_BUNDLE_MANIFEST", ""),
    }
    Path(args.provider_metadata_out).write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_result_marker(args: argparse.Namespace, plan_doc: dict) -> None:
    if plan_doc.get("will_build"):
        text = "Native torchvision wheel build requested and prefix installation completed.\n"
    else:
        text = "Token-safe native torchvision dry run. No wheel was built.\n"
    Path(args.result_marker_out).write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--pins", required=True)
    parser.add_argument("--prefix-out", required=True)
    parser.add_argument("--build-work-root", default="")
    parser.add_argument("--build-plan-out", required=True)
    parser.add_argument("--provider-metadata-out", required=True)
    parser.add_argument("--result-marker-out", required=True)
    parser.add_argument("--wheel-out", required=True)
    parser.add_argument("--source-anchor", required=True)
    parser.add_argument("--source-files", action="append", default=[])
    parser.add_argument("--torch-prefix", required=True)
    parser.add_argument("--prefix-file", action="append", default=[])
    parser.add_argument("--python-abi", default="derived")
    parser.add_argument("--torch-cuda-arch-list", default="10.0")
    parser.add_argument("--max-jobs", default="1")
    parser.add_argument("--token", default="")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--synthetic-prefixes-for-dry-run", action="store_true")
    args = parser.parse_args(argv)

    _check_insula()
    _normalize_declared_output_paths(args)
    prefixes = _read_prefix_files(args.prefix_file)
    prefixes["torch"] = _absolute_action_path(args.torch_prefix)
    synthetic_root: tempfile.TemporaryDirectory[str] | None = None
    if args.synthetic_prefixes_for_dry_run:
        if args.execute:
            raise SystemExit("synthetic prefixes are allowed only for token-safe dry-run actions")
        synthetic_root = tempfile.TemporaryDirectory(prefix="vaso-torchvision-prefixes-", dir=str(_required_tmpdir()))
        prefixes = _materialize_synthetic_prefixes(Path(synthetic_root.name), args.python_abi)

    for output in (args.prefix_out, args.provider_metadata_out, args.result_marker_out, args.wheel_out):
        Path(output).parent.mkdir(parents=True, exist_ok=True)
    source_dir = _source_dir_for_action(args)
    if not args.execute:
        _write_dry_run_prefix(Path(args.prefix_out), args.python_abi)
        _write_dry_run_wheel(args.wheel_out)
    rc, plan_doc = _run_plan(args, prefixes, source_dir)
    if rc != 0:
        return rc
    build_work = _estate_build_work_dir(args, prefixes) if plan_doc.get("will_build") else None
    if plan_doc.get("will_build"):
        source_dir = _build_and_install(args, plan_doc, prefixes, source_dir, build_work)
    _write_metadata(args, plan_doc, prefixes, source_dir, build_work)
    _write_result_marker(args, plan_doc)
    if synthetic_root is not None:
        synthetic_root.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
