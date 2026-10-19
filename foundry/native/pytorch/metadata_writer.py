#!/usr/bin/env python3
"""Write cheap PyTorch provider metadata for release repair."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: expected a JSON object")
    return data


def package_key(plan: dict[str, Any]) -> str:
    package = plan.get("package", {})
    name = package.get("name") if isinstance(package, dict) else None
    if not isinstance(name, str) or not name:
        raise SystemExit("PyTorch plan is missing package.name")
    return f"{plan.get('profile')}/{plan.get('line')}/{name}"


def plan_merkle_key(plan: dict[str, Any], keys: dict[str, Any]) -> str:
    packages = keys.get("packages")
    if not isinstance(packages, dict):
        raise SystemExit("plans/keys.json is missing packages")
    key = package_key(plan)
    item = packages.get(key)
    if not isinstance(item, dict) or not isinstance(item.get("merkle_key"), str):
        raise SystemExit(f"plans/keys.json is missing {key}")
    return item["merkle_key"]


def parse_name_path(items: list[str], flag: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"{flag} must be KEY=PATH, got {item!r}")
        key, value = item.split("=", 1)
        if not key or not value:
            raise SystemExit(f"{flag} must be KEY=PATH, got {item!r}")
        values[key] = value
    return values


def python_abi_from_plan(plan: dict[str, Any]) -> str | None:
    toolchain = plan.get("toolchain", {})
    python = toolchain.get("python") if isinstance(toolchain, dict) else None
    if not isinstance(python, str):
        return None
    match = re.search(r"python_([0-9]+)_native$", python)
    if match is None:
        return None
    return "cp" + match.group(1)


def python_abi_from_wheel(wheel: Path, requested: str, plan: dict[str, Any]) -> str:
    if requested and requested != "derived":
        return requested
    match = re.search(r"-(cp[0-9]{2,})-", wheel.name)
    if match is not None:
        return match.group(1)
    plan_abi = python_abi_from_plan(plan)
    if plan_abi is not None:
        return plan_abi
    raise SystemExit(f"could not derive Python ABI from wheel name {wheel.name!r}")


def write_outputs(args: argparse.Namespace) -> None:
    plan = read_json(args.plan)
    keys = read_json(args.keys)
    package = plan["package"]
    action_input_digest = plan_merkle_key(plan, keys)
    dep_prefixes = parse_name_path(args.dep_prefix, "--dep-prefix")
    args.build_plan_out.parent.mkdir(parents=True, exist_ok=True)
    args.provider_metadata_out.parent.mkdir(parents=True, exist_ok=True)
    args.result_marker_out.parent.mkdir(parents=True, exist_ok=True)

    args.build_plan_out.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    metadata = {
        "schema_version": 1,
        "package": package["name"],
        "version": package["version"],
        "action_input_digest": action_input_digest,
        "declared_cuda_architectures": list(plan.get("gpu_archs", [])),
        "torch_cuda_arch_list": torch_cuda_arch_list(plan),
        "python_abi": python_abi_from_wheel(args.wheel, args.python_abi, plan),
        "mechanism": "committed-plan",
        "wheel": str(args.wheel),
        "input_prefixes": dep_prefixes,
        "rootfs_manifest": str(args.rootfs_identity),
        "mode": "execute",
        "will_build": True,
        "token_present": True,
    }
    args.provider_metadata_out.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.result_marker_out.write_text(
        "Native PyTorch committed-plan wheel build completed.\n",
        encoding="utf-8",
    )


def torch_cuda_arch_list(plan: dict[str, Any]) -> str:
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--keys", type=Path, required=True)
    parser.add_argument("--rootfs-identity", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--build-plan-out", type=Path, required=True)
    parser.add_argument("--provider-metadata-out", type=Path, required=True)
    parser.add_argument("--result-marker-out", type=Path, required=True)
    parser.add_argument("--python-abi", default="derived")
    parser.add_argument("--dep-prefix", action="append", default=[])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    write_outputs(parse_args(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
