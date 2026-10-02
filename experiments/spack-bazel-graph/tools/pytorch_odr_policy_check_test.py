#!/usr/bin/env python3
"""Regression tests for pytorch_odr_policy_check.py."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("pytorch_odr_policy_check.py")
EXPERIMENT_ROOT = SCRIPT.parent.parent
PLAN = EXPERIMENT_ROOT / "native" / "pytorch" / "plan.py"
SPEC = importlib.util.spec_from_file_location("pytorch_odr_policy_check", SCRIPT)
assert SPEC is not None
checker = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)


BASE_OVERRIDES = {
    "native": {
        "boost@1.90.0": "@boost_native//:lib",
        "protobuf@21.12": "@protobuf_native//:lib",
        "py-protobuf@4.21.12": "@py_protobuf_native//:lib",
        "zlib-ng": "@zlib_ng_native//:lib",
    }
}


def _graph(*nodes: tuple[str, str]) -> dict:
    return {"nodes": [{"package": name, "version": version} for name, version in nodes]}


class PyTorchOdrPolicyCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, name: str, data: object) -> Path:
        path = self.tmp / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def _plan(self, mutation: str) -> Path:
        """A plan module that execs the real plan.py, then applies `mutation`."""
        # A fresh file name per fixture: reusing one path lets Python serve a
        # stale __pycache__ entry for the previous mutation.
        self._plans = getattr(self, "_plans", 0) + 1
        path = self.tmp / f"plan_fixture_{self._plans}.py"
        path.write_text(
            "from pathlib import Path\n"
            f"exec(compile(Path({str(PLAN)!r}).read_text(encoding='utf-8'), {str(PLAN)!r}, 'exec'))\n"
            f"{mutation}\n",
            encoding="utf-8",
        )
        return path

    def _run(
        self,
        *,
        plan: Path | None = None,
        graph: dict | None = None,
        overrides: dict | None = None,
        ledger_graph: dict | None = None,
        extra_args: list[str] | None = None,
    ) -> tuple[int, str]:
        overrides_path = self._write("native_overrides.json", overrides or BASE_OVERRIDES)
        ledger_graph_path = self._write(
            "lean_graph.json",
            ledger_graph or _graph(("protobuf", "3.13.0"), ("py-protobuf", "3.13.0"), ("boost", "1.90.0")),
        )
        ledger_path = self._write(
            "migration_ledger.json",
            {"frontier_graphs": {"py-torch": {"graph": ledger_graph_path.name}}},
        )
        argv = [
            "--plan",
            str(plan or PLAN),
            "--native-overrides",
            str(overrides_path),
            "--ledger",
            str(ledger_path),
        ]
        if graph is not None:
            argv += ["--graph", str(self._write("graph.json", graph))]
        argv += extra_args or []
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
            try:
                code = checker.main(argv)
            except SystemExit as exc:  # argparse or unhandled input errors
                code = exc.code if isinstance(exc.code, int) else 1
        return code, stderr.getvalue()

    def _overrides_with(self, **extra: str) -> dict:
        native = dict(BASE_OVERRIDES["native"])
        native.update(extra)
        return {"native": native}

    def test_baseline_passes(self) -> None:
        code, err = self._run()
        self.assertEqual(code, 0, err)

    def test_mixed_protobuf_family_is_rejected(self) -> None:
        code, err = self._run(graph=_graph(("protobuf", "21.12"), ("py-protobuf", "3.13.0")))
        self.assertEqual(code, 1)
        self.assertIn("ODR family protobuf", err)

    def test_aligned_protobuf_family_is_accepted(self) -> None:
        code, err = self._run(graph=_graph(("protobuf", "21.12"), ("py-protobuf", "4.21.12")))
        self.assertEqual(code, 0, err)

    def test_py_protobuf_5x_family_is_normalized(self) -> None:
        code, err = self._run(graph=_graph(("protobuf", "26.1"), ("py-protobuf", "5.26.1")))
        self.assertEqual(code, 0, err)

    def test_grpc_and_abseil_nodes_are_rejected(self) -> None:
        for package in ("grpc", "grpc-cpp", "py-grpcio", "abseil-cpp"):
            with self.subTest(package=package):
                code, err = self._run(graph=_graph((package, "1.0.0")))
                self.assertEqual(code, 1)
                self.assertIn(package, err)

    def test_range_override_key_is_rejected(self) -> None:
        code, err = self._run(overrides=self._overrides_with(**{"protobuf@3:": "@x//:lib"}))
        self.assertEqual(code, 1)
        self.assertIn("protobuf@3:", err)

    def test_extra_exact_abseil_override_is_rejected(self) -> None:
        code, err = self._run(overrides=self._overrides_with(**{"abseil-cpp@20240116": "@x//:lib"}))
        self.assertEqual(code, 1)
        self.assertIn("abseil-cpp@20240116", err)

    def test_second_protobuf_override_is_rejected(self) -> None:
        code, err = self._run(overrides=self._overrides_with(**{"protobuf@3.13.0": "@x//:lib"}))
        self.assertEqual(code, 1)
        self.assertIn("protobuf@3.13.0", err)

    def test_exact_py_protobuf_override_is_accepted(self) -> None:
        overrides = self._overrides_with(**{"py-protobuf@4.21.12": "@py_protobuf_native//:lib"})
        code, err = self._run(overrides=overrides)
        self.assertEqual(code, 0, err)

    def test_unversioned_odr_overrides_are_rejected(self) -> None:
        for key in ("protobuf", "boost"):
            with self.subTest(key=key):
                code, err = self._run(overrides=self._overrides_with(**{key: "@x//:lib"}))
                self.assertEqual(code, 1)
                self.assertIn(repr(key), err)

    def test_missing_selected_override_is_rejected(self) -> None:
        native = dict(BASE_OVERRIDES["native"])
        del native["protobuf@21.12"]
        code, err = self._run(overrides={"native": native})
        self.assertEqual(code, 1)
        self.assertIn("protobuf@21.12", err)

    def test_missing_graph_file_is_input_error(self) -> None:
        code, err = self._run(extra_args=["--graph", str(self.tmp / "missing.json")])
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err)
        self.assertIn("missing.json", err)

    def test_ledger_graph_is_checked(self) -> None:
        code, err = self._run(ledger_graph=_graph(("protobuf", "3.13.0"), ("protobuf", "21.12")))
        self.assertEqual(code, 1)
        self.assertIn("lean_graph.json", err)

    def test_real_plan_admits_exactly_protobuf_boost_and_py_protobuf(self) -> None:
        plan = checker._load_module("real_plan", PLAN)
        expected, errors = checker.expected_odr_overrides(plan)
        self.assertEqual(errors, [])
        self.assertEqual(
            expected,
            {
                "boost@1.90.0": "@boost_native//:lib",
                "protobuf@21.12": "@protobuf_native//:lib",
                "py-protobuf@4.21.12": "@py_protobuf_native//:lib",
            },
        )

    def test_plan_cannot_admit_a_provider_it_does_not_select(self) -> None:
        plan = self._plan(
            'ODR_PROVIDER_FAMILIES["abseil-cpp"]["native_override_key"] = "abseil-cpp@20240116"\n'
            'ODR_PROVIDER_FAMILIES["abseil-cpp"]["native_override_label"] = "@x//:lib"'
        )
        code, err = self._run(plan=plan, overrides=self._overrides_with(**{"abseil-cpp@20240116": "@x//:lib"}))
        self.assertEqual(code, 1)
        self.assertIn("abseil-cpp@20240116", err)

    def test_unblocked_py_protobuf_requires_label(self) -> None:
        plan = self._plan('del ODR_PROVIDER_FAMILIES["protobuf"]["python_native_override_label"]')
        code, err = self._run(plan=plan)
        self.assertEqual(code, 1)
        self.assertIn("python_native_override_label", err)
        self.assertNotIn("Traceback", err)

    def test_unblocked_py_protobuf_is_admitted(self) -> None:
        plan = self._plan(
            'ODR_PROVIDER_FAMILIES["protobuf"]["python_native_override_status"] = "accepted"\n'
            'ODR_PROVIDER_FAMILIES["protobuf"]["python_native_override_label"] = "@py_protobuf_native//:lib"'
        )
        overrides = self._overrides_with(**{"py-protobuf@4.21.12": "@py_protobuf_native//:lib"})
        code, err = self._run(plan=plan, overrides=overrides)
        self.assertEqual(code, 0, err)
        native = dict(BASE_OVERRIDES["native"])
        del native["py-protobuf@4.21.12"]
        code, err = self._run(plan=plan, overrides={"native": native})
        self.assertEqual(code, 1)
        self.assertIn("missing native override py-protobuf@4.21.12", err)

    def test_override_label_mismatch_is_rejected(self) -> None:
        code, err = self._run(overrides=self._overrides_with(**{"protobuf@21.12": "@other//:lib"}))
        self.assertEqual(code, 1)
        self.assertIn("differs from the plan", err)

    def test_variant_and_compiler_keys_are_rejected(self) -> None:
        for key in ("protobuf+shared@21.12", "protobuf%gcc@21.12", "Protobuf@21.12", "protobuf @21.12"):
            with self.subTest(key=key):
                code, err = self._run(overrides=self._overrides_with(**{key: "@x//:lib"}))
                self.assertEqual(code, 1)
                self.assertIn(repr(key), err)

    def test_plan_relation_violations_are_rejected(self) -> None:
        cases = {
            "gitlink": (
                'SOURCE_DEPENDENCY_POLICY["upstream_source_evidence"]["pytorch"]'
                '["submodule_gitlinks"]["third_party/protobuf"] = "0" * 40',
                "gitlink",
            ),
            "peel": (
                'SOURCE_DEPENDENCY_POLICY["upstream_source_evidence"]["protobuf"]'
                '["spack_style_tag_peeled_commit"] = "0" * 40',
                "does not peel",
            ),
            "contract": (
                'SOURCE_DEPENDENCY_POLICY["dependency_contract"]["boost"]["provider"] = "boost@1.89.0"',
                "dependency_contract boost",
            ),
            "rejected role": (
                'REJECTED_ODR_PREFIX_KEYS = frozenset({"grpc", "grpc-cpp", "py-grpcio", "abseil-cpp"})',
                "boost is a rejected ODR provider",
            ),
            "family": (
                'ODR_PROVIDER_FAMILIES["protobuf"]["selected_python_provider"] = "py-protobuf@4.25.1"\n'
                'SOURCE_DEPENDENCY_POLICY["dependency_contract"]["py-protobuf"]["provider"] = "py-protobuf@4.25.1"',
                "not one family",
            ),
        }
        for name, (mutation, needle) in cases.items():
            with self.subTest(case=name):
                code, err = self._run(plan=self._plan(mutation))
                self.assertEqual(code, 1)
                self.assertIn(needle, err)

    def test_graph_node_only_provider_must_match_graph(self) -> None:
        code, err = self._run(ledger_graph=_graph(("protobuf", "3.13.0"), ("boost", "1.89.0")))
        self.assertEqual(code, 1)
        self.assertIn("boost@1.89.0", err)

    def test_odr_node_without_version_is_rejected(self) -> None:
        code, err = self._run(graph={"nodes": [{"package": "protobuf"}]})
        self.assertEqual(code, 1)
        self.assertIn("no version", err)

    def test_malformed_inputs_are_input_errors(self) -> None:
        bad_plan = self.tmp / "bad_plan.py"
        bad_plan.write_text("def broken(:\n", encoding="utf-8")
        cases = {
            "missing plan": {"plan": self.tmp / "missing_plan.py"},
            "syntax error plan": {"plan": bad_plan},
            "directory plan": {"plan": self.tmp},
            "list graph": {"graph": []},
            "missing overrides": {"extra_args": ["--native-overrides", str(self.tmp / "missing_overrides.json")]},
        }
        for name, kwargs in cases.items():
            with self.subTest(case=name):
                code, err = self._run(**kwargs)
                self.assertEqual(code, 2, err)
                self.assertNotIn("Traceback", err)
                self.assertTrue(err.startswith("input error:"), err)

    def test_list_rooted_ledger_frontier_is_input_error(self) -> None:
        ledger = self._write("bad_ledger.json", {"frontier_graphs": []})
        code, err = self._run(extra_args=["--ledger", str(ledger)])
        self.assertEqual(code, 2, err)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
