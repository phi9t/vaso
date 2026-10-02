#!/usr/bin/env python3
"""Regression tests for native_dep_wiring_check.py."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("native_dep_wiring_check.py")
SPEC = importlib.util.spec_from_file_location("native_dep_wiring_check", SCRIPT)
assert SPEC is not None
check = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = check
SPEC.loader.exec_module(check)

MODULE = """
python_native(
    name = "python_native",
    version = "3.14.5",
    zlib_prefix_file = "@zlib_ng_native//:prefix_path.txt",
)

python_native(
    name = "python_313_native",
    version = "3.13.13",
    zlib_prefix_file = "@zlib_ng_native//:prefix_path.txt",
)

python_venv_native(
    name = "python_venv_native",
    python_prefix_file = "@python_native//:prefix_path.txt",
)

meson_native(
    name = "meson_native",
    python_prefix_file = "@python_313_native//:prefix_path.txt",
    python_venv_prefix_file = "@python_venv_native//:prefix_path.txt",
    helper_prefix_file = "@not_a_graph_dep_native//:prefix_path.txt",
)
"""


def graph() -> dict:
    nodes = [
        {"package": "zlib-ng", "version": "2.3.3", "spack_hash": "z", "deps": []},
        {"package": "python", "version": "3.13.13", "spack_hash": "p", "deps": [
            {"hash": "z", "name": "zlib-ng", "deptypes": ["build", "link"]}]},
        {"package": "python-venv", "version": "1.0", "spack_hash": "v", "deps": [
            {"hash": "p", "name": "python", "deptypes": ["build", "run"]}]},
        {"package": "meson", "version": "1.11.1", "spack_hash": "m", "deps": [
            {"hash": "p", "name": "python", "deptypes": ["build", "run"]},
            {"hash": "v", "name": "python-venv", "deptypes": ["build", "run"]}]},
    ]
    return {"nodes": nodes}


OVERRIDES = {
    "native": {
        "zlib-ng": "@zlib_ng_native//:lib",
        "python@3.13.13": "@python_313_native//:lib",
        "python@3.14.5": "@python_native//:lib",
        "python-venv": "@python_venv_native//:lib",
        "meson": "@meson_native//:lib",
        "not-a-graph-dep": "@not_a_graph_dep_native//:lib",
    }
}


class NativeDepWiringCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _mismatches(self, module: str = MODULE, overrides: dict = OVERRIDES) -> list[str]:
        return sorted(str(m) for m in check.wiring_mismatches(check.parse_module(module), overrides["native"], graph()))

    def test_wrong_python_wiring_is_reported(self) -> None:
        self.assertEqual(
            self._mismatches(),
            ["python_venv_native.python_prefix_file -> @python_native, graph python@3.13.13 is served by @python_313_native"],
        )

    def test_consistent_wiring_passes(self) -> None:
        fixed = MODULE.replace(
            'name = "python_venv_native",\n    python_prefix_file = "@python_native',
            'name = "python_venv_native",\n    python_prefix_file = "@python_313_native',
        )
        self.assertEqual(self._mismatches(fixed), [])

    def test_preferred_python_repo_can_satisfy_build_tool_python_edges(self) -> None:
        fixed = MODULE.replace(
            'name = "python_venv_native",\n    python_prefix_file = "@python_native',
            'name = "python_venv_native",\n    python_prefix_file = "@python_313_native',
        )
        self.assertEqual(
            check.wiring_mismatches(
                check.parse_module(fixed),
                OVERRIDES["native"],
                graph(),
                preferred_python_repo="python_313_native",
            ),
            [],
        )

    def test_unversioned_key_serving_every_version_is_consistent(self) -> None:
        overrides = {"native": dict(OVERRIDES["native"])}
        del overrides["native"]["python@3.13.13"], overrides["native"]["python@3.14.5"]
        overrides["native"]["python"] = "@python_native//:lib"
        module = MODULE.replace("@python_313_native//", "@python_native//")
        self.assertEqual(self._mismatches(module, overrides), [])

    def test_lean_graph_consumers_must_not_wire_python_native(self) -> None:
        module = (
            MODULE.replace(
                'name = "python_venv_native",\n    python_prefix_file = "@python_native',
                'name = "python_venv_native",\n    python_prefix_file = "@python_313_native',
            )
            + """
re2c_native(
    name = "re2c_native",
    python_prefix_file = "@python_native//:prefix_path.txt",
)

