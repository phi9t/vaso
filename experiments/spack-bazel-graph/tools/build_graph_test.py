#!/usr/bin/env python3
"""Regression tests for build_graph.py."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).with_name("build_graph.py")
SPEC = importlib.util.spec_from_file_location("build_graph", SCRIPT)
assert SPEC is not None
build_graph = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.path.insert(0, str(SCRIPT.parent))
sys.modules[SPEC.name] = build_graph
SPEC.loader.exec_module(build_graph)


class BuildGraphTest(unittest.TestCase):
    def test_spack_spec_json_reports_non_json_stdout_and_stderr(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["spack", "spec", "--json", "cuda@12.9.1"],
            returncode=0,
            stdout="",
            stderr="==> Found no new compilers\n",
        )

        with mock.patch.object(build_graph.subprocess, "run", return_value=completed):
            with self.assertRaises(SystemExit) as ctx:
                build_graph.spack_spec_json("spack", "cuda@12.9.1", 1)

        msg = str(ctx.exception)
        self.assertIn("spack spec produced invalid JSON", msg)
        self.assertIn("stdout was empty", msg)
        self.assertIn("stderr:", msg)
        self.assertIn("Found no new compilers", msg)

    def test_nodes_keep_concrete_parameters_and_known_perl_strategy(self) -> None:
        spec = {
            "spec": {
                "nodes": [
                    {
                        "name": "perl-data-dumper",
                        "version": "2.173",
                        "hash": "perlhash",
                        "parameters": {
                            "build_system": "perl",
                            "shared": True,
                        },
                        "dependencies": [],
                    },
                    {
                        "name": "boost",
                        "version": "1.90.0",
                        "hash": "boosthash",
                        "parameters": {
                            "build_system": "generic",
                            "shared": True,
                            "iostreams": False,
                            "cxxstd": "11",
                        },
                        "dependencies": [
                            {
                                "name": "perl-data-dumper",
                                "hash": "perlhash",
                                "parameters": {"deptypes": ["build"]},
                            }
                        ],
                    },
                ]
            }
        }

        graph = build_graph.build_graph(spec, "/unused/spack", resolve_prefix=False)
        by_name = {node["package"]: node for node in graph["nodes"]}

        self.assertEqual(
            by_name["perl-data-dumper"]["native_strategy"],
            "perl Makefile.PL && make install (captured ExtUtils::MakeMaker)",
        )
        self.assertEqual(by_name["boost"]["parameters"]["shared"], True)
        self.assertEqual(by_name["boost"]["parameters"]["iostreams"], False)
        self.assertEqual(by_name["boost"]["parameters"]["cxxstd"], "11")

    def test_exact_version_native_override_marks_only_matching_node_native(self) -> None:
        spec = {
            "spec": {
                "nodes": [
                    {
                        "name": "nlohmann-json",
                        "version": "3.11.2",
                        "hash": "oldhash",
                        "parameters": {"build_system": "cmake"},
                        "dependencies": [],
                    },
                    {
                        "name": "nlohmann-json",
                        "version": "3.11.3",
                        "hash": "newhash",
                        "parameters": {"build_system": "cmake"},
                        "dependencies": [],
                    },
                ]
            }
        }

        graph = build_graph.build_graph(
            spec,
            "/unused/spack",
            resolve_prefix=False,
            native_overrides={"nlohmann-json@3.11.3": "@nlohmann_json_native//:lib"},
        )
        by_hash = {node["spack_hash"]: node for node in graph["nodes"]}

        self.assertEqual(by_hash["oldhash"]["status"], "spack")
        self.assertNotIn("native_prefix", by_hash["oldhash"])
        self.assertEqual(by_hash["newhash"]["status"], "native")
        self.assertEqual(by_hash["newhash"]["native_prefix"], "@nlohmann_json_native//:lib")

    def test_lean_font_resources_rejects_default_resource_set(self) -> None:
        graph = {
            "nodes": [
                {
                    "package": "font-util",
                    "parameters": {
                        "fonts": [
                            "adobe-100dpi",
                            "encodings",
                        ],
                    },
                },
            ],
        }

        with self.assertRaises(SystemExit) as ctx:
            build_graph.enforce_lean_font_resources(graph)

        self.assertIn("font-util lean resource check failed", str(ctx.exception))
        self.assertIn("adobe-100dpi", str(ctx.exception))

    def test_lean_font_resources_accepts_only_encodings(self) -> None:
        graph = {
            "nodes": [
                {
                    "package": "font-util",
                    "parameters": {
                        "fonts": ["encodings"],
                    },
                },
            ],
        }

        build_graph.enforce_lean_font_resources(graph)

    def test_lean_font_resources_rejects_broad_resource_package_node(self) -> None:
        graph = {
            "nodes": [
                {
                    "package": "font-util",
                    "parameters": {
                        "fonts": ["encodings"],
                    },
                },
                {
                    "package": "font-adobe-100dpi",
                    "parameters": {},
                },
            ],
        }

        with self.assertRaises(SystemExit) as ctx:
            build_graph.enforce_lean_font_resources(graph)

        self.assertIn("broad font-resource package", str(ctx.exception))
        self.assertIn("font-adobe-100dpi", str(ctx.exception))

    def test_committed_pytorch_graph_keeps_font_resources_lean(self) -> None:
        graph_path = SCRIPT.parent.parent / "py_torch_build_graph.json"
        graph = json.loads(graph_path.read_text())

        font_util_nodes = [
            node for node in graph.get("nodes", [])
            if node.get("package") == "font-util"
        ]
        self.assertEqual(len(font_util_nodes), 1)
        self.assertEqual(
            font_util_nodes[0].get("parameters", {}).get("fonts"),
            build_graph.LEAN_FONT_RESOURCES,
        )
        broad_packages = [
            node.get("package") for node in graph.get("nodes", [])
            if node.get("package") in build_graph.BROAD_FONT_RESOURCE_PACKAGES
        ]
        self.assertEqual(broad_packages, [])
        self.assertEqual(graph.get("n_nodes"), len(graph.get("nodes", [])))


if __name__ == "__main__":
    unittest.main()
