#!/usr/bin/env python3
"""Check that native repository rules consume the provider the graph selects.

native_overrides.json decides which native repo serves each graph node
(name@version wins over name). The version-parity guard proves each provider
builds its node's version, but a native rule also *consumes* other providers
through `<dep>_prefix_file = "@repo//:prefix_path.txt"` attributes in
MODULE.bazel. If a consumer is wired to a repo that the graph no longer selects
for that dependency (for example `@python_native`, 3.14.5, after the graph's
python@3.13.13 moves to `@python_313_native`), the consumer is built against
the wrong dependency even though every version check passes.

For every native repo X and every `*_prefix_file` wired to repo R that serves
package P, this finds X's graph node(s) that depend on P, resolves the repo the
overrides select for that exact P@version, and reports X.attr -> R when that is
not R. Wiring to packages that are not graph dependencies of X (build helpers)
is out of scope.

Known debt lives in an allowlist, `<repo>.<attr> @<expected repo>` per line.
Entries that no longer match a mismatch fail as stale, so the list can only
shrink.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


BLOCK = re.compile(r"^(\w+)\(\s*\n\s*name = \"([^\"]+)\",(.*?)^\)", re.S | re.M)
PREFIX_ATTR = re.compile(r'(\w+_prefix_file) = "@(\w+)//:prefix_path\.txt"')
LABEL_REPO = re.compile(r"^@(\w+)//")


@dataclass(frozen=True, order=True)
class Mismatch:
    consumer: str
    attr: str
    wired: str
    dependency: str
    expected: str

    def key(self) -> str:
        return f"{self.consumer}.{self.attr} @{self.expected}"

    def __str__(self) -> str:
        return (
            f"{self.consumer}.{self.attr} -> @{self.wired}, graph {self.dependency} "
            f"is served by @{self.expected}"
        )


@dataclass(frozen=True, order=True)
class ForbiddenPythonNativeWiring:
    consumer: str
    attr: str

    def __str__(self) -> str:
        return f"lean graph native repo {self.consumer}.{self.attr} still wires @python_native"


def parse_module(text: str) -> dict[str, list[tuple[str, str]]]:
    """repo name -> [(attr, wired repo)] for every repository rule call."""
    return {name: PREFIX_ATTR.findall(body) for _rule, name, body in BLOCK.findall(text)}


def _repo_of(label: str) -> str | None:
    match = LABEL_REPO.match(label)
    return match.group(1) if match else None


def _serving_repo(package: str, version: str, overrides: dict[str, str]) -> str | None:
    label = overrides.get(f"{package}@{version}", overrides.get(package))
    return _repo_of(label) if label else None


def wiring_mismatches(
    module: dict[str, list[tuple[str, str]]],
    overrides: dict[str, str],
    graph: dict,
    preferred_python_repo: str | None = None,
) -> list[Mismatch]:
    nodes = {node["spack_hash"]: node for node in graph["nodes"]}
    packages_by_repo: dict[str, set[str]] = {}
    for key, label in overrides.items():
        repo = _repo_of(label)
        if repo:
            packages_by_repo.setdefault(repo, set()).add(key.split("@", 1)[0])
    consumer_nodes: dict[str, list[dict]] = {}
    for node in graph["nodes"]:
        repo = _serving_repo(node["package"], node["version"], overrides)
        if repo:
            consumer_nodes.setdefault(repo, []).append(node)

    found: set[Mismatch] = set()
    for consumer, attrs in module.items():
        for node in consumer_nodes.get(consumer, []):
            deps = [nodes[d["hash"]] for d in node.get("deps", []) if d["hash"] in nodes]
            for attr, wired in attrs:
                for package in packages_by_repo.get(wired, set()):
                    for dep in deps:
                        if dep["package"] != package:
                            continue
                        expected = _serving_repo(dep["package"], dep["version"], overrides)
                        if (
                            dep["package"] == "python"
                            and attr == "python_prefix_file"
                            and preferred_python_repo is not None
                            and wired == preferred_python_repo
                        ):
                            continue
                        if expected and expected != wired:
                            found.add(Mismatch(consumer, attr, wired, f'{dep["package"]}@{dep["version"]}', expected))
    return sorted(found)


def _repos_serving_graph(overrides: dict[str, str], graph: dict) -> set[str]:
    repos = set()
    for node in graph["nodes"]:
        repo = _serving_repo(node["package"], node["version"], overrides)
        if repo:
            repos.add(repo)
    return repos


def forbidden_lean_python_native_wiring(
    module: dict[str, list[tuple[str, str]]],
    overrides: dict[str, str],
    graph: dict,
) -> list[ForbiddenPythonNativeWiring]:
    """Return lean-graph native repos that still consume the legacy Python repo."""
    python_repos = {
        _serving_repo(node["package"], node["version"], overrides)
        for node in graph["nodes"]
        if node.get("package") == "python"
    }
    if "python_native" in python_repos:
        return []
    lean_repos = _repos_serving_graph(overrides, graph)
    found = set()
    for consumer in lean_repos:
        for attr, wired in module.get(consumer, []):
            if wired == "python_native":
                found.add(ForbiddenPythonNativeWiring(consumer, attr))
    return sorted(found)


def read_allowlist(path: Path) -> set[str]:
    entries = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            entries.add(" ".join(line.split()))
    return entries


def preferred_python_repo(ledger: dict, overrides: dict[str, str]) -> str | None:
    """Return the native repo for the ledger's selected PyTorch Python line."""
    python_entry = ledger.get("packages", {}).get("python", {})
    label = None
    if isinstance(python_entry, dict):
        label = python_entry.get("native_rule")
    if not label:
        label = overrides.get("python@3.13.13")
    return _repo_of(label) if isinstance(label, str) else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--module", type=Path, required=True)
    parser.add_argument("--native-overrides", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True, help="its py-torch frontier graph is checked")
    parser.add_argument(
        "--graph",
        type=Path,
        action="append",
        default=[],
        help="explicit graph to check instead of the ledger's py-torch frontier graph; may be repeated",
    )
    parser.add_argument("--allowlist", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
        graph_paths = args.graph or [args.ledger.parent / ledger["frontier_graphs"]["py-torch"]["graph"]]
        graphs = [json.loads(path.read_text(encoding="utf-8")) for path in graph_paths]
        overrides = json.loads(args.native_overrides.read_text(encoding="utf-8"))["native"]
        module = parse_module(args.module.read_text(encoding="utf-8"))
        preferred_python = preferred_python_repo(ledger, overrides)
        allowlist = read_allowlist(args.allowlist)
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2

    mismatches = sorted({
        mismatch
        for graph in graphs
        for mismatch in wiring_mismatches(module, overrides, graph, preferred_python)
    })
    keys = {m.key() for m in mismatches}
    errors = [str(m) for m in mismatches if m.key() not in allowlist]
    errors += [f"stale allowlist entry (wiring is consistent now; remove it): {k}" for k in sorted(allowlist - keys)]
    forbidden = sorted({
        wiring
        for graph in graphs
        for wiring in forbidden_lean_python_native_wiring(module, overrides, graph)
    })
    errors += [str(w) for w in forbidden]
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        return 1
    print(f"native dependency wiring consistent ({len(mismatches)} allowlisted mismatches)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
