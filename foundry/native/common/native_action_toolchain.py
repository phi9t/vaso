#!/usr/bin/env python3
"""Runtime helpers for Bazel-owned native action toolchain descriptors."""

from __future__ import annotations

import json
import os
import shlex
from functools import lru_cache
from pathlib import Path
from typing import Any

_CUDA_FAMILY_PROVIDER_LABELS = {
    "cuda": "@cuda_native//:lib",
}


def read_native_toolchain(path: str | Path | None, *, consumer: str | None = None) -> dict[str, Any]:
    """Read and minimally validate a native action toolchain descriptor."""
    if path is None or str(path) == "":
        raise SystemExit("native action toolchain descriptor is required; pass --native-toolchain")
    descriptor_path = Path(path)
    if not descriptor_path.is_file():
        raise SystemExit(f"native action toolchain descriptor is missing: {descriptor_path}")
    try:
        toolchain = json.loads(descriptor_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"native action toolchain descriptor is invalid JSON: {descriptor_path}: {exc}") from exc
    if not isinstance(toolchain, dict):
        raise SystemExit(f"native action toolchain descriptor must be a JSON object: {descriptor_path}")
    if toolchain.get("schema_version") != 1:
        raise SystemExit(
            "native action toolchain descriptor schema_version must be 1: "
            f"{descriptor_path}"
        )
    validate_native_toolchain(toolchain, consumer=consumer)
    return toolchain


def _repo_file(relative: str) -> Path:
    candidates = []
    for base in (Path(__file__).resolve().parents[2], Path.cwd().resolve()):
        candidates.append(base / relative)
    if runfiles := os.environ.get("RUNFILES_DIR"):
        candidates.append(Path(runfiles) / "_main" / relative)
        candidates.append(Path(runfiles) / relative)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise SystemExit(f"required repo data file is missing from runfiles: {relative}")


@lru_cache(maxsize=1)
def _registered_cc_toolchains() -> dict[str, dict[str, str]]:
    policy = json.loads(_repo_file("toolchains/native_action_cc_toolchains.json").read_text(encoding="utf-8"))
    if policy.get("schema_version") != 1:
        raise SystemExit("native action C++ toolchain map schema_version must be 1")
    raw_toolchains = policy.get("toolchains")
    if not isinstance(raw_toolchains, dict):
        raise SystemExit("native action C++ toolchain map must contain a toolchains object")
    toolchains: dict[str, dict[str, str]] = {}
    for identifier, item in raw_toolchains.items():
        fields = _require_object(item, field=f"toolchains.{identifier}")
        family = _require_string(fields, "family", field=f"toolchains.{identifier}")
        if family not in {"gcc", "clang"}:
            raise SystemExit(
                f"native action C++ toolchain map has unsupported family for {identifier}: {family}"
            )
        toolchains[identifier] = {
            "cc": _require_absolute(fields, "cc", field=f"toolchains.{identifier}"),
            "cxx": _require_absolute(fields, "cxx", field=f"toolchains.{identifier}"),
            "family": family,
        }
    return toolchains


@lru_cache(maxsize=1)
def _consumer_compiler_families() -> dict[str, str]:
    policy = json.loads(_repo_file("toolchains/native_action_consumer_families.json").read_text(encoding="utf-8"))
    if policy.get("schema_version") != 1:
        raise SystemExit("native action consumer family map schema_version must be 1")
    raw_families = policy.get("families")
    if not isinstance(raw_families, dict):
        raise SystemExit("native action consumer family map must contain a families object")
    families: dict[str, str] = {}
    for consumer, family in raw_families.items():
        if family not in {"gcc", "clang"}:
            raise SystemExit(
                f"native action consumer family map has unsupported family for {consumer}: {family}"
            )
        families[str(consumer)] = str(family)
    return families


