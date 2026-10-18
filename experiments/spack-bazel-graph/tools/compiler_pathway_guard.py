#!/usr/bin/env python3
"""Validate native workload build plans against the compiler pathway policy."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Iterable, Mapping


DEFAULT_PROFILE = "torch"
DEFAULT_CONSUMERS = ("torch", "torchvision", "torchaudio", "triton", "jaxlib")
DEFAULT_PLAN_PATHS = {
    "torch": "native/pytorch/plan.py",
    "torchvision": "native/torchvision/plan.py",
    "torchaudio": "native/torchaudio/plan.py",
    "triton": "native/triton/plan.py",
    "jaxlib": "native/jaxlib/plan.py",
}
SPECIAL_PREFIXES = {
    "cuda": "/usr/local/cuda",
    "cudnn": "/usr/local/cuda",
    "nccl": "/usr/local/cuda",
    "nvshmem": "/usr/local/cuda",
    "llvm": "/usr/lib/llvm-23",
}
PYTHON_ABI = "cp313"
TORCH_CUDA_ARCH_LIST = "10.0"
RULE_IDS = frozenset(
    (
        "allowed-host-compiler",
        "forbidden-env",
        "forbidden-regex",
        "forbidden-substring",
        "missing-plan-module",
        "plan-snapshot",
        "required-args",
        "required-env",
        "required-module-constant",
        "required-path-entry",
    )
)


@dataclass(frozen=True)
class PlanSnapshot:
    env: dict[str, str]
    args: list[str]
    constants: dict[str, object]


def load_policy(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def profile_name(policy: Mapping[str, object], profile: str | None = None) -> str | None:
    profiles = policy.get("profiles", {})
    if not isinstance(profiles, dict) or not profiles:
        return None
    selected = profile or str(policy.get("default_profile") or DEFAULT_PROFILE)
    if selected not in profiles:
        raise SystemExit(f"unknown compiler pathway profile {selected!r}")
    return selected


def policy_consumers(policy: Mapping[str, object], profile: str | None = None) -> dict[str, object]:
    profiles = policy.get("profiles", {})
    if isinstance(profiles, dict) and profiles:
        selected = profile_name(policy, profile)
        assert selected is not None
        profile_config = profiles.get(selected, {})
        if not isinstance(profile_config, dict):
            raise SystemExit(f"compiler pathway profile {selected!r} must be an object")
        consumers = profile_config.get("consumers", {})
    else:
        consumers = policy.get("consumers", {})
    if not isinstance(consumers, dict):
        raise SystemExit("compiler pathway policy must contain a consumers object")
    return consumers


def consumers_for_profile(policy: Mapping[str, object], profile: str | None = None) -> tuple[str, ...]:
    return tuple(str(consumer) for consumer in policy_consumers(policy, profile))


def canonical_consumer(policy: Mapping[str, object], name: str, profile: str | None = None) -> str:
    consumers = policy_consumers(policy, profile)
    if name in consumers:
        return name
    for consumer, config in consumers.items():
        if not isinstance(config, dict):
            continue
        aliases = config.get("aliases", [])
        if isinstance(aliases, list) and name in aliases:
            return str(consumer)
    raise SystemExit(f"unknown compiler pathway consumer {name!r}")


def fake_prefixes(module: object) -> dict[str, str]:
    keys = getattr(module, "REQUIRED_PREFIXES", None)
    if keys is None:
        keys = getattr(module, "REQUIRED_PREFIX_KEYS", ())
    return {str(key): SPECIAL_PREFIXES.get(str(key), f"/fake-prefix/{key}") for key in keys}


def load_plan_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"compiler_pathway_{name}", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load plan module for {name}: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def snapshot_plan(consumer: str, module: object) -> PlanSnapshot:
    prefixes = fake_prefixes(module)
    if consumer == "torch":
        env = module.build_env(prefixes, TORCH_CUDA_ARCH_LIST, "2.14.0", "1", PYTHON_ABI)
        args: list[str] = []
    elif consumer in {"torchvision", "torchaudio"}:
        env = module.build_env(prefixes, TORCH_CUDA_ARCH_LIST, "1", PYTHON_ABI)
        args = []
    elif consumer == "triton":
        env = module.build_env(prefixes, PYTHON_ABI)
        args = []
    elif consumer == "jaxlib":
        env = module.build_env(prefixes, PYTHON_ABI, "cu130")
        args = module.build_py_args(prefixes, "cu130")
    else:
        raise SystemExit(f"unsupported compiler pathway consumer {consumer!r}")
    constants = {
        "ONE_LLVM_COMMIT": getattr(module, "ONE_LLVM_COMMIT", None),
    }
    return PlanSnapshot(
        env={str(key): str(value) for key, value in env.items()},
        args=[str(arg) for arg in args],
        constants=constants,
    )


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _stringify_env(env: Mapping[str, str]) -> str:
    return "\n".join(f"{key}={value}" for key, value in sorted(env.items()))


def _stringify_args(args: Iterable[str]) -> str:
    return "\n".join(args)


def _check_required_env(consumer: str, env: Mapping[str, str], required: Mapping[str, object]) -> list[str]:
    errors: list[str] = []
    for key, expected in _as_dict(required.get("env")).items():
        actual = env.get(str(key))
        if actual != str(expected):
            errors.append(f"{consumer}: env {key}={actual!r}; expected {expected!r}")
    return errors


def _check_required_args(consumer: str, args: list[str], required: Mapping[str, object]) -> list[str]:
    errors: list[str] = []
    for expected in _as_list(required.get("args")):
        if str(expected) not in args:
            errors.append(f"{consumer}: missing required args entry {str(expected)!r}")
    return errors


def _check_required_path_entries(consumer: str, env: Mapping[str, str], required: Mapping[str, object]) -> list[str]:
    errors: list[str] = []
    path_entries = env.get("PATH", "").split(os.pathsep) if env.get("PATH") else []
    for expected in _as_list(required.get("path_entries")):
        if str(expected) not in path_entries:
            errors.append(f"{consumer}: PATH is missing required entry {str(expected)!r}")
    return errors


def _check_allowed_host_compiler(
    consumer: str,
    env: Mapping[str, str],
    config: Mapping[str, object],
    errors: list[str],
) -> None:
    allowed_cc = {str(item) for item in _as_list(config.get("allowed_host_cc_paths"))}
    allowed_cxx = {str(item) for item in _as_list(config.get("allowed_host_cxx_paths"))}
    if env.get("CC") and allowed_cc and env["CC"] not in allowed_cc:
        errors.append(
            f"{consumer}: env CC={env['CC']!r}; expected one of {sorted(allowed_cc)!r}"
        )
    if env.get("CXX") and allowed_cxx and env["CXX"] not in allowed_cxx:
        errors.append(
            f"{consumer}: env CXX={env['CXX']!r}; expected one of {sorted(allowed_cxx)!r}"
        )


def _check_required_constants(
    consumer: str,
    constants: Mapping[str, object],
    config: Mapping[str, object],
) -> list[str]:
    errors: list[str] = []
    for key, expected in _as_dict(config.get("required_module_constants")).items():
        actual = constants.get(str(key))
        if actual != expected:
            errors.append(f"{consumer}: module constant {key}={actual!r}; expected {expected!r}")
    return errors


def _check_forbidden_env(consumer: str, env: Mapping[str, str], forbidden: Mapping[str, object]) -> list[str]:
    errors: list[str] = []
    for key, needles in _as_dict(forbidden.get("env")).items():
        if key not in env:
            continue
        value = env[str(key)]
        for needle in _as_list(needles):
            if str(needle) in value:
                errors.append(f"{consumer}: forbidden env {key} contains {str(needle)!r} (value: {value})")
                break
    return errors


def _check_forbidden_substrings(
    consumer: str,
    env: Mapping[str, str],
    args: list[str],
    forbidden: Mapping[str, object],
) -> list[str]:
    errors: list[str] = []
    env_text = _stringify_env(env)
    args_text = _stringify_args(args)
    for needle in _as_list(forbidden.get("substrings")):
        needle_text = str(needle)
        if needle_text in args_text:
            errors.append(f"{consumer}: forbidden substring {needle_text!r} present in args")
        if needle_text in env_text:
            errors.append(f"{consumer}: forbidden substring {needle_text!r} present in env")
    return errors


def _check_forbidden_regexes(
    consumer: str,
    env: Mapping[str, str],
    args: list[str],
    forbidden: Mapping[str, object],
) -> list[str]:
    errors: list[str] = []
    env_text = _stringify_env(env)
    args_text = _stringify_args(args)
    for pattern in _as_list(forbidden.get("regexes")):
        regex = re.compile(str(pattern))
        if regex.search(args_text):
            errors.append(f"{consumer}: forbidden pattern {str(pattern)!r} matched args")
        if regex.search(env_text):
            errors.append(f"{consumer}: forbidden pattern {str(pattern)!r} matched env")
    return errors


def check_snapshot(consumer: str, config: Mapping[str, object], snapshot: PlanSnapshot) -> list[str]:
    required = _as_dict(config.get("required"))
    forbidden = _as_dict(config.get("forbidden"))
    errors: list[str] = []
    errors.extend(_check_required_env(consumer, snapshot.env, required))
    errors.extend(_check_required_args(consumer, snapshot.args, required))
    errors.extend(_check_required_path_entries(consumer, snapshot.env, required))
    _check_allowed_host_compiler(consumer, snapshot.env, config, errors)
    errors.extend(_check_required_constants(consumer, snapshot.constants, config))
    errors.extend(_check_forbidden_env(consumer, snapshot.env, forbidden))
    errors.extend(_check_forbidden_substrings(consumer, snapshot.env, snapshot.args, forbidden))
    errors.extend(_check_forbidden_regexes(consumer, snapshot.env, snapshot.args, forbidden))
    return errors


def check_modules(
    policy: Mapping[str, object],
    modules: Mapping[str, object],
    consumers: Iterable[str] | None = None,
    profile: str | None = None,
) -> list[str]:
    selected_consumers = consumers_for_profile(policy, profile) if consumers is None else tuple(consumers)
    policy_consumer_configs = policy_consumers(policy, profile)
    errors: list[str] = []
    for requested_consumer in selected_consumers:
        consumer = canonical_consumer(policy, requested_consumer, profile)
        if requested_consumer in modules:
            module = modules[requested_consumer]
        elif consumer in modules:
            module = modules[consumer]
        else:
            errors.append(f"{consumer}: missing plan module")
            continue
        try:
            snapshot = snapshot_plan(consumer, module)
        except Exception as exc:
            errors.append(f"{consumer}: could not build fake-prefix plan: {exc}")
            continue
        errors.extend(check_snapshot(consumer, _as_dict(policy_consumer_configs.get(consumer)), snapshot))
    return errors


def partition_expected_failures(errors: Iterable[str], expected_by_consumer: Mapping[str, Iterable[str]]) -> tuple[list[str], list[str]]:
    active: list[str] = []
    expected: list[str] = []
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for consumer, patterns in expected_by_consumer.items():
        for pattern in patterns:
            compiled.append((consumer, re.compile(str(pattern))))
    for error in errors:
        matched = False
        for consumer, regex in compiled:
            if not error.startswith(f"{consumer}:"):
                continue
            if regex.search(error):
                matched = True
                break
        if matched:
            expected.append(error)
        else:
            active.append(error)
    return active, expected


def parse_plan_arg(item: str) -> tuple[str, Path]:
    if "=" not in item:
        raise SystemExit(f"--plan must be CONSUMER=PATH, got {item!r}")
    consumer, path = item.split("=", 1)
    return consumer, Path(path)


def parse_expected_failure(item: str) -> tuple[str, str]:
    if ":" not in item:
        raise SystemExit(f"--expect-failure must be CONSUMER:REGEX, got {item!r}")
    consumer, pattern = item.split(":", 1)
    return consumer, pattern


def load_modules_from_paths(
    policy: Mapping[str, object],
    plan_paths: Mapping[str, Path],
    profile: str | None = None,
) -> dict[str, ModuleType]:
    modules: dict[str, ModuleType] = {}
    for name, path in plan_paths.items():
        consumer = canonical_consumer(policy, name, profile)
        modules[consumer] = load_plan_module(consumer, path)
    return modules


def default_policy_path() -> Path:
    return Path(__file__).with_name("compiler_pathways.json")


def default_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=default_policy_path())
    parser.add_argument("--profile", default=None, help="Compiler profile to check (default: policy default profile).")
    parser.add_argument("--repo-root", type=Path, default=default_repo_root())
    parser.add_argument("--consumer", action="append", default=[])
    parser.add_argument("--plan", action="append", default=[], metavar="CONSUMER=PATH")
    parser.add_argument(
        "--expect-failure",
        action="append",
        default=[],
        metavar="CONSUMER:REGEX",
        help="Treat matching live-plan errors as expected while the named external switch is pending.",
    )
    args = parser.parse_args(argv)

    policy = load_policy(args.policy)
    profile = profile_name(policy, args.profile)
    requested = tuple(args.consumer) if args.consumer else consumers_for_profile(policy, profile)
    plan_paths = {
        canonical_consumer(policy, name, profile): args.repo_root / DEFAULT_PLAN_PATHS[canonical_consumer(policy, name, profile)]
        for name in requested
    }
    for item in args.plan:
        name, path = parse_plan_arg(item)
        consumer = canonical_consumer(policy, name, profile)
        plan_paths[consumer] = path

    expected_by_consumer: dict[str, list[str]] = {}
    for item in args.expect_failure:
        consumer, pattern = parse_expected_failure(item)
        expected_by_consumer.setdefault(canonical_consumer(policy, consumer, profile), []).append(pattern)

    modules = load_modules_from_paths(policy, plan_paths, profile)
    errors = check_modules(policy, modules, consumers=requested, profile=profile)
    active, expected = partition_expected_failures(errors, expected_by_consumer)
    if expected:
        print("compiler pathway guard expected failure(s):\n  " + "\n  ".join(expected), file=sys.stderr)
    if active:
        print("compiler pathway guard failed:\n  " + "\n  ".join(active), file=sys.stderr)
        return 1
    checked = ", ".join(canonical_consumer(policy, name, profile) for name in requested)
    profile_text = f" profile={profile}" if profile else ""
    print(f"compiler pathway guard checked{profile_text}: {checked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
