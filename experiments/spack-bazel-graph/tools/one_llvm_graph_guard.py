#!/usr/bin/env python3
"""Guard triumvirate graph captures against a second LLVM."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable


ROOTFS_LLVM_COMMIT = "35901313800ea6e6cbeb9226e51c7c4b29bfc40e"
ROOTFS_LLVM_VERSION = "23.0.0"
TRITON_TORCH_PIN_COMMIT = "675c59878aa2280b31f722aaf42b825fcee21de8"
TRITON_TORCH_PIN_VERSION = "3.8.0"
REQUIRED_PYTHON_VERSION = "3.13.13"
TRIUMVIRATE_CONSUMERS = frozenset({"py-torch", "py-triton", "py-jaxlib", "py-jax"})
TRITON_REQUIRED_DIRECT_DEPS = {
    "cuda": ("build", "link", "run"),
    "nlohmann-json": ("build",),
}
JAXLIB_BAZEL_BUILD_TOOL_DEPS = frozenset({"bash", "openjdk", "zip"})
JAX_DEPS_NATIVE_EXEMPT_PACKAGES = frozenset(
    {
        "bazel",
        "compiler-wrapper",
        "gcc",
        "gcc-runtime",
        "glibc",
        "py-jax",
        "py-jaxlib",
    }
)
RULE_IDS = frozenset(
    (
        "jax-native-deps",
        "jaxlib-bazel-rootfs",
        "jaxlib-cuda-compiler",
        "python-singleton",
        "rootfs-llvm",
        "triton-direct-deps",
    )
)


def _node_hash(node: dict) -> str:
    return str(node.get("spack_hash") or node.get("hash") or "")


def _node_label(node: dict) -> str:
    version = node.get("version", "<unknown>")
    return f"{node.get('package', '<unknown>')}@{version}"


def _rootfs_llvm(node: dict) -> bool:
    if str(node.get("version", "")) != ROOTFS_LLVM_VERSION:
        return False
    params = node.get("parameters", {})
    return params.get("clang") is True and params.get("lld") is True and params.get("mlir") is True


def _torch_pinned_triton(node: dict) -> bool:
    if node.get("package") != "py-triton":
        return False
    parameters = node.get("parameters", {})
    return (
        str(node.get("version", "")) == TRITON_TORCH_PIN_VERSION
        and parameters.get("commit") == TRITON_TORCH_PIN_COMMIT
    )


def _direct_dep_node(
    node: dict,
    dep_name: str,
    by_hash: dict[str, dict],
    by_name: dict[str, list[dict]],
) -> tuple[dict | None, set[str]]:
    for dep in node.get("deps", []):
        if dep.get("name") != dep_name:
            continue
        dep_node = by_hash.get(str(dep.get("hash")))
        if dep_node is None:
            matches = by_name.get(dep_name, [])
            dep_node = matches[0] if len(matches) == 1 else None
        return dep_node, set(dep.get("deptypes", []))
    return None, set()


def _dependency_closure(start: dict, by_hash: dict[str, dict], by_name: dict[str, list[dict]]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []

    def visit(node: dict) -> None:
        key = _node_hash(node) or f"{node.get('package')}@{node.get('version')}"
        if key in seen:
            return
        seen.add(key)
        out.append(node)
        for dep in node.get("deps", []):
            dep_node = by_hash.get(str(dep.get("hash")))
            if dep_node is None:
                matches = by_name.get(str(dep.get("name")), [])
                dep_node = matches[0] if len(matches) == 1 else None
            if dep_node is not None:
                visit(dep_node)

    visit(start)
    return out


def _jax_dependency_root(nodes: list[dict]) -> dict | None:
    for package in ("py-jax", "py-jaxlib"):
        for node in nodes:
            if node.get("package") == package:
                return node
    return None


def check_graph(graph: dict, graph_name: str) -> list[str]:
    nodes = list(graph.get("nodes", []))
    by_hash = {_node_hash(node): node for node in nodes if _node_hash(node)}
    by_name: dict[str, list[dict]] = {}
    for node in nodes:
        by_name.setdefault(str(node.get("package")), []).append(node)

    errors: list[str] = []
    has_triumvirate_consumer = any(node.get("package") in TRIUMVIRATE_CONSUMERS for node in nodes)
    if has_triumvirate_consumer:
        python_nodes = [node for node in nodes if node.get("package") == "python"]
        if len(python_nodes) != 1 or python_nodes[0].get("version") != REQUIRED_PYTHON_VERSION:
            found = ", ".join(_node_label(node) for node in python_nodes) or "<none>"
            errors.append(
                f"{graph_name}: triumvirate graph must contain exactly one python node "
                f"at version {REQUIRED_PYTHON_VERSION}, found {found}"
            )

    for consumer in nodes:
        if consumer.get("package") not in TRIUMVIRATE_CONSUMERS:
            continue
        for dep in _dependency_closure(consumer, by_hash, by_name):
            if dep.get("package") != "llvm":
                continue
            if _rootfs_llvm(dep):
                continue
            errors.append(
                f"{graph_name}: {consumer.get('package')} depends on non-rootfs LLVM "
                f"{dep.get('version', '<unknown>')}; expected rootfs llvm@{ROOTFS_LLVM_VERSION} "
                f"from llvm-project {ROOTFS_LLVM_COMMIT}"
            )
        if _torch_pinned_triton(consumer):
            for dep_name, expected_deptypes in TRITON_REQUIRED_DIRECT_DEPS.items():
                _, actual_deptypes = _direct_dep_node(consumer, dep_name, by_hash, by_name)
                if set(expected_deptypes).issubset(actual_deptypes):
                    continue
                errors.append(
                    f"{graph_name}: {_node_label(consumer)} is missing direct dependency "
                    f"{dep_name} with deptypes {', '.join(expected_deptypes)}"
                )

        if consumer.get("package") == "py-jaxlib" and consumer.get("parameters", {}).get("cuda"):
            cuda_node, _ = _direct_dep_node(consumer, "cuda", by_hash, by_name)
            if cuda_node and cuda_node.get("parameters", {}).get("allow-unsupported-compilers") is True:
                continue
            errors.append(
                f"{graph_name}: {_node_label(consumer)} uses CUDA without "
                "cuda+allow-unsupported-compilers; required for rootfs LLVM clang 23"
            )
        if consumer.get("package") == "py-jaxlib":
            bazel_node, _ = _direct_dep_node(consumer, "bazel", by_hash, by_name)
            if bazel_node is not None:
                build_tool_deps = {
                    dep.get("package"): dep
                    for dep in _dependency_closure(bazel_node, by_hash, by_name)
                    if dep.get("package") in JAXLIB_BAZEL_BUILD_TOOL_DEPS
                }
                for package in sorted(build_tool_deps):
                    errors.append(
                        f"{graph_name}: {_node_label(consumer)} still pulls Bazel build-tool "
                        f"dependency {_node_label(build_tool_deps[package])}; make bazel a "
                        "non-buildable rootfs external"
                    )
    jax_root = _jax_dependency_root(nodes)
    if jax_root is not None and any("status" in node for node in nodes):
        for dep in _dependency_closure(jax_root, by_hash, by_name):
            package = str(dep.get("package"))
            if package in JAX_DEPS_NATIVE_EXEMPT_PACKAGES:
                continue
            if dep.get("status") == "spack":
                errors.append(
                    f"{graph_name}: {jax_root.get('package')} dependency {_node_label(dep)} is not native; "
                    "B7 requires native providers for the JAX dependency graph except "
                    "py-jax/py-jaxlib and rootfs tool boundaries"
                )
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
        print("one-LLVM graph guard failed:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 1
    print(f"one-LLVM graph guard checked {len(args.graphs)} graph(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
