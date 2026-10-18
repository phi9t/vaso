#!/usr/bin/env python3
"""Shared release-wheel relocation policy."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


ROOTFS_LINE_COMPONENTS = frozenset(
    (
        "cuda_toolkit",
        "cudnn",
        "cudss",
        "cusparselt",
        "nccl",
        "nvshmem",
        "tensorrt",
    )
)


def load_rootfs_lock(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _append_library_dirs(out: set[str], layout: dict[str, Any]) -> None:
    for key, value in layout.items():
        if not isinstance(value, str) or not value.startswith("/"):
            continue
        lowered = key.lower()
        if lowered == "lib" or lowered.endswith("_lib") or lowered.endswith("_libdir"):
            out.add(value.rstrip("/") or "/")
        if lowered == "prefix" and value.rstrip("/") == "/usr/local/cuda":
            out.add("/usr/local/cuda/targets/x86_64-linux/lib")


def _iter_component_docs(lock: dict[str, Any], line: str) -> Iterable[dict[str, Any]]:
    for component in lock.get("components", {}).values():
        if isinstance(component, dict):
            yield component

    lines = lock.get("lines", {})
    if line not in lines:
        raise ValueError(f"rootfs lock does not define line {line!r}")
    line_doc = lines[line]
    if not isinstance(line_doc, dict):
        raise ValueError(f"rootfs lock line {line!r} must be an object")
    components = line_doc.get("components", {})
    if not isinstance(components, dict):
        raise ValueError(f"rootfs lock line {line!r} components must be an object")
    for name, component in components.items():
        if name in ROOTFS_LINE_COMPONENTS and isinstance(component, dict):
            yield component


def rootfs_library_dirs(lock: dict[str, Any], line: str) -> tuple[str, ...]:
    dirs: set[str] = set()
    for component in _iter_component_docs(lock, line):
        layout = component.get("install_layout", {})
        if isinstance(layout, dict):
            _append_library_dirs(dirs, layout)

    runtime_driver = lock.get("lines", {}).get(line, {}).get("runtime_driver", {})
    if isinstance(runtime_driver, dict):
        for path in runtime_driver.get("bind_libs", []):
            if isinstance(path, str) and path.startswith("/"):
                dirs.add(str(Path(path).parent))

    return tuple(sorted(dirs))


def is_under_rootfs_dir(path: str, rootfs_dirs: Iterable[str]) -> bool:
    normalized = Path(path).as_posix().rstrip("/") or "/"
    for rootfs_dir in rootfs_dirs:
        root = Path(rootfs_dir).as_posix().rstrip("/") or "/"
        if normalized == root or normalized.startswith(root + "/"):
            return True
    return False
