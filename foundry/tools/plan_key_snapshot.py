#!/usr/bin/env python3
"""Compute Merkle keys for committed native build plans."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

NATIVE_COMMON = Path(__file__).resolve().parents[1] / "native" / "common"
if str(NATIVE_COMMON) not in sys.path:
    sys.path.insert(0, str(NATIVE_COMMON))

from plan_schema import load_schema, stable_json, validate_plan


ACTION_ENV = {
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
    "PYTHONHASHSEED": "0",
    "VASO_ACTION_RESERVED_CPUS": "4",
}

LEAF_ACTION_ARGV = [
    "python3",
    "native/common/plan_executor.py",
    "--plan",
    "<plan>",
    "--schema",
    "plans/schema/v1.json",
    "--source-archive",
    "main=<source-archive>",
    "--rootfs-identity",
    "<rootfs-identity>",
    "--prefix-out",
    "<prefix>",
]
LEAF_ACTION_WRAPPERS = {
    "bzip2": "native/bzip2/bzip2_action.bzl",
    "xz": "native/xz/xz_action.bzl",
    "zstd": "native/zstd/zstd_action.bzl",
}

TRITON_ACTION_ARGV = [
    "python3",
    "native/common/plan_executor.py",
    "--plan",
    "<plan>",
    "--schema",
    "plans/schema/v1.json",
    "--source-archive",
    "main=<source-archive>",
    "--rootfs-identity",
    "<rootfs-identity>",
    "--prefix-out",
    "<prefix>",
    "--wheel-out",
    "<wheel>",
    "--patch",
    "<patch>",
    "--dep-prefix",
    "<dependency-prefixes>",
    "--input",
    "<declared-inputs>",
]

TORCHVISION_ACTION_ARGV = [
    "python3",
    "native/common/plan_executor.py",
    "--plan",
    "<plan>",
    "--schema",
    "plans/schema/v1.json",
    "--source-archive",
    "main=<source-archive>",
    "--rootfs-identity",
    "<rootfs-identity>",
    "--prefix-out",
    "<prefix>",
    "--wheel-out",
    "<wheel>",
    "--dep-prefix",
    "<dependency-prefixes>",
    "--input",
    "<declared-inputs>",
]

TORCHAUDIO_ACTION_ARGV = [
    "python3",
    "native/common/plan_executor.py",
    "--plan",
    "<plan>",
    "--schema",
    "plans/schema/v1.json",
    "--source-archive",
    "main=<source-archive>",
    "--rootfs-identity",
    "<rootfs-identity>",
    "--prefix-out",
    "<prefix>",
    "--wheel-out",
    "<wheel>",
    "--dep-prefix",
    "<dependency-prefixes>",
    "--input",
    "<declared-inputs>",
]

PYTORCH_ACTION_ARGV = [
    "python3",
    "native/pytorch/runtime_jobs_executor.py",
    "--executor",
    "native/common/plan_executor.py",
    "--runtime-jobs-file",
    "<runtime-jobs-file>",
    "--zstd-prefix",
    "<zstd-prefix>",
    "--",
    "--plan",
    "<plan>",
    "--schema",
    "plans/schema/v1.json",
    "--source-archive",
    "main=<source-archive>",
    "--rootfs-identity",
    "<rootfs-identity>",
    "--prefix-out",
    "<prefix>",
    "--wheel-out",
    "<wheel>",
    "--dep-prefix",
    "<dependency-prefixes>",
    "--input",
    "<declared-inputs>",
]

JAXLIB_ACTION_ARGV = [
    "python3",
    "native/jaxlib/runtime_jobs_executor.py",
    "--executor",
    "native/common/plan_executor.py",
    "--runtime-jobs-file",
    "/vaso/lines/<line>/state/native/jaxlib_jobs.txt",
    "--",
    "--plan",
    "<plan>",
    "--schema",
    "plans/schema/v1.json",
    "--source-archive",
    "main=<source-archive>",
    "--rootfs-identity",
    "<rootfs-identity>",
    "--prefix-out",
    "<prefix>",
    "--wheel-out",
    "<wheelhouse>",
    "--nested-cache-manifest",
    "distdir=<nested-cache-manifest>",
    "--nested-cache-manifest",
    "repository_cache=<nested-cache-manifest>",
    "--nested-distdir",
    "<nested-distdir>",
    "--nested-repository-cache",
    "<nested-repository-cache>",
    "--nested-disk-cache",
    "<nested-disk-cache>",
    "--dep-prefix",
    "<dependency-prefixes>",
    "--input",
    "<declared-inputs>",
]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def label_to_path(label: str) -> Path:
    prefix = "//plans:"
    if not label.startswith(prefix):
        raise ValueError(f"unsupported plan label {label!r}")
    return Path("plans") / label.removeprefix(prefix)


def is_repo_plan_label(label: str) -> bool:
    return label.startswith("//plans:")


def is_action_manifest_label(label: str) -> bool:
    return label.startswith("action://")


def plan_entries(repo_root: Path) -> list[Path]:
    return sorted((repo_root / "plans").glob("*/*/*.plan.json"))


def input_path(repo_root: Path, path_text: str) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return repo_root / path


def package_key(plan: dict[str, Any]) -> str:
    package_name = plan["package"]["name"]
    return f"{plan['profile']}/{plan['line']}/{package_name}"


def normalize_package_name(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def dependency_identifiers(dep_name: str, dep: dict[str, Any]) -> set[str]:
    identifiers = {dep_name}
    label = dep.get("label", "")
    if isinstance(label, str) and label.startswith("//") and ":" in label:
        package_path = label[2:].split(":", 1)[0]
        package_basename = Path(package_path).name
        if package_basename:
            identifiers.add(package_basename)
        target_name = label.rsplit(":", 1)[1]
        if target_name:
            identifiers.add(target_name)
            for suffix in ("_action_prefix", "_prefix", "-action-prefix", "-prefix"):
                if target_name.endswith(suffix):
                    identifiers.add(target_name[: -len(suffix)])
    return identifiers


def dependency_plan_keys(
    parent_plan: dict[str, Any],
    dep_name: str,
    dep: dict[str, Any],
    index: dict[str, Path],
) -> list[str]:
    manifest_label = dep.get("manifest")
    if (
        isinstance(manifest_label, str)
        and manifest_label
        and not is_repo_plan_label(manifest_label)
        and not is_action_manifest_label(manifest_label)
    ):
        return []
    prefix = f"{parent_plan['profile']}/{parent_plan['line']}/"
    identifiers = dependency_identifiers(dep_name, dep)
    normalized = {normalize_package_name(identifier) for identifier in identifiers}
    matches = [
        key
        for key in index
        if key.startswith(prefix)
        and (
            key.removeprefix(prefix) in identifiers
            or normalize_package_name(key.removeprefix(prefix)) in normalized
        )
    ]
    return sorted(matches)


def plan_index(repo_root: Path) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for plan_path in plan_entries(repo_root):
        plan = json.loads(plan_path.read_bytes())
        index[package_key(plan)] = plan_path
    return index


def package_key_for_plan(repo_root: Path, plan_path: Path) -> tuple[str, dict[str, Any]]:
    return package_key_for_plan_with_deps(repo_root, plan_path, {})


def package_key_for_plan_with_deps(
    repo_root: Path,
    plan_path: Path,
    computed_package_keys: dict[str, str],
    index: dict[str, Path] | None = None,
) -> tuple[str, dict[str, Any]]:
    if index is None:
        index = {}
    schema_rel = Path("plans/schema/v1.json")
    schema = load_schema(repo_root / schema_rel)
    plan_bytes = plan_path.read_bytes()
    plan = json.loads(plan_bytes)
    errors = validate_plan(plan, schema)
    if errors:
        raise ValueError(f"{plan_path.relative_to(repo_root).as_posix()}: " + "; ".join(errors))
    rel_plan = plan_path.relative_to(repo_root).as_posix()
    toolchain = plan["toolchain"]
    rootfs_identity_rel = label_to_path(toolchain["rootfs_identity"]["label"])
    rootfs_identity_path = repo_root / rootfs_identity_rel
    rootfs_sha = sha256_file(rootfs_identity_path)
    expected_rootfs_sha = toolchain["rootfs_identity"]["sha256"]
    if rootfs_sha != expected_rootfs_sha:
        raise ValueError(
            f"{rel_plan}: rootfs identity sha256 mismatch: expected {expected_rootfs_sha}, got {rootfs_sha}"
        )
    inputs: list[dict[str, str]] = [
        {
            "kind": "plan",
            "path": rel_plan,
            "sha256": sha256_bytes(plan_bytes),
        },
        {
            "kind": "schema",
            "path": schema_rel.as_posix(),
            "sha256": sha256_file(repo_root / schema_rel),
        },
    ]
    for source in plan["sources"]:
        inputs.append(
            {
                "kind": "source",
                "name": source["name"],
                "path": f"source:{source['strip_prefix']}.tar.gz",
                "sha256": source["sha256"],
            }
        )
        for patch in source.get("patches", []):
            patch_path = input_path(repo_root, patch["path"])
            patch_sha = sha256_file(patch_path)
            expected_patch_sha = patch["sha256"]
            if patch_sha != expected_patch_sha:
                raise ValueError(
                    f"{rel_plan}: patch sha256 mismatch for {patch['path']}: "
                    f"expected {expected_patch_sha}, got {patch_sha}"
                )
            inputs.append(
                {
                    "kind": "patch",
                    "path": patch["path"],
                    "sha256": patch_sha,
                }
            )
    for item in plan["inputs"]:
        path = input_path(repo_root, item["path"])
        actual_sha = sha256_file(path)
        expected_sha = item["sha256"]
        if actual_sha != expected_sha:
            raise ValueError(
                f"{rel_plan}: input sha256 mismatch for {item['path']}: "
                f"expected {expected_sha}, got {actual_sha}"
            )
        inputs.append(
            {
                "kind": "input",
                "path": item["path"],
                "sha256": actual_sha,
            }
        )
    inputs.extend(
        [
        {
            "kind": "executor",
            "path": "native/common/plan_executor.py",
            "sha256": sha256_file(repo_root / "native" / "common" / "plan_executor.py"),
        },
        {
            "kind": "executor",
            "path": "native/common/plan_schema.py",
            "sha256": sha256_file(repo_root / "native" / "common" / "plan_schema.py"),
        },
        {
            "kind": "rootfs",
            "path": rootfs_identity_rel.as_posix(),
            "sha256": rootfs_sha,
        },
        ]
    )
    for dep_name, dep in sorted(plan["deps"].items()):
        dep_plan_keys = dependency_plan_keys(plan, dep_name, dep, index)
        if len(dep_plan_keys) > 1:
            raise ValueError(
                f"{rel_plan}: dependency {dep_name} matches multiple committed plans: {', '.join(dep_plan_keys)}"
            )
        computed_key = computed_package_keys.get(dep_plan_keys[0]) if dep_plan_keys else None
        dep_key = dep["key"]
        if computed_key is not None and isinstance(dep_key, str) and dep_key.startswith("action://"):
            dep_key = computed_key
        elif computed_key is not None and dep_key != computed_key:
            raise ValueError(
                f"{rel_plan}: dependency key mismatch for {dep_name}: expected computed key {computed_key}, got {dep['key']}"
            )
        dep_input: dict[str, str] = {
            "kind": "dep",
            "name": dep_name,
            "label": dep["label"],
            "key": dep_key,
        }
        if dep_plan_keys:
            dep_input["package_key"] = dep_plan_keys[0]
        manifest_label = dep.get("manifest")
        if manifest_label:
            if is_repo_plan_label(manifest_label):
                manifest_rel = label_to_path(manifest_label)
                dep_input["manifest"] = manifest_rel.as_posix()
                dep_input["manifest_sha256"] = sha256_file(repo_root / manifest_rel)
            else:
                dep_input["manifest"] = manifest_label
        inputs.append(dep_input)
    if plan["package"]["name"] == "zlib-ng":
        inputs.append(
            {
                "kind": "action-wrapper",
                "path": "native/zlib_ng/zlib_ng_action.bzl",
                "sha256": sha256_file(repo_root / "native" / "zlib_ng" / "zlib_ng_action.bzl"),
                "argv": LEAF_ACTION_ARGV,
                "env": ACTION_ENV,
            }
        )
    elif plan["package"]["name"] in LEAF_ACTION_WRAPPERS:
        wrapper_path = LEAF_ACTION_WRAPPERS[plan["package"]["name"]]
        inputs.extend(
            [
                {
                    "kind": "executor",
                    "path": "native/common/prefix_manifest.py",
                    "sha256": sha256_file(repo_root / "native" / "common" / "prefix_manifest.py"),
                },
                {
                    "kind": "action-wrapper",
                    "path": "native/common/committed_plan_prefix.bzl",
                    "sha256": sha256_file(repo_root / "native" / "common" / "committed_plan_prefix.bzl"),
                    "argv": LEAF_ACTION_ARGV,
                    "env": ACTION_ENV,
                },
                {
                    "kind": "action-wrapper",
                    "path": wrapper_path,
                    "sha256": sha256_file(repo_root / wrapper_path),
                },
            ]
        )
    elif plan["package"]["name"] == "py-triton":
        inputs.append(
            {
                "kind": "action-wrapper",
                "path": "native/triton/triton_action.bzl",
                "sha256": sha256_file(repo_root / "native" / "triton" / "triton_action.bzl"),
                "argv": TRITON_ACTION_ARGV,
                "env": ACTION_ENV,
            }
        )
    elif plan["package"]["name"] == "py-torchvision":
        inputs.extend(
            [
                {
                    "kind": "executor",
                    "path": "native/common/prefix_manifest.py",
                    "sha256": sha256_file(repo_root / "native" / "common" / "prefix_manifest.py"),
                },
                {
                    "kind": "action-wrapper",
                    "path": "native/torchvision/torchvision_action.bzl",
                    "sha256": sha256_file(repo_root / "native" / "torchvision" / "torchvision_action.bzl"),
                    "argv": TORCHVISION_ACTION_ARGV,
                    "env": ACTION_ENV,
                },
            ]
        )
    elif plan["package"]["name"] == "py-torchaudio":
        inputs.extend(
            [
                {
                    "kind": "executor",
                    "path": "native/common/prefix_manifest.py",
                    "sha256": sha256_file(repo_root / "native" / "common" / "prefix_manifest.py"),
                },
                {
                    "kind": "action-wrapper",
                    "path": "native/torchaudio/torchaudio_action.bzl",
                    "sha256": sha256_file(repo_root / "native" / "torchaudio" / "torchaudio_action.bzl"),
                    "argv": TORCHAUDIO_ACTION_ARGV,
                    "env": ACTION_ENV,
                },
            ]
        )
    elif plan["package"]["name"] == "py-jaxlib":
        nested_bazel = plan.get("nested_bazel", {})
        inputs.extend(
            [
                {
                    "kind": "executor",
                    "path": "native/jaxlib/runtime_jobs_executor.py",
                    "sha256": sha256_file(repo_root / "native" / "jaxlib" / "runtime_jobs_executor.py"),
                },
                {
                    "kind": "executor",
                    "path": "native/common/prefix_manifest.py",
                    "sha256": sha256_file(repo_root / "native" / "common" / "prefix_manifest.py"),
                },
                {
                    "kind": "nested-bazel",
                    "archive_count": len(nested_bazel.get("archives", [])),
                    "distdir_manifest": nested_bazel.get("distdir_manifest", {}).get("label", ""),
                    "distdir_manifest_sha256": nested_bazel.get("distdir_manifest", {}).get("sha256", ""),
                    "repository_cache_manifest": nested_bazel.get("repository_cache_manifest", {}).get("label", ""),
                    "repository_cache_manifest_sha256": nested_bazel.get("repository_cache_manifest", {}).get("sha256", ""),
                },
                {
                    "kind": "action-wrapper",
                    "path": "native/jaxlib/jaxlib_action.bzl",
                    "sha256": sha256_file(repo_root / "native" / "jaxlib" / "jaxlib_action.bzl"),
                    "argv": JAXLIB_ACTION_ARGV,
                    "env": ACTION_ENV,
                },
            ]
        )
    elif plan["package"]["name"] == "py-torch":
        inputs.extend(
            [
                {
                    "kind": "executor",
                    "path": "native/pytorch/runtime_jobs_executor.py",
                    "sha256": sha256_file(repo_root / "native" / "pytorch" / "runtime_jobs_executor.py"),
                },
                {
                    "kind": "action-wrapper",
                    "path": "native/pytorch/pytorch_action.bzl",
                    "sha256": sha256_file(repo_root / "native" / "pytorch" / "pytorch_action.bzl"),
                    "argv": PYTORCH_ACTION_ARGV,
                    "env": ACTION_ENV,
                },
            ]
        )
    merkle_key = sha256_bytes(stable_json({"inputs": inputs}).encode("utf-8"))
    key = package_key(plan)
    return key, {
        "inputs": inputs,
        "merkle_key": merkle_key,
    }


def compute_snapshot(repo_root: Path) -> dict[str, Any]:
    packages: dict[str, Any] = {}
    index = plan_index(repo_root)
    pending = plan_entries(repo_root)
    progress = True
    while pending and progress:
        progress = False
        next_pending: list[Path] = []
        computed_keys = {key: value["merkle_key"] for key, value in packages.items()}
        for plan_path in pending:
            plan = json.loads(plan_path.read_bytes())
            deps = plan.get("deps") if isinstance(plan.get("deps"), dict) else {}
            missing: list[str] = []
            for dep_name, dep in deps.items():
                dep_plan_keys = dependency_plan_keys(plan, dep_name, dep, index)
                if len(dep_plan_keys) > 1:
                    raise ValueError(
                        f"{plan_path.relative_to(repo_root).as_posix()}: dependency {dep_name} "
                        f"matches multiple committed plans: {', '.join(dep_plan_keys)}"
                    )
                missing.extend(key for key in dep_plan_keys if key not in computed_keys)
            if missing:
                next_pending.append(plan_path)
                continue
            key, value = package_key_for_plan_with_deps(repo_root, plan_path, computed_keys, index)
            packages[key] = value
            progress = True
        pending = next_pending
    if pending:
        unresolved = ", ".join(path.relative_to(repo_root).as_posix() for path in pending)
        raise ValueError(f"could not resolve dependency plan keys for: {unresolved}")
    return {
        "schema": 1,
        "packages": packages,
    }


def write_snapshot(path: Path, snapshot: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stable_json(snapshot), encoding="utf-8")


def check_snapshot(repo_root: Path, snapshot: dict[str, Any]) -> list[str]:
    path = repo_root / "plans" / "keys.json"
    if not path.is_file():
        return ["missing plans/keys.json"]
    actual = path.read_text(encoding="utf-8")
    expected = stable_json(snapshot)
    if actual != expected:
        return ["plans/keys.json is stale; run tools/plan_key_snapshot.py --out plans/keys.json and commit the result"]
    return []


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--check", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    snapshot = compute_snapshot(args.repo_root)
    if args.check:
        errors = check_snapshot(args.repo_root, snapshot)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        return 0
    if args.out:
        write_snapshot(args.out, snapshot)
    else:
        print(stable_json(snapshot), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
