#!/usr/bin/env python3
"""Guard that canonical lock repos are imported by MODULE.bazel."""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path


STRING_RE = re.compile(r'"(spack_[^"]+)"')


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


def _quoted_spack_repos(text: str) -> set[str]:
    return set(STRING_RE.findall(text))


class ModuleSpackRepoImportsTest(unittest.TestCase):
    def test_lock_repos_are_ensured_and_imported(self) -> None:
        if len(sys.argv) != 3:
            self.fail("usage: module_spack_repo_imports_test.py MODULE.bazel spack_graph.lock.json")
        module_text = Path(sys.argv[1]).read_text(encoding="utf-8")
        lock = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        lock_repos = set(lock["packages"])

        graph_body = _extract_call_body(module_text, "spack_graph.graph(")
        use_repo_body = _extract_call_body(module_text, "use_repo(\n    spack_graph,")
        ensured = _quoted_spack_repos(graph_body)
        imported = _quoted_spack_repos(use_repo_body)

        missing_ensures = sorted(lock_repos - ensured)
        missing_imports = sorted(lock_repos - imported)
        self.assertEqual(missing_ensures, [], "spack_graph.graph ensure_repos is missing lock repos")
        self.assertEqual(missing_imports, [], "use_repo(spack_graph, ...) is missing lock repos")


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
