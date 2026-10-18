#!/usr/bin/env python3
"""Guard that canonical graph repos are imported by MODULE.bazel."""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path


GRAPH_REPO_RE = re.compile(r'"(vaso_[^"]+)"')
COMPAT_REPO_RE = re.compile(r'"(spack_[^"]+)"')


def _extract_call_body(text: str, call_prefix: str) -> str:
    start = text.index(call_prefix) + len(call_prefix)
    depth = 1
    pos = start
    while pos < len(text) and depth:
        char = text[pos]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        pos += 1
    if depth:
        raise ValueError(f"unterminated call: {call_prefix}")
    return text[start : pos - 1]


def _quoted_graph_repos(text: str) -> set[str]:
    return set(GRAPH_REPO_RE.findall(text))


def _quoted_compat_repos(text: str) -> set[str]:
    return set(COMPAT_REPO_RE.findall(text))


class ModuleGraphRepoImportsTest(unittest.TestCase):
    def test_lock_repos_are_ensured_and_imported(self) -> None:
        if len(sys.argv) != 3:
            self.fail("usage: module_graph_repo_imports_test.py MODULE.bazel locks/<profile>.lock.json")
        module_text = Path(sys.argv[1]).read_text(encoding="utf-8")
        lock = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        lock_repos = set(lock["packages"])

        graph_body = _extract_call_body(module_text, "vaso_graph.graph(")
        use_repo_body = _extract_call_body(module_text, "use_repo(\n    vaso_graph,")
        ensured = _quoted_graph_repos(graph_body)
        imported = _quoted_graph_repos(use_repo_body)
        compat_imported = _quoted_compat_repos(module_text)

        missing_ensures = sorted(lock_repos - ensured)
        missing_imports = sorted(lock_repos - imported)
        missing_compat = sorted("spack_" + repo.removeprefix("vaso_") for repo in lock_repos if "spack_" + repo.removeprefix("vaso_") not in compat_imported)
        self.assertEqual(missing_ensures, [], "vaso_graph.graph ensure_repos is missing lock repos")
        self.assertEqual(missing_imports, [], "use_repo(vaso_graph, ...) is missing lock repos")
        self.assertEqual(missing_compat, [], "temporary compatibility use_repo block is missing aliases")


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
