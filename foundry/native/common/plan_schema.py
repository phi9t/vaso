"""Shared schema and validation helpers for committed native build plans."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
PROFILES = ("jax", "torch")
LINES = ("cu129", "cu130")
SUPPORTED_PLACEHOLDERS = (
    "cuda_home",
    "jobs",
    "nested_build_py_args",
    "nested_disk_cache",
    "nested_distdir",
    "nested_repository_cache",
    "out",
    "pcfiledir",
    "prefix_map_flags",
    "rootfs_identity",
    "seed",
    "source_archive",
    "source_date_epoch",
    "src",
    "wheel",
    "work",
)
PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")


def stable_json(data: object) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def schema_v1() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "x-allowed-placeholders": list(SUPPORTED_PLACEHOLDERS),
        "additionalProperties": False,
        "properties": {
            "build": {"$ref": "#/$defs/phase"},
            "configure": {"$ref": "#/$defs/phase"},
            "deps": {
                "additionalProperties": {"$ref": "#/$defs/dep"},
                "type": "object",
            },
            "determinism": {
                "additionalProperties": False,
                "properties": {
                    "prefix_map": {
                        "additionalProperties": {"const": "."},
                        "propertyNames": {"enum": ["{work}", "{out}"]},
                        "type": "object",
                    },
                    "seed": {"type": "string"},
                    "source_date_epoch": {"type": "integer"},
                },
                "required": ["prefix_map", "seed", "source_date_epoch"],
                "type": "object",
            },
            "gpu_archs": {"items": {"type": "string"}, "type": "array"},
            "inputs": {"items": {"$ref": "#/$defs/input"}, "type": "array"},
            "install": {"$ref": "#/$defs/phase"},
            "line": {"enum": list(LINES)},
            "live_checks": {"$ref": "#/$defs/live_checks"},
            "nested_bazel": {
                "additionalProperties": False,
                "properties": {
                    "archives": {"items": {"$ref": "#/$defs/nested_archive"}, "type": "array"},
                    "build_py_args": {"items": {"type": "string"}, "minItems": 1, "type": "array"},
                    "distdir_manifest": {"$ref": "#/$defs/manifest"},
                    "repository_cache_manifest": {"$ref": "#/$defs/manifest"},
                },
                "required": ["archives", "build_py_args", "distdir_manifest", "repository_cache_manifest"],
                "type": "object",
            },
            "outputs": {"items": {"$ref": "#/$defs/output"}, "minItems": 1, "type": "array"},
            "package": {
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "version": {"type": "string"},
                },
                "required": ["name", "version"],
                "type": "object",
            },
            "profile": {"enum": list(PROFILES)},
            "schema": {"const": SCHEMA_VERSION},
            "sources": {"items": {"$ref": "#/$defs/source"}, "minItems": 1, "type": "array"},
            "toolchain": {
                "additionalProperties": False,
                "properties": {
                    "cc": {"type": "string"},
                    "cuda": {"type": "string"},
                    "cxx": {"type": "string"},
                    "env": {
                        "additionalProperties": {"type": "string"},
                        "properties": {
                            "CC": {"type": "string"},
                            "CUDAFLAGS": {"type": "string"},
                            "CXX": {"type": "string"},
                            "CFLAGS": {"type": "string"},
                            "CXXFLAGS": {"type": "string"},
                            "NVCC_FLAGS": {"type": "string"},
                        },
                        "required": ["CC", "CXX", "CFLAGS", "CXXFLAGS", "CUDAFLAGS", "NVCC_FLAGS"],
                        "type": "object",
                    },
                    "python": {"type": "string"},
                    "rootfs_identity": {
                        "additionalProperties": False,
                        "properties": {
                            "label": {"type": "string"},
                            "sha256": {"type": "string"},
                        },
                        "required": ["label", "sha256"],
                        "type": "object",
                    },
                },
                "required": ["cc", "cuda", "cxx", "env", "python", "rootfs_identity"],
                "type": "object",
            },
        },
        "required": [
            "schema",
            "profile",
            "line",
            "package",
            "sources",
            "toolchain",
            "deps",
            "configure",
            "build",
            "install",
            "outputs",
            "inputs",
            "gpu_archs",
            "determinism",
            "live_checks",
        ],
        "type": "object",
        "$defs": {
            "live_check_command": {
                "additionalProperties": False,
                "properties": {
                    "argv": {"items": {"type": "string"}, "minItems": 1, "type": "array"},
                    "expect_stdout": {"type": "string"},
                    "name": {"type": "string"},
                },
                "required": ["name", "argv", "expect_stdout"],
                "type": "object",
            },
            "live_checks": {
                "additionalProperties": False,
                "properties": {
                    "commands": {
                        "items": {"$ref": "#/$defs/live_check_command"},
                        "minItems": 1,
                        "type": "array",
                    },
                },
                "required": ["commands"],
                "type": "object",
            },
            "dep": {
                "additionalProperties": False,
                "properties": {
                    "key": {"type": "string"},
                    "label": {"type": "string"},
                    "manifest": {"type": "string"},
                },
                "required": ["label", "key"],
                "type": "object",
            },
            "input": {
                "additionalProperties": False,
                "properties": {
                    "label": {"type": "string"},
                    "path": {"type": "string"},
                    "sha256": {"type": "string"},
                },
                "required": ["path", "sha256"],
                "type": "object",
            },
            "manifest": {
                "additionalProperties": False,
                "properties": {
                    "label": {"type": "string"},
                    "sha256": {"type": "string"},
                },
                "required": ["label", "sha256"],
                "type": "object",
            },
            "nested_archive": {
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "sha256": {"type": "string"},
                    "urls": {"items": {"type": "string"}, "type": "array"},
                },
                "required": ["name", "sha256", "urls"],
                "type": "object",
            },
            "output": {
                "additionalProperties": False,
                "properties": {
                    "kind": {"enum": ["prefix", "wheel"]},
                    "path": {"type": "string"},
                },
                "required": ["kind", "path"],
                "type": "object",
            },
            "patch": {
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                    "sha256": {"type": "string"},
                },
                "required": ["path", "sha256"],
                "type": "object",
            },
            "phase": {
                "additionalProperties": False,
                "properties": {
                    "argv": {"items": {"type": "string"}, "minItems": 1, "type": "array"},
                    "cwd": {"type": "string"},
                    "env": {"additionalProperties": {"type": "string"}, "type": "object"},
                },
                "required": ["cwd", "argv", "env"],
                "type": "object",
            },
            "source": {
                "additionalProperties": False,
                "properties": {
                    "archive": {"type": "string"},
                    "name": {"type": "string"},
                    "patches": {"items": {"$ref": "#/$defs/patch"}, "type": "array"},
                    "sha256": {"type": "string"},
                    "strip_prefix": {"type": "string"},
                    "urls": {"items": {"type": "string"}, "type": "array"},
                },
                "required": ["name", "archive", "patches", "sha256", "strip_prefix", "urls"],
                "type": "object",
            },
        },
    }


def load_schema(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON schema object")
    return data


def validate_plan(plan: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    errors = _validate_schema(plan, schema, schema, "plan")
    errors.extend(_validate_placeholders(plan, schema))
    seed = plan.get("determinism", {}).get("seed") if isinstance(plan.get("determinism"), dict) else None
    if not isinstance(seed, str) or not seed.startswith("sha256:"):
        errors.append("plan.determinism.seed must be recorded as sha256:<digest>")
    return errors


def _validate_schema(value: Any, schema: dict[str, Any], root: dict[str, Any], path: str) -> list[str]:
    if "$ref" in schema:
        return _validate_schema(value, _resolve_ref(schema["$ref"], root), root, path)
    errors: list[str] = []
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path} must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path} must be one of {schema['enum']!r}")
    expected_type = schema.get("type")
    if expected_type and not _type_matches(value, expected_type):
        errors.append(f"{path} must be a {expected_type}")
        return errors
    if expected_type == "object":
        assert isinstance(value, dict)
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{path} is missing {key}")
        property_names = schema.get("propertyNames")
        if isinstance(property_names, dict):
            for key in value:
                errors.extend(_validate_schema(key, property_names, root, f"{path} property name {key!r}"))
        properties = schema.get("properties", {})
        for key, child in properties.items():
            if key in value:
                errors.extend(_validate_schema(value[key], child, root, f"{path}.{key}"))
        additional = schema.get("additionalProperties", True)
        for key, child_value in value.items():
            if key in properties:
                continue
            if additional is False:
                errors.append(f"{path} has unsupported field {key}")
            elif isinstance(additional, dict):
                errors.extend(_validate_schema(child_value, additional, root, f"{path}.{key}"))
    elif expected_type == "array":
        assert isinstance(value, list)
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and len(value) < min_items:
            errors.append(f"{path} must contain at least {min_items} item(s)")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(_validate_schema(item, item_schema, root, f"{path}[{index}]"))
    return errors


def _resolve_ref(ref: str, root: dict[str, Any]) -> dict[str, Any]:
    prefix = "#/"
    if not ref.startswith(prefix):
        raise ValueError(f"unsupported schema ref {ref!r}")
    node: Any = root
    for part in ref.removeprefix(prefix).split("/"):
        node = node[part]
    if not isinstance(node, dict):
        raise ValueError(f"schema ref {ref!r} does not resolve to an object")
    return node


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    return True


def _validate_placeholders(plan: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    allowed = set(schema.get("x-allowed-placeholders", []))
    deps = plan.get("deps") if isinstance(plan.get("deps"), dict) else {}
    dep_names = set(deps)
    sources = plan.get("sources") if isinstance(plan.get("sources"), list) else []
    source_names = {source.get("name") for source in sources if isinstance(source, dict) and isinstance(source.get("name"), str)}
    errors: list[str] = []
    for field, value in _templated_values(plan):
        for match in PLACEHOLDER_RE.finditer(value):
            name = match.group(1)
            if name.startswith("prefix:"):
                dep = name.removeprefix("prefix:")
                if dep not in dep_names:
                    errors.append(f"{field} references missing dependency prefix {dep!r}")
            elif name.startswith("src:"):
                source = name.removeprefix("src:")
                if source not in source_names:
                    errors.append(f"{field} references missing source {source!r}")
            elif name.startswith("source_archive:"):
                source = name.removeprefix("source_archive:")
                if source not in source_names:
                    errors.append(f"{field} references missing source archive {source!r}")
            elif name not in allowed:
                errors.append(f"{field} references unsupported placeholder {{{name}}}")
    return errors


def _templated_values(plan: dict[str, Any]) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for source_index, source in enumerate(plan.get("sources", [])):
        if isinstance(source, dict) and isinstance(source.get("archive"), str):
            values.append((f"plan.sources[{source_index}].archive", source["archive"]))
    for input_index, item in enumerate(plan.get("inputs", [])):
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            values.append((f"plan.inputs[{input_index}].path", item["path"]))
    for phase_name in ("configure", "build", "install"):
        phase = plan.get(phase_name)
        if not isinstance(phase, dict):
            continue
        if isinstance(phase.get("cwd"), str):
            values.append((f"plan.{phase_name}.cwd", phase["cwd"]))
        for index, item in enumerate(phase.get("argv", [])):
            if isinstance(item, str):
                values.append((f"plan.{phase_name}.argv[{index}]", item))
        env = phase.get("env")
        if isinstance(env, dict):
            for key, item in env.items():
                if isinstance(item, str):
                    values.append((f"plan.{phase_name}.env[{key}]", item))
    for output_index, output in enumerate(plan.get("outputs", [])):
        if isinstance(output, dict) and isinstance(output.get("path"), str):
            values.append((f"plan.outputs[{output_index}].path", output["path"]))
    nested_bazel = plan.get("nested_bazel")
    if isinstance(nested_bazel, dict):
        for index, item in enumerate(nested_bazel.get("build_py_args", [])):
            if isinstance(item, str):
                values.append((f"plan.nested_bazel.build_py_args[{index}]", item))
    return values
