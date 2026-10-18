#!/usr/bin/env python3
"""Plan the shared rootfs LLVM cache build from cuda_ecosystem.lock.json."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
from pathlib import Path
from typing import Any


BOOL_VALUES = {"ON", "OFF"}
FILEPATH_KEYS = {"CMAKE_C_COMPILER", "CMAKE_CXX_COMPILER"}
PATH_KEYS = {"CMAKE_INSTALL_PREFIX"}


def load_lock(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("lock must contain a JSON object")
    return data


def llvm_component(lock: dict[str, Any]) -> dict[str, Any]:
    components = lock.get("components")
    if not isinstance(components, dict):
        raise ValueError("lock must contain top-level components")
    llvm = components.get("llvm")
    if not isinstance(llvm, dict):
        raise ValueError("lock must contain components.llvm")
    return llvm


def patchset_sha8(llvm: dict[str, Any]) -> str:
    identity = {
        "commit": llvm["commit"],
        "source": llvm["source"],
        "patches": llvm.get("patches", []),
        "build": llvm["build"],
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:8]


def cache_key(llvm: dict[str, Any]) -> str:
    return f"{llvm['commit']}-{patchset_sha8(llvm)}"


def install_prefix(llvm: dict[str, Any]) -> str:
    return str(llvm["install_layout"]["prefix"])


def cmake_defines(llvm: dict[str, Any]) -> dict[str, str]:
    defines = {str(key): str(value) for key, value in llvm["build"]["cmake"].items()}
    compiler = llvm["build"]["compiler"]
    defines["CMAKE_INSTALL_PREFIX"] = install_prefix(llvm)
    defines["CMAKE_C_COMPILER"] = str(compiler["cc"])
    defines["CMAKE_CXX_COMPILER"] = str(compiler["cxx"])
    return dict(sorted(defines.items()))


def cmake_arg(key: str, value: str) -> str:
    if key in FILEPATH_KEYS:
        kind = "FILEPATH"
    elif key in PATH_KEYS:
        kind = "PATH"
    elif value in BOOL_VALUES:
        kind = "BOOL"
    else:
        kind = "STRING"
    return f"-D{key}:{kind}={value}"


def cmake_args(llvm: dict[str, Any]) -> list[str]:
    return [cmake_arg(key, value) for key, value in cmake_defines(llvm).items()]


def build_plan(lock: dict[str, Any], estate_root: Path) -> dict[str, Any]:
    llvm = llvm_component(lock)
    key = cache_key(llvm)
    return {
        "schema_version": 1,
        "component": "llvm",
        "version": llvm["version"],
        "commit": llvm["commit"],
        "source": llvm["source"],
        "patches": llvm.get("patches", []),
        "build_base_image": llvm["build"]["base_image"],
        "install_prefix": install_prefix(llvm),
        "cache_key": key,
        "cache_dir": str(estate_root / "rootfs-lines" / "_llvm" / key),
        "docker_image": f"vaso-llvm-{key}:latest",
        "max_jobs": int(llvm["build"]["max_jobs"]),
        "cmake_args": cmake_args(llvm),
    }


def emit_env(plan: dict[str, Any]) -> str:
    values = {
        "LLVM_CACHE_DIR": plan["cache_dir"],
        "LLVM_CACHE_KEY": plan["cache_key"],
        "LLVM_COMMIT": plan["commit"],
        "LLVM_DOCKER_IMAGE": plan["docker_image"],
        "LLVM_BUILD_BASE_REF": plan["build_base_image"]["reference"],
        "LLVM_INSTALL_PREFIX": plan["install_prefix"],
        "LLVM_MAX_JOBS": plan["max_jobs"],
        "LLVM_PATCHSET_SHA8": str(plan["cache_key"]).rsplit("-", 1)[1],
        "LLVM_SHA256": plan["source"]["sha256"],
        "LLVM_STRIP_PREFIX": plan["source"]["strip_prefix"],
        "LLVM_URL": plan["source"]["url"],
        "LLVM_VERSION": plan["version"],
    }
    return "".join(f"{key}={shlex.quote(str(value))}\n" for key, value in sorted(values.items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--estate-root", type=Path, required=True)
    parser.add_argument("--format", choices=("json", "env", "cmake-args"), default="json")
    args = parser.parse_args(argv)

    plan = build_plan(load_lock(args.lock), args.estate_root)
    if args.format == "env":
        print(emit_env(plan), end="")
    elif args.format == "cmake-args":
        for item in plan["cmake_args"]:
            print(item)
    else:
        print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
