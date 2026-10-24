#!/usr/bin/env python3
"""Keep the repository layout from drifting after the graph module move."""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


RULE_IDS = frozenset(
    (
        "bulk-gate-evidence",
        "generated-graph-root",
        "old-graph-module-path",
        "old-harness-path",
        "old-vendored-path",
        "top-level-tests-dir",
    )
)

GENERATED_GRAPH_SUFFIXES = (
    "_build_graph.json",
    "_spack_graph.lock.json",
)
BULK_GATE_EVIDENCE_PREFIX = ".scratch/production-build/evidence/"
RETIRED_GRAPH_MODULES = ("experiments/spack-bazel-graph", "graph")
RETIRED_HARNESS_PATHS = (
    ("src/vaso", "insula/execution/vaso"),
    ("experiments/bazel-lab", "tools/bazel-lab"),
)
RETIRED_VENDORED_PATHS = (
    ("foundry/examples/consumers/third_party", "third_party"),
    ("foundry/examples/consumers/rust_pyo3/crates", "third_party/rust_pyo3_cargo"),
)
GRAPH_MODULE = "foundry"
GRAPH_OUTPUT_DIR = "foundry/graphs"
EVIDENCE_POINTER = ".scratch/production-build/evidence-archive.md"


@dataclass(frozen=True, order=True)
class Finding:
    rule: str
    path: str
    message: str

    @property
    def identity(self) -> str:
        return f"{self.rule}\t{self.path}\t{self.message}"


def repo_root_from(anchor: Path) -> Path:
    resolved = anchor.resolve()
    for candidate in (resolved, *resolved.parents):
        if (candidate / "CONSTITUTION.md").is_file() and (candidate / GRAPH_MODULE).is_dir():
            return candidate
    if resolved.parent.name == "tools" and resolved.parents[1].name == GRAPH_MODULE:
        return resolved.parents[2]
    return Path.cwd()


def _git_tracked_paths(repo_root: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or f"{repo_root} is not a git worktree")
    return sorted(raw.decode("utf-8") for raw in proc.stdout.split(b"\0") if raw)


def _generated_graph_destination(path: str) -> str | None:
    rel = PurePosixPath(path)
    if len(rel.parts) != 2 or rel.parts[0] != GRAPH_MODULE:
        return None
    if not path.endswith(GENERATED_GRAPH_SUFFIXES):
        return None
    return f"{GRAPH_OUTPUT_DIR}/{rel.name}"


def _retired_graph_module(path: str) -> str | None:
    for module in RETIRED_GRAPH_MODULES:
        if path == module or path.startswith(f"{module}/"):
            return module
    return None


def _old_graph_module_destination(path: str, module: str) -> str:
    suffix = PurePosixPath(path).relative_to(module).parts
    return PurePosixPath(GRAPH_MODULE, *suffix).as_posix()


def finding_for_path(path: str) -> Finding | None:
    generated_destination = _generated_graph_destination(path)
    if generated_destination is not None:
        return Finding(
            "generated-graph-root",
            path,
            f"generated graph files belong under {GRAPH_OUTPUT_DIR}/; move this file to {generated_destination}",
        )

    if path.startswith(BULK_GATE_EVIDENCE_PREFIX):
        return Finding(
            "bulk-gate-evidence",
            path,
            f"bulk gate evidence belongs on the estate; keep only the pointer at {EVIDENCE_POINTER}",
        )

    retired = _retired_graph_module(path)
    if retired is not None:
        destination = _old_graph_module_destination(path, retired)
        return Finding(
            "old-graph-module-path",
            path,
            f"the vaso_graph module belongs under {GRAPH_MODULE}/; move this file to {destination}",
        )

    if path == "fixtures/vaso-fixtures" or path.startswith("fixtures/vaso-fixtures/"):
        return Finding(
            "old-harness-path",
            path,
            "retire the obsolete smoke scaffold; CPU execution acceptance lives at insula/execution/acceptance.py",
        )

    for retired, replacement in RETIRED_HARNESS_PATHS:
        if path == retired or path.startswith(f"{retired}/"):
            destination = PurePosixPath(replacement, PurePosixPath(path).relative_to(retired)).as_posix()
            return Finding(
                "old-harness-path",
                path,
                f"use the owning module; move this file to {destination}",
            )

    if path.startswith("tests/"):
        return Finding(
            "top-level-tests-dir",
            path,
            "tests belong beside the code they cover as <name>_test.py, not in a top-level tests/ directory",
        )

    for retired, replacement in RETIRED_VENDORED_PATHS:
        if path == retired or path.startswith(f"{retired}/"):
            destination = PurePosixPath(replacement, PurePosixPath(path).relative_to(retired)).as_posix()
            return Finding(
                "old-vendored-path",
                path,
                f"vendored code belongs under the repo-root third_party/; move this file to {destination}",
            )

    return None


def collect_findings(repo_root: Path) -> list[Finding]:
    return sorted(
        finding
        for path in _git_tracked_paths(repo_root.resolve())
        for finding in [finding_for_path(path)]
        if finding is not None
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--source-anchor", type=Path, default=Path(__file__))
    args = parser.parse_args(argv)

    repo_root = (args.repo_root or repo_root_from(args.source_anchor)).resolve()
    try:
        findings = collect_findings(repo_root)
    except RuntimeError as exc:
        print(f"layout guard could not list tracked files: {exc}", file=sys.stderr)
        return 2

    for finding in findings:
        print(f"{finding.path}: {finding.rule}: {finding.message}", file=sys.stderr)
    if findings:
        print(f"layout guard found {len(findings)} tracked file(s) in retired locations", file=sys.stderr)
        return 1
    print("layout guard passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