def _require_object(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SystemExit(f"native action toolchain {field} must be a JSON object")
    return value


def _require_string(mapping: dict[str, Any], key: str, *, field: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise SystemExit(f"native action toolchain {field}.{key} is required")
    return value


def _require_absolute(mapping: dict[str, Any], key: str, *, field: str) -> str:
    value = _require_string(mapping, key, field=field)
    if not value.startswith("/"):
        raise SystemExit(f"native action toolchain {field}.{key} must be an absolute rootfs path")
    return value


def _validate_cc_toolchain(toolchain: dict[str, Any], *, consumer: str | None = None) -> None:
    cc = _require_object(toolchain.get("cc"), field="cc")
    identifier = _require_string(cc, "toolchain_identifier", field="cc")
    expected = _registered_cc_toolchains().get(identifier)
    if expected is None:
        raise SystemExit(f"native action toolchain cc.toolchain_identifier is unknown: {identifier}")
    for key, expected_path in expected.items():
        if key == "family":
            continue
        actual = _require_absolute(cc, key, field="cc")
        if actual != expected_path:
            raise SystemExit(
                f"native action toolchain cc.{key}={actual!r} does not match "
                f"{identifier} expected {expected_path!r}"
            )
    if consumer is not None:
        required_family = _consumer_compiler_families().get(consumer)
        if required_family is None:
            actual_cc = _require_absolute(cc, "cc", field="cc")
            actual_cxx = _require_absolute(cc, "cxx", field="cc")
            raise SystemExit(
                f"native action toolchain consumer is unknown: {consumer}; "
                f"cc={actual_cc}; cxx={actual_cxx}; toolchain_identifier={identifier}"
            )
        actual_family = expected["family"]
        if actual_family != required_family:
            raise SystemExit(
                f"{consumer} requires compiler family {required_family}; "
                f"{identifier} provides {actual_family}"
            )


def _validate_cuda_providers(providers: object) -> None:
    if providers is None:
        raise SystemExit("native action toolchain cuda.providers is required")
    if not isinstance(providers, list):
        raise SystemExit("native action toolchain cuda.providers must be a list")
    seen: dict[str, str] = {}
    for item in providers:
        provider = _require_object(item, field="cuda.providers[]")
        package = _require_string(provider, "package", field="cuda.providers[]")
        label = _require_string(provider, "label", field="cuda.providers[]")
        if package in seen:
            raise SystemExit(
                f"CUDA-family package {package} appears more than once: "
                f"{seen[package]}, {label}"
            )
        expected_label = _CUDA_FAMILY_PROVIDER_LABELS.get(package)
        if expected_label is not None and label != expected_label:
            raise SystemExit(f"CUDA-family package {package} must use provider {expected_label}")
        seen[package] = label


def _validate_cuda_toolchain(toolchain: dict[str, Any]) -> None:
    cuda = _require_object(toolchain.get("cuda"), field="cuda")
    home = _require_absolute(cuda, "home", field="cuda")
    nvcc = _require_absolute(cuda, "nvcc", field="cuda")
    expected_nvcc = str(Path(home) / "bin" / "nvcc")
    if nvcc != expected_nvcc:
        raise SystemExit(
            f"native action toolchain cuda.nvcc={nvcc!r} does not match "
            f"cuda.home expected {expected_nvcc!r}"
        )
    _require_string(cuda, "prefix_file", field="cuda")
    _validate_cuda_providers(cuda.get("providers"))


def validate_native_toolchain(toolchain: dict[str, Any] | None, *, consumer: str | None = None) -> None:
    if not toolchain:
        raise SystemExit("native action toolchain descriptor is required")
    _validate_cc_toolchain(toolchain, consumer=consumer)
    _validate_cuda_toolchain(toolchain)


def _normalized_path_text(path: str | Path) -> str:
    value = str(path)
    return value.rstrip("/") or "/"


def _same_cuda_prefix(descriptor_home: str, selected: str) -> bool:
    """True when both name one directory.

    The descriptor holds the fixed-mount absolute path (/vaso-bazel-ob/external/...),
    while native actions receive dependency prefixes execroot-relative (external/...)
    so no output-base path reaches the action key. Compare by filesystem identity when
    both exist, else by the relative tail, never by raw string alone.
    """
    if descriptor_home == selected:
        return True
    descriptor_path = Path(descriptor_home)
    selected_path = Path(selected)
    if descriptor_path.exists() and selected_path.exists():
        return os.path.realpath(descriptor_path) == os.path.realpath(selected_path)
    if not selected_path.is_absolute():
        return descriptor_home.endswith("/" + selected.lstrip("./"))
    return False


def validate_native_toolchain_cuda_prefix(
    toolchain: dict[str, Any] | None,
    selected_cuda_prefix: str | Path | None,
) -> None:
    if selected_cuda_prefix is None or str(selected_cuda_prefix) == "":
        raise SystemExit("selected cuda prefix is required")
    validate_native_toolchain(toolchain)
    assert toolchain is not None
    cuda = _require_object(toolchain.get("cuda"), field="cuda")
    descriptor_home = _normalized_path_text(_require_absolute(cuda, "home", field="cuda"))
    selected = _normalized_path_text(selected_cuda_prefix)
    if not _same_cuda_prefix(descriptor_home, selected):
        raise SystemExit(
            f"native action toolchain cuda.home={descriptor_home!r} does not match "
            f"selected cuda prefix {selected!r}"
        )


def _string_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for key, item in value.items():
        if item is None:
            continue
        result[str(key)] = str(item)
    return result


def native_toolchain_environment(
    toolchain: dict[str, Any] | None,
    *,
    compilers: bool = True,
    cuda_home: bool = True,
    cuda_toolkit_root_dir: bool = False,
    determinism: bool = True,
    nvcc_flags: bool = True,
    consumer: str | None = None,
) -> dict[str, str]:
    """Return process environment owned by the selected native toolchain."""
    validate_native_toolchain(toolchain, consumer=consumer)
    assert toolchain is not None
    env = (
        _string_map(toolchain.get("determinism", {}).get("env") if isinstance(toolchain.get("determinism"), dict) else {})
        if determinism
        else {}
    )
    if not nvcc_flags:
        env.pop("NVCC_FLAGS", None)
    cc = toolchain.get("cc", {})
    if compilers and isinstance(cc, dict):
        env["CC"] = str(cc["cc"])
        env["CXX"] = str(cc["cxx"])
    cuda = toolchain.get("cuda", {})
    if cuda_home and isinstance(cuda, dict):
        home = str(cuda["home"])
        env["CUDA_HOME"] = home
        env["CUDA_PATH"] = home
        if cuda_toolkit_root_dir:
            env["CUDA_TOOLKIT_ROOT_DIR"] = home
    return env


def native_toolchain_cuda_home(toolchain: dict[str, Any] | None, *, consumer: str | None = None) -> str | None:
    validate_native_toolchain(toolchain, consumer=consumer)
    assert toolchain is not None
    cuda = toolchain.get("cuda", {})
    return str(cuda["home"])


def deterministic_cmake_flag_args(toolchain: dict[str, Any] | None) -> list[str]:
    env = native_toolchain_environment(toolchain, compilers=False, cuda_home=False)
    args: list[str] = []
    if cflags := env.get("CFLAGS", "").strip():
        args.append(f"-DCMAKE_C_FLAGS={cflags}")
    if cxxflags := env.get("CXXFLAGS", "").strip():
        args.append(f"-DCMAKE_CXX_FLAGS={cxxflags}")
    if nvcc_flags := env.get("NVCC_FLAGS", "").strip():
        args.append(f"-DCUDA_NVCC_FLAGS={';'.join(shlex.split(nvcc_flags))}")
    return args


def append_unique_cmake_args(existing: str, args: list[str]) -> str:
    existing = existing.strip()
    if not args:
        return existing
    parts: list[str] = [existing] if existing else []
    seen = set(shlex.split(existing)) if existing else set()
    for arg in args:
        if arg and arg not in seen:
            parts.append(arg)
            seen.add(arg)
    if existing:
        return " ".join([parts[0]] + [shlex.quote(part) for part in parts[1:]])
    return " ".join(shlex.quote(part) for part in parts)


def deterministic_prefix_map_fragments(toolchain: dict[str, Any] | None) -> tuple[str, ...]:
    env = native_toolchain_environment(toolchain, compilers=False, cuda_home=False)
    fragments: list[str] = []
    for key in ("CFLAGS", "CXXFLAGS"):
        value = env.get(key, "")
        try:
            tokens = shlex.split(value)
        except ValueError:
            tokens = value.split()
        fragments.extend(token for token in tokens if "prefix-map=" in token)
    return tuple(dict.fromkeys(fragments))
