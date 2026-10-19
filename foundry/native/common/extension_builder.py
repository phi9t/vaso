#!/usr/bin/env python3
"""Build a Vaso PyTorch CUDA extension wheel inside the hermetic insula."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

_INSULA_ERROR = "vaso_torch_extension must run inside the hermetic insula"


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: expected a JSON object")
    return data


def _insula_manifest() -> str:
    manifest = os.environ.get("VASO_ROOTFS_BUNDLE_MANIFEST", "")
    if os.environ.get("VASO_IN_INSULA") != "1" or not manifest or not Path(manifest).is_file():
        raise SystemExit(_INSULA_ERROR)
    return manifest


def _torch_cuda_arch_list(plan: dict[str, Any]) -> str:
    configure = plan.get("configure", {})
    argv = configure.get("argv") if isinstance(configure, dict) else None
    if isinstance(argv, list):
        text = "\n".join(str(item) for item in argv)
        match = re.search(r"TORCH_CUDA_ARCH_LIST=([0-9][0-9A-Za-z.+_;-]*)", text)
        if match is not None:
            return match.group(1)
    archs = plan.get("gpu_archs")
    if isinstance(archs, list) and archs:
        return ";".join(str(item).removeprefix("sm_").replace("+PTX", "+PTX") for item in archs)
    return ""


def _cxx11_abi(plan: dict[str, Any]) -> str:
    configure = plan.get("configure", {})
    argv = configure.get("argv") if isinstance(configure, dict) else None
    if isinstance(argv, list):
        text = "\n".join(str(item) for item in argv)
        match = re.search(r"_GLIBCXX_USE_CXX11_ABI=([01])", text)
        if match is not None:
            return match.group(1)
    return "1"


def _normalize_cxx_standard(value: str) -> str:
    normalized = value.strip().removeprefix("c++")
    if normalized not in {"17", "20", "23"}:
        raise SystemExit(f"unsupported extension C++ standard: {value}")
    return normalized


def _required_cxx_standard(prefix: Path) -> str | None:
    for include_dir in _torch_include_dirs(prefix):
        for header in (include_dir / "ATen" / "ATen.h", include_dir / "torch" / "all.h"):
            if not header.is_file():
                continue
            text = header.read_text(encoding="utf-8", errors="ignore")
            if "__cplusplus < 202002L" in text:
                return "20"
            if "__cplusplus < 201703L" in text:
                return "17"
    return None


def _python_version_from_abi(python_abi: str) -> str:
    if python_abi.startswith("cp"):
        digits = python_abi[2:]
        if len(digits) >= 2 and digits.isdigit():
            return f"{digits[0]}.{digits[1:]}"
    if re.fullmatch(r"[0-9]+\.[0-9]+", python_abi):
        return python_abi
    raise SystemExit(f"cannot derive Python include version from ABI {python_abi!r}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _action_digest(args: argparse.Namespace, plan: dict[str, Any], toolchain: dict[str, Any]) -> str:
    digest = hashlib.sha256()
    payload = {
        "schema_version": 1,
        "package": args.package,
        "version": args.version,
        "python_abi": args.python_abi,
        "cxx11_abi": args.cxx11_abi,
        "cxx_standard": args.cxx_standard,
        "cuda_arch_list": args.cuda_arch_list,
        "rdc": args.rdc,
        "toolchain": toolchain,
        "plan_package": plan.get("package"),
        "inputs": {
            "srcs": [str(path) for path in args.src],
            "cuda_srcs": [str(path) for path in args.cuda_src],
            "py_srcs": [str(path) for path in args.py_src],
            "dep_files": [str(path) for path in args.dep_file],
            "device_link_files": [str(path) for path in args.device_link_file],
            "link_files": [str(path) for path in args.link_file],
        },
        "prefixes": {
            "python": str(args.python_prefix) if args.python_prefix is not None else None,
            "nvshmem": str(args.nvshmem_prefix) if args.nvshmem_prefix is not None else None,
        },
    }
    digest.update(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    for path in (
        list(args.src)
        + list(args.cuda_src)
        + list(args.py_src)
        + list(args.dep_file)
        + list(args.link_file)
        + [
            args.torch_prefix_manifest,
            args.torch_build_plan,
            args.torch_provider_metadata,
            args.cuda_prefix_manifest,
            args.nccl_prefix_manifest,
            args.rootfs_identity,
        ]
    ):
        digest.update(str(path).encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode("ascii"))
        digest.update(b"\0")
    optional_paths = [
        args.nvshmem_prefix_manifest,
        *args.device_link_file,
    ]
    for path in optional_paths:
        if path is None:
            continue
        digest.update(str(path).encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _run(argv: list[str]) -> None:
    result = subprocess.run(argv, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)


def _torch_include_dirs(prefix: Path) -> list[Path]:
    candidates = [
        prefix / "torch" / "include",
        prefix / "torch" / "include" / "torch" / "csrc" / "api" / "include",
        prefix / "include",
        prefix / "include" / "torch" / "csrc" / "api" / "include",
    ]
    for site in sorted(prefix.glob("lib/python*/site-packages")):
        candidates.extend(
            [
                site / "torch" / "include",
                site / "torch" / "include" / "torch" / "csrc" / "api" / "include",
            ]
        )
    return [path for path in candidates if path.is_dir()]


def _python_include_dirs(prefix: Path, python_abi: str) -> list[Path]:
    version = _python_version_from_abi(python_abi)
    candidates = [
        prefix / "include" / f"python{version}",
        prefix / "include",
    ]
    return [path for path in candidates if (path / "Python.h").is_file()]


def _torch_lib_dirs(prefix: Path) -> list[Path]:
    candidates = [prefix / "torch" / "lib", prefix / "lib", prefix / "lib64"]
    for site in sorted(prefix.glob("lib/python*/site-packages")):
        candidates.append(site / "torch" / "lib")
    return [path for path in candidates if path.is_dir()]


def _prefix_include_dir(prefix: Path) -> Path:
    targets = prefix / "targets" / "x86_64-linux" / "include"
    if targets.is_dir():
        return targets
    return prefix / "include"


def _prefix_include_dirs(prefix: Path) -> list[Path]:
    include = _prefix_include_dir(prefix)
    candidates = [include, include / "cccl"]
    return [path for path in candidates if path.is_dir()]


def _prefix_lib_dirs(prefix: Path) -> list[Path]:
    candidates = [
        prefix / "lib64",
        prefix / "lib",
        prefix / "targets" / "x86_64-linux" / "lib",
        prefix / "targets" / "x86_64-linux" / "lib64",
    ]
    return [path for path in candidates if path.is_dir()]


def _nvcc_arch_flags(cuda_arch_list: str) -> list[str]:
    flags: list[str] = []
    for raw in re.split(r"[;,\s]+", cuda_arch_list):
        if not raw:
            continue
        wants_ptx = raw.upper().endswith("+PTX")
        base = raw[:-4] if wants_ptx else raw
        match = re.fullmatch(r"([0-9]+)\.([0-9]+)([a-z]?)", base)
        if match is None:
            match = re.fullmatch(r"([0-9])([0-9])([a-z]?)", base)
        if match is None:
            raise SystemExit(f"unsupported TORCH_CUDA_ARCH_LIST entry: {raw}")
        arch = f"{match.group(1)}{match.group(2)}{match.group(3)}"
        flags.extend(["-gencode", f"arch=compute_{arch},code=sm_{arch}"])
        if wants_ptx:
            flags.extend(["-gencode", f"arch=compute_{arch},code=compute_{arch}"])
    return flags


def _nvcc_random_seed(args: argparse.Namespace, index: int, src: Path) -> str:
    digest = hashlib.sha256()
    digest.update(args.package.encode("utf-8"))
    digest.update(b"\0")
    digest.update(str(index).encode("ascii"))
    digest.update(b"\0")
    digest.update(src.name.encode("utf-8"))
    digest.update(b"\0")
    digest.update(_sha256_file(src).encode("ascii"))
    return str(int(digest.hexdigest()[:8], 16))


def _compile_objects(args: argparse.Namespace, work: Path) -> tuple[Path, list[Path]]:
    objects = work / "objects"
    objects.mkdir(parents=True, exist_ok=True)
    torch_prefix = args.torch_prefix
    cuda_prefix = args.cuda_prefix
    nccl_prefix = args.nccl_prefix
    include_flags = []
    for path in _torch_include_dirs(torch_prefix):
        include_flags.extend(["-I", str(path)])
    if args.python_prefix is not None:
        for path in _python_include_dirs(args.python_prefix, args.python_abi):
            include_flags.extend(["-I", str(path)])
    for prefix in (cuda_prefix, nccl_prefix):
        for path in _prefix_include_dirs(prefix):
            include_flags.extend(["-I", str(path)])
    if args.nvshmem_prefix is not None:
        for path in _prefix_include_dirs(args.nvshmem_prefix):
            include_flags.extend(["-I", str(path)])
    for path in args.include_dir:
        include_flags.extend(["-I", str(path)])
    for path in args.system_include_dir:
        include_flags.extend(["-isystem", str(path)])
    define_flags = [f"-D{define}" for define in args.define]
    cxx_flags = [
        f"-std=c++{args.cxx_standard}",
        "-fPIC",
        "-O2",
        f"-D_GLIBCXX_USE_CXX11_ABI={args.cxx11_abi}",
        *define_flags,
        *include_flags,
        *args.extra_cxx_flag,
    ]
    built_objects: list[Path] = []
    for index, src in enumerate(args.src):
        out = objects / f"cc_{index}.o"
        _run([args.cxx, "-c", str(src), "-o", str(out), *cxx_flags])
        built_objects.append(out)
    nvcc = args.nvcc if Path(args.nvcc).is_file() else str(cuda_prefix / "bin" / "nvcc")
    for index, src in enumerate(args.cuda_src):
        out = objects / f"cu_{index}.o"
        extra_cuda_flags = list(args.extra_cuda_flag)
        if args.rdc and not any(flag.startswith("-rdc") or "relocatable-device-code" in flag for flag in extra_cuda_flags):
            extra_cuda_flags.append("-rdc=true")
        _run(
            [
                nvcc,
                "-c",
                str(src),
                "-o",
                str(out),
                f"-std=c++{args.cxx_standard}",
                "-Xcompiler",
                "-fPIC",
                "--objdir-as-tempdir",
                "--frandom-seed",
                _nvcc_random_seed(args, index, src),
                f"-D_GLIBCXX_USE_CXX11_ABI={args.cxx11_abi}",
                *_nvcc_arch_flags(args.cuda_arch_list),
                *define_flags,
                *include_flags,
                *extra_cuda_flags,
            ]
        )
        built_objects.append(out)
    return objects, built_objects


def _device_link_objects(args: argparse.Namespace, objects: list[Path], work: Path) -> list[Path]:
    if not args.rdc:
        return []
    out = work / "objects" / "cuda_device_link.o"
    nvcc = args.nvcc if Path(args.nvcc).is_file() else str(args.cuda_prefix / "bin" / "nvcc")
    lib_flags = []
    for prefix in (args.cuda_prefix, args.nccl_prefix, args.nvshmem_prefix):
        if prefix is None:
            continue
        for path in _prefix_lib_dirs(prefix):
            lib_flags.extend(["-L", str(path)])
    _run(
        [
            nvcc,
            "-dlink",
            "-o",
            str(out),
            *[str(path) for path in objects],
            *[str(path) for path in args.device_link_file],
            *_nvcc_arch_flags(args.cuda_arch_list),
            *lib_flags,
            "-Xcompiler",
            "-fPIC",
        ]
    )
    return [out]


def _link_extension(args: argparse.Namespace, objects: list[Path], out: Path) -> None:
    link_flags = []
    for path in _torch_lib_dirs(args.torch_prefix):
        link_flags.extend(["-L", str(path)])
    for prefix in (args.cuda_prefix, args.nccl_prefix, args.nvshmem_prefix):
        if prefix is None:
            continue
        for path in _prefix_lib_dirs(prefix):
            link_flags.extend(["-L", str(path)])
    for path in args.device_link_file:
        link_flags.append(str(path))
    for path in args.link_file:
        link_flags.append(str(path))
    link_flags.extend(args.link_flag)
    _run([args.cxx, "-shared", "-o", str(out), *[str(path) for path in objects], *link_flags])


def _record_hash(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode("ascii").rstrip("=")
    return f"sha256={digest}"


def _write_record(root: Path, record_rel: PurePosixPath) -> None:
    record_path = root / record_rel.as_posix()
    rows: list[list[str]] = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        rel = PurePosixPath(path.relative_to(root).as_posix())
        if rel == record_rel:
            continue
        data = path.read_bytes()
        rows.append([rel.as_posix(), _record_hash(data), str(len(data))])
    rows.append([record_rel.as_posix(), "", ""])
    with record_path.open("w", encoding="utf-8", newline="") as fh:
        csv.writer(fh, lineterminator="\n").writerows(rows)


def _package_name(package: str) -> str:
    return package.replace("-", "_")


def _python_destination(package_name: str, src: Path) -> PurePosixPath:
    parts = src.parts
    if package_name in parts:
        index = len(parts) - 1 - list(reversed(parts)).index(package_name)
        return PurePosixPath(*parts[index:])
    return PurePosixPath(package_name) / src.name


def _write_wheel(args: argparse.Namespace, extension: Path, wheel: Path) -> None:
    root = wheel.parent / (wheel.stem + "_wheel")
    if root.exists():
        shutil.rmtree(root)
    package_name = _package_name(args.package)
    package_dir = root / package_name
    dist = args.package.replace("-", "_").replace(".", "_").lower()
    dist_info = root / f"{dist}-{args.version}.dist-info"
    package_dir.mkdir(parents=True)
    dist_info.mkdir(parents=True)
    has_init = False
    for src in args.py_src:
        dest = _python_destination(package_name, src)
        target = root / dest.as_posix()
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        has_init = has_init or dest == PurePosixPath(package_name) / "__init__.py"
    if not has_init:
        (package_dir / "__init__.py").write_text(
            "from importlib import import_module as _import_module\n"
            "_import_module('.' + '_C', __name__)\n",
            encoding="utf-8",
        )
    shutil.copy2(extension, package_dir / "_C.so")
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {args.package}\nVersion: {args.version}\n",
        encoding="utf-8",
    )
    (dist_info / "WHEEL").write_text(
        "Wheel-Version: 1.0\n"
        "Generator: vaso_torch_extension\n"
        "Root-Is-Purelib: false\n"
        f"Tag: {args.python_abi}-{args.python_abi}-linux_x86_64\n",
        encoding="utf-8",
    )
    record_rel = PurePosixPath(dist_info.relative_to(root).as_posix()) / "RECORD"
    _write_record(root, record_rel)
    wheel.parent.mkdir(parents=True, exist_ok=True)
    if wheel.exists():
        wheel.unlink()
    timestamp = (1980, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
            rel = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(rel, date_time=timestamp)
            info.external_attr = (path.stat().st_mode & 0o777) << 16
            zf.writestr(info, path.read_bytes())


def _write_metadata(args: argparse.Namespace, plan: dict[str, Any], toolchain: dict[str, Any], action_digest: str, rootfs_manifest: str) -> None:
    package = plan.get("package", {})
    declared_archs = plan.get("gpu_archs") if isinstance(plan.get("gpu_archs"), list) else []
    torch_cuda_arch_list = _torch_cuda_arch_list(plan)
    if not declared_archs and not torch_cuda_arch_list:
        declared_archs = [args.cuda_arch_list]
    metadata = {
        "schema_version": 1,
        "package": args.package,
        "version": args.version,
        "action_input_digest": action_digest,
        "declared_cuda_architectures": declared_archs,
        "torch_cuda_arch_list": torch_cuda_arch_list,
        "python_abi": args.python_abi,
        "mechanism": "vaso-torch-extension",
        "wheel": str(args.wheel_out),
        "input_prefixes": {
            "torch": str(args.torch_prefix),
            "cuda": str(args.cuda_prefix),
            "nccl": str(args.nccl_prefix),
            "nvshmem": str(args.nvshmem_prefix) if args.nvshmem_prefix is not None else None,
        },
        "rdc": args.rdc,
        "device_link_files": [str(path) for path in args.device_link_file],
        "rootfs_manifest": rootfs_manifest,
        "mode": "execute",
        "will_build": True,
        "torch_provider_package": package.get("name"),
        "torch_provider_version": package.get("version"),
        "toolchain": toolchain,
    }
    args.build_plan_out.parent.mkdir(parents=True, exist_ok=True)
    args.provider_metadata_out.parent.mkdir(parents=True, exist_ok=True)
    args.build_plan_out.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.provider_metadata_out.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.result_marker_out.write_text("Vaso PyTorch extension wheel build completed.\n", encoding="utf-8")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel-out", type=Path, required=True)
    parser.add_argument("--build-plan-out", type=Path, required=True)
    parser.add_argument("--provider-metadata-out", type=Path, required=True)
    parser.add_argument("--result-marker-out", type=Path, required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--python-abi", required=True)
    parser.add_argument("--torch-prefix", type=Path, required=True)
    parser.add_argument("--torch-prefix-manifest", type=Path, required=True)
    parser.add_argument("--torch-build-plan", type=Path, required=True)
    parser.add_argument("--torch-provider-metadata", type=Path, required=True)
    parser.add_argument("--cuda-prefix", type=Path, required=True)
    parser.add_argument("--cuda-prefix-manifest", type=Path, required=True)
    parser.add_argument("--nccl-prefix", type=Path, required=True)
    parser.add_argument("--nccl-prefix-manifest", type=Path, required=True)
    parser.add_argument("--python-prefix", type=Path)
    parser.add_argument("--nvshmem-prefix", type=Path)
    parser.add_argument("--nvshmem-prefix-manifest", type=Path)
    parser.add_argument("--rootfs-identity", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path, required=True)
    parser.add_argument("--cxx", required=True)
    parser.add_argument("--nvcc", required=True)
    parser.add_argument("--cxx11-abi", required=True)
    parser.add_argument("--cxx-standard", required=True)
    parser.add_argument("--cuda-arch-list", required=True)
    parser.add_argument("--rdc", action="store_true")
    parser.add_argument("--src", type=Path, action="append", default=[])
    parser.add_argument("--cuda-src", type=Path, action="append", default=[])
    parser.add_argument("--py-src", type=Path, action="append", default=[])
    parser.add_argument("--device-link-file", type=Path, action="append", default=[])
    parser.add_argument("--dep-file", type=Path, action="append", default=[])
    parser.add_argument("--include-dir", type=Path, action="append", default=[])
    parser.add_argument("--system-include-dir", type=Path, action="append", default=[])
    parser.add_argument("--define", action="append", default=[])
    parser.add_argument("--link-file", type=Path, action="append", default=[])
    parser.add_argument("--link-flag", action="append", default=[])
    parser.add_argument("--extra-cxx-flag", action="append", default=[])
    parser.add_argument("--extra-cuda-flag", action="append", default=[])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    rootfs_manifest = _insula_manifest()
    plan = _read_json(args.torch_build_plan)
    toolchain = _read_json(args.toolchain)
    args.cxx_standard = _normalize_cxx_standard(args.cxx_standard)
    expected_abi = _cxx11_abi(plan)
    expected_archs = _torch_cuda_arch_list(plan)
    required_cxx = _required_cxx_standard(args.torch_prefix)
    if required_cxx is not None and int(args.cxx_standard) < int(required_cxx):
        raise SystemExit(f"extension C++ standard c++{args.cxx_standard} is older than torch requires c++{required_cxx}")
    if args.cxx11_abi != expected_abi:
        raise SystemExit(f"extension CXX11 ABI {args.cxx11_abi} does not match torch plan {expected_abi}")
    if expected_archs and args.cuda_arch_list != expected_archs:
        raise SystemExit(f"extension CUDA arch list {args.cuda_arch_list} does not match torch plan {expected_archs}")
    if args.python_prefix is not None and not _python_include_dirs(args.python_prefix, args.python_abi):
        raise SystemExit(f"Python prefix {args.python_prefix} does not provide Python.h for ABI {args.python_abi}")
    if args.nvshmem_prefix is not None and args.nvshmem_prefix_manifest is None:
        raise SystemExit("--nvshmem-prefix requires --nvshmem-prefix-manifest")
    if args.nvshmem_prefix_manifest is not None and args.nvshmem_prefix is None:
        raise SystemExit("--nvshmem-prefix-manifest requires --nvshmem-prefix")
    if args.nvshmem_prefix is not None and not args.rdc:
        raise SystemExit("NVSHMEM extensions must pass --rdc")
    if args.nvshmem_prefix is not None and not args.device_link_file:
        raise SystemExit("NVSHMEM extensions must pass --device-link-file for libnvshmem_device.a")
    work = args.wheel_out.parent / (args.wheel_out.stem + "_work")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    _, objects = _compile_objects(args, work)
    objects.extend(_device_link_objects(args, objects, work))
    extension = work / "_C.so"
    _link_extension(args, objects, extension)
    _write_wheel(args, extension, args.wheel_out)
    action_digest = _action_digest(args, plan, toolchain)
    _write_metadata(args, plan, toolchain, action_digest, rootfs_manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
