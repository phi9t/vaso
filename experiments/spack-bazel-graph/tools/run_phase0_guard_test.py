#!/usr/bin/env python3
"""Guard run.sh phase-0 fetch behavior."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


RUN_SH = Path(__file__).resolve().parents[1] / "run.sh"
REPO_ROOT = Path(__file__).resolve().parents[3]
LAST_RESULT_SH = REPO_ROOT / "scripts" / "insula" / "last-result.sh"
CUDA_BZL = Path(__file__).resolve().parents[1] / "native/cuda/cuda.bzl"
ROOTFS_CUDA_COMPONENT_BZL = (
    Path(__file__).resolve().parents[1] / "native/common/rootfs_cuda_component.bzl"
)
SYNTHETIC_DIR = Path(__file__).resolve().parents[1] / "synthetic"


def _writable_test_root() -> Path:
    raw = os.environ.get("TEST_TMPDIR")
    if raw:
        root = Path(raw) / "run-phase0-guard-test"
    else:
        root = REPO_ROOT / ".scratch" / "run-phase0-guard-test"
    root.mkdir(parents=True, exist_ok=True)
    return root


class RunPhase0GuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = RUN_SH.read_text(encoding="utf-8")

    def test_phase0_does_not_unconditionally_force_all_configurable_repos(self) -> None:
        self.assertIsNone(
            re.search(r"(?m)^\s*bazel_insula\s+fetch\s+--configure\s+--force\s*$", self.text),
            "phase 0 must not globally force every configurable repository",
        )

    def test_phase0_is_stamped_by_rootfs_identity(self) -> None:
        missing = [
            token
            for token in [
                "PHASE0_ROOTFS_STAMP_HOST",
                "rootfs_identity",
                "ROOTFS_MODE",
                "ROOTFS_DIGEST",
            ]
            if token not in self.text
        ]
        self.assertEqual(missing, [])

    def test_phase0_refreshes_only_compiler_config_repos_by_default(self) -> None:
        self.assertIn("@@rules_cc++cc_configure_extension+local_config_cc", self.text)
        self.assertIn("@@rules_cc++cc_configure_extension+local_config_cc_toolchains", self.text)
        self.assertRegex(self.text, r"bazel_insula\s+fetch\s+--force\s+--repo=")

    def test_pinned_prefetch_is_split_from_offline_builds(self) -> None:
        self.assertIn('if [[ "${1:-}" == "--fetch-only" ]]', self.text)
        self.assertIn("SHA256_PINNED_FETCH_REPOS=(", self.text)
        self.assertIn('"@py_absl_py_native"', self.text)
        self.assertIn('"@py_numpy_native"', self.text)
        self.assertIn('"@py_networkx_native"', self.text)
        self.assertIn('"@py_pillow_native"', self.text)
        self.assertIn('"@py_ml_dtypes_native"', self.text)
        self.assertIn('"@py_ply_native"', self.text)
        self.assertIn('"@py_pythran_native"', self.text)
        self.assertIn('"@py_scipy_native"', self.text)
        self.assertIn('"@py_jax_native"', self.text)
        self.assertIn("absl-py-1.4.0.tar.gz", self.text)
        self.assertIn(
            "d2c244d01048ba476e7c080bd2c6df5e141d211de80223460d5b3b8a2a58433d",
            self.text,
        )
        self.assertIn("jax-0.10.2.tar.gz", self.text)
        self.assertIn(
            "bf77428a8c2e6904c4f46d5ab12aa5cfc6cad2179f07f7e4c0fc75ac86ef0639",
            self.text,
        )
        self.assertIn("TrsmUnrolls.inc", self.text)
        self.assertIn(
            "aa05b280fc2419e0378961a534de8c0fb2be5b15cf7638b314a6097863e5634f",
            self.text,
        )
        self.assertIn('"@py_scikit_build_core_native"', self.text)
        self.assertIn('"@py_setuptools_82_native"', self.text)
        self.assertIn('"@py_setuptools_scm_9_native"', self.text)
        self.assertIn('"@torchvision_v0_29_0_source"', self.text)
        self.assertIn('"@torchaudio_v2_11_0_source"', self.text)
        self.assertIn('"@triton_v2_14_0_source"', self.text)
        self.assertIn('"@jax_v0_10_2_source"', self.text)
        self.assertIn('"@jax_v0_10_2_source_archive"', self.text)
        self.assertIn('"@rootfs_bazel_native"', self.text)
        self.assertIn('"@nlohmann_json_native"', self.text)
        self.assertIn('"@py_lit_native"', self.text)
        self.assertIn('"@py_pybind11_native"', self.text)
        self.assertIn('"@sleef_native"', self.text)
        self.assertIn('"@gmake_native"', self.text)
        self.assertIn('"@libxml2_215_native"', self.text)
        self.assertIn("libxml2-2.15.3.tar.xz", self.text)
        self.assertIn(
            "78262a6e7ac170d6528ebfe2efccdf220191a5af6a6cd61ea4a9a9a5042c7a07",
            self.text,
        )
        self.assertIn("VASO_FETCH_REPOS", self.text)
        self.assertIn("NATIVE_SOURCE_DISTDIR_HOST", self.text)
        self.assertIn("NATIVE_SOURCE_DISTDIR_SB", self.text)
        self.assertIn("TRITON_SOURCE_DISTDIR_HOST", self.text)
        self.assertIn("TRITON_SOURCE_DISTDIR_SB", self.text)
        self.assertIn("prefetch_sha256_source()", self.text)
        self.assertIn("prefetch_one_sha256_source()", self.text)
        self.assertIn("sha256sum", self.text)
        self.assertIn("curl", self.text)
        self.assertIn('fetch-only: fetching sha256-pinned source: $repo', self.text)

        offline = re.search(r"bazel_insula\(\) \{(?P<body>.*?)\n\}", self.text, re.S)
        self.assertIsNotNone(offline)
        self.assertNotIn("BAZEL_OFFLINE_ARGS", offline.group("body"))
        self.assertIn('INSULA_SH="$REPO_ROOT/scripts/insula/insula.sh"', self.text)

        self.assertNotIn("bazel_insula_prefetch", self.text)
        fetch_only_mode = self.text.index('if [[ "${1:-}" == "--fetch-only" ]]')
        fetch_only_start = self.text.index('if [[ "${VASO_FETCH:-0}" == "1" ]]; then', fetch_only_mode)
        next_mode_branch = self.text.index('if [[ "${1:-}" == "--jaxlib-prefetch" ]]')
        fetch_only = self.text[fetch_only_start:next_mode_branch]
        self.assertIn('fetch-only: ok repos=${#FETCH_REPOS[@]}', fetch_only)
        self.assertNotRegex(fetch_only, r"\bbazel_insula\b")
        self.assertIn("insula_network_fetch --sha256 \"$sha256\" -- curl", self.text)

    def test_libxml2_parity_target_follows_locked_version(self) -> None:
        self.assertIn('SPACK_LIBXML2_VERSION="$(lock_version spack_libxml2)"', self.text)
        self.assertIn('libxml2_parity_target="//synthetic:libxml2_abi_parity"', self.text)
        self.assertIn('if [[ "$SPACK_LIBXML2_VERSION" == "2.15.3" ]]; then', self.text)
        self.assertIn('libxml2_parity_target="//synthetic:libxml2_215_abi_parity"', self.text)
        self.assertIn('bazel_insula test "$libxml2_parity_target"', self.text)

    def test_jax_nested_bazel_is_separate_pinned_estate_tool(self) -> None:
        self.assertIn('ROOTFS_BAZEL_VERSION="7.7.0"', self.text)
        self.assertIn(
            'ROOTFS_BAZEL_SHA256="fe7e799cbc9140f986b063e06800a3d4c790525075c877d00a7112669824acbf"',
            self.text,
        )
        self.assertIn('ROOTFS_BAZEL_HOST="$OPT_VASO_HOST/bazel-$ROOTFS_BAZEL_VERSION/bin/bazel-real"', self.text)
        self.assertIn('ROOTFS_BAZEL_SB="/opt/vaso/bazel-$ROOTFS_BAZEL_VERSION/bin/bazel-real"', self.text)
        self.assertIn("materialize_rootfs_bazel_tool()", self.text)
        self.assertIn('*"//native/jaxlib:jaxlib_nested_prefetch"*', self.text)
        self.assertIn("--repo_env=VASO_BAZEL=$ROOTFS_BAZEL_SB", self.text)
        self.assertNotIn("--action_env=VASO_BAZEL=", self.text)

    def test_jaxlib_nested_prefetch_is_explicit_and_online_only_for_nested_bazel(self) -> None:
        self.assertIn('if [[ "${1:-}" == "--jaxlib-prefetch" ]]', self.text)
        self.assertIn('echo "== prefetch: JAX nested Bazel repositories (online, explicit) =="', self.text)
        self.assertIn('prefetch_sha256_source "@jax_v0_10_2_source_archive"', self.text)
        self.assertIn('prefetch_sha256_source "@rootfs_bazel_native"', self.text)
        self.assertIn("//native/jaxlib:jaxlib_nested_prefetch", self.text)
        self.assertIn("--//native/jaxlib:token=$JAXLIB_BUILD_TOKEN", self.text)
        self.assertIn("jaxlib-prefetch: ok line=$VASO_CUDA_LINE", self.text)

        prefetch_start = self.text.index('if [[ "${1:-}" == "--jaxlib-prefetch" ]]')
        build_start = self.text.index('if [[ "${1:-}" == "--bazel-build" ]]')
        prefetch_block = self.text[prefetch_start:build_start]
        self.assertIn('bazel_insula_online build --announce_rc //native/jaxlib:jaxlib_nested_prefetch', prefetch_block)
        self.assertNotIn("${BAZEL_OFFLINE_ARGS[@]}", prefetch_block)
        self.assertNotIn("--repository_disable_download", prefetch_block)

    def test_jaxlib_max_jobs_is_per_run_and_reaches_actions(self) -> None:
        self.assertIn("JAXLIB_MAX_JOBS_CAP=96", self.text)
        self.assertIn("resolve_jaxlib_max_jobs()", self.text)
        self.assertIn("VASO_JAXLIB_MAX_JOBS must be <= $JAXLIB_MAX_JOBS_CAP", self.text)
        self.assertIn("VASO_JAXLIB_MAX_JOBS_RESOLVED", self.text)
        self.assertIn("--action_env=VASO_JAXLIB_MAX_JOBS=$VASO_JAXLIB_MAX_JOBS_RESOLVED", self.text)
        self.assertIn("--setenv VASO_JAXLIB_MAX_JOBS \"$VASO_JAXLIB_MAX_JOBS_RESOLVED\"", self.text)

    def test_explicit_bazel_build_mode_uses_offline_insula_wrapper(self) -> None:
        self.assertIn('if [[ "${1:-}" == "--bazel-build" ]]', self.text)
        self.assertIn('echo "== requested Bazel build targets (inside insula, offline) =="', self.text)
        self.assertIn('bazel_insula build --announce_rc "$@"', self.text)
        self.assertIn('bazel-build: ok line=$VASO_CUDA_LINE targets=$#', self.text)
        build_start = self.text.index('if [[ "${1:-}" == "--bazel-build" ]]')
        main_phase = self.text.index('echo "== phase 0: refresh Bazel configurable repos inside insula =="')
        self.assertLess(build_start, main_phase)

    def test_explicit_insula_command_mode_uses_rootfs_wrapper(self) -> None:
        self.assertIn('if [[ "${1:-}" == "--insula-cmd" ]]', self.text)
        self.assertIn('echo "== requested command (inside insula) =="', self.text)
        self.assertIn('insula "$@"', self.text)
        self.assertIn('insula-cmd: ok line=$VASO_CUDA_LINE', self.text)
        cmd_start = self.text.index('if [[ "${1:-}" == "--insula-cmd" ]]')
        main_phase = self.text.index('echo "== phase 0: refresh Bazel configurable repos inside insula =="')
        self.assertLess(cmd_start, main_phase)

    def test_repository_environment_is_pinned(self) -> None:
        self.assertIn("--repo_env=VASO_IN_INSULA=1", self.text)
        self.assertIn("--repo_env=MAKE_JOBS=", self.text)
        self.assertIn("--repo_env=TMPDIR=$INSULA_TMP_SB", self.text)

    def test_cuda_line_and_home_reach_configured_actions(self) -> None:
        self.assertIn("--action_env=VASO_CUDA_LINE=$VASO_CUDA_LINE", self.text)
        self.assertIn("--action_env=VASO_HOME=/vaso", self.text)

    def test_pytorch_max_jobs_is_per_run_and_reaches_actions(self) -> None:
        self.assertIn("PYTORCH_MAX_JOBS_CAP=96", self.text)
        self.assertIn("resolve_pytorch_max_jobs()", self.text)
        self.assertIn("VASO_PYTORCH_MAX_JOBS must be <= $PYTORCH_MAX_JOBS_CAP", self.text)
        self.assertIn("VASO_PYTORCH_MAX_JOBS_RESOLVED", self.text)
        self.assertIn("--action_env=VASO_PYTORCH_MAX_JOBS=$VASO_PYTORCH_MAX_JOBS_RESOLVED", self.text)
        self.assertIn("--//native/pytorch:max_jobs=$VASO_PYTORCH_MAX_JOBS_RESOLVED", self.text)
        self.assertIn("--local_resources=cpu=$VASO_PYTORCH_MAX_JOBS_RESOLVED", self.text)

    def test_vision_audio_max_jobs_are_per_run_and_reach_actions(self) -> None:
        self.assertIn("TORCHVISION_MAX_JOBS_CAP=96", self.text)
        self.assertIn("TORCHAUDIO_MAX_JOBS_CAP=96", self.text)
        self.assertIn("resolve_torchvision_max_jobs()", self.text)
        self.assertIn("resolve_torchaudio_max_jobs()", self.text)
        self.assertIn("VASO_TORCHVISION_MAX_JOBS_RESOLVED", self.text)
        self.assertIn("VASO_TORCHAUDIO_MAX_JOBS_RESOLVED", self.text)
        self.assertIn("--action_env=VASO_TORCHVISION_MAX_JOBS=$VASO_TORCHVISION_MAX_JOBS_RESOLVED", self.text)
        self.assertIn("--action_env=VASO_TORCHAUDIO_MAX_JOBS=$VASO_TORCHAUDIO_MAX_JOBS_RESOLVED", self.text)
        self.assertIn("--//native/torchvision:max_jobs=$VASO_TORCHVISION_MAX_JOBS_RESOLVED", self.text)
        self.assertIn("--//native/torchaudio:max_jobs=$VASO_TORCHAUDIO_MAX_JOBS_RESOLVED", self.text)

    def test_insula_runs_are_leased_and_release_on_exit(self) -> None:
        self.assertIn('LEASE_PY="$REPO_ROOT/scripts/insula/lease.py"', self.text)
        self.assertIn('trap cleanup_on_exit EXIT', self.text)
        self.assertRegex(self.text, r"cleanup_on_exit\(\) \{[^}]*release_leases", "EXIT trap must release leases")
        self.assertIn('acquire_lease "insula:$VASO_CUDA_LINE"', self.text)
        self.assertIn('acquire_lease "gpu"', self.text)
        self.assertIn('acquire_lease "host-cpu"', self.text)
        self.assertIn('export CUDA_VISIBLE_DEVICES="$lease_cuda_visible_devices"', self.text)
        self.assertIn('export VASO_GPU_SET="$lease_vaso_gpu_set"', self.text)
        self.assertIn('export VASO_HOST_CPU_JOBS="$lease_host_cpu_jobs"', self.text)
        self.assertIn('DEFAULT_VASO_GPUS=0', self.text)
        self.assertIn('DEFAULT_VASO_GPUS=8', self.text)

    def test_output_base_can_use_agent_estate_binding(self) -> None:
        self.assertIn("VASO_BAZEL_OB", self.text)
        self.assertIn("/vaso-bazel-ob", self.text)
        self.assertRegex(self.text, r"--bind\s+\"\$BAZEL_OUTPUT_BASE_HOST\"\s+\"\$BAZEL_OUTPUT_BASE_SB\"")

    def test_output_base_is_keyed_by_line_and_agent_but_trae_keeps_legacy_path(self) -> None:
        scratch = _writable_test_root()
        with tempfile.TemporaryDirectory(dir=scratch) as root:
            estate = Path(root) / "estate"
            bazel = Path(root) / "bazel"
            bazel.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            bazel.chmod(0o755)
            env = {
                **os.environ,
                "VASO_ESTATE_ROOT": str(estate),
                "VASO_CUDA_LINE": "cu129",
                "VASO_REQUIRED_GIB": "0",
                "BAZEL_BIN": str(bazel),
                "VASO_AGENT": "worker-a",
                "VASO_GPUS": "0",
            }
            env.pop("VASO_BAZEL_OB", None)
            worker = subprocess.run(
                ["bash", str(RUN_SH), "--print-config-for-test"],
                cwd=RUN_SH.parent,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(worker.returncode, 0, worker.stderr)
            self.assertIn(f"BAZEL_OUTPUT_BASE_HOST={estate}/vaso/lines/cu129/cache/bazel/output-base-worker-a", worker.stdout)
            self.assertIn("BAZEL_OUTPUT_BASE_SB=/vaso/lines/cu129/cache/bazel/output-base-worker-a", worker.stdout)

            env["VASO_AGENT"] = "trae"
            shutil.rmtree(estate, ignore_errors=True)
            trae = subprocess.run(
                ["bash", str(RUN_SH), "--print-config-for-test"],
                cwd=RUN_SH.parent,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(trae.returncode, 0, trae.stderr)
            self.assertIn(f"BAZEL_OUTPUT_BASE_HOST={estate}/vaso/lines/cu129/cache/bazel/output-base", trae.stdout)
            self.assertIn("BAZEL_OUTPUT_BASE_SB=/vaso/lines/cu129/cache/bazel/output-base", trae.stdout)

    def test_stub_stage_writes_structured_result_on_failure(self) -> None:
        scratch = _writable_test_root()
        with tempfile.TemporaryDirectory(dir=scratch) as root:
            estate = Path(root) / "estate"
            bazel = Path(root) / "bazel"
            bazel.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            bazel.chmod(0o755)
            env = {
                **os.environ,
                "VASO_ESTATE_ROOT": str(estate),
                "VASO_CUDA_LINE": "cu129",
                "VASO_REQUIRED_GIB": "0",
                "BAZEL_BIN": str(bazel),
                "VASO_AGENT": "worker-a",
                "VASO_GPUS": "0",
            }
            env.pop("VASO_BAZEL_OB", None)
            completed = subprocess.run(
                ["bash", str(RUN_SH), "--stub-stage-for-test", "fail", "--token", "secret", "public=value"],
                cwd=RUN_SH.parent,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 17)
            results = sorted((estate / "agents" / "worker-a" / "results").glob("*-stub-stage-for-test.json"))
            self.assertEqual(len(results), 1)
            result = json.loads(results[0].read_text(encoding="utf-8"))
            self.assertEqual(result["mode"], "stub-stage-for-test")
            self.assertEqual(result["line"], "cu129")
            self.assertEqual(result["verdict"], "failed")
            self.assertEqual(result["stages"][0]["name"], "stub-stage")
            self.assertEqual(result["stages"][0]["exit_code"], 17)
            self.assertEqual(result["stages"][0]["first_error_line"], "FAILED: stub stage")
            self.assertTrue(Path(result["stages"][0]["log_path"]).is_file())
            self.assertTrue(result["leases"])
            self.assertEqual(result["args"], ["--stub-stage-for-test", "fail", "<redacted>", "<redacted>", "public=value"])
            latest = subprocess.run(
                ["bash", str(LAST_RESULT_SH), "--agent", "worker-a"],
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(latest.returncode, 0, latest.stderr)
            self.assertEqual(json.loads(latest.stdout)["mode"], "stub-stage-for-test")

    def test_stub_spack_solve_failure_writes_result_diagnostics(self) -> None:
        scratch = _writable_test_root()
        with tempfile.TemporaryDirectory(dir=scratch) as root:
            estate = Path(root) / "estate"
            bazel = Path(root) / "bazel"
            bazel.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            bazel.chmod(0o755)
            env = {
                **os.environ,
                "VASO_ESTATE_ROOT": str(estate),
                "VASO_CUDA_LINE": "cu130",
                "VASO_REQUIRED_GIB": "0",
                "BAZEL_BIN": str(bazel),
                "VASO_AGENT": "worker-a",
                "VASO_GPUS": "0",
            }
            completed = subprocess.run(
                ["bash", str(RUN_SH), "--stub-stage-for-test", "spack-solve-fail"],
                cwd=RUN_SH.parent,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 17)
            results = sorted((estate / "agents" / "worker-a" / "results").glob("*-stub-stage-for-test.json"))
            self.assertEqual(len(results), 1)
            result = json.loads(results[0].read_text(encoding="utf-8"))
            self.assertEqual(result["solve_diagnostics"][0]["stage"], "spack-dry-solve")
            self.assertEqual(result["solve_diagnostics"][0]["spec"], "py-jax@0.10.2 ^py-jaxlib@0.10.2+cuda+nccl")
            self.assertIn(
                "No version exists that satisfies these input specs",
                result["solve_diagnostics"][0]["conflicting_constraints"],
            )

    def test_dry_solve_mode_only_concretizes_inside_insula(self) -> None:
        branch = re.search(
            r'if \[\[ "\$\{1:-\}" == "--dry-solve" \]\]; then(?P<body>.*?)\nfi',
            self.text,
            re.S,
        )
        self.assertIsNotNone(branch)
        body = branch.group("body")
        self.assertIn('bazel_insula run @spack_dist//:spack -- spec --fresh --reuse "$DRY_SOLVE_SPEC"', body)
        self.assertNotIn("//tools:spack_lock", body)
        self.assertNotIn("install", body)

    def test_preflight_failure_writes_structured_result(self) -> None:
        scratch = _writable_test_root()
        with tempfile.TemporaryDirectory(dir=scratch) as root:
            estate = Path(root) / "estate"
            bazel = Path(root) / "bazel"
            bazel.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            bazel.chmod(0o755)
            env = {
                **os.environ,
                "VASO_ESTATE_ROOT": str(estate),
                "VASO_CUDA_LINE": "bad-line",
                "VASO_REQUIRED_GIB": "0",
                "BAZEL_BIN": str(bazel),
                "VASO_AGENT": "worker-a",
                "VASO_GPUS": "0",
            }
            completed = subprocess.run(
                ["bash", str(RUN_SH)],
                cwd=RUN_SH.parent,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 2)
            results = sorted((estate / "agents" / "worker-a" / "results").glob("*-gate.json"))
            self.assertEqual(len(results), 1)
            result = json.loads(results[0].read_text(encoding="utf-8"))
            self.assertEqual(result["mode"], "gate")
            self.assertEqual(result["line"], "bad-line")
            self.assertEqual(result["stages"], [])
            self.assertEqual(result["exit_code"], 2)
            self.assertEqual(result["verdict"], "failed")

    def test_cuda_line_is_explicit_and_selects_line_rootfs(self) -> None:
        self.assertIn('VASO_CUDA_LINE="${VASO_CUDA_LINE:-}"', self.text)
        self.assertIn('case "$VASO_CUDA_LINE" in', self.text)
        self.assertIn('cu129|cu130)', self.text)
        self.assertIn('rootfs-lines/$VASO_CUDA_LINE/rootfs', self.text)
        self.assertIn('rootfs-lines/$VASO_CUDA_LINE/rootfs-bundle.json', self.text)
        self.assertNotIn('CUDA_ROOTFS="$ESTATE_ROOT/rootfs"', self.text)
        self.assertNotIn('ROOTFS_BUNDLE_MANIFEST="$ESTATE_ROOT/rootfs-bundle.json"', self.text)
        self.assertNotIn("VASO_ALLOW_HOST_ROOTFS", self.text)

    def test_cuda_line_partitions_bazel_and_native_state(self) -> None:
        self.assertIn('VASO_LINE_HOST="$VASO_HOST/lines/$VASO_CUDA_LINE"', self.text)
        self.assertIn('$VASO_LINE_HOST/cache/bazel/output-base', self.text)
        self.assertIn('$VASO_LINE_HOST/cache/bazel/disk-cache', self.text)
        self.assertIn('$VASO_LINE_HOST/state/native', self.text)
        self.assertIn('$VASO_LINE_HOST/state/stamps', self.text)
        self.assertIn('BAZEL_REPO_CACHE_SB="/vaso/cache/bazel/repository-cache"', self.text)

    def test_insula_binds_host_driver_like_language_verifier(self) -> None:
        for token in (
            "CUDA_DRIVER_BINDS",
            "libcuda.so.1",
            "libnvidia-ml.so.1",
            "/run/nvidia-driver/lib",
            "--dev-bind-try",
            "/dev/nvidia",
            "LD_LIBRARY_PATH",
        ):
            self.assertIn(token, self.text)
        self.assertIn("MP_ARGS+=(--mountpoint /run/nvidia-driver)", self.text)
        self.assertIn("MP_ARGS+=(--mountpoint /run/nvidia-driver/lib)", self.text)
        self.assertRegex(self.text, r"--dir\s+/run/nvidia-driver\s+--dir\s+/run/nvidia-driver/lib")
        self.assertIn('--setenv LD_LIBRARY_PATH "$INSULA_LIBRARY_PATH"', self.text)

    def test_bazel_tests_prefer_bound_nvidia_driver_libraries(self) -> None:
        self.assertIn('INSULA_LIBRARY_PATH="/run/nvidia-driver/lib:$VASO_CUDA_HOME_SB/lib64"', self.text)
        self.assertIn('--setenv LD_LIBRARY_PATH "$INSULA_LIBRARY_PATH"', self.text)
        self.assertIn('--test_env=LD_LIBRARY_PATH=$INSULA_LIBRARY_PATH', self.text)
        self.assertIn('--test_env=VASO_CUDA_DRIVER_LIB=/run/nvidia-driver/lib', self.text)

    def test_cuda_line_smoke_survives_shell_quoting(self) -> None:
        self.assertIn('cuda = manifest["verified_versions"]["components"]["cuda_toolkit"]["expected"]', self.text)
        self.assertNotIn("manifest['verified_versions']", self.text)

    def test_cuda_boundary_smokes_use_exact_runfile_repositories(self) -> None:
        for script in (
            "use_cuda_boundary.sh",
            "use_cudnn_native.sh",
            "use_cusparselt_native.sh",
            "use_cudss_native.sh",
            "use_nccl_native.sh",
            "nccl_two_rank_native.sh",
            "use_nvshmem_native.sh",
        ):
            text = (SYNTHETIC_DIR / script).read_text(encoding="utf-8")
            self.assertNotRegex(text, r"find_runfile '\*[^']*_native\*/")
            self.assertNotRegex(text, r'find_runfile "\*[^"]*_native\*/')
            self.assertRegex(text, r"\*/\+[^+]+\+[^+]+/")

    def test_cuda_rootfs_component_exposes_optional_cccl_headers(self) -> None:
        boundary_text = ROOTFS_CUDA_COMPONENT_BZL.read_text(encoding="utf-8")
        self.assertIn('"prefix/include/cccl"', boundary_text)
        self.assertIn('if [[ -d "${include_root}/cccl" ]]; then', boundary_text)
        self.assertIn('ln -s "${include_root}/cccl" "$PREFIX/include/cccl"', boundary_text)

    def test_run_sh_leaves_the_toolchain_to_bazel_and_the_rootfs(self) -> None:
        # Only Bazel sets toolchain settings; the toolchain comes from the
        # insula rootfs. insula() runs with --clearenv, so run.sh passes none.
        toolchain = r"(CC|CXX|CPP|FC|F77|LD|AR|NM|RANLIB|STRIP|CFLAGS|CXXFLAGS|CPPFLAGS|LDFLAGS|CMAKE)"
        self.assertIsNone(re.search(rf"--(repo_env|action_env|host_action_env)={toolchain}=", self.text))
        self.assertIsNone(re.search(r"--(copt|cxxopt|conlyopt|linkopt|host_copt|host_linkopt)\b", self.text))

    def test_cuda_boundary_reads_expected_versions_from_manifest(self) -> None:
        cuda_text = CUDA_BZL.read_text(encoding="utf-8")
        self.assertIn('"verified_versions"', cuda_text)
        self.assertIn('"cuda_toolkit"', cuda_text)
        self.assertIn('"expected"', cuda_text)
        self.assertNotIn('cuda_version = "12.9.1"', cuda_text)
        self.assertNotIn('nvcc_version = "12.9.86"', cuda_text)

    def test_spack_externals_smoke_solves_manifest_specs(self) -> None:
        self.assertIn("--spack-externals-smoke", self.text)
        self.assertIn("tools/rootfs_spack_externals.py", self.text)
        self.assertIn("@spack_dist//:spack", self.text)
        self.assertIn("spec --fresh --reuse", self.text)
        self.assertIn("spack-externals-smoke: ok line=", self.text)

    def test_rootfs_boundaries_smoke_tests_cuda_native_boundaries(self) -> None:
        self.assertIn("--rootfs-boundaries-smoke", self.text)
        for target in (
            "//synthetic:use_cuda_boundary",
            "//synthetic:use_cudnn_native",
            "//synthetic:use_cusparselt_native",
            "//synthetic:use_cudss_native",
            "//synthetic:use_nccl_native",
            "//synthetic:use_nvshmem_native",
        ):
            self.assertIn(target, self.text)
        self.assertIn("rootfs-boundaries-smoke: ok line=", self.text)

if __name__ == "__main__":
    unittest.main()
