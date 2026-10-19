#!/usr/bin/env python3
"""Repository rules may only declare environment variables that are constant in the insula.

A variable in a repository rule's `environ` re-runs the rule whenever its value changes.
Per-run, per-commit and resource values (TMPDIR, SOURCE_DATE_EPOCH, MAKE_JOBS, NINJA_JOBS)
made every gate rebuild native dependencies (post-N1-2 no-change rerun, 2026-10-07).
Rule: docs/agents/build-stability.md.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {
    "CC",
    "CFLAGS",
    "CMAKE",
    "CXX",
    "ROOTFS_LLVM_HOME",
    "VASO_BAZEL",
    "VASO_CUDA_HOME",
    "VASO_IN_INSULA",
    "VASO_ROOTFS_BUNDLE_MANIFEST",
}
ALLOWED_PATTERN = re.compile(r"^VASO_NATIVE_[A-Z0-9_]+_TOKEN$")
ENVIRON = re.compile(r"environ\s*=\s*\[([^\]]*)\]")


def declared_environ(text: str) -> list[str]:
    names: list[str] = []
    for match in ENVIRON.finditer(text):
        names.extend(re.findall(r'"([A-Za-z0-9_]+)"', match.group(1)))
    return names


class RepoRuleEnvironGuardTest(unittest.TestCase):
    def test_native_repo_rules_declare_only_constant_env(self) -> None:
        offenders = []
        for path in sorted((ROOT / "native").rglob("*.bzl")):
            for name in declared_environ(path.read_text(encoding="utf-8")):
                if name not in ALLOWED and not ALLOWED_PATTERN.match(name):
                    offenders.append(f"{path.relative_to(ROOT)}: {name}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_guard_rejects_varying_names(self) -> None:
        text = 'x = repository_rule(environ = ["TMPDIR", "MAKE_JOBS", "VASO_IN_INSULA"])'
        bad = [n for n in declared_environ(text) if n not in ALLOWED and not ALLOWED_PATTERN.match(n)]
        self.assertEqual(bad, ["TMPDIR", "MAKE_JOBS"])

    def test_native_repo_rules_do_not_watch_trees(self) -> None:
        # watch_tree on a shared cache made Bazel digest unrelated trees and hit a Skyframe
        # cycle (jaxlib nested cache manifest, 2026-10-07). Watch specific pinned files.
        offenders = [
            str(path.relative_to(ROOT))
            for path in sorted((ROOT / "native").rglob("*.bzl"))
            if "watch_tree(" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
