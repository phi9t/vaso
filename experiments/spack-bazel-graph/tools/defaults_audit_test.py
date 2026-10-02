#!/usr/bin/env python3
"""Audit known silent defaults that must stay explicit in repository policy."""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = EXPERIMENT_ROOT / "tools"
RUN_SH = EXPERIMENT_ROOT / "run.sh"
COMPILER_PATHWAYS_JSON = TOOLS_DIR / "compiler_pathways.json"
SPACK_DIST_BZL = TOOLS_DIR / "spack_dist.bzl"
RUNTIME_COMPILER_ASSERTIONS = TOOLS_DIR / "runtime_compiler_assertions.py"
ABI_INVARIANTS = TOOLS_DIR / "abi_invariants.py"
GATE_INSULA_SH = EXPERIMENT_ROOT / "scripts/agents/triumvirate-gate-insula.sh"
WORKLOADS_RUNNER = EXPERIMENT_ROOT / "workloads/run_workloads.py"


def _import_module(path: Path, name: str) -> object:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _function_body(text: str, name: str) -> str:
    match = re.search(rf"(?m)^{re.escape(name)}\(\) \{{\n", text)
    if match is None:
        raise AssertionError(f"{name}() is missing from run.sh")
    start = match.end()
    end = re.search(r"(?m)^\}\s*$", text[start:])
    if end is None:
        raise AssertionError(f"{name}() body is not closed")
    return text[start : start + end.start()]


def _case_branch(text: str, header: str, next_header: str) -> str:
    start = text.index(header)
    end = text.index(next_header, start)
    return text[start:end]


def _literal_assignments(module_path: Path) -> dict[str, object]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    values: dict[str, object] = {}
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
            continue
        target = stmt.targets[0]
        if isinstance(target, ast.Name):
            try:
                values[target.id] = ast.literal_eval(stmt.value)
            except (ValueError, SyntaxError):
                pass
    return values


