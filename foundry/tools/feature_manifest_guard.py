#!/usr/bin/env python3
"""Validate claimed native features against release workload coverage."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Iterable, Mapping


PYTHON_ABI = "cp313"
TORCH_CUDA_ARCH_LIST = "10.0"
BOOLEAN_TRUE = frozenset(("1", "ON", "TRUE", "YES"))
BOOLEAN_FALSE = frozenset(("0", "OFF", "FALSE", "NO", ""))
FLAG_PREFIXES = ("USE_", "BUILD_", "TORCHVISION_USE_")
IGNORED_BUILD_FLAGS = frozenset(("BUILD_VERSION",))
VALID_STATUSES = frozenset(("verified", "off", "unverified-pending"))
FC_D1_OFF_FLAGS = frozenset(
    (
        "pytorch:USE_MAGMA",
        "pytorch:USE_MKLDNN",
        "pytorch:USE_MKL",
        "torchvision:TORCHVISION_USE_WEBP",
    )
)
PROFILE_CONSUMERS = {
    "torch": ("pytorch", "torchvision", "torchaudio", "triton"),
    "jax": ("jaxlib",),
}
DEFAULT_PLAN_PATHS = {
    "pytorch": "native/pytorch/plan.py",
    "torchvision": "native/torchvision/plan.py",
    "torchaudio": "native/torchaudio/plan.py",
    "triton": "native/triton/plan.py",
    "jaxlib": "native/jaxlib/plan.py",
}
CC_TOOLCHAIN_FIXTURES = {
    "gcc": {
        "cc": "/usr/bin/gcc",
        "cxx": "/usr/bin/g++",
        "toolchain_identifier": "vaso-rootfs-gcc-13.3",
    },
    "clang": {
        "cc": "/usr/lib/llvm-23/bin/clang",
        "cxx": "/usr/lib/llvm-23/bin/clang++",
        "toolchain_identifier": "vaso-rootfs-clang-23",
    },
}
SPECIAL_PREFIXES = {
    "cuda": "/usr/local/cuda",
    "cudnn": "/usr/local/cuda",
    "nccl": "/usr/local/cuda",
    "nvshmem": "/usr/local/cuda",
    "llvm": "/usr/lib/llvm-23",
}
JAXLIB_FEATURE_KEYS = {
    "LOCAL_CUDA_PATH": "jaxlib:cuda",
    "LOCAL_CUDNN_PATH": "jaxlib:cudnn",
    "LOCAL_NCCL_PATH": "jaxlib:nccl",
    "LOCAL_NVSHMEM_PATH": "jaxlib:nvshmem",
}
RULE_IDS = frozenset(
    (
        "dangling-verifier-id",
        "enabled-flag-manifest-entry",
        "fc-d1-off-flag",
        "jaxlib-feature-manifest-entry",
        "manifest-schema",
        "release-selection",
        "unverified-pending",
    )
)


@dataclass(frozen=True)
class GuardError:
    rule_id: str
    message: str


@dataclass(frozen=True)
class PlanSnapshot:
    env: dict[str, str]
    args: list[str]


def source_root_from(anchor: Path) -> Path:
    return anchor.resolve().parent.parent


def load_json(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise SystemExit(f"{path} must contain a JSON object")
    return value


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _string_items(value: object) -> list[str]:
    return [str(item) for item in _as_list(value)]


def _is_enabled(value: object) -> bool:
    return str(value).strip().upper() in BOOLEAN_TRUE


def _is_boolean_like(value: object) -> bool:
    return str(value).strip().upper() in BOOLEAN_TRUE | BOOLEAN_FALSE


def _is_feature_flag(key: str, value: object) -> bool:
    return (
        key not in IGNORED_BUILD_FLAGS
        and key.startswith(FLAG_PREFIXES)
        and _is_boolean_like(value)
    )


def fake_prefixes(module: object) -> dict[str, str]:
    keys = getattr(module, "REQUIRED_PREFIXES", None)
    if keys is None:
        keys = getattr(module, "REQUIRED_PREFIX_KEYS", ())
    return {str(key): SPECIAL_PREFIXES.get(str(key), f"/fake-prefix/{key}") for key in keys}


def native_toolchain_fixture(consumer: str) -> dict[str, object]:
    cc_family = "clang" if consumer == "jaxlib" else "gcc"
    return {
        "schema_version": 1,
        "cc": dict(CC_TOOLCHAIN_FIXTURES[cc_family]),
        "cuda": {
            "arch_list": "10.0+PTX",
            "home": "/usr/local/cuda",
            "nvcc": "/usr/local/cuda/bin/nvcc",
            "prefix_file": "@cuda_native//:prefix_path.txt",
            "providers": [
                {
                    "label": "@cuda_native//:lib",
                    "package": "cuda",
                },
            ],
        },
        "determinism": {
            "env": {},
            "prefix_map_flags": "",
            "prefix_map_roots": "",
        },
        "python": {
            "prefix_file": "@python_313_native//:prefix_path.txt",
            "version": "3.13.13",
        },
    }


def load_plan_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"feature_manifest_{name}", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load plan module for {name}: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def snapshot_plan(consumer: str, module: object) -> PlanSnapshot:
    prefixes = fake_prefixes(module)
    native_toolchain = native_toolchain_fixture(consumer)
    if consumer == "pytorch":
        env = module.build_env(prefixes, TORCH_CUDA_ARCH_LIST, "2.14.0", "1", PYTHON_ABI, native_toolchain)
        args: list[str] = []
    elif consumer in {"torchvision", "torchaudio"}:
        env = module.build_env(prefixes, TORCH_CUDA_ARCH_LIST, "1", PYTHON_ABI, native_toolchain)
        args = []
    elif consumer == "triton":
        env = module.build_env(prefixes, PYTHON_ABI, native_toolchain)
        args = []
    elif consumer == "jaxlib":
        env = module.build_env(prefixes, PYTHON_ABI, "cu130", native_toolchain)
        args = module.build_py_args(prefixes, "cu130", native_toolchain)
    else:
        raise SystemExit(f"unsupported feature manifest consumer {consumer!r}")
    return PlanSnapshot(
        env={str(key): str(value) for key, value in env.items()},
        args=[str(arg) for arg in args],
    )


def enabled_flags(consumer: str, snapshot: PlanSnapshot) -> set[str]:
    return {
        f"{consumer}:{key}"
        for key, value in snapshot.env.items()
        if _is_feature_flag(key, value) and _is_enabled(value)
    }


def jaxlib_features(snapshot: PlanSnapshot) -> set[str]:
    features: set[str] = set()
    for key, feature in JAXLIB_FEATURE_KEYS.items():
        if key in snapshot.env or any(arg.endswith(f"{key}={snapshot.env.get(key, '')}") for arg in snapshot.args):
            features.add(feature)
    for arg in snapshot.args:
        if arg.startswith("--wheels="):
            for wheel in arg.split("=", 1)[1].split(","):
                if wheel:
                    features.add(f"jaxlib:wheel:{wheel}")
        elif arg.startswith("--cuda_compute_capabilities="):
            capabilities = [item for item in arg.split("=", 1)[1].split(",") if item]
            for capability in capabilities:
                features.add(f"jaxlib:{capability}")
        elif arg == "--build_cuda_with_clang":
            features.add("jaxlib:clang_cuda")
    return features


def manifest_features(manifest: Mapping[str, object], profile: str) -> dict[str, object]:
    profiles = _as_dict(manifest.get("profiles"))
    profile_doc = _as_dict(profiles.get(profile))
    return _as_dict(profile_doc.get("features"))


def _feature_label(profile: str, feature_name: str) -> str:
    return f"{profile}:{feature_name}"


def _feature_flag_index(features: Mapping[str, object]) -> dict[str, str]:
    index: dict[str, str] = {}
    for name, raw_entry in features.items():
        entry = _as_dict(raw_entry)
        for flag in _string_items(entry.get("flags")):
            index[flag] = str(name)
    return index


def _jaxlib_feature_index(features: Mapping[str, object]) -> dict[str, str]:
    index: dict[str, str] = {}
    for name, raw_entry in features.items():
        entry = _as_dict(raw_entry)
        for feature in _string_items(entry.get("jaxlib_features")):
            index[feature] = str(name)
    return index


def _validate_entry_schema(profile: str, feature_name: str, entry: Mapping[str, object]) -> list[GuardError]:
    errors: list[GuardError] = []
    label = _feature_label(profile, feature_name)
    status = entry.get("status")
    if status not in VALID_STATUSES:
        errors.append(
            GuardError(
                "manifest-schema",
                f"{label}: status must be one of {sorted(VALID_STATUSES)}, got {status!r}",
            )
        )
    for list_key in ("flags", "jaxlib_features", "verifier_ids"):
        if list_key in entry and not isinstance(entry[list_key], list):
            errors.append(GuardError("manifest-schema", f"{label}: {list_key} must be a list"))
    for list_key in ("flags", "jaxlib_features", "verifier_ids"):
        if any(not isinstance(item, str) or not item for item in _as_list(entry.get(list_key))):
            errors.append(GuardError("manifest-schema", f"{label}: {list_key} entries must be non-empty strings"))
    if status == "verified" and not _string_items(entry.get("verifier_ids")):
        errors.append(GuardError("manifest-schema", f"{label}: verified entries must name verifier_ids"))
    if status in {"off", "unverified-pending"} and not str(entry.get("reason", "")).strip():
        errors.append(GuardError("manifest-schema", f"{label}: {status} entries must include a reason"))
    return errors


def validate_manifest(
    manifest: Mapping[str, object],
    *,
    enabled_flags_by_profile: Mapping[str, set[str]],
    jaxlib_features_by_profile: Mapping[str, set[str]],
    workload_ids: set[str],
    release_workload_ids_by_profile: Mapping[str, set[str]],
) -> list[GuardError]:
    errors: list[GuardError] = []
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("profiles"), dict):
        errors.append(GuardError("manifest-schema", "manifest requires schema_version=1 and profiles object"))
        return errors

    profiles = set(_as_dict(manifest.get("profiles"))) | set(enabled_flags_by_profile) | set(jaxlib_features_by_profile)
    for profile in sorted(profiles):
        features = manifest_features(manifest, profile)
        if not features:
            errors.append(GuardError("manifest-schema", f"{profile}: missing features object"))
            continue
        flag_index = _feature_flag_index(features)
        jax_index = _jaxlib_feature_index(features)

        for feature_name, raw_entry in features.items():
            entry = _as_dict(raw_entry)
            if not isinstance(raw_entry, dict):
                errors.append(GuardError("manifest-schema", f"{profile}:{feature_name}: entry must be an object"))
                continue
            errors.extend(_validate_entry_schema(profile, str(feature_name), entry))
            if entry.get("status") == "unverified-pending":
                errors.append(
                    GuardError(
                        "unverified-pending",
                        f"{profile}:{feature_name}: feature claims must be verified or off, not unverified-pending",
                    )
                )
            for verifier_id in _string_items(entry.get("verifier_ids")):
                if verifier_id not in workload_ids:
                    errors.append(
                        GuardError(
                            "dangling-verifier-id",
                            f"{profile}:{feature_name}: verifier id {verifier_id} does not exist in workloads",
                        )
                    )
            if entry.get("status") == "verified":
                release_ids = release_workload_ids_by_profile.get(profile, set())
                for verifier_id in _string_items(entry.get("verifier_ids")):
                    if verifier_id not in release_ids:
                        errors.append(
                            GuardError(
                                "release-selection",
                                f"{profile}:{feature_name}: verifier id {verifier_id} is not selected by release workloads",
                            )
                        )

        for flag in sorted(enabled_flags_by_profile.get(profile, set())):
            feature_name = flag_index.get(flag)
            if feature_name is None:
                errors.append(
                    GuardError(
                        "enabled-flag-manifest-entry",
                        f"{profile}: enabled build flag {flag} has no manifest feature entry",
                    )
                )
                continue
            entry = _as_dict(features.get(feature_name))
            if entry.get("status") == "off":
                errors.append(
                    GuardError(
                        "enabled-flag-manifest-entry",
                        f"{profile}:{feature_name}: enabled build flag {flag} is recorded as off",
                    )
                )

        for feature in sorted(jaxlib_features_by_profile.get(profile, set())):
            feature_name = jax_index.get(feature)
            if feature_name is None:
                errors.append(
                    GuardError(
                        "jaxlib-feature-manifest-entry",
                        f"{profile}: jaxlib feature {feature} has no manifest feature entry",
                    )
                )
                continue
            entry = _as_dict(features.get(feature_name))
            if entry.get("status") == "off":
                errors.append(
                    GuardError(
                        "jaxlib-feature-manifest-entry",
                        f"{profile}:{feature_name}: jaxlib feature {feature} is recorded as off",
                    )
                )

    for flag in sorted(FC_D1_OFF_FLAGS):
        found = False
        for profile, raw_profile in _as_dict(manifest.get("profiles")).items():
            for feature_name, raw_entry in _as_dict(_as_dict(raw_profile).get("features")).items():
                entry = _as_dict(raw_entry)
                if flag not in _string_items(entry.get("flags")):
                    continue
                found = True
                if entry.get("status") != "off" or "FC-D1" not in str(entry.get("reason", "")):
                    errors.append(
                        GuardError(
                            "fc-d1-off-flag",
                            f"{profile}:{feature_name}: {flag} must be status=off with an FC-D1 reason",
                        )
                    )
        if not found:
            errors.append(
                GuardError(
                    "fc-d1-off-flag",
                    f"{flag} must be recorded as status=off with an FC-D1 reason",
                )
            )

    return errors


def workload_ids_from_module(path: Path) -> set[str]:
    module = load_plan_module("run_workloads", path)
    workloads = getattr(module, "WORKLOADS", None)
    if not isinstance(workloads, dict):
        raise SystemExit(f"{path}: missing WORKLOADS dict")
    return {str(key) for key in workloads}


def _array_items(script_text: str, name: str) -> list[str]:
    match = re.search(rf"(?ms)^{re.escape(name)}=\(\n(?P<body>.*?)^\)", script_text)
    if not match:
        return []
    items: list[str] = []
    for raw_line in match.group("body").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        items.extend(shlex.split(line))
    return items


def release_workload_ids(path: Path) -> dict[str, set[str]]:
    text = path.read_text(encoding="utf-8")
    return {
        "torch": set(_array_items(text, "torch_workloads")),
        "jax": set(_array_items(text, "jax_workloads")) | set(_array_items(text, "jax_model_workloads")),
    }


def parse_plan_args(items: Iterable[str]) -> dict[str, Path]:
    parsed: dict[str, Path] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--plan must be CONSUMER=PATH, got {item!r}")
        consumer, path = item.split("=", 1)
        if not consumer or not path:
            raise SystemExit(f"--plan must be CONSUMER=PATH, got {item!r}")
        parsed[consumer] = Path(path)
    return parsed


def default_plan_paths(root: Path) -> dict[str, Path]:
    return {consumer: root / rel for consumer, rel in DEFAULT_PLAN_PATHS.items()}


def collect_claims(
    plan_paths: Mapping[str, Path],
    profiles: Iterable[str],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    enabled_by_profile: dict[str, set[str]] = {}
    jax_by_profile: dict[str, set[str]] = {}
    modules: dict[str, ModuleType] = {
        consumer: load_plan_module(consumer, path)
        for consumer, path in plan_paths.items()
    }
    snapshots = {
        consumer: snapshot_plan(consumer, module)
        for consumer, module in modules.items()
    }
    for profile in profiles:
        profile_enabled: set[str] = set()
        profile_jax: set[str] = set()
        for consumer in PROFILE_CONSUMERS.get(profile, ()):
            snapshot = snapshots.get(consumer)
            if snapshot is None:
                continue
            profile_enabled.update(enabled_flags(consumer, snapshot))
            if consumer == "jaxlib":
                profile_jax.update(jaxlib_features(snapshot))
        enabled_by_profile[profile] = profile_enabled
        jax_by_profile[profile] = profile_jax
    return enabled_by_profile, jax_by_profile


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    root = source_root_from(Path(__file__))
    parser.add_argument("--manifest", type=Path, default=root / "native" / "feature_manifest.json")
    parser.add_argument("--workloads", type=Path, default=root / "workloads" / "run_workloads.py")
    parser.add_argument("--release-script", type=Path, default=root / "scripts" / "agents" / "triumvirate-gate-insula.sh")
    parser.add_argument("--profile", action="append", default=[])
    parser.add_argument("--plan", action="append", default=[], metavar="CONSUMER=PATH")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    manifest = load_json(args.manifest)
    profiles = args.profile or sorted(_as_dict(manifest.get("profiles")))
    root = source_root_from(Path(__file__))
    plan_paths = default_plan_paths(root)
    plan_paths.update(parse_plan_args(args.plan))
    enabled_by_profile, jax_by_profile = collect_claims(plan_paths, profiles)
    errors = validate_manifest(
        manifest,
        enabled_flags_by_profile=enabled_by_profile,
        jaxlib_features_by_profile=jax_by_profile,
        workload_ids=workload_ids_from_module(args.workloads),
        release_workload_ids_by_profile=release_workload_ids(args.release_script),
    )
    if errors:
        for error in errors:
            print(f"{error.rule_id}: {error.message}", file=sys.stderr)
        return 1
    print(f"feature manifest guard passed for profiles: {', '.join(profiles)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
