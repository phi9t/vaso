#!/usr/bin/env python3
"""Refuse the NP-17 flip without passing two-line acceptance evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable


LINES = ("cu129", "cu130")
ACCEPTANCE_PROFILE = "torch"
RULE_IDS = frozenset(
    (
        "acceptance-ancestor-freshness",
        "acceptance-complete",
        "acceptance-commit",
        "acceptance-line",
        "acceptance-profile",
        "acceptance-verdict",
        "acceptance-waiver",
    )
)
EXPERIMENT_PREFIX = "experiments/spack-bazel-graph/"
ACCEPTANCE_NAMES = {
    "cu129": "acceptance-torch-cu129.json",
    "cu130": "acceptance-torch-cu130.json",
}
SELF_GUARD_PATHS = {
    "tools/np17_acceptance_gate_guard.py",
    "tools/np17_acceptance_gate_guard_test.py",
}


def is_build_input_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("/")
    if not normalized.startswith(EXPERIMENT_PREFIX):
        return False
    rel = normalized[len(EXPERIMENT_PREFIX):]
    if not rel:
        return False
    if rel == "acceptance-waivers.json" or (
        "/" not in rel and rel.startswith("acceptance-") and rel.endswith(".json")
    ):
        return False
    if rel in SELF_GUARD_PATHS:
        return False
    first = rel.split("/", 1)[0]
    return first not in {".scratch", "docs"}


def validate_waivers(doc: object) -> list[str]:
    if not isinstance(doc, list):
        return ["acceptance-waivers.json must be a JSON list"]
    errors: list[str] = []
    for index, waiver in enumerate(doc):
        if not isinstance(waiver, dict):
            errors.append(f"waiver {index} must be an object")
            continue
        if not waiver.get("human_decision"):
            errors.append(f"waiver {index} must include human_decision")
    return errors


def check_acceptance_pair(
    acceptances: dict[str, dict[str, object] | None],
    *,
    flip_commit: str,
    is_ancestor: Callable[[str, str], bool],
    changed_paths: Callable[[str, str], list[str]],
) -> list[str]:
    errors: list[str] = []
    for line in LINES:
        filename = ACCEPTANCE_NAMES[line]
        doc = acceptances.get(line)
        if doc is None:
            errors.append(f"{filename} is missing")
            continue
        if doc.get("profile") != ACCEPTANCE_PROFILE:
            errors.append(f"{filename} profile must be {ACCEPTANCE_PROFILE}, got {doc.get('profile')}")
        if doc.get("line") != line:
            errors.append(f"{filename} line must be {line}, got {doc.get('line')}")
        verdict = doc.get("verdict")
        if verdict != "passed":
            errors.append(f"{filename} verdict must be passed, got {verdict}")
        commit = str(doc.get("commit") or "")
        if not commit:
            errors.append(f"{filename} must record commit")
            continue
        if commit == flip_commit:
            continue
        if not is_ancestor(commit, flip_commit):
            errors.append(
                f"{filename} commit {commit} is neither the flip commit {flip_commit} nor an ancestor"
            )
            continue
        changed_build_inputs = sorted(path for path in changed_paths(commit, flip_commit) if is_build_input_path(path))
        if changed_build_inputs:
            errors.append(
                f"{filename} is for ancestor {commit} but build inputs changed: "
                + ", ".join(changed_build_inputs)
            )
    return errors


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _git(repo_root: Path, args: list[str]) -> str:
    return subprocess.check_output(["git", "-C", str(repo_root), *args], text=True).strip()


def _git_is_ancestor(repo_root: Path) -> Callable[[str, str], bool]:
    def check(ancestor: str, descendant: str) -> bool:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "merge-base", "--is-ancestor", ancestor, descendant],
            check=False,
        )
        return result.returncode == 0

    return check


def _git_changed_paths(repo_root: Path) -> Callable[[str, str], list[str]]:
    def paths(ancestor: str, descendant: str) -> list[str]:
        output = _git(repo_root, ["diff", "--name-only", f"{ancestor}..{descendant}"])
        return [line for line in output.splitlines() if line]

    return paths


def _default_acceptance_dir(repo_root: Path) -> Path:
    experiment = repo_root / "experiments" / "spack-bazel-graph"
    return experiment if experiment.is_dir() else repo_root


def _load_acceptances(acceptance_dir: Path) -> dict[str, dict[str, object] | None]:
    docs: dict[str, dict[str, object] | None] = {}
    for line, name in ACCEPTANCE_NAMES.items():
        path = acceptance_dir / name
        docs[line] = _load_json(path) if path.is_file() else None
    return docs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--acceptance-dir", type=Path)
    parser.add_argument("--flip-commit")
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    acceptance_dir = (args.acceptance_dir or _default_acceptance_dir(repo_root)).resolve()
    flip_commit = args.flip_commit or _git(repo_root, ["rev-parse", "HEAD"])
    waivers_path = acceptance_dir / "acceptance-waivers.json"

    errors: list[str] = []
    if not waivers_path.is_file():
        errors.append(f"{waivers_path.name} is missing")
    else:
        errors.extend(validate_waivers(_load_json(waivers_path)))

    errors.extend(
        check_acceptance_pair(
            _load_acceptances(acceptance_dir),
            flip_commit=flip_commit,
            is_ancestor=_git_is_ancestor(repo_root),
            changed_paths=_git_changed_paths(repo_root),
        )
    )
    if errors:
        print("NP-17 acceptance guard failed:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 1
    print(f"NP-17 acceptance guard passed for {flip_commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
