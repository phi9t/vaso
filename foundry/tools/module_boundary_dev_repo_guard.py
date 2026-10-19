#!/usr/bin/env python3
"""Guard exported module toolchains from depending on dev-only repositories."""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path


EXTERNAL_REPO_RE = re.compile(r"@([A-Za-z0-9_.+\-]+)//")
RULE_IDS = frozenset(("non-dev-toolchain-dev-repo",))


@dataclass(frozen=True)
class RegisteredToolchain:
    label: str
    line: int


@dataclass(frozen=True)
class Target:
    package: str
    name: str
    labels: frozenset[str]


@dataclass(frozen=True)
class Violation:
    registered_label: str
    repo: str
    path: tuple[str, ...]

    def format(self) -> str:
        route = " -> ".join(self.path)
        return f"{self.registered_label} reaches dev-only repo @{self.repo} through {route}"


def _parse_starlark_like(path: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        raise SystemExit(f"could not parse {path}: {exc}") from exc


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def _literal_bool(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _keyword_string(call: ast.Call, name: str) -> str | None:
    node = _keyword(call, name)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_dev_dependency(call: ast.Call) -> bool:
    return _literal_bool(_keyword(call, "dev_dependency"))


def _string_constants(node: ast.AST) -> set[str]:
    return {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }


def _calls(tree: ast.Module) -> list[ast.Call]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def _repo_declarations(path: Path, *, file_is_dev_only: bool) -> tuple[set[str], set[str]]:
    dev_only: set[str] = set()
    non_dev: set[str] = set()
    for call in _calls(_parse_starlark_like(path)):
        name = _keyword_string(call, "name")
        if not name:
            continue
        if file_is_dev_only or _is_dev_dependency(call):
            dev_only.add(name)
        else:
            non_dev.add(name)
    return dev_only, non_dev


def dev_only_repositories(module_path: Path, dev_module_path: Path) -> set[str]:
    module_dev, module_non_dev = _repo_declarations(module_path, file_is_dev_only=False)
    dev_module_dev, dev_module_non_dev = _repo_declarations(dev_module_path, file_is_dev_only=True)
    return (module_dev | dev_module_dev) - (module_non_dev | dev_module_non_dev)


def registered_toolchains(module_path: Path) -> list[RegisteredToolchain]:
    registrations: list[RegisteredToolchain] = []
    for call in _calls(_parse_starlark_like(module_path)):
        if _call_name(call) != "register_toolchains" or _is_dev_dependency(call):
            continue
        for arg in call.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                registrations.append(RegisteredToolchain(label=arg.value, line=call.lineno))
    return registrations


def _target_from_label(label: str, *, package: str) -> tuple[str, str] | None:
    if label.startswith(":"):
        return package, label[1:]
    package_prefix = f"//{package}:"
    if label.startswith(package_prefix):
        return package, label.removeprefix(package_prefix)
    if label.startswith("//") and "@" not in label:
        raw = label.removeprefix("//")
        if ":" in raw:
            target_package, target_name = raw.split(":", 1)
            return target_package, target_name
        target_package = raw
        return target_package, target_package.rsplit("/", 1)[-1]
    return None


def _target_label(name: str, *, package: str) -> str:
    return f"//{package}:{name}"


def targets_from_build(build_path: Path, *, package: str) -> dict[str, Target]:
    targets: dict[str, Target] = {}
    for call in _calls(_parse_starlark_like(build_path)):
        name = _keyword_string(call, "name")
        if not name:
            continue
        labels: set[str] = set()
        for keyword in call.keywords:
            if keyword.arg == "name":
                continue
            labels.update(_string_constants(keyword.value))
        targets[name] = Target(package=package, name=name, labels=frozenset(labels))
    return targets


class TargetIndex:
    def __init__(self, module_root: Path):
        self._module_root = module_root
        self._packages: dict[str, dict[str, Target]] = {}

    def get(self, package: str, name: str) -> Target | None:
        if package not in self._packages:
            build_path = self._module_root / package / "BUILD.bazel"
            if not build_path.exists():
                build_path = self._module_root / package / "BUILD"
            if not build_path.exists():
                self._packages[package] = {}
            else:
                self._packages[package] = targets_from_build(build_path, package=package)
        return self._packages[package].get(name)


def _walk_registered_target(
    *,
    registered_label: str,
    target_package: str,
    target_name: str,
    targets: TargetIndex,
    dev_repos: set[str],
) -> list[Violation]:
    violations: list[Violation] = []
    seen: set[tuple[str, str]] = set()

    def visit(package: str, name: str, route: tuple[str, ...]) -> None:
        key = (package, name)
        if key in seen:
            return
        seen.add(key)
        target = targets.get(package, name)
        if target is None:
            return
        current = _target_label(name, package=target.package)
        current_route = route + (current,)
        for label in sorted(target.labels):
            for repo in EXTERNAL_REPO_RE.findall(label):
                if repo in dev_repos:
                    violations.append(
                        Violation(
                            registered_label=registered_label,
                            repo=repo,
                            path=current_route + (label,),
                        )
                    )
            next_target = _target_from_label(label, package=package)
            if next_target:
                next_package, next_name = next_target
                visit(next_package, next_name, current_route)

    visit(target_package, target_name, ())
    return violations


def find_violations(module_path: Path, dev_module_path: Path, toolchains_build_path: Path) -> list[Violation]:
    del toolchains_build_path  # Retained in the public test/CLI API for compatibility.
    module_root = module_path.parent
    dev_repos = dev_only_repositories(module_path, dev_module_path)
    targets = TargetIndex(module_root)
    violations: list[Violation] = []
    for registration in registered_toolchains(module_path):
        target = _target_from_label(registration.label, package="")
        if target is None:
            for repo in EXTERNAL_REPO_RE.findall(registration.label):
                if repo in dev_repos:
                    violations.append(
                        Violation(
                            registered_label=registration.label,
                            repo=repo,
                            path=(registration.label,),
                        )
                    )
            continue
        target_package, target_name = target
        violations.extend(
            _walk_registered_target(
                registered_label=registration.label,
                target_package=target_package,
                target_name=target_name,
                targets=targets,
                dev_repos=dev_repos,
            )
        )
    return sorted(set(violations), key=lambda item: (item.registered_label, item.repo, item.path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--module", type=Path, required=True)
    parser.add_argument("--dev-module", type=Path, required=True)
    parser.add_argument("--toolchains-build", type=Path, required=True)
    args = parser.parse_args(argv)

    violations = find_violations(args.module, args.dev_module, args.toolchains_build)
    if violations:
        print("non-dev MODULE.bazel toolchain registrations depend on dev-only repositories:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation.format()}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
