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
REPO_ROOT = Path(__file__).resolve().parents[2]
NATIVE_DETERMINISM_ARGS_SH = Path(__file__).resolve().parents[1] / "tools/native_determinism_args.sh"
LAST_RESULT_SH = REPO_ROOT / "insula" / "execution" / "last-result.sh"
JAXLIB_NESTED_HTTP_ARCHIVES = (
    Path(__file__).resolve().parents[1]
    / "native/jaxlib/nested_http_archives.json"
)
LEASE_PY = REPO_ROOT / "insula" / "execution" / "lease.py"
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

    def _run_sh_env(self, root: Path, *, profile: str = "torch") -> dict[str, str]:
        estate = root / "estate"
        bazel = root / "bazel"
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
            "VASO_PROFILE": profile,
        }
        env.pop("VASO_BAZEL_OB", None)
        return env

    def _minimal_insula_root(self, estate: Path) -> None:
        rootfs = estate / "rootfs-lines" / "cu129"
        (rootfs / "rootfs" / "bin").mkdir(parents=True, exist_ok=True)
        bash_link = rootfs / "rootfs" / "bin" / "bash"
        if not bash_link.exists():
            bash_link.symlink_to("/bin/bash")
        (rootfs / "rootfs-bundle.json").write_text(
            json.dumps(
                {
                    "line": "cu129",
                    "derived_image_id": "sha256:test-rootfs",
                    "verified_versions": {
                        "components": {
                            "cuda_toolkit": {
                                "expected": "12.9.1",
                                "nvcc": "12.9.86",
                            },
                        },
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def _fake_bwrap(self, root: Path) -> Path:
        bwrap = root / "fake-bwrap"
        log = root / "fake-bwrap.jsonl"
        bwrap.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "python3 - \"$@\" <<'PY'\n"
            "import json\n"
            "import os\n"
            "import sys\n"
            "args = sys.argv[1:]\n"
            "cmd = args[args.index('--') + 1:] if '--' in args else args\n"
            "with open(os.environ['FAKE_BWRAP_LOG'], 'a', encoding='utf-8') as fh:\n"
            "    fh.write(json.dumps({'argv': args, 'cmd': cmd}, sort_keys=True) + '\\n')\n"
            "if any(token == 'spack' or token.endswith('/spack') for token in cmd):\n"
            "    raise SystemExit('fake bwrap refused spack command: ' + repr(cmd))\n"
            "if '@spack_dist' in ' '.join(cmd) or '//tools:spack_lock' in cmd:\n"
            "    raise SystemExit('fake bwrap refused Spack build path: ' + repr(cmd))\n"
            "PY\n",
            encoding="utf-8",
        )
        bwrap.chmod(0o755)
        log.write_text("", encoding="utf-8")
        return bwrap

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
        self.assertIn('"@zlib_ng_action_archive"', self.text)
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
        self.assertIn('"@jaxlib_nested_http_archives"', self.text)
        self.assertIn('"@nlohmann_json_native"', self.text)
        self.assertIn('"@py_lit_native"', self.text)
        self.assertIn('"@py_pybind11_native"', self.text)
        self.assertIn('"@sleef_native"', self.text)
        self.assertIn('"@coreutils_native"', self.text)
        self.assertIn("coreutils-9.10.tar.xz", self.text)
        self.assertIn(
            "16535a9adf0b10037364e2d612aad3d9f4eca3a344949ced74d12faf4bd51d25",
            self.text,
        )
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
        self.assertIn("prefetch_jaxlib_nested_http_archives()", self.text)
        self.assertIn("seed_bazel_repository_cache()", self.text)
        self.assertIn("sha256sum", self.text)
        self.assertIn("curl", self.text)
        self.assertIn('fetch-only: fetching sha256-pinned source: $repo', self.text)

        offline = re.search(r"bazel_insula\(\) \{(?P<body>.*?)\n\}", self.text, re.S)
        self.assertIsNotNone(offline)
        self.assertNotIn("BAZEL_OFFLINE_ARGS", offline.group("body"))
        self.assertIn('INSULA_SH="$REPO_ROOT/foundry/insula.sh"', self.text)

        self.assertNotIn("bazel_insula_prefetch", self.text)
        fetch_only_mode = self.text.index('if [[ "${1:-}" == "--fetch-only" ]]')
        fetch_only_start = self.text.index('if [[ "${VASO_FETCH:-0}" == "1" ]]; then', fetch_only_mode)
        next_mode_branch = self.text.index('if [[ "${1:-}" == "--jaxlib-prefetch" ]]')
        fetch_only = self.text[fetch_only_start:next_mode_branch]
        self.assertIn('fetch-only: ok repos=${#FETCH_REPOS[@]}', fetch_only)
        self.assertNotRegex(fetch_only, r"\bbazel_insula\b")
        self.assertIn("insula_network_fetch --sha256 \"$sha256\" -- curl", self.text)

    def test_libxml2_parity_target_follows_locked_version(self) -> None:
        self.assertIn('SPACK_LIBXML2_VERSION="$(lock_version vaso_libxml2)"', self.text)
        self.assertIn('libxml2_parity_target="//synthetic:libxml2_abi_parity"', self.text)
        self.assertIn('if [[ "$SPACK_LIBXML2_VERSION" == "2.15.3" ]]; then', self.text)
        self.assertIn('libxml2_parity_target="//synthetic:libxml2_215_abi_parity"', self.text)
        self.assertIn('bazel_insula test "$libxml2_parity_target"', self.text)

    def test_lock_config_uses_checked_in_profile_lock(self) -> None:
        scratch = _writable_test_root()
        for profile in ("torch", "jax"):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory(dir=scratch) as root:
                env = self._run_sh_env(Path(root), profile=profile)
                completed = subprocess.run(
                    ["bash", str(RUN_SH), "--print-lock-config-for-test"],
                    cwd=RUN_SH.parent,
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                config = dict(line.split("=", 1) for line in completed.stdout.splitlines() if "=" in line)
                canonical_host = RUN_SH.parent / "locks" / f"{profile}.lock.json"
                self.assertEqual(config["VASO_PROFILE"], profile)
                self.assertEqual(config["CANONICAL_LOCK_HOST"], str(canonical_host))
                self.assertEqual(
                    config["CANONICAL_LOCK_SB"],
                    f"/workspace/experiment/locks/{profile}.lock.json",
                )
                self.assertEqual(
                    config["VASO_LOCK_OUT"],
                    f"/vaso/lines/cu129/state/spack-lock-captures/{profile}.lock.json",
                )
                self.assertEqual(
                    config["LOCK_OUT_HOST"],
                    f"{env['VASO_ESTATE_ROOT']}/vaso/lines/cu129/state/spack-lock-captures/{profile}.lock.json",
                )
                self.assertNotIn("/workspace/experiment/spack_graph.lock.json", completed.stdout)

    def test_lock_config_rejects_legacy_root_lock_output(self) -> None:
        scratch = _writable_test_root()
        with tempfile.TemporaryDirectory(dir=scratch) as root:
            env = {
                **self._run_sh_env(Path(root)),
                "VASO_LOCK_OUT": "/workspace/experiment/spack_graph.lock.json",
            }
            completed = subprocess.run(
                ["bash", str(RUN_SH), "--print-lock-config-for-test"],
                cwd=RUN_SH.parent,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
            self.assertIn("refusing to write legacy root lock output", completed.stderr)

    def test_lock_config_rejects_checked_in_profile_lock_output(self) -> None:
        scratch = _writable_test_root()
        for profile in ("torch", "jax"):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory(dir=scratch) as root:
                env = {
                    **self._run_sh_env(Path(root), profile=profile),
                    "VASO_LOCK_OUT": f"/workspace/experiment/locks/{profile}.lock.json",
                }
                completed = subprocess.run(
                    ["bash", str(RUN_SH), "--print-lock-config-for-test"],
                    cwd=RUN_SH.parent,
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                )

                self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
                self.assertIn("refusing to overwrite checked-in profile lock", completed.stderr)

    def test_jax_nested_bazel_is_separate_pinned_estate_tool(self) -> None:
        self.assertIn('ROOTFS_BAZEL_VERSION="7.7.0"', self.text)
        self.assertIn(
            'ROOTFS_BAZEL_SHA256="fe7e799cbc9140f986b063e06800a3d4c790525075c877d00a7112669824acbf"',
            self.text,
        )
        self.assertIn('ROOTFS_BAZEL_HOST="$OPT_VASO_HOST/bazel-$ROOTFS_BAZEL_VERSION/bin/bazel-real"', self.text)
        self.assertIn('ROOTFS_BAZEL_SB="/opt/vaso/bazel-$ROOTFS_BAZEL_VERSION/bin/bazel-real"', self.text)
        self.assertIn("materialize_rootfs_bazel_tool()", self.text)
        self.assertIn('*"//native/jaxlib:jaxlib_action"*', self.text)
        self.assertIn("--repo_env=VASO_BAZEL=$ROOTFS_BAZEL_SB", self.text)
        self.assertNotIn("--action_env=VASO_BAZEL=", self.text)

    def test_jaxlib_prefetch_uses_manifest_fetch_without_online_bazel(self) -> None:
        self.assertIn('if [[ "${1:-}" == "--jaxlib-prefetch" ]]', self.text)
        self.assertIn('echo "== prefetch: JAX nested Bazel repositories (sha256 manifest) =="', self.text)
        self.assertIn('prefetch_sha256_source "@jax_v0_10_2_source_archive"', self.text)
        self.assertIn('prefetch_sha256_source "@rootfs_bazel_native"', self.text)
        self.assertIn('prefetch_sha256_source "@jaxlib_nested_http_archives"', self.text)
        self.assertIn("jaxlib-prefetch: ok line=$VASO_CUDA_LINE", self.text)
        self.assertNotIn("bazel_insula_online", self.text)
        self.assertNotIn("//native/jaxlib:jaxlib_nested_prefetch", self.text)

        prefetch_start = self.text.index('if [[ "${1:-}" == "--jaxlib-prefetch" ]]')
        cubin_check_start = self.text.index('if [[ "${1:-}" == "--cubin-guard-offline-check" ]]')
        prefetch_block = self.text[prefetch_start:cubin_check_start]
        self.assertNotIn("bazel_insula", prefetch_block)
        self.assertNotIn("--repository_disable_download", prefetch_block)

    def test_retired_reference_oracle_modes_are_absent(self) -> None:
        retired_modes = (
            "--reference-" + "fetch",
            "--reference-" + "install",
            "--reference-" + "probe",
        )
        for mode in retired_modes:
            self.assertNotIn(mode, self.text)
        self.assertNotIn("reference_" + "wheels", self.text)
        self.assertNotIn("install_" + "reference_" + "prefixes", self.text)
        self.assertNotIn("/vaso/" + "reference", self.text)
        self.assertNotIn("REFERENCE_" + "PREFIX_", self.text)

    def test_jaxlib_nested_manifest_is_checked_in_and_sha_pinned(self) -> None:
        manifest = json.loads(JAXLIB_NESTED_HTTP_ARCHIVES.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["jaxlib_version"], "0.10.2")
        self.assertEqual(manifest["archive_count"], len(manifest["archives"]))
        self.assertGreaterEqual(len(manifest["archives"]), 80)
        self.assertEqual(manifest["distdir"], "/vaso/sources/jax/distdir")
        self.assertEqual(manifest["repository_cache"], "/vaso/cache/bazel/repository-cache")

        filenames: set[str] = set()
        names = {archive["name"] for archive in manifest["archives"]}
        for archive in manifest["archives"]:
            with self.subTest(name=archive["name"]):
                self.assertRegex(archive["sha256"], r"^[0-9a-f]{64}$")
                self.assertTrue(archive["filename"])
                self.assertNotIn("/", archive["filename"])
                self.assertNotIn("..", archive["filename"])
                self.assertNotIn(archive["filename"], filenames)
                filenames.add(archive["filename"])
                self.assertGreaterEqual(len(archive["urls"]), 1)
                self.assertTrue(all(url.startswith("https://") for url in archive["urls"]))

        for required in ("xla", "rules_ml_toolchain", "rules_python", "llvm-raw", "go_sdk_linux_amd64"):
            self.assertIn(required, names)

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
        self.assertIn("jaxlib_bazel_build_token_args()", self.text)
        self.assertIn('"--//native/jaxlib:token=$JAXLIB_BUILD_TOKEN"', self.text)
        self.assertIn('bazel_insula build --announce_rc "${extra_bazel_args[@]}" "$@"', self.text)
        self.assertIn('bazel-build: ok line=$VASO_CUDA_LINE targets=$#', self.text)
        build_start = self.text.index('if [[ "${1:-}" == "--bazel-build" ]]')
        main_phase = self.text.index('echo "== phase 0: refresh Bazel configurable repos inside insula =="')
        self.assertLess(build_start, main_phase)

    def test_cubin_guard_offline_check_uses_downloads_disabled_bazel_wrapper(self) -> None:
        self.assertIn('if [[ "${1:-}" == "--cubin-guard-offline-check" ]]', self.text)
        check_start = self.text.index('if [[ "${1:-}" == "--cubin-guard-offline-check" ]]')
        build_start = self.text.index('if [[ "${1:-}" == "--bazel-build" ]]')
        check_block = self.text[check_start:build_start]

        self.assertIn('echo "== cubin_guard offline check (inside insula, downloads disabled) =="', check_block)
        self.assertIn('bazel_insula test //tools:cubin_guard_test', check_block)
        self.assertIn("--test_output=all", check_block)
        self.assertIn("--announce_rc", check_block)
        self.assertIn("cubin-guard-offline-check: ok line=$VASO_CUDA_LINE", check_block)
        self.assertNotIn("prefetch_sha256_source", check_block)
        self.assertNotIn("--fetch-only", check_block)

    def test_explicit_bazel_build_does_not_reach_spack_when_legacy_install_env_is_set(self) -> None:
        scratch = _writable_test_root()
        with tempfile.TemporaryDirectory(dir=scratch) as root:
            root_path = Path(root)
            env = {
                **self._run_sh_env(root_path),
                "FAKE_BWRAP_LOG": str(root_path / "fake-bwrap.jsonl"),
                "INSULA_BWRAP_BIN": str(self._fake_bwrap(root_path)),
                "INSULA_SKIP_PREFLIGHT": "1",
                "PATH": "/usr/bin:/bin",
                "VASO_SPACK_INSTALL": "1",
            }
            self._minimal_insula_root(Path(env["VASO_ESTATE_ROOT"]))
            # This control-flow fixture fakes execution and prerequisite checking.
            # Tool validation itself has real content-drift tests in insula.
            fake_bin = root_path / "fixture-bin"
            fake_bin.mkdir()
            checker = fake_bin / "python3"
            checker.write_text('#!/bin/bash\ncase "${1:-}" in */foundry_tools.py|*/bcr_mirror.py) exit 0;; esac\nexec /usr/bin/python3 "$@"\n')
            checker.chmod(0o755)
            env["PATH"] = str(fake_bin) + ":/usr/bin:/bin"

            completed = subprocess.run(
                ["bash", str(RUN_SH), "--bazel-build", "//synthetic:use_zlib_ng"],
                cwd=RUN_SH.parent,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertIn("bazel-build: ok line=cu129 targets=1", completed.stdout)
            log = [
                json.loads(line)
                for line in Path(env["FAKE_BWRAP_LOG"]).read_text(encoding="utf-8").splitlines()
                if line
            ]
            self.assertEqual(len(log), 1)
            command_text = " ".join(log[0]["cmd"])
            self.assertIn("build", log[0]["cmd"])
            self.assertIn("//synthetic:use_zlib_ng", log[0]["cmd"])
            self.assertNotIn("spack", command_text)
            self.assertNotIn("//tools:spack_lock", command_text)
            self.assertNotIn("@spack_dist", command_text)

    def test_run_sh_has_no_active_spack_build_entrypoints(self) -> None:
        self.assertNotIn("VASO_SPACK_INSTALL", self.text)
        self.assertNotIn("VASO_SPACK_FRESH", self.text)
        self.assertNotIn("VASO_SPACK_TIMEOUT", self.text)
        self.assertNotIn("--spack-externals-smoke", self.text)
        self.assertNotIn("--dry-solve", self.text)
        self.assertNotIn("//tools:spack_lock", self.text)
        self.assertNotIn("@spack_dist", self.text)
        self.assertNotIn("spack spec", self.text)
        self.assertNotIn("spack install", self.text)

    def test_explicit_insula_command_mode_uses_rootfs_wrapper(self) -> None:
        self.assertIn('if [[ "${1:-}" == "--insula-cmd" ]]', self.text)
        cmd_start = self.text.index('if [[ "${1:-}" == "--insula-cmd" ]]')
        main_phase = self.text.index('echo "== phase 0: refresh Bazel configurable repos inside insula =="')
        self.assertLess(cmd_start, main_phase)
        cmd_block = self.text[cmd_start:main_phase]
        self.assertIn('echo "== validate canonical profile lock ($CANONICAL_LOCK_SB) =="', cmd_block)
        self.assertIn("validate_canonical_lock", cmd_block)
        self.assertIn('echo "== requested command (inside insula) =="', cmd_block)
        self.assertIn('insula "$@"', cmd_block)
        self.assertIn('insula-cmd: ok line=$VASO_CUDA_LINE', cmd_block)

    def test_insula_command_mode_passes_native_release_tokens_through_clearenv(self) -> None:
        for token_var in (
            "VASO_NATIVE_PYTORCH_TOKEN",
            "VASO_NATIVE_TORCHVISION_TOKEN",
            "VASO_NATIVE_TORCHAUDIO_TOKEN",
            "VASO_NATIVE_TRITON_TOKEN",
            "VASO_NATIVE_JAXLIB_TOKEN",
            "VASO_NATIVE_LLVM_TOKEN",
        ):
            self.assertIn(f'--setenv {token_var} "${{{token_var}:-}}"', self.text)

    def test_insula_command_mode_forwards_held_lease_ids_for_nested_release_runs(self) -> None:
        self.assertIn('--setenv VASO_LEASES_HELD "${VASO_LEASES_HELD:-}"', self.text)

    def test_insula_command_mode_exposes_release_runner_inputs(self) -> None:
        self.assertIn('materialize_rootfs_bazel_tool()', self.text)
        self.assertIn('insula/rootfs/foundry_tools.py', self.text)
        self.assertNotIn('cp "$BWRAP_BIN"', self.text)
        self.assertNotIn('command -v bazel', self.text)
        self.assertIn("--setenv INSULA_BWRAP_BIN /vaso/tools/bin/bwrap", self.text)
        self.assertIn("--mountpoint /workspace/repo", self.text)
        self.assertIn("--mountpoint /vaso/rootfs-lines", self.text)
        self.assertIn('--ro-bind "$REPO_ROOT" /workspace/repo', self.text)
        self.assertIn('--ro-bind "$ESTATE_ROOT/rootfs-lines" /vaso/rootfs-lines', self.text)
        self.assertIn('--bind "$ESTATE_ROOT/agents" /vaso/agents', self.text)

    def test_repository_environment_is_pinned(self) -> None:
        self.assertIn("--repo_env=VASO_IN_INSULA=1", self.text)
        self.assertIn("--repo_env=MAKE_JOBS=", self.text)
        # Constant per line: a per-run TMPDIR would change native keys every run.
        self.assertIn("--repo_env=TMPDIR=$BAZEL_TMP_SB", self.text)
        self.assertIn("--action_env=TMPDIR=$BAZEL_TMP_SB", self.text)
        self.assertIn('BAZEL_TMP_SB="/vaso/tmp/bazel-$VASO_CUDA_LINE"', self.text)
        self.assertNotIn("TMPDIR=$INSULA_TMP_SB", self.text)

    def test_cuda_line_and_home_reach_configured_actions(self) -> None:
        self.assertIn("--action_env=VASO_CUDA_LINE=$VASO_CUDA_LINE", self.text)
        self.assertIn("--action_env=VASO_HOME=/vaso", self.text)

    def test_deterministic_release_config_is_toolchain_owned(self) -> None:
        helper = NATIVE_DETERMINISM_ARGS_SH.read_text(encoding="utf-8")

        self.assertIn('. "$EXP_DIR/tools/native_determinism_args.sh"', self.text)
        self.assertIn('VASO_RELEASE_GIT_SHA_INPUT="${VASO_RELEASE_GIT_SHA:-HEAD}"', self.text)
        self.assertIn(
            'VASO_RELEASE_GIT_SHA="$(native_determinism_resolve_release_git_sha "$REPO_ROOT" "$VASO_RELEASE_GIT_SHA_INPUT")"',
            self.text,
        )
        self.assertIn(
            'VASO_SOURCE_DATE_EPOCH="$(native_determinism_source_date_epoch "$REPO_ROOT" "$VASO_RELEASE_GIT_SHA")"',
            self.text,
        )
        self.assertIn('BAZEL_EXECROOT_SB="$BAZEL_OUTPUT_BASE_SB/execroot/_main"', self.text)
        self.assertIn('BAZEL_NATIVE_WORK_SB="/vaso/lines/$VASO_CUDA_LINE/work"', self.text)
        self.assertNotIn("VASO_DETERMINISTIC_PREFIX_MAP_FLAGS", self.text)
        self.assertNotIn("VASO_DETERMINISTIC_NVCC_FLAGS", self.text)
        self.assertNotIn("--action_env=SOURCE_DATE_EPOCH=$VASO_SOURCE_DATE_EPOCH", self.text)
        self.assertNotIn("--action_env=PYTHONHASHSEED=0", self.text)
        self.assertNotIn("--action_env=CFLAGS=", self.text)
        self.assertNotIn("--action_env=CXXFLAGS=", self.text)
        self.assertNotIn("--action_env=NVCC_FLAGS=", self.text)
        # Fixed epoch for repo-rule deps, never the commit time (FC-N1-5).
        self.assertIn("--repo_env=SOURCE_DATE_EPOCH=$(native_repo_source_date_epoch)", self.text)
        self.assertNotIn("--repo_env=SOURCE_DATE_EPOCH=$VASO_SOURCE_DATE_EPOCH", self.text)
        self.assertIn("append_native_determinism_bazel_args \\", self.text)
        self.assertIn("  BAZEL_RELEASE_ARGS \\", self.text)
        self.assertIn("--//native/common:release_line=", helper)
        self.assertIn("--//native/common:release_git_sha=", helper)
        self.assertIn("--//native/common:source_date_epoch=", helper)
        self.assertIn("--//native/common:deterministic_seed=", helper)
        self.assertIn(
            "--//native/common:deterministic_prefix_map_roots=",
            helper,
        )
        self.assertIn("--//native/common:native_action_cuda_home=", helper)
        self.assertIn('"$output_base/external/+cuda_native+cuda_native/prefix"', helper)
        self.assertNotIn("--//native/common:native_action_cuda_home=", self.text)

    def test_print_config_reports_deterministic_toolchain_config(self) -> None:
        scratch = _writable_test_root()
        with tempfile.TemporaryDirectory(dir=scratch) as root:
            estate = Path(root) / "estate"
            bazel = Path(root) / "bazel"
            bazel.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            bazel.chmod(0o755)
            sha = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            epoch = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "show", "-s", "--format=%ct", sha],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            env = {
                **os.environ,
                "VASO_ESTATE_ROOT": str(estate),
                "VASO_CUDA_LINE": "cu129",
                "VASO_REQUIRED_GIB": "0",
                "BAZEL_BIN": str(bazel),
                "VASO_AGENT": "worker-a",
                "VASO_GPUS": "0",
                "VASO_RELEASE_GIT_SHA": sha,
                "VASO_SOURCE_DATE_EPOCH": epoch,
            }
            env.pop("VASO_BAZEL_OB", None)

            completed = subprocess.run(
                ["bash", str(RUN_SH), "--print-config-for-test"],
                cwd=RUN_SH.parent,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            execroot = "/vaso-bazel-ob/execroot/_main"
            native_work = "/vaso/lines/cu129/work"
            self.assertIn(f"VASO_RELEASE_GIT_SHA={sha}\n", completed.stdout)
            self.assertIn(f"SOURCE_DATE_EPOCH={epoch}\n", completed.stdout)
            self.assertIn(f"BAZEL_EXECROOT_SB={execroot}\n", completed.stdout)
            self.assertIn(f"BAZEL_NATIVE_WORK_SB={native_work}\n", completed.stdout)
            self.assertIn(f"NATIVE_ACTION_DETERMINISTIC_PREFIX_MAP_ROOTS={execroot},{native_work}\n", completed.stdout)
            self.assertIn(f"NATIVE_ACTION_DETERMINISTIC_SEED={sha}\n", completed.stdout)
            self.assertIn("BAZEL_RELEASE_ARG=--//native/common:release_line=cu129\n", completed.stdout)
            self.assertIn(f"BAZEL_RELEASE_ARG=--//native/common:release_git_sha={sha}\n", completed.stdout)
            self.assertIn(f"BAZEL_RELEASE_ARG=--//native/common:source_date_epoch={epoch}\n", completed.stdout)
            self.assertIn(f"BAZEL_RELEASE_ARG=--//native/common:deterministic_seed={sha}\n", completed.stdout)
            self.assertIn(
                f"BAZEL_RELEASE_ARG=--//native/common:deterministic_prefix_map_roots={execroot},{native_work}\n",
                completed.stdout,
            )
            self.assertNotIn("VASO_DETERMINISTIC_PREFIX_MAP_FLAGS=", completed.stdout)
            self.assertNotIn("NVCC_FLAGS=", completed.stdout)
            self.assertNotIn("-compress=false", completed.stdout)

    def test_pytorch_max_jobs_is_per_run_and_kept_out_of_action_keys(self) -> None:
        self.assertIn("PYTORCH_MAX_JOBS_CAP=96", self.text)
        self.assertIn("resolve_pytorch_max_jobs()", self.text)
        self.assertIn("VASO_PYTORCH_MAX_JOBS must be <= $PYTORCH_MAX_JOBS_CAP", self.text)
        self.assertIn("VASO_PYTORCH_MAX_JOBS_RESOLVED", self.text)
        self.assertIn('printf \'%s\\n\' "$VASO_PYTORCH_MAX_JOBS_RESOLVED" > "$VASO_NATIVE_STATE_HOST/pytorch_jobs.txt"', self.text)
        self.assertNotIn("--action_env=VASO_PYTORCH_MAX_JOBS=$VASO_PYTORCH_MAX_JOBS_RESOLVED", self.text)
        self.assertNotIn("--//native/pytorch:max_jobs=$VASO_PYTORCH_MAX_JOBS_RESOLVED", self.text)
        self.assertIn("--local_resources=cpu=$VASO_PYTORCH_MAX_JOBS_RESOLVED", self.text)

    def test_audio_and_vision_max_jobs_do_not_reach_committed_plan_action_keys(self) -> None:
        self.assertIn("TORCHVISION_MAX_JOBS_CAP=96", self.text)
        self.assertIn("TORCHAUDIO_MAX_JOBS_CAP=96", self.text)
        self.assertIn("resolve_torchvision_max_jobs()", self.text)
        self.assertIn("resolve_torchaudio_max_jobs()", self.text)
        self.assertIn("VASO_TORCHVISION_MAX_JOBS_RESOLVED", self.text)
        self.assertIn("VASO_TORCHAUDIO_MAX_JOBS_RESOLVED", self.text)
        self.assertIn("--action_env=VASO_TORCHVISION_MAX_JOBS=$VASO_TORCHVISION_MAX_JOBS_RESOLVED", self.text)
        self.assertIn("--action_env=VASO_TORCHAUDIO_MAX_JOBS=$VASO_TORCHAUDIO_MAX_JOBS_RESOLVED", self.text)
        self.assertNotIn("--//native/torchvision:max_jobs=", self.text)
        self.assertNotIn("--//native/torchaudio:max_jobs=", self.text)

    def test_insula_runs_are_leased_and_release_on_exit(self) -> None:
        self.assertIn('LEASE_PY="$REPO_ROOT/insula/execution/lease.py"', self.text)
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

    def test_inherited_lease_ids_must_be_live_for_the_expected_resource(self) -> None:
        scratch = _writable_test_root()
        with tempfile.TemporaryDirectory(dir=scratch) as root:
            estate = Path(root) / "estate"
            bazel = Path(root) / "bazel"
            bazel.write_text(
                "#!/usr/bin/env bash\n"
                "if [ \"${1:-}\" = \"--version\" ]; then echo \"bazel 7.7.0\"; fi\n"
                "exit 0\n",
                encoding="utf-8",
            )
            bazel.chmod(0o755)
            base_env = {
                **os.environ,
                "VASO_ESTATE_ROOT": str(estate),
                "VASO_CUDA_LINE": "cu129",
                "VASO_REQUIRED_GIB": "0",
                "BAZEL_BIN": str(bazel),
                "VASO_AGENT": "worker-a",
                "VASO_GPUS": "0",
                "VASO_LEASE_TIMEOUT": "0",
            }
            stale_env = {
                **base_env,
                "VASO_LEASES_HELD": "insula:cu129:insula_cu129-not-live",
            }
            stale = subprocess.run(
                ["bash", str(RUN_SH), "--stub-stage-for-test", "ok"],
                cwd=RUN_SH.parent,
                env=stale_env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(stale.returncode, 75, stale.stdout + stale.stderr)
            self.assertIn("inherited lease is not live: resource=insula:cu129", stale.stderr)

            acquire = subprocess.run(
                [
                    "python3",
                    str(LEASE_PY),
                    "acquire",
                    "--resource",
                    "gpu",
                    "--holder",
                    "pytest",
                    "--pid",
                    str(os.getpid()),
                    "--ttl",
                    "60",
                    "--timeout",
                    "0",
                    "--amount",
                    "1",
                    "--capacity",
                    "1",
                ],
                cwd=REPO_ROOT,
                env=base_env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(acquire.returncode, 0, acquire.stderr)
            gpu_lease = json.loads(acquire.stdout)
            try:
                foreign_env = {
                    **base_env,
                    "VASO_LEASES_HELD": f"insula:cu129:{gpu_lease['id']}",
                }
                foreign = subprocess.run(
                    ["bash", str(RUN_SH), "--stub-stage-for-test", "ok"],
                    cwd=RUN_SH.parent,
                    env=foreign_env,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(foreign.returncode, 75, foreign.stdout + foreign.stderr)
                self.assertIn("inherited lease is not live: resource=insula:cu129", foreign.stderr)
            finally:
                subprocess.run(
                    ["python3", str(LEASE_PY), "release", "--id", str(gpu_lease["id"]), "--resource", "gpu"],
                    cwd=REPO_ROOT,
                    env=base_env,
                    check=False,
                    capture_output=True,
                    text=True,
                )

    def test_output_base_can_use_agent_estate_binding(self) -> None:
        self.assertIn("VASO_BAZEL_OB", self.text)
        self.assertIn('BAZEL_OUTPUT_BASE_SB="$(native_determinism_fixed_output_base_mount)"', self.text)
        self.assertRegex(self.text, r"--bind\s+\"\$BAZEL_OUTPUT_BASE_HOST\"\s+\"\$BAZEL_OUTPUT_BASE_SB\"")

    def test_output_base_host_is_keyed_by_line_and_agent_but_insula_mount_is_fixed(self) -> None:
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
            self.assertIn("BAZEL_OUTPUT_BASE_SB=/vaso-bazel-ob", worker.stdout)
            self.assertIn("BAZEL_EXECROOT_SB=/vaso-bazel-ob/execroot/_main", worker.stdout)
            self.assertIn(
                "BAZEL_RELEASE_ARG=--//native/common:native_action_cuda_home="
                "/vaso-bazel-ob/external/+cuda_native+cuda_native/prefix\n",
                worker.stdout,
            )

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
            self.assertIn("BAZEL_OUTPUT_BASE_SB=/vaso-bazel-ob", trae.stdout)
            self.assertIn("BAZEL_EXECROOT_SB=/vaso-bazel-ob/execroot/_main", trae.stdout)

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

    def test_stub_solve_failure_writes_result_diagnostics(self) -> None:
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
                ["bash", str(RUN_SH), "--stub-stage-for-test", "solve-fail"],
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
            self.assertEqual(result["solve_diagnostics"][0]["stage"], "lock-validate")
            self.assertEqual(result["solve_diagnostics"][0]["spec"], "py-jax@0.10.2 ^py-jaxlib@0.10.2+cuda+nccl")
            self.assertIn(
                "No version exists that satisfies these input specs",
                result["solve_diagnostics"][0]["conflicting_constraints"],
            )

    def test_dry_solve_mode_is_retired(self) -> None:
        self.assertNotIn("--dry-solve", self.text)
        self.assertNotIn("DRY_SOLVE_SPEC", self.text)

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
        # insula rootfs. Deterministic C/C++ prefix maps enter only as Bazel
        # action_env and the package drivers pass them through unchanged.
        toolchain = r"(CC|CXX|CPP|FC|F77|LD|AR|NM|RANLIB|STRIP|CPPFLAGS|LDFLAGS|CMAKE)"
        self.assertIsNone(re.search(rf"--(repo_env|action_env|host_action_env)={toolchain}=", self.text))
        self.assertIsNone(re.search(r"--(repo_env|host_action_env)=C(?:XX)?FLAGS=", self.text))
        self.assertIsNone(re.search(r"--(copt|cxxopt|conlyopt|linkopt|host_copt|host_linkopt)\b", self.text))

    def test_cuda_boundary_reads_expected_versions_from_manifest(self) -> None:
        cuda_text = CUDA_BZL.read_text(encoding="utf-8")
        self.assertIn('"verified_versions"', cuda_text)
        self.assertIn('"cuda_toolkit"', cuda_text)
        self.assertIn('"expected"', cuda_text)
        self.assertNotIn('cuda_version = "12.9.1"', cuda_text)
        self.assertNotIn('nvcc_version = "12.9.86"', cuda_text)

    def test_spack_externals_smoke_mode_is_retired(self) -> None:
        self.assertNotIn("--spack-externals-smoke", self.text)
        self.assertNotIn("rootfs_spack_externals.py", self.text)

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
