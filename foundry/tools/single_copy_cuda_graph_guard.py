#!/usr/bin/env python3
"""Guard captured graphs against second CUDA-family copies."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable


CUDA_FAMILY_PACKAGES = frozenset(
    {
        "cuda",
        "cudnn",
        "nccl",
        "cusparselt",
        "cudss",
        "nvshmem",
    }
)
RULE_IDS = frozenset(
    (
        "duplicate-cuda-family",
        "pip-nvidia-cuda",
    )
)


def _node_label(node: dict[str, object]) -> str:
    return f"{node.get('package', '<unknown>')}@{node.get('version', '<unknown>')}"


def _is_forbidden_pip_nvidia_node(package: str) -> bool:
    return package.startswith("py-nvidia-") or package.startswith("nvidia-")


def check_graph(graph: dict[str, object], graph_name: str) -> list[str]:
    nodes = list(graph.get("nodes", []))
    errors: list[str] = []
    by_cuda_package: dict[str, list[dict[str, object]]] = {}
    for node in nodes:
        package = str(node.get("package", ""))
        if package in CUDA_FAMILY_PACKAGES:
            by_cuda_package.setdefault(package, []).append(node)
        if _is_forbidden_pip_nvidia_node(package):
            errors.append(f"{graph_name}: forbidden pip NVIDIA CUDA package node {_node_label(node)}")

    for package, package_nodes in sorted(by_cuda_package.items()):
        if len(package_nodes) <= 1:
            continue
        versions = ", ".join(str(node.get("version", "<unknown>")) for node in package_nodes)
        errors.append(f"{graph_name}: CUDA-family package {package} appears more than once: {versions}")
    return errors


def check_graph_files(paths: Iterable[Path]) -> list[str]:
    errors: list[str] = []
    for path in paths:
        graph = json.loads(path.read_text(encoding="utf-8"))
        errors.extend(check_graph(graph, path.name))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("graphs", nargs="+", type=Path)
    args = parser.parse_args(argv)

    errors = check_graph_files(args.graphs)
    if errors:
        print("single-copy CUDA graph guard failed:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 1
    print(f"single-copy CUDA graph guard checked {len(args.graphs)} graph(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
