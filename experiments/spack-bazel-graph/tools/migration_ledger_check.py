#!/usr/bin/env python3
"""Check the Spack-to-native migration ledger against checked-in evidence."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path


def _load_spack_to_bazel():
    """Load the sibling lock generator so override resolution has one definition."""
    script = Path(__file__).with_name("spack_to_bazel.py")
    spec = importlib.util.spec_from_file_location("spack_to_bazel", script)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot import {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


spack_to_bazel = _load_spack_to_bazel()


NATIVE_STATUSES = {"native"}
IN_PROGRESS_STATUSES = {"recipe-captured", "spack"}
TERMINAL_STATUSES = NATIVE_STATUSES | {"not-a-target"}
KNOWN_STATUSES = TERMINAL_STATUSES | IN_PROGRESS_STATUSES
TOPO_INDEX_KEYS = ("topo_index", "focused_topo_index", "lean_py_torch_topo_index")
PRUNED_PY_TORCH_NOTE = "not in the pruned py-torch closure (ticket 14)"
DECIDED_PYTHON_VERSION = "3.13.13"


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _synthetic_target_exists(build_text: str, target: str) -> bool:
    if not target.startswith("//synthetic:"):
        return True
    name = target.removeprefix("//synthetic:")
    return re.search(rf'\bname\s*=\s*"{re.escape(name)}"', build_text) is not None


def _front_package(front: str, packages: dict[str, dict]) -> str | None:
    if not front:
        return None
    for pattern in (
        r"^([A-Za-z0-9_.+-]+)\s+is\s+the\s+active\b",
        r"\bactive(?:\s+\w+){0,4}\s+frontier\s*:\s*([A-Za-z0-9_.+-]+)\b",
        r"\bnext(?:\s+\w+){0,4}\s+frontier\s*:\s*([A-Za-z0-9_.+-]+)\b",
    ):
        match = re.search(pattern, front)
        if match and match.group(1) in packages:
            return match.group(1)
    return None


def _front_lean_py_torch_topo_index(front: str) -> int | None:
    match = re.search(r"\blean\s+py-torch\s+topo\s+index\s+([0-9]+)\b", front)
    return int(match.group(1)) if match else None


def _topological_order_indices(graph: dict) -> dict[str, int]:
    order = graph.get("topological_order", [])
    if not isinstance(order, list):
        return {}
    indices: dict[str, int] = {}
    for index, item in enumerate(order):
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = item.get("name")
        else:
            name = None
        if isinstance(name, str):
            indices[name] = index
    return indices


def _python_versions(graph: dict) -> set[str]:
    versions: set[str] = set()
    nodes = graph.get("nodes", [])
    if not isinstance(nodes, list):
        return versions
    for node in nodes:
        if isinstance(node, dict) and node.get("package") == "python" and isinstance(node.get("version"), str):
            versions.add(node["version"])
    return versions


def check_repo(root: Path) -> list[str]:
    root = root.resolve()
    if root.is_file():
        root = root.parent
    ledger = _load_json(root / "migration_ledger.json")
    overrides_doc = _load_json(root / "native_overrides.json")
    native_overrides = overrides_doc.get("native", {})
    provided_versions = overrides_doc.get("provided_versions")
    native_override_values = set(native_overrides.values())
    synthetic_build_path = root / "synthetic" / "BUILD.bazel"
    synthetic_build = synthetic_build_path.read_text(encoding="utf-8") if synthetic_build_path.exists() else ""
    packages = ledger.get("packages", {})
    errors: list[str] = []

    if not isinstance(packages, dict):
        return ["migration_ledger.json: packages must be an object"]
    known_version_substitutions = overrides_doc.get("known_version_substitutions", [])
    if native_overrides and not isinstance(provided_versions, dict):
        errors.append("native_overrides.json: provided_versions must record every native key's built version")
    if isinstance(provided_versions, dict):
        errors.extend(
            f"native_overrides.json: {error}"
            for error in spack_to_bazel.native_provider_version_static_errors(
                native_overrides, provided_versions, known_version_substitutions
            )
        )

    for name, entry in sorted(packages.items()):
        status = entry.get("status")
        if status not in KNOWN_STATUSES:
            errors.append(f"{name}: unknown status {status!r}")

        pruned_from_py_torch = PRUNED_PY_TORCH_NOTE in str(entry.get("note", ""))
        if (
            status in IN_PROGRESS_STATUSES
            and not pruned_from_py_torch
            and not any(isinstance(entry.get(key), int) for key in TOPO_INDEX_KEYS)
        ):
            errors.append(f"{name}: one of {', '.join(TOPO_INDEX_KEYS)} must be an integer")

        recipe = entry.get("recipe")
        if status in {"recipe-captured", "native"}:
            if not recipe:
                errors.append(f"{name}: {status} entry must name a recipe")
            elif not (root / recipe).is_file():
                errors.append(f"{name}: recipe does not exist: {recipe}")

        if status == "native":
            native_rule = entry.get("native_rule")
            if not native_rule:
                errors.append(f"{name}: native entry must name native_rule")
            elif native_rule not in native_override_values:
                errors.append(f"{name}: native_rule is not present in native_overrides.json: {native_rule}")

            abi_gate = entry.get("abi_gate")
            if not abi_gate:
                errors.append(f"{name}: native entry must name abi_gate")
            elif not _synthetic_target_exists(synthetic_build, abi_gate):
                errors.append(f"{name}: abi_gate target not found: {abi_gate}")

        focused_graph = entry.get("focused_graph")
        if name.startswith("py-") and isinstance(focused_graph, str):
            graph_path = root / focused_graph
            if not graph_path.is_file():
                errors.append(f"{name}: focused_graph does not exist: {focused_graph}")
            else:
                versions = _python_versions(_load_json(graph_path))
                if versions != {DECIDED_PYTHON_VERSION}:
                    errors.append(
                        f"{name}: focused_graph {focused_graph} contains Python versions "
                        f"{', '.join(sorted(versions)) or '<none>'}; expected only {DECIDED_PYTHON_VERSION}"
                    )

    front = _front_package(str(ledger.get("front", "")), packages)
    if front and packages[front].get("status") == "native":
        errors.append(f"front names native package as active frontier: {front}")
    front_lean_index = _front_lean_py_torch_topo_index(str(ledger.get("front", "")))
    if front and front_lean_index is not None:
        entry_index = packages[front].get("lean_py_torch_topo_index")
        if isinstance(entry_index, int) and entry_index != front_lean_index:
            errors.append(
                "front lean py-torch topo index "
                f"{front_lean_index} does not match "
                f"{front}.lean_py_torch_topo_index {entry_index}"
            )

    py_torch_graph = ledger.get("frontier_graphs", {}).get("py-torch", {}).get("graph")
    if isinstance(py_torch_graph, str):
        graph_path = root / py_torch_graph
        if graph_path.is_file():
            py_torch_graph_data = _load_json(graph_path)
            errors.extend(
                f"{py_torch_graph}: {error}"
                for error in spack_to_bazel.native_provider_version_graph_errors(
                    py_torch_graph_data.get("nodes", []),
                    native_overrides,
                    provided_versions if isinstance(provided_versions, dict) else {},
                    known_version_substitutions,
                    report_stale=True,
                )
            )
            graph_indices = _topological_order_indices(py_torch_graph_data)
            for name, entry in sorted(packages.items()):
                entry_index = entry.get("lean_py_torch_topo_index")
                if not isinstance(entry_index, int):
                    continue
                graph_index = graph_indices.get(name)
                if graph_index is None:
                    errors.append(f"{name}: not found in {py_torch_graph}")
                elif graph_index != entry_index:
                    errors.append(
                        f"{name}: lean_py_torch_topo_index {entry_index} "
                        f"does not match {py_torch_graph} index {graph_index}"
                    )
        else:
            errors.append(
                f"py-torch frontier graph does not exist: {py_torch_graph}; "
                "native provider version parity cannot be checked"
            )

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)

    errors = check_repo(args.root)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
