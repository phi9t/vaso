#!/usr/bin/env python3
"""Verify the PyTorch ODR-sensitive provider version policy.

This checker is intentionally local and hermetic: it reads checked-in graph
snapshots, native_overrides.json, migration_ledger.json, and
native/pytorch/plan.py. Live upstream network refreshes are research evidence
for updating those files, not a test dependency.

The ODR package families and companion-version normalization come from
spack_to_bazel.py, which enforces the same families at lock time. Literal
source pins (releases, commits, tags) are owned by //native/pytorch:plan_test;
this checker asserts the relations *between* the plan, the native overrides,
and the checked graphs.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from types import ModuleType
from typing import Any


EXACT_VERSION_KEY = re.compile(r"^(?P<package>[a-z0-9][a-z0-9-]*)@(?P<version>\d+(?:\.\d+)*)$")
# Leading package-name run of an override key, before any version, variant,
# compiler, or whitespace, so `protobuf+shared@21.12` is still a protobuf key.
KEY_PACKAGE = re.compile(r"^\s*([A-Za-z0-9_-]+)")
GRPC_PACKAGES = ("grpc", "grpc-cpp", "py-grpcio")


class InputError(Exception):
    """A required input file is missing or unreadable."""


def _load_module(name: str, path: Path) -> ModuleType:
    if not path.is_file():
        raise InputError(f"cannot import {name}: {path} is not a file")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise InputError(f"cannot import {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except (OSError, SyntaxError) as exc:
        raise InputError(f"cannot import {name} from {path}: {exc}") from None
    return module


spack_to_bazel = _load_module("spack_to_bazel", Path(__file__).with_name("spack_to_bazel.py"))
ODR_PACKAGES = spack_to_bazel.ODR_SENSITIVE_PROVIDER_PACKAGES
ODR_FAMILIES = spack_to_bazel.ODR_SENSITIVE_PROVIDER_FAMILIES


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise InputError(f"{path}: file does not exist") from None
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"{path}: cannot read JSON: {exc}") from None
    if not isinstance(data, dict):
        raise InputError(f"{path}: expected a JSON object, got {type(data).__name__}")
    return data


def _object_field(data: dict[str, Any], key: str, where: Path) -> dict[str, Any]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise InputError(f"{where}: {key!r} must be an object, got {type(value).__name__}")
    return value


def _graph_nodes(graph_path: Path) -> list[dict[str, Any]]:
    data = _load_json_object(graph_path)
    nodes = data["nodes"] if "nodes" in data else _object_field(data, "spec", graph_path).get("nodes", [])
    if not isinstance(nodes, list) or not all(isinstance(node, dict) for node in nodes):
        raise InputError(f"{graph_path}: nodes must be a list of objects")
    return nodes


def _node_name(node: dict[str, Any]) -> str:
    return str(node.get("package") or node.get("name") or "")


def _node_version(node: dict[str, Any]) -> str:
    return str(node.get("version") or "")


def _ledger_py_torch_graph(ledger_path: Path) -> Path:
    ledger = _load_json_object(ledger_path)
    frontier_graphs = _object_field(ledger, "frontier_graphs", ledger_path)
    py_torch = frontier_graphs.get("py-torch", {})
    graph = py_torch.get("graph") if isinstance(py_torch, dict) else None
    if not isinstance(graph, str) or not graph:
        raise InputError(f"{ledger_path}: frontier_graphs['py-torch'].graph is not set")
    # Resolve next to the ledger without following symlinks, so a Bazel test
    # must declare the graph as data instead of reading the source tree.
    return ledger_path.parent / graph


def expected_odr_overrides(plan: ModuleType) -> tuple[dict[str, str], list[str]]:
    """ODR-family native override keys the PyTorch plan admits, with labels.

    A family may only admit the provider it selects: a `native_override_key`
    on a family whose selected provider is None (or a different version) is a
    plan error, not an admission.
    """
    expected: dict[str, str] = {}
    errors: list[str] = []
    for name, family in sorted(plan.ODR_PROVIDER_FAMILIES.items()):
        key = family.get("native_override_key")
        if key:
            selected = family.get("selected_cpp_provider", family.get("selected_provider"))
            label = family.get("native_override_label")
            if key != selected:
                errors.append(
                    f"ODR_PROVIDER_FAMILIES[{name!r}] native_override_key {key!r} is not its selected "
                    f"provider {selected!r}"
                )
            elif not label:
                errors.append(f"ODR_PROVIDER_FAMILIES[{name!r}] has no native_override_label")
            else:
                expected[key] = label
        python_key = family.get("selected_python_provider")
        if python_key and family.get("python_native_override_status") != "blocked":
            label = family.get("python_native_override_label")
            if not label:
                errors.append(
                    f"ODR_PROVIDER_FAMILIES[{name!r}] unblocks {python_key} but has no "
                    "python_native_override_label"
                )
            else:
                expected[python_key] = label
    return expected, errors


def _check_plan_relations(plan: ModuleType) -> list[str]:
    errors: list[str] = []
    policy = plan.SOURCE_DEPENDENCY_POLICY
    contract = policy["dependency_contract"]
    evidence = policy["upstream_source_evidence"]
    families = plan.ODR_PROVIDER_FAMILIES

    gitlink = evidence["pytorch"]["submodule_gitlinks"].get("third_party/protobuf")
    protobuf_evidence = evidence["protobuf"]
    if protobuf_evidence.get("commit") != gitlink:
        errors.append(
            "protobuf source commit does not equal the PyTorch third_party/protobuf gitlink: "
            f"{protobuf_evidence.get('commit')!r} != {gitlink!r}"
        )
    if protobuf_evidence.get("spack_style_tag_peeled_commit") != gitlink:
        errors.append(
            f"protobuf Spack-style tag {protobuf_evidence.get('spack_style_tag')!r} does not peel to "
            f"the PyTorch gitlink: {protobuf_evidence.get('spack_style_tag_peeled_commit')!r} != {gitlink!r}"
        )

    protobuf_family = families["protobuf"]
    selected_providers = {
        name: family.get("selected_cpp_provider", family.get("selected_provider"))
        for name, family in families.items()
    }
    selected_providers["py-protobuf"] = protobuf_family.get("selected_python_provider")
    for name, selected in sorted(selected_providers.items()):
        provider = contract.get(name, {}).get("provider")
        if provider != selected:
            errors.append(
                f"dependency_contract {name} provider {provider!r} differs from "
                f"ODR_PROVIDER_FAMILIES {selected!r}"
            )
    cpp_version = str(protobuf_family.get("selected_cpp_provider", "")).partition("@")[2]
    python_version = str(protobuf_family.get("selected_python_provider", "")).partition("@")[2]
    if spack_to_bazel.odr_family_version("protobuf", cpp_version) != spack_to_bazel.odr_family_version(
        "py-protobuf", python_version
    ):
        errors.append(
            "selected protobuf providers are not one family: "
            f"protobuf@{cpp_version} vs py-protobuf@{python_version}"
        )

    for name, entry in sorted(contract.items()):
        role = entry.get("prefix_role")
        if role == "rejected-odr-provider" and name not in plan.REJECTED_ODR_PREFIX_KEYS:
            errors.append(f"{name} is a rejected ODR provider but the planner accepts its prefix")
        if role == "accepted-build-input" and name not in plan.SUPPORTED_PREFIX_KEYS:
            errors.append(f"{name} is an accepted build input but the planner has no prefix key for it")
    overlap = sorted(plan.REJECTED_ODR_PREFIX_KEYS & plan.SUPPORTED_PREFIX_KEYS)
    if overlap:
        errors.append(f"planner both accepts and rejects prefix keys: {', '.join(overlap)}")
    return errors


def _check_native_overrides(overrides_path: Path, expected: dict[str, str]) -> list[str]:
    errors: list[str] = []
    overrides = _object_field(_load_json_object(overrides_path), "native", overrides_path)
    odr_keys: dict[str, str] = {}
    for key, label in sorted(overrides.items()):
        match = KEY_PACKAGE.match(key)
        package = match.group(1).lower() if match else ""
        if package not in ODR_PACKAGES:
            continue
        if "@" not in key:
            errors.append(f"ODR-sensitive native override {key!r} must be keyed as '{package}@<version>'")
            continue
        if not EXACT_VERSION_KEY.match(key):
            errors.append(
                f"ODR-sensitive native override {key!r} must be exactly '{package}@X.Y.Z' "
                "(no range, variant, compiler, case change, or whitespace)"
            )
            continue
        odr_keys[key] = label

    for key in sorted(set(odr_keys) - set(expected)):
        errors.append(f"native override {key} is not an ODR provider admitted by the PyTorch plan")
    for key, label in sorted(expected.items()):
        if key not in odr_keys:
            errors.append(f"missing native override {key} => {label} required by the PyTorch plan")
        elif odr_keys[key] != label:
            errors.append(f"native override {key} => {odr_keys[key]} differs from the plan's {label}")
    return errors


def graph_node_only_providers(plan: ModuleType) -> dict[str, str]:
    """Package -> version the plan pins for providers that exist only as graph nodes."""
    pinned: dict[str, str] = {}
    for name, family in plan.ODR_PROVIDER_FAMILIES.items():
        if family.get("provider_scope") == "spack-graph-node-only" and family.get("selected_provider"):
            pinned[name] = str(family["selected_provider"]).partition("@")[2]
    return pinned


def _check_graph(graph_path: Path, pinned: dict[str, str]) -> list[str]:
    errors: list[str] = []
    versions_by_package: dict[str, set[str]] = defaultdict(set)
    versions_by_family: dict[str, set[str]] = defaultdict(set)

    for node in _graph_nodes(graph_path):
        package = _node_name(node)
        if package not in ODR_PACKAGES:
            continue
        version = _node_version(node)
        if not version:
            errors.append(f"{graph_path}: ODR-sensitive node {package} has no version")
            continue
        versions_by_package[package].add(version)
        versions_by_family[ODR_FAMILIES[package]].add(spack_to_bazel.odr_family_version(package, version))

    for package, versions in sorted(versions_by_package.items()):
        if len(versions) > 1:
            errors.append(f"{graph_path}: {package} appears at multiple versions: {', '.join(sorted(versions))}")
    for family, versions in sorted(versions_by_family.items()):
        if len(versions) > 1:
            errors.append(
                f"{graph_path}: ODR family {family} appears at multiple versions: {', '.join(sorted(versions))}"
            )
    if "abseil-cpp" in versions_by_package:
        errors.append(f"{graph_path}: abseil-cpp is not admitted to the PyTorch source island")
    for package in GRPC_PACKAGES:
        if package in versions_by_package:
            errors.append(f"{graph_path}: {package} is not admitted to the PyTorch source island")
    for package, version in sorted(pinned.items()):
        for found in sorted(versions_by_package.get(package, set()) - {version}):
            errors.append(
                f"{graph_path}: {package}@{found} differs from the plan's graph-node provider "
                f"{package}@{version}"
            )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--native-overrides", type=Path, required=True)
    parser.add_argument(
        "--ledger",
        type=Path,
        required=True,
        help="migration_ledger.json; its py-torch frontier graph is always checked",
    )
    parser.add_argument("--graph", type=Path, action="append", default=[], help="additional graph to check")
    args = parser.parse_args(argv)

    try:
        plan = _load_module("pytorch_native_plan", args.plan)
        errors = _check_plan_relations(plan)
        expected, plan_errors = expected_odr_overrides(plan)
        errors.extend(plan_errors)
        errors.extend(_check_native_overrides(args.native_overrides, expected))
        pinned = graph_node_only_providers(plan)
        for graph_path in [_ledger_py_torch_graph(args.ledger), *args.graph]:
            errors.extend(_check_graph(graph_path, pinned))
    except InputError as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    print("PyTorch ODR policy check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
