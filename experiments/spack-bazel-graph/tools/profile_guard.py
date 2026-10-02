#!/usr/bin/env python3
"""Guard profile captures against loading torch and JAX families together."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable, Mapping


TORCH_FAMILY = frozenset({"py-torch", "py-triton"})
JAX_FAMILY = frozenset({"py-jax", "py-jaxlib"})
PROFILES = frozenset({"torch", "jax"})
RULE_IDS = frozenset(
    (
        "invalid-profile",
        "mixed-profile-families",
        "profile-family-membership",
    )
)


def _validate_profile(profile: str) -> str:
    if profile not in PROFILES:
        raise SystemExit(f"unknown profile {profile!r}; expected one of {', '.join(sorted(PROFILES))}")
    return profile


def _collect_package_names(value: object) -> set[str]:
    found: set[str] = set()

    def visit(item: object, parent_key: str | None = None) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                visit(child, str(key))
            return
        if isinstance(item, list):
            for child in item:
                visit(child, parent_key)
            return
        if not isinstance(item, str):
            return
        if parent_key in {"package", "name", "root"} and item in TORCH_FAMILY | JAX_FAMILY:
            found.add(item)

    visit(value)
    return found


def _package_list(packages: Iterable[str]) -> str:
    return ", ".join(sorted(packages))


def _check_packages(packages: set[str], source_name: str, profile: str) -> list[str]:
    selected = _validate_profile(profile)
    torch = packages & TORCH_FAMILY
    jax = packages & JAX_FAMILY
    errors: list[str] = []

    if selected == "torch":
        for package in sorted(jax):
            errors.append(f"{source_name}: profile torch cannot include JAX package {package}")
    if selected == "jax":
        for package in sorted(torch):
            errors.append(f"{source_name}: profile jax cannot include torch-family package {package}")

    if torch and jax:
        errors.append(
            f"{source_name}: graph includes both torch-family packages {_package_list(torch)} "
            f"and JAX packages {_package_list(jax)}"
        )
    return errors


def check_document(document: Mapping[str, object], source_name: str, profile: str) -> list[str]:
    return _check_packages(_collect_package_names(document), source_name, profile)


def check_invocation(text: str, source_name: str, profile: str) -> list[str]:
    packages = set(re.findall(r"(?<![A-Za-z0-9_-])(py-(?:torch|triton|jax|jaxlib))(?![A-Za-z0-9_-])", text))
    return _check_packages(packages, source_name, profile)


def check_graph_files(paths: Iterable[Path], profile: str) -> list[str]:
    errors: list[str] = []
    for path in paths:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}: could not parse JSON: {exc}")
            continue
        errors.extend(check_document(document, path.name, profile))
    return errors


def check_invocation_files(paths: Iterable[Path], profile: str) -> list[str]:
    errors: list[str] = []
    for path in paths:
        errors.extend(check_invocation(path.read_text(encoding="utf-8"), path.name, profile))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, choices=sorted(PROFILES))
    parser.add_argument("--invocation", action="append", default=[], metavar="TEXT")
    parser.add_argument("--invocation-file", action="append", default=[], type=Path)
    parser.add_argument("graphs", nargs="*", type=Path)
    args = parser.parse_args(argv)

    errors = check_graph_files(args.graphs, args.profile)
    errors.extend(check_invocation_files(args.invocation_file, args.profile))
    for index, text in enumerate(args.invocation, start=1):
        errors.extend(check_invocation(text, f"--invocation[{index}]", args.profile))

    if errors:
        print("profile guard failed:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 1
    checked = len(args.graphs) + len(args.invocation_file) + len(args.invocation)
    print(f"profile guard checked profile={args.profile}: {checked} input(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