class DefaultsAuditTest(unittest.TestCase):
    def test_spack_python_version_is_required(self) -> None:
        text = SPACK_DIST_BZL.read_text(encoding="utf-8")

        self.assertRegex(
            text,
            r"(?ms)^\s*python:\n(?:\s+[^\n]*\n){0,8}\s+require:\s+[\"']@3\.13\.13[\"']",
        )

    def test_compiler_pathway_profiles_are_declared(self) -> None:
        policy = json.loads(COMPILER_PATHWAYS_JSON.read_text(encoding="utf-8"))

        self.assertEqual(policy["default_profile"], "torch")
        self.assertEqual(policy["rootfs"]["gcc"]["cc"], "/usr/bin/gcc")
        self.assertEqual(policy["rootfs"]["gcc"]["cxx"], "/usr/bin/g++")
        self.assertEqual(policy["llvm"]["prefix"], "/usr/lib/llvm-23")
        self.assertEqual(policy["llvm"]["clang"], "/usr/lib/llvm-23/bin/clang")
        self.assertEqual(policy["llvm"]["clangxx"], "/usr/lib/llvm-23/bin/clang++")

        profiles = policy["profiles"]
        self.assertEqual(set(profiles), {"torch", "jax"})
        self.assertEqual(
            set(profiles["torch"]["consumers"]),
            {"torch", "torchvision", "torchaudio", "triton"},
        )
        self.assertEqual(set(profiles["jax"]["consumers"]), {"jaxlib"})
        for consumer in ("torch", "torchvision", "torchaudio", "triton"):
            with self.subTest(torch_consumer=consumer):
                spec = profiles["torch"]["consumers"][consumer]
                self.assertEqual(spec["allowed_host_cc_paths"], ["/usr/bin/gcc"])
                self.assertEqual(spec["allowed_host_cxx_paths"], ["/usr/bin/g++"])

    def test_runtime_compiler_defaults_are_pinned_in_policy_gate_and_workloads(self) -> None:
        runtime = _import_module(RUNTIME_COMPILER_ASSERTIONS, "runtime_compiler_assertions_defaults_audit")
        self.assertEqual(runtime.EXPECTED_CC, "/usr/bin/gcc")
        self.assertEqual(runtime.EXPECTED_CXX, "/usr/bin/g++")

        gate = GATE_INSULA_SH.read_text(encoding="utf-8")
        self.assertIn('export CC="/usr/bin/gcc"', gate)
        self.assertIn('export CXX="/usr/bin/g++"', gate)
        torch_gate = _case_branch(gate, 'if [[ "$profile" == "torch" ]]; then', "else\n  jax_runtime_args=()")
        self.assertIn("tools/runtime_compiler_assertions.py", torch_gate)

        workload_env = WORKLOADS_RUNNER.read_text(encoding="utf-8")
        self.assertIn('"CC": "/usr/bin/gcc"', workload_env)
        self.assertIn('"CXX": "/usr/bin/g++"', workload_env)

    def test_torch_profile_openmp_runtime_policy_is_rootfs_libgomp(self) -> None:
        abi = _import_module(ABI_INVARIANTS, "abi_invariants_defaults_audit")
        maps = "\n".join(
            [
                "7f /usr/lib/x86_64-linux-gnu/libstdc++.so.6.0.33",
                "7f /usr/lib/x86_64-linux-gnu/libgomp.so.1.0.0",
                "7f /run/nvidia-driver/lib/libcuda.so.1",
                "7f /usr/local/cuda/lib64/libcudart.so.13",
            ]
        )
        self.assertEqual(abi.check_probe_maps(maps, line="cu130"), [])

        forbidden = maps + "\n7f /usr/lib/llvm-23/lib/libomp.so"
        self.assertIn(
            "I5 maps: forbidden OpenMP runtime mapped /usr/lib/llvm-23/lib/libomp.so",
            abi.check_probe_maps(forbidden, line="cu130"),
        )

        gate = GATE_INSULA_SH.read_text(encoding="utf-8")
        torch_gate = _case_branch(gate, 'if [[ "$profile" == "torch" ]]; then', "else\n  jax_runtime_args=()")
        self.assertIn("tools/abi_invariants.py", torch_gate)
        self.assertIn("--profile torch", torch_gate)
        self.assertIn("--probe", torch_gate)

    def test_non_fetch_bazel_phases_use_repository_disable_download(self) -> None:
        # B5: the offline policy lives in scripts/insula/insula.sh, which
        # injects --repository_disable_download into every Bazel invocation
        # unless the stage is marked `--phase fetch`. run.sh must route
        # through that wrapper and mark only the explicit prefetch helper as
        # a fetch phase.
        run_sh = RUN_SH.read_text(encoding="utf-8")
        self.assertIn('INSULA_SH="$REPO_ROOT/scripts/insula/insula.sh"', run_sh)
        offline_body = _function_body(run_sh, "bazel_insula")
        self.assertNotIn("--phase fetch", offline_body)
        fetch_stages = re.findall(r'run_stage "[^"]*" insula --phase fetch ', run_sh)
        self.assertEqual(len(fetch_stages), 1, "only the explicit prefetch helper may run a fetch phase")
        self.assertIn('run_stage "bazel-fetch-$cmd" insula --phase fetch', run_sh)

    def test_max_jobs_defaults_derive_from_nproc_and_avoid_small_literals(self) -> None:
        run_sh = RUN_SH.read_text(encoding="utf-8")
        default_jobs = _function_body(run_sh, "resolve_default_jobs")
        self.assertIn('jobs="$(nproc 2>/dev/null || echo 1)"', default_jobs)

        for name in ("pytorch", "torchvision", "torchaudio", "jaxlib"):
            with self.subTest(resolver=name):
                body = _function_body(run_sh, f"resolve_{name}_max_jobs")
                self.assertIn("resolve_default_jobs", body)
                cap_name = f"{name.upper()}_MAX_JOBS_CAP"
                self.assertIn(f"{cap_name}=96", run_sh)
                self.assertIn(f'"--action_env=VASO_{name.upper()}_MAX_JOBS=$VASO_{name.upper()}_MAX_JOBS_RESOLVED"', run_sh)

        for label, flag in (
            ("pytorch", "--//native/pytorch:max_jobs=$VASO_PYTORCH_MAX_JOBS_RESOLVED"),
            ("torchvision", "--//native/torchvision:max_jobs=$VASO_TORCHVISION_MAX_JOBS_RESOLVED"),
            ("torchaudio", "--//native/torchaudio:max_jobs=$VASO_TORCHAUDIO_MAX_JOBS_RESOLVED"),
        ):
            with self.subTest(action_flag=label):
                self.assertIn(f'"{flag}"', run_sh)

        for path in (
            EXPERIMENT_ROOT / "native/pytorch/BUILD.bazel",
            EXPERIMENT_ROOT / "native/torchvision/BUILD.bazel",
            EXPERIMENT_ROOT / "native/torchaudio/BUILD.bazel",
        ):
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"build_setting_default\s*=\s*(?P<jobs>[0-9]+)", text):
                jobs = int(match.group("jobs"))
                self.assertGreaterEqual(jobs, 16, f"{path}: max_jobs default must not be a small literal")

        for plan in (
            EXPERIMENT_ROOT / "native/pytorch/plan.py",
            EXPERIMENT_ROOT / "native/torchvision/plan.py",
            EXPERIMENT_ROOT / "native/torchaudio/plan.py",
        ):
            values = _literal_assignments(plan)
            self.assertEqual(values.get("MAX_JOBS_CAP"), 96, plan)
            text = plan.read_text(encoding="utf-8")
            self.assertIn("detect_nproc()", text)
            self.assertIn("min(detect_nproc(), MAX_JOBS_CAP)", text)

        jaxlib_values = _literal_assignments(EXPERIMENT_ROOT / "native/jaxlib/plan.py")
        self.assertEqual(jaxlib_values.get("JAXLIB_MAX_JOBS_CAP"), 96)


if __name__ == "__main__":
    unittest.main()