llvm_native(
    name = "llvm_native",
    python_prefix_file = "@python_native//:prefix_path.txt",
)
"""
        )
        overrides = {"native": dict(OVERRIDES["native"])}
        overrides["native"]["re2c"] = "@re2c_native//:lib"
        overrides["native"]["llvm"] = "@llvm_native//:lib"
        lean_graph = graph()
        lean_graph["nodes"].append({
            "package": "re2c",
            "version": "4.4",
            "spack_hash": "r",
            "deps": [],
        })

        self.assertEqual(
            [
                str(wiring)
                for wiring in check.forbidden_lean_python_native_wiring(
                    check.parse_module(module),
                    overrides["native"],
                    lean_graph,
                )
            ],
            [
                "lean graph native repo re2c_native.python_prefix_file still wires @python_native",
            ],
        )

    def _run(self, allowlist: str, module: str = MODULE, graph_doc: dict | None = None, overrides: dict = OVERRIDES) -> int:
        (self.root / "MODULE.bazel").write_text(module, encoding="utf-8")
        (self.root / "native_overrides.json").write_text(json.dumps(overrides), encoding="utf-8")
        (self.root / "graph.json").write_text(json.dumps(graph_doc or graph()), encoding="utf-8")
        (self.root / "migration_ledger.json").write_text(
            json.dumps({"frontier_graphs": {"py-torch": {"graph": "graph.json"}}}), encoding="utf-8")
        (self.root / "allow.txt").write_text(allowlist, encoding="utf-8")
        return check.main([
            "--module", str(self.root / "MODULE.bazel"),
            "--native-overrides", str(self.root / "native_overrides.json"),
            "--ledger", str(self.root / "migration_ledger.json"),
            "--allowlist", str(self.root / "allow.txt"),
        ])

    def test_explicit_graph_replaces_ledger_frontier_graph(self) -> None:
        (self.root / "MODULE.bazel").write_text(MODULE, encoding="utf-8")
        (self.root / "native_overrides.json").write_text(json.dumps(OVERRIDES), encoding="utf-8")
        (self.root / "old_graph.json").write_text(json.dumps(graph()), encoding="utf-8")
        new_graph = graph()
        for node in new_graph["nodes"]:
            if node["package"] == "python":
                node["version"] = "3.14.5"
        (self.root / "new_graph.json").write_text(json.dumps(new_graph), encoding="utf-8")
        (self.root / "migration_ledger.json").write_text(
            json.dumps({"frontier_graphs": {"py-torch": {"graph": "old_graph.json"}}}), encoding="utf-8")
        (self.root / "allow.txt").write_text("", encoding="utf-8")

        rc = check.main([
            "--module", str(self.root / "MODULE.bazel"),
            "--native-overrides", str(self.root / "native_overrides.json"),
            "--ledger", str(self.root / "migration_ledger.json"),
            "--allowlist", str(self.root / "allow.txt"),
            "--graph", str(self.root / "new_graph.json"),
        ])

        self.assertEqual(rc, 0)

    def test_allowlist_ratchet(self) -> None:
        module = """
python_native(
    name = "python_native",
    version = "3.14.5",
)

new_zlib_native(
    name = "new_zlib_native",
)

old_zlib_native(
    name = "old_zlib_native",
)

foo_native(
    name = "foo_native",
    zlib_ng_prefix_file = "@old_zlib_native//:prefix_path.txt",
)
"""
        graph_doc = {
            "nodes": [
                {"package": "zlib-ng", "version": "2.3.3", "spack_hash": "z", "deps": []},
                {"package": "foo", "version": "1.0", "spack_hash": "f", "deps": [
                    {"hash": "z", "name": "zlib-ng", "deptypes": ["build", "link"]}]},
            ]
        }
        overrides = {
            "native": {
                "zlib-ng@2.2.5": "@old_zlib_native//:lib",
                "zlib-ng@2.3.3": "@new_zlib_native//:lib",
                "foo": "@foo_native//:lib",
            }
        }
        self.assertEqual(self._run("", module, graph_doc, overrides), 1)
        self.assertEqual(
            self._run("foo_native.zlib_ng_prefix_file @new_zlib_native  # ticket 08\n", module, graph_doc, overrides),
            0,
        )
        self.assertEqual(
            self._run(
                "foo_native.zlib_ng_prefix_file @new_zlib_native\n"
                "meson_native.python_prefix_file @python_313_native\n",
                module,
                graph_doc,
                overrides,
            ),
            1,
            "a stale entry (already consistent wiring) must fail",
        )


if __name__ == "__main__":
    unittest.main()
