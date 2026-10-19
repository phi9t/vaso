#!/usr/bin/env python3
"""Emit a CycloneDX SBOM and evaluate the release license policy."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SHA256_RE = re.compile(r"^[A-Fa-f0-9]{64}$")


@dataclass
class Component:
    source: str
    name: str
    version: str
    purl: str
    license_id: str = "NOASSERTION"
    hashes: set[str] = field(default_factory=set)
    properties: dict[str, str] = field(default_factory=dict)
    component_type: str = "library"

    @property
    def bom_ref(self) -> str:
        return f"{self.source}:{self.name}@{self.version}"

    def merge(self, other: "Component") -> None:
        if self.license_id == "NOASSERTION" and other.license_id != "NOASSERTION":
            self.license_id = other.license_id
        self.hashes.update(other.hashes)
        self.properties.update(other.properties)

    def to_cyclonedx(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "type": self.component_type,
            "bom-ref": self.bom_ref,
            "name": self.name,
            "version": self.version,
            "purl": self.purl,
            "licenses": [{"license": {"id": self.license_id}}],
        }
        if self.hashes:
            doc["hashes"] = [
                {"alg": "SHA-256", "content": value}
                for value in sorted(self.hashes)
            ]
        if self.properties:
            doc["properties"] = [
                {"name": key, "value": value}
                for key, value in sorted(self.properties.items())
            ]
        return doc


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"{path}: expected a JSON object")
    return value


def license_id(value: Any) -> str:
    if value is None:
        return "NOASSERTION"
    if isinstance(value, str):
        text = value.strip()
        return text or "NOASSERTION"
    if isinstance(value, list):
        items = [license_id(item) for item in value]
        items = [item for item in items if item != "NOASSERTION"]
        return " AND ".join(items) if items else "NOASSERTION"
    return str(value).strip() or "NOASSERTION"


def sha256_values(node: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for key in ("sha256", "checksum", "digest"):
        raw = node.get(key)
        if not isinstance(raw, str):
            continue
        digest = raw.removeprefix("sha256:").lower()
        if SHA256_RE.fullmatch(digest):
            values.add(digest)
    source = node.get("source")
    if isinstance(source, dict):
        values.update(sha256_values(source))
    return values


def purl_for(name: str, version: str) -> str:
    return f"pkg:generic/{name}@{version}"


def normalize_version(value: Any) -> str:
    text = str(value or "").strip()
    return text or "unknown"


def add_component(components: dict[str, Component], component: Component) -> None:
    existing = components.get(component.bom_ref)
    if existing is None:
        components[component.bom_ref] = component
    else:
        existing.merge(component)


def components_from_spack_lock(path: Path) -> list[Component]:
    lock = load_json(path)
    result: list[Component] = []
    packages = lock.get("packages", {})
    if not isinstance(packages, dict):
        return result
    for node in packages.values():
        if not isinstance(node, dict):
            continue
        name = str(node.get("package") or "").strip()
        if not name:
            continue
        version = normalize_version(node.get("version"))
        spack_hash = str(node.get("spack_hash") or "").strip()
        hashes = sha256_values(node)
        properties = {"spack:hash": spack_hash} if spack_hash else {}
        result.append(
            Component(
                source="spack",
                name=name,
                version=version,
                purl=purl_for(name, version),
                license_id=license_id(node.get("license") or node.get("licenses")),
                hashes=hashes,
                properties=properties,
            )
        )
    return result


def components_from_build_graph(path: Path) -> list[Component]:
    graph = load_json(path)
    result: list[Component] = []
    nodes = graph.get("nodes", [])
    if not isinstance(nodes, list):
        return result
    for node in nodes:
        if not isinstance(node, dict):
            continue
        name = str(node.get("package") or node.get("name") or "").strip()
        if not name:
            continue
        version = normalize_version(node.get("version"))
        result.append(
            Component(
                source="bazel",
                name=name,
                version=version,
                purl=purl_for(name, version),
                license_id=license_id(node.get("license") or node.get("licenses")),
                hashes=sha256_values(node),
            )
        )
    return result


def parse_dpkg_list(path: Path) -> dict[str, str]:
    packages: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("ii "):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        packages[parts[1]] = parts[2]
    return packages


def split_apt_pin(value: str) -> tuple[str, str]:
    name, sep, version = value.partition("=")
    return name.strip(), version.strip() if sep else ""


def rootfs_line(lock: dict[str, Any], line: str) -> dict[str, Any]:
    lines = lock.get("lines", {})
    if isinstance(lines, dict):
        value = lines.get(line, {})
        if isinstance(value, dict):
            return value
    return {}


def packages_from_apt_source(source: dict[str, Any]) -> list[dict[str, Any]]:
    raw_packages = source.get("packages")
    if isinstance(raw_packages, (dict, list)):
        return apt_package_items(raw_packages)
    package_pin = source.get("package")
    if isinstance(package_pin, str) and package_pin.strip():
        return [{"package": package_pin, **{k: v for k, v in source.items() if k != "packages"}}]
    return []


def apt_package_items(raw_packages: Any) -> list[dict[str, Any]]:
    if isinstance(raw_packages, dict):
        items: list[dict[str, Any]] = []
        for raw_name, raw_value in raw_packages.items():
            if isinstance(raw_value, dict):
                item = dict(raw_value)
                item.setdefault("name", raw_name)
                items.append(item)
            else:
                items.append({"name": raw_name, "version": raw_value})
        return items
    if isinstance(raw_packages, list):
        items = []
        for raw_item in raw_packages:
            if isinstance(raw_item, dict):
                items.append(raw_item)
            elif isinstance(raw_item, str) and raw_item.strip():
                items.append({"package": raw_item})
        return items
    return []


def rootfs_lock_packages(lock: dict[str, Any], line: str) -> dict[str, dict[str, Any]]:
    packages: dict[str, dict[str, Any]] = {}

    # Compact fixture and resolved lock shapes:
    # {"lines": {"cu129": {"apt": {"packages": [...]}}}}
    # {"lines": {"cu129": {"apt": {"packages": {"cuda-cudart-12-9": "12.9.79-1"}}}}}
    line_doc = rootfs_line(lock, line)
    apt = line_doc.get("apt") if isinstance(line_doc, dict) else None
    if isinstance(apt, dict):
        for item in apt_package_items(apt.get("packages")):
            raw_name = str(item.get("name") or item.get("package") or "")
            name, pinned_version = split_apt_pin(raw_name)
            if not name:
                continue
            packages[name] = {
                **item,
                "name": name,
                "version": item.get("version") or pinned_version,
            }

    # Committed lock shape: {"lines": {"cu129": {"components": {"tensorrt": {"source": {"kind": "apt", ...}}}}}}
    components = line_doc.get("components") if isinstance(line_doc, dict) else None
    if isinstance(components, dict):
        for component in components.values():
            if not isinstance(component, dict):
                continue
            source = component.get("source")
            if not isinstance(source, dict) or source.get("kind") != "apt":
                continue
            for item in packages_from_apt_source(source):
                name, pinned_version = split_apt_pin(str(item.get("package") or item.get("name") or ""))
                if not name:
                    continue
                packages[name] = {
                    **item,
                    "name": name,
                    "version": item.get("version") or pinned_version,
                }
    return packages


def rootfs_lock_properties(lock_item: dict[str, Any]) -> dict[str, str]:
    properties: dict[str, str] = {}
    locked_version = lock_item.get("version")
    if locked_version is not None and str(locked_version).strip():
        properties["rootfs:locked_version"] = str(locked_version).strip()
    for key, value in sorted(lock_item.items()):
        if key in {"name", "package", "version", "license", "licenses", "sha256", "checksum", "digest"}:
            continue
        if isinstance(value, (str, int, float, bool)) and str(value).strip():
            properties[f"rootfs:lock:{key}"] = str(value)
    return properties


def components_from_rootfs_with_report(
    rootfs_lock_path: Path,
    dpkg_list_path: Path,
    line: str,
) -> tuple[list[Component], dict[str, Any]]:
    lock = load_json(rootfs_lock_path)
    observed = parse_dpkg_list(dpkg_list_path)
    locked = rootfs_lock_packages(lock, line)
    result: list[Component] = []
    missing_observed: list[dict[str, str]] = []
    for name, version in sorted(observed.items()):
        lock_item = locked.get(name, {})
        if not lock_item:
            missing_observed.append({"name": name, "version": version})
        result.append(
            Component(
                source="rootfs",
                name=name,
                version=normalize_version(version),
                purl=purl_for(name, version),
                license_id=license_id(lock_item.get("license") or lock_item.get("licenses")),
                hashes=sha256_values(lock_item),
                properties=rootfs_lock_properties(lock_item),
                component_type="operating-system",
            )
        )
    report = {
        "missing_observed_packages": missing_observed,
    }
    return result, report


def components_from_rootfs(rootfs_lock_path: Path, dpkg_list_path: Path, line: str) -> list[Component]:
    components, _report = components_from_rootfs_with_report(rootfs_lock_path, dpkg_list_path, line)
    return components


def policy_sets(path: Path) -> tuple[set[str], set[str], set[str]]:
    doc = load_json(path)
    allowed = {str(item) for item in doc.get("allowed", []) if str(item)}
    review = {str(item) for item in doc.get("review", []) if str(item)}
    denied = {str(item) for item in doc.get("denied", []) if str(item)}
    return allowed, review, denied


def evaluate_policy(
    components: list[Component],
    policy_path: Path,
    *,
    rootfs_report: dict[str, Any] | None = None,
    strict_rootfs_lock: bool = False,
) -> dict[str, Any]:
    allowed, review, denied = policy_sets(policy_path)
    denied_hits: list[dict[str, str]] = []
    review_hits: list[dict[str, str]] = []
    unknown_hits: list[dict[str, str]] = []
    for component in sorted(components, key=lambda item: item.bom_ref):
        item = {"component": component.bom_ref, "license": component.license_id}
        if component.license_id in denied:
            denied_hits.append(item)
        elif component.license_id in allowed:
            continue
        elif component.license_id in review:
            review_hits.append(item)
        else:
            unknown_hits.append(item)
    rootfs_lock = {
        "strict": strict_rootfs_lock,
        "missing_observed_packages": [],
    }
    if rootfs_report is not None:
        missing = rootfs_report.get("missing_observed_packages")
        if isinstance(missing, list):
            rootfs_lock["missing_observed_packages"] = missing
    return {
        "schema_version": 1,
        "verdict": "failed"
        if denied_hits or unknown_hits or (strict_rootfs_lock and rootfs_lock["missing_observed_packages"])
        else "passed",
        "denied": denied_hits,
        "unknown": unknown_hits,
        "review": review_hits,
        "rootfs_lock": rootfs_lock,
    }


def cyclone_dx(profile: str, line: str, components: list[Component]) -> dict[str, Any]:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "vaso-production-build",
                "version": f"{profile}-{line}",
            },
            "properties": [
                {"name": "vaso:profile", "value": profile},
                {"name": "vaso:line", "value": line},
            ],
        },
        "components": [component.to_cyclonedx() for component in sorted(components, key=lambda item: item.bom_ref)],
    }


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def build_components(args: argparse.Namespace) -> tuple[list[Component], dict[str, Any]]:
    components: dict[str, Component] = {}
    for component in components_from_spack_lock(args.spack_lock):
        add_component(components, component)
    for build_graph in args.build_graph:
        for component in components_from_build_graph(build_graph):
            add_component(components, component)
    rootfs_components, rootfs_report = components_from_rootfs_with_report(args.rootfs_lock, args.dpkg_list, args.line)
    for component in rootfs_components:
        add_component(components, component)
    return list(components.values()), rootfs_report


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--line", required=True)
    parser.add_argument("--spack-lock", type=Path, required=True)
    parser.add_argument("--build-graph", type=Path, required=True, action="append")
    parser.add_argument("--rootfs-lock", type=Path, required=True)
    parser.add_argument("--dpkg-list", type=Path, required=True)
    parser.add_argument("--license-policy", type=Path, required=True)
    parser.add_argument("--policy-out", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--strict-rootfs-lock",
        action="store_true",
        help="fail when an observed dpkg package is missing from the resolved rootfs lock",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    components, rootfs_report = build_components(args)
    write_json(args.out, cyclone_dx(args.profile, args.line, components))
    policy = evaluate_policy(
        components,
        args.license_policy,
        rootfs_report=rootfs_report,
        strict_rootfs_lock=args.strict_rootfs_lock,
    )
    write_json(args.policy_out, policy)
    missing_rootfs = policy["rootfs_lock"]["missing_observed_packages"]
    if args.strict_rootfs_lock and missing_rootfs:
        print(f"ROOTFS_LOCK_MISSING_PACKAGES count={len(missing_rootfs)}", file=sys.stderr)
    if policy["denied"] or policy["unknown"]:
        print(
            f"LICENSE_POLICY_FAILED denied={len(policy['denied'])} unknown={len(policy['unknown'])}",
            file=sys.stderr,
        )
    if policy["verdict"] != "passed":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
