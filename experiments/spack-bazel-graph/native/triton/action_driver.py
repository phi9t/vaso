#!/usr/bin/env python3
"""Action driver for the token-gated native Triton prefix build."""

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


REQUIRED_TOKEN = "build-native-triton"
PREFIX_KEYS = (
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
PYTHON_BUILD_PREFIX_KEYS = (
    "py-pip",
    "py-setuptools",
    "py-wheel",
    "py-filelock",
    "py-lit",
    "py-pybind11",
    "python-venv",
)


def _normalize_declared_output_paths(args: argparse.Namespace) -> None:
    for name in (
        "prefix_out",
        "build_plan_out",
        "provider_metadata_out",
        "result_marker_out",
        "wheel_out",
    ):
        value = getattr(args, name)
        setattr(args, name, str(Path(value).resolve(strict=False)))


def _check_insula() -> None:
    if os.environ.get("VASO_IN_INSULA") != "1":
        raise SystemExit(
            "native Triton actions must run inside the hermetic insula "
            "(VASO_IN_INSULA=1)"
        )
    manifest = os.environ.get("VASO_ROOTFS_BUNDLE_MANIFEST", "")
    if not manifest or not Path(manifest).is_file():
        raise SystemExit(
            "native Triton actions require VASO_ROOTFS_BUNDLE_MANIFEST "
            "inside the hermetic CUDA rootfs"
        )


def _required_tmpdir() -> Path:
    value = os.environ.get("TMPDIR", "")
    if not value:
        raise SystemExit("native Triton actions require TMPDIR from the insula action environment")
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
            raise SystemExit(f"unsupported Triton native prefix key {key!r}")
        path = Path(path_text)
        if not path.is_file():
            raise SystemExit(f"{key} prefix file is missing: {path}")
        prefixes[key] = path.read_text(encoding="utf-8").strip()
    missing = [key for key in PREFIX_KEYS if key not in prefixes]
    if missing:
        raise SystemExit("missing Triton native prefix file(s): " + ", ".join(missing))
    return prefixes


def _write_executable(path: Path, text: str) -> None:
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


def _materialize_synthetic_prefixes(root: Path, python_abi: str) -> dict[str, str]:
    prefixes = {key: root / key for key in PREFIX_KEYS}
    for path in prefixes.values():
        path.mkdir(parents=True, exist_ok=True)

    (prefixes["python"] / "bin").mkdir()
    _write_executable(
        prefixes["python"] / "bin" / "python3",
        "#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n',
    )
    (prefixes["python"] / "include").mkdir()

    site_packages = _python_site_packages_dir(str(prefixes["python"]), python_abi)
    if site_packages.parent.name.startswith("python"):
        header_dir = prefixes["python"] / "include" / site_packages.parent.name
        header_dir.mkdir(parents=True, exist_ok=True)
        (header_dir / "Python.h").write_text("", encoding="utf-8")

    (prefixes["python-venv"] / "bin").mkdir()
    _write_executable(
        prefixes["python-venv"] / "bin" / "python3",
        "#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n',
    )
    (prefixes["python-venv"] / "pyvenv.cfg").write_text("", encoding="utf-8")
    (prefixes["python-venv"] / site_packages).mkdir(parents=True, exist_ok=True)

    def python_package_prefix(name: str, package_path: str) -> None:
        package = prefixes[name] / site_packages / package_path
        package.parent.mkdir(parents=True, exist_ok=True)
        if package_path.endswith(".py"):
            package.write_text("", encoding="utf-8")
        else:
            package.mkdir(parents=True, exist_ok=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
        (prefixes[name] / "bin").mkdir(exist_ok=True)

    python_package_prefix("py-pip", "pip")
    _write_executable(prefixes["py-pip"] / "bin" / "pip", "#!/bin/sh\n")
    python_package_prefix("py-setuptools", "setuptools")
    python_package_prefix("py-wheel", "wheel")
    _write_executable(prefixes["py-wheel"] / "bin" / "wheel", "#!/bin/sh\n")
    python_package_prefix("py-filelock", "filelock")
    python_package_prefix("py-lit", "lit")
    _write_executable(prefixes["py-lit"] / "bin" / "lit", "#!/bin/sh\n")
    python_package_prefix("py-pybind11", "pybind11")

    (prefixes["cmake"] / "bin").mkdir()
    _write_executable(prefixes["cmake"] / "bin" / "cmake", "#!/bin/sh\n")
    (prefixes["ninja"] / "bin").mkdir()
    _write_executable(prefixes["ninja"] / "bin" / "ninja", "#!/bin/sh\n")

    for rel in (
        "bin/llvm-config",
        "bin/clang",
        "bin/ld.lld",
        "bin/FileCheck",
    ):
        path = prefixes["llvm"] / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_executable(path, "#!/bin/sh\n")
    for rel in (
        "include/llvm/Config/llvm-config.h",
        "lib/cmake/llvm/LLVMConfig.cmake",
        "lib/cmake/mlir/MLIRConfig.cmake",
    ):
        path = prefixes["llvm"] / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    (prefixes["nlohmann_json"] / "include" / "nlohmann").mkdir(parents=True)
    (prefixes["nlohmann_json"] / "include" / "nlohmann" / "json.hpp").write_text("", encoding="utf-8")

    for rel in ("bin/ptxas", "bin/nvdisasm", "bin/cuobjdump"):
        path = prefixes["cuda"] / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_executable(path, "#!/bin/sh\n")
    for rel in ("include/cuda.h", "lib64/libcupti.so", "nvvm/libdevice/libdevice.10.bc"):
        path = prefixes["cuda"] / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    (prefixes["zlib_ng"] / "include").mkdir()
    (prefixes["zlib_ng"] / "include" / "zlib.h").write_text("", encoding="utf-8")
    (prefixes["zlib_ng"] / "lib").mkdir()
    (prefixes["zlib_ng"] / "lib" / "libz.so").write_text("", encoding="utf-8")

    return {key: str(path) for key, path in prefixes.items()}


def _run_plan(args: argparse.Namespace, prefixes: dict[str, str]) -> tuple[int, dict]:
    Path(args.build_plan_out).parent.mkdir(parents=True, exist_ok=True)
    plan_argv = [
        sys.executable,
        args.plan,
        "--pins",
        args.pins,
        "--out",
        args.build_plan_out,
        "--python-abi",
        args.python_abi,
        "--token",
        args.token,
    ]
    if args.execute:
        plan_argv.append("--execute")
    for patch, sha256 in _patch_pairs(args):
        plan_argv.extend(["--patch-file", str(patch), "--patch-sha256", sha256])
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
    patch_files = _patch_pairs(args)
    for index, (patch, _sha256) in enumerate(patch_files):
        files[f"patch_{index}"] = str(patch)
    rootfs_manifest = os.environ.get("VASO_ROOTFS_BUNDLE_MANIFEST", "")
    if rootfs_manifest:
        files["rootfs_manifest"] = rootfs_manifest
    payload = {
        "execute": bool(args.execute),
        "files": {},
        "patch_sha256s": [sha256 for _patch, sha256 in patch_files],
        "prefixes": {key: prefixes[key] for key in PREFIX_KEYS},
        "python_abi": args.python_abi,
        "vaso_cuda_line": os.environ.get("VASO_CUDA_LINE", ""),
    }
    for key, value in sorted(files.items()):
        path = Path(value)
        if not path.is_file():
            raise SystemExit(f"native Triton action digest input is missing: {key}={path}")
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
            raise SystemExit("native Triton actions require VASO_CUDA_LINE for build work reuse")
        vaso_home = os.environ.get("VASO_HOME", "/vaso")
        root = Path(vaso_home) / "lines" / line / "work" / "triton"
    if not root.is_absolute():
        raise SystemExit(f"native Triton build work root must be absolute: {root}")
    work_dir = root.resolve(strict=False) / _action_input_digest(args, prefixes)
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def _source_dir_for_action(args: argparse.Namespace) -> Path:
    source_anchor = Path(args.source_anchor)
    if not source_anchor.is_file():
        raise SystemExit(f"Triton source anchor is missing: {source_anchor}")
    return source_anchor.parent


def _patch_pairs(args: argparse.Namespace) -> list[tuple[Path, str]]:
    patch_files = [Path(item) for item in args.patch_file]
    patch_sha256s = list(args.patch_sha256)
    if len(patch_files) != len(patch_sha256s):
        raise SystemExit(
            "--patch-file and --patch-sha256 must be supplied the same number of times"
        )
    patches = []
    for patch, expected in zip(patch_files, patch_sha256s):
        if not patch.is_file():
            raise SystemExit(f"Triton patch file is missing: {patch}")
        patch = patch.resolve()
        actual = _file_sha256(patch)
        if actual != expected:
            raise SystemExit(
                f"Triton patch sha256 mismatch for {patch}: got {actual}, expected {expected}"
            )
        patches.append((patch, expected))
    return patches


def _ready_marker_payload(args: argparse.Namespace, source_dir: Path, patches: list[tuple[Path, str]]) -> dict[str, object]:
    source_anchor = Path(args.source_anchor)
    return {
        "schema_version": 1,
        "source_anchor_sha256": _file_sha256(source_anchor),
        "source_anchor_rel": source_anchor.relative_to(source_dir).as_posix(),
        "patches": [
            {"path": str(patch), "sha256": sha256}
            for patch, sha256 in patches
        ],
    }


def _copy_source_for_build(args: argparse.Namespace, source_dir: Path, build_work: Path) -> tuple[Path, list[str]]:
    destination = build_work / "src"
    patches = _patch_pairs(args)
    marker = build_work / "source_ready.json"
    desired = _ready_marker_payload(args, source_dir, patches)
    if destination.is_dir() and marker.is_file():
        try:
            current = json.loads(marker.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            current = {}
        if current == desired:
            return destination, [patch.name for patch, _sha256 in patches]

    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        source_dir,
        destination,
        ignore=shutil.ignore_patterns(".git", "__pycache__"),
        symlinks=False,
    )
    for patch, _sha256 in patches:
        result = subprocess.run(
            ["patch", "-p1", "-i", str(patch)],
            cwd=destination,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            raise SystemExit(
                "failed to apply Triton patch {} (rc={}):\n{}\n{}".format(
                    patch,
                    result.returncode,
                    result.stdout,
                    result.stderr,
                )
            )
    marker.write_text(json.dumps(desired, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination, [patch.name for patch, _sha256 in patches]


def _python_executable(prefixes: dict[str, str], python_abi: str) -> str:
    version = _python_version_from_abi(python_abi)
    candidates = []
    if version:
        candidates.extend(
            [
                Path(prefixes["python-venv"]) / "bin" / ("python" + version),
                Path(prefixes["python"]) / "bin" / ("python" + version),
            ]
        )
    candidates.extend(
        [
            Path(prefixes["python-venv"]) / "bin" / "python3",
            Path(prefixes["python"]) / "bin" / "python3",
        ]
    )
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise SystemExit("native Triton action could not find a Python executable in native prefixes")


def _minimal_build_env(plan_doc: dict) -> dict[str, str]:
    tmpdir = _required_tmpdir()
    action_home = tmpdir / "triton-home"
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
    env["TRITON_OFFLINE_BUILD"] = "1"
    return env


def _run_checked(argv: list[str], *, cwd: Path, env: dict[str, str], label: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        raise SystemExit(f"{label} failed with rc={result.returncode}")
    return result


def _build_and_install(args: argparse.Namespace, plan_doc: dict, prefixes: dict[str, str], source_dir: Path, build_work: Path) -> tuple[Path, list[str]]:
    build_source, applied_patches = _copy_source_for_build(args, source_dir, build_work)
    env = _minimal_build_env(plan_doc)
    python = _python_executable(prefixes, args.python_abi)
    wheelhouse = build_work / "wheelhouse"
    if wheelhouse.exists():
        shutil.rmtree(wheelhouse)
    wheelhouse.mkdir(parents=True)
    package_dir = build_source
    if not (package_dir / "setup.py").is_file():
        raise SystemExit(f"Triton package setup.py is missing under copied source: {package_dir}")

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
        cwd=package_dir,
        env=env,
        label="native Triton wheel build",
    )
    wheels = sorted(wheelhouse.glob("triton-*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"native Triton wheel build expected one triton wheel, found {len(wheels)}")

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
        label="native Triton prefix install",
    )

    wheel_out = Path(args.wheel_out)
    wheel_out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(wheels[0], wheel_out)
    return build_source, applied_patches


def _write_dry_run_prefix(prefix: Path, python_prefix: str, python_abi: str) -> None:
    if prefix.exists():
        shutil.rmtree(prefix)
    site_packages = _python_site_packages_dir(python_prefix, python_abi)
    (prefix / "artifacts" / "wheels").mkdir(parents=True)
    (prefix / site_packages / "triton").mkdir(parents=True)
    (prefix / "DRY_RUN_DO_NOT_USE_AS_TRITON_PREFIX").write_text(
        "Token-safe native Triton dry run. No wheel was built.\n",
        encoding="utf-8",
    )


def _write_dry_run_wheel(path_text: str) -> None:
    wheel = Path(path_text)
    wheel.parent.mkdir(parents=True, exist_ok=True)
    wheel.write_text(
        "Token-safe native Triton dry run. No wheel was built.\n",
        encoding="utf-8",
    )


def _write_metadata(
    args: argparse.Namespace,
    plan_doc: dict,
    prefixes: dict[str, str],
    source_dir: Path,
    build_work: Path | None,
    applied_patches: list[str] | None = None,
) -> None:
    metadata = {
        "schema_version": 1,
        "package": "py-triton",
        "version": plan_doc.get("version", "3.8.0"),
        "pytorch_version": plan_doc.get("pytorch_version", "2.14.0"),
        "python_abi": args.python_abi,
        "mechanism": "python-wheel-action",
        "source": str(source_dir),
        "source_anchor": args.source_anchor,
        "build_work": str(build_work) if plan_doc.get("will_build") and build_work is not None else "",
        "wheel": args.wheel_out,
        "applied_patches": applied_patches or [],
        "execute_requested": bool(args.execute),
        "token_present": args.token == REQUIRED_TOKEN,
        "will_build": bool(plan_doc.get("will_build")),
        "mode": plan_doc.get("mode", "dry-run"),
        "input_prefixes": prefixes,
        "rootfs_manifest": os.environ.get("VASO_ROOTFS_BUNDLE_MANIFEST", ""),
    }
    Path(args.provider_metadata_out).write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_result_marker(args: argparse.Namespace, plan_doc: dict) -> None:
    if plan_doc.get("will_build"):
        text = "Native Triton wheel build requested and prefix installation completed.\n"
    else:
        text = "Token-safe native Triton dry run. No wheel was built.\n"
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
    parser.add_argument("--patch-file", action="append", default=[])
    parser.add_argument("--patch-sha256", action="append", default=[])
    parser.add_argument("--prefix-file", action="append", default=[])
    parser.add_argument("--python-abi", default="derived")
    parser.add_argument("--token", default="")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--synthetic-prefixes-for-dry-run", action="store_true")
    args = parser.parse_args(argv)

    _check_insula()
    _normalize_declared_output_paths(args)
    prefixes = _read_prefix_files(args.prefix_file)
    for output in (
        args.prefix_out,
        args.provider_metadata_out,
        args.result_marker_out,
        args.wheel_out,
    ):
        Path(output).parent.mkdir(parents=True, exist_ok=True)
    synthetic_root: tempfile.TemporaryDirectory[str] | None = None
    if args.synthetic_prefixes_for_dry_run:
        if args.execute:
            raise SystemExit("synthetic prefixes are allowed only for token-safe dry-run actions")
        synthetic_root = tempfile.TemporaryDirectory(prefix="vaso-triton-prefixes-", dir=str(_required_tmpdir()))
        prefixes = _materialize_synthetic_prefixes(Path(synthetic_root.name), args.python_abi)

    source_dir = _source_dir_for_action(args)
    if not args.execute:
        _write_dry_run_prefix(Path(args.prefix_out), prefixes["python"], args.python_abi)
        _write_dry_run_wheel(args.wheel_out)
    rc, plan_doc = _run_plan(args, prefixes)
    if rc != 0:
        return rc
    build_work = _estate_build_work_dir(args, prefixes) if plan_doc.get("will_build") else None
    applied_patches: list[str] = []
    if plan_doc.get("will_build"):
        source_dir, applied_patches = _build_and_install(args, plan_doc, prefixes, source_dir, build_work)
    _write_metadata(args, plan_doc, prefixes, source_dir, build_work, applied_patches)
    _write_result_marker(args, plan_doc)
    if synthetic_root is not None:
        synthetic_root.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
