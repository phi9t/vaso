#!/usr/bin/env python3
"""Emit Spack externals for packages supplied by the rootfs/estate."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOTFS_COMPONENTS: dict[str, str] = {
    "cuda_toolkit": "cuda",
    "cudnn": "cudnn",
    "cudss": "cudss",
    "cusparselt": "cusparselt",
    "nccl": "nccl",
    "nvshmem": "nvshmem",
}
GLOBAL_ROOTFS_COMPONENTS: dict[str, str] = {
    "llvm": "llvm",
}

# TensorRT is deliberately excluded by the D10 human decision. The cached Spack
# distribution also has no builtin tensorrt package for this graph.
OUT_OF_SCOPE_COMPONENTS: frozenset[str] = frozenset({"tensorrt"})

@dataclass(frozen=True)
class External:
    package: str
    spec: str
    prefix: str
    extra_attributes: dict[str, object] | None = None


ROOTFS_TOOL_EXTERNALS: tuple[External, ...] = (
    External("bash", "bash@5.2", "/usr"),
    External("zip", "zip@3.0", "/usr"),
    External("bazel", "bazel@7.7.0", "/opt/vaso/bazel-7.7.0"),
)


def _verified_components(manifest: dict[str, object]) -> dict[str, object]:
    verified = manifest.get("verified_versions")
    if not isinstance(verified, dict):
        raise ValueError("rootfs manifest must contain verified_versions")
    components = verified.get("components")
    if not isinstance(components, dict):
        raise ValueError("rootfs manifest must contain verified_versions.components")
    return components


def _expected(manifest: dict[str, object], component: str) -> str:
    components = _verified_components(manifest)
    data = components.get(component)
    if not isinstance(data, dict):
        raise ValueError(f"missing verified rootfs component: {component}")
    expected = data.get("expected")
    if not isinstance(expected, str) or not expected:
        raise ValueError(f"missing expected version for rootfs component: {component}")
    return expected


def _cuda_major(manifest: dict[str, object]) -> int:
    version = _expected(manifest, "cuda_toolkit")
    match = re.match(r"^([0-9]+)\.", version)
    if not match:
        raise ValueError(f"invalid CUDA toolkit version: {version!r}")
    return int(match.group(1))


def _first_three(version: str, component: str) -> str:
    parts = version.split(".")
    if len(parts) < 3:
        raise ValueError(f"{component} version must have at least three components: {version!r}")
    return ".".join(parts[:3])


def _llvm_spec_version(version: str) -> str:
    if version.endswith("git"):
        version = version[: -len("git")]
    return version


def _cusparselt_spec_version(manifest_version: str, cuda_major: int) -> str:
    if cuda_major == 12:
        cuda_suffix = "cuda120"
    elif cuda_major == 13:
        cuda_suffix = "cuda130"
    else:
        raise ValueError(f"unsupported CUDA major for cuSPARSELt external: {cuda_major}")
    return f"{_first_three(manifest_version, 'cusparselt')}-{cuda_suffix}"


def externals_from_manifest(
    manifest: dict[str, object],
    *,
    cuda_prefix: str = "/usr/local/cuda",
    cuda_arch: str = "100",
    target_triplet: str = "x86_64-linux",
    include_nvtx: bool = True,
    include_build_tools: bool = True,
) -> list[External]:
    """Return recipe-compatible Spack external specs for a rootfs manifest."""

    cuda_major = _cuda_major(manifest)
    cuda_version = _expected(manifest, "cuda_toolkit")
    cudnn_version = _expected(manifest, "cudnn")
    nccl_version = _expected(manifest, "nccl")
    cusparselt_version = _expected(manifest, "cusparselt")
    cudss_version = _expected(manifest, "cudss")
    nvshmem_version = _expected(manifest, "nvshmem")
    llvm = _verified_components(manifest).get("llvm")

    entries = [
        External("cuda", f"cuda@{cuda_version}", cuda_prefix),
        External("cuda", f"cuda@{cuda_version} +allow-unsupported-compilers", cuda_prefix),
        External("cudnn", f"cudnn@{cudnn_version}-{cuda_major}", cuda_prefix),
        External(
            "nccl",
            f"nccl@{nccl_version}-1 +cuda cuda_arch={cuda_arch} fabrics=verbs",
            cuda_prefix,
        ),
        External(
            "cusparselt",
            f"cusparselt@{_cusparselt_spec_version(cusparselt_version, cuda_major)}",
            cuda_prefix,
        ),
        External("cudss", f"cudss@{_first_three(cudss_version, 'cudss')}", cuda_prefix),
        External(
            "nvshmem",
            (
                f"nvshmem@{nvshmem_version} +cuda +ucx +gdrcopy ~mpi "
                f"~nccl ~shmem ~libfabric cuda_arch={cuda_arch}"
            ),
            cuda_prefix,
        ),
    ]
    if include_nvtx:
        entries.append(
            External(
                "nvtx",
                "nvtx@3.3.0 ~python",
                f"{cuda_prefix}/targets/{target_triplet}",
            )
        )
    if isinstance(llvm, dict):
        commit = llvm.get("commit")
        version = llvm.get("expected")
        prefix = llvm.get("prefix", "/usr/lib/llvm-23")
        if not isinstance(commit, str) or not re.match(r"^[0-9a-f]{40}$", commit):
            raise ValueError("missing LLVM commit for rootfs external")
        if not isinstance(version, str) or not version:
            raise ValueError("missing LLVM version for rootfs external")
        if not isinstance(prefix, str) or not prefix.startswith("/"):
            raise ValueError("missing LLVM prefix for rootfs external")
        entries.append(
            External(
                "llvm",
                f"llvm@{_llvm_spec_version(version)} +clang +lld +mlir",
                prefix,
                {
                    "compilers": {
                        "c": f"{prefix}/bin/clang",
                        "cxx": f"{prefix}/bin/clang++",
                    },
                },
            )
        )
    if include_build_tools:
        entries.extend(ROOTFS_TOOL_EXTERNALS)
    return entries


def yaml_scalar(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value)


def append_yaml_mapping(lines: list[str], data: dict[str, object], indent: int) -> None:
    spaces = " " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{spaces}{key}:")
            append_yaml_mapping(lines, value, indent + 2)
        else:
            lines.append(f"{spaces}{key}: {yaml_scalar(value)}")


def packages_yaml_fragment(entries: Iterable[External]) -> str:
    lines: list[str] = []
    grouped: dict[str, list[External]] = {}
    for entry in entries:
        grouped.setdefault(entry.package, []).append(entry)
    for package, package_entries in grouped.items():
        lines.extend(
            [
                f"  {package}:",
                "    externals:",
            ]
        )
        for entry in package_entries:
            lines.extend(
                [
                    f"    - spec: {json.dumps(entry.spec)}",
                    f"      prefix: {entry.prefix}",
                ]
            )
            if entry.extra_attributes:
                lines.append("      extra_attributes:")
                append_yaml_mapping(lines, entry.extra_attributes, 8)
        lines.append("    buildable: false")
    return "\n".join(lines) + "\n"


def lock_components_requiring_externals(lock: dict[str, object]) -> set[str]:
    lines = lock.get("lines")
    if not isinstance(lines, dict):
        raise ValueError("CUDA ecosystem lock must contain lines")
    required: set[str] = set()
    for line_name, line_data in lines.items():
        if not isinstance(line_data, dict):
            raise ValueError(f"invalid lock line entry: {line_name!r}")
        components = line_data.get("components")
        if not isinstance(components, dict):
            raise ValueError(f"missing components for lock line: {line_name}")
        unknown = set(components) - set(ROOTFS_COMPONENTS) - OUT_OF_SCOPE_COMPONENTS
        if unknown:
            raise ValueError(f"unclassified rootfs component(s) with possible Spack coverage: {sorted(unknown)}")
        required.update(ROOTFS_COMPONENTS[name] for name in components if name in ROOTFS_COMPONENTS)
    global_components = lock.get("components", {})
    if not isinstance(global_components, dict):
        raise ValueError("CUDA ecosystem lock top-level components must be an object")
    unknown_global = set(global_components) - set(GLOBAL_ROOTFS_COMPONENTS)
    if unknown_global:
        raise ValueError(f"unclassified global rootfs component(s) with possible Spack coverage: {sorted(unknown_global)}")
    required.update(GLOBAL_ROOTFS_COMPONENTS[name] for name in global_components if name in GLOBAL_ROOTFS_COMPONENTS)
    return required


def check_lock_coverage(lock: dict[str, object], entries: Iterable[External]) -> None:
    required = lock_components_requiring_externals(lock)
    provided = {entry.package for entry in entries}
    missing = sorted(required - provided)
    if missing:
        raise ValueError(f"missing non-buildable rootfs Spack external(s): {', '.join(missing)}")


def load_json(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--lock", type=Path)
    parser.add_argument("--cuda-prefix", default="/usr/local/cuda")
    parser.add_argument("--cuda-arch", default="100")
    parser.add_argument("--target-triplet", default="x86_64-linux")
    parser.add_argument("--format", choices=("packages-yaml", "specs"), default="packages-yaml")
    parser.add_argument("--no-nvtx", action="store_true")
    args = parser.parse_args()

    manifest = load_json(args.manifest)
    entries = externals_from_manifest(
        manifest,
        cuda_prefix=args.cuda_prefix,
        cuda_arch=args.cuda_arch,
        target_triplet=args.target_triplet,
        include_nvtx=not args.no_nvtx,
    )
    if args.lock:
        check_lock_coverage(load_json(args.lock), entries)
    if args.format == "specs":
        for entry in entries:
            print(entry.spec)
    else:
        print(packages_yaml_fragment(entries), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
