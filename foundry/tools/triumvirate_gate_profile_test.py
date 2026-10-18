#!/usr/bin/env python3
"""Tests for the profile-aware triumvirate gate shell wrappers."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
INSULA_GATE = EXPERIMENT_ROOT / "scripts" / "agents" / "triumvirate-gate-insula.sh"
NATIVE_DETERMINISM_ARGS_SH = EXPERIMENT_ROOT / "tools/native_determinism_args.sh"
COMMIT = "abcdef1234567890abcdef1234567890abcdef12"


def _bash_array_body(text: str, name: str) -> str:
    match = re.search(rf"(?ms)^  {re.escape(name)}=\(\n(?P<body>.*?)^  \)", text)
    if match is None:
        match = re.search(rf"(?ms)^{re.escape(name)}=\(\n(?P<body>.*?)^\)", text)
    if match is None:
        raise AssertionError(f"missing bash array {name}")
    return match.group("body")


def _bash_function_body(text: str, name: str) -> str:
    return text.split(f"{name}() {{", 1)[1].split("\n}", 1)[0]


def _main_profile_tail(text: str) -> str:
    return text.split('run_stage "compiler_pathway_guard"', 1)[1]


def _torch_profile_block(text: str) -> str:
    return _main_profile_tail(text).split('if [[ "$profile" == "torch" ]]; then', 1)[1].split(
        "else\n  jax_runtime_args=()",
        1,
    )[0]


def _jax_profile_block(text: str) -> str:
    return _main_profile_tail(text).split("else\n  jax_runtime_args=()", 1)[1].split(
        "\n  acceptance_prefix_args=(",
        1,
    )[0]


def _write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    path.chmod(0o755)


class TriumvirateGateProfileTest(unittest.TestCase):
    def test_shared_repository_compilers_ignore_ambient_compiler_spelling(self) -> None:
        script = '''
source "$1"
args=()
append_native_determinism_bazel_args args "$2" /missing/git/root "$3" /vaso-bazel-ob /vaso/work 1234567890
printf '%s\\n' "${args[@]}"
'''
        for line in ("cu129", "cu130"):
            with self.subTest(line=line):
                result = subprocess.run(
                    ["bash", "-c", script, "test", str(NATIVE_DETERMINISM_ARGS_SH), line, COMMIT],
                    env={**os.environ, "CC": "/ambient/compiler", "CXX": "/ambient/compiler++"},
                    capture_output=True, text=True, check=True,
                )
                args = result.stdout.splitlines()
                self.assertEqual([arg for arg in args if arg.startswith("--repo_env=CC=")], ["--repo_env=CC=gcc"])
                self.assertEqual([arg for arg in args if arg.startswith("--repo_env=CXX=")], ["--repo_env=CXX=g++"])
                self.assertNotIn("/ambient/", result.stdout)

    def test_insula_gate_writes_profile_acceptance_file(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")

        self.assertIn('acceptance_out="${acceptance_out:-acceptance-$profile-$line.json}"', text)
        self.assertIn("--profile \"$profile\"", text)

    def test_default_prefixes_follow_the_fixed_insula_output_base_mount(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")
        profile_args = _bash_function_body(text, "bazel_profile_command_args")
        release_build = _bash_function_body(text, "bazel_profile_build_stage")

        self.assertIn('ob="$(native_determinism_fixed_output_base_mount)"', text)
        self.assertNotIn('/vaso/lines/$line/cache/bazel/output-base}', text)
        self.assertIn('release_bazel_ob="$ob"', text)
        self.assertIn('"--action_env=VASO_BAZEL_OB=$release_bazel_ob"', profile_args)
        self.assertIn("bazel_profile_command_args args build", release_build)
        self.assertIn("bazel_profile_command_args args cquery", text)
        self.assertNotIn('ob="/vaso/lines/$line/cache/bazel/output-base"', text)

    def test_release_flag_adds_static_release_stages_and_fast_mode_stays_unchanged(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")

        self.assertIn("release_mode=0", text)
        self.assertRegex(text, r"--release\)\s+release_mode=1")
        self.assertIn('if [[ "$release_mode" == "1" ]]; then', text)
        self.assertIn('"cubin_guard"', text)
        self.assertIn("tools/cubin_guard.py", text)
        self.assertIn('"relocatability"', text)
        self.assertIn("tools/relocatability_guard.py", text)

    def test_release_mode_builds_wheels_and_runs_release_only_checks_after_fast_gate(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")
        release_block = text.split('if [[ "$release_mode" == "1" ]]; then', 1)[1]
        fast_tail = text.split("python3 tools/triumvirate_acceptance.py", 1)[0]

        for target in (
            "//native/pytorch:pytorch_release_wheel",
            "//native/torchvision:torchvision_release_wheel",
            "//native/torchaudio:torchaudio_release_wheel",
            "//native/triton:triton_release_wheel",
            "//native/common:torch_runtime_release_wheelhouse",
            "//native/jaxlib:jaxlib_release_wheels",
        ):
            self.assertIn(target, text)
        self.assertIn('"release_wheels"', release_block)
        self.assertIn('"cubin_guard"', release_block)
        helper = NATIVE_DETERMINISM_ARGS_SH.read_text(encoding="utf-8")
        self.assertIn("--//native/common:release_line=", helper)
        self.assertIn("--//native/common:release_git_sha=", helper)
        self.assertIn("append_native_determinism_bazel_args out_args", text)
        self.assertNotIn("workloads/" + "parity/run_tests.py", text)
        self.assertNotIn("workloads/" + "parity/bench.py", text)
        self.assertIn('"release_dir"', release_block)
        self.assertIn('"release_workloads"', release_block)
        self.assertIn('"release_gates"', release_block)
        self.assertIn("foundry/insula.sh", release_block)
        self.assertIn('INSULA_RELEASE_RUN_ID=$release_workloads_run_id', release_block)
        self.assertIn('INSULA_RELEASE_RUN_ID=$release_gates_run_id', release_block)
        self.assertIn("--release \"$release_dir\"", release_block)
        self.assertIn("--gpus 8", release_block)
        self.assertIn("-m workloads.run_workloads", release_block)
        self.assertIn('--out "$release_workloads_sb"', release_block)
        self.assertIn("native/pytorch/gates.py", release_block)
        self.assertIn("--from-release", release_block)
        self.assertIn("tools/sbom.py", text)
        self.assertIn("tools/release_dir.py", text)
        self.assertIn("--release-wheel-manifest", release_block)
        self.assertIn("--runtime-wheel-manifest", text)
        self.assertIn("--release-workload-summary", release_block)
        self.assertIn("--release-gate-results", release_block)
        self.assertIn("--release-sbom", release_block)
        self.assertIn("release_wheel_manifest_targets=(", text)
        self.assertIn("pytorch=//tools:pytorch_release_wheel_manifest", text)
        self.assertIn("jaxlib=//tools:jaxlib_release_wheels_manifest", text)
        release_wheels_stage = text.split("release_wheels_stage() {", 1)[1].split("\n}", 1)[0]
        # The manifest cquery runs with --nofetch, so it must follow the release
        # build: a fresh output base has not fetched the wheel rules' repos yet.
        self.assertLess(
            release_wheels_stage.index("bazel_release_build_stage"),
            release_wheels_stage.index("assert_named_cquery_files release_wheel_manifest_targets"),
        )
        self.assertLess(
            release_wheels_stage.index("bazel_release_build_stage"),
            release_wheels_stage.index("assert_named_cquery_files release_runtime_wheel_manifest_targets"),
        )
        cubin_stage = text.split("release_cubin_guard_stage() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("--cuda-prefix-file \"$cuda_prefix_file\"", cubin_stage)
        self.assertIn('$release_bazel_ob/external/+cuda_native+cuda_native/prefix_path.txt', cubin_stage)
        self.assertNotIn("release_reference_prefix", release_block)
        self.assertNotIn("functional_parity", release_block)
        self.assertNotIn("performance_parity", release_block)
        self.assertNotIn("--release-parity", release_block)

        self.assertLess(fast_tail.index('"workloads"'), fast_tail.index('run_release_stage "release_wheels"'))
        self.assertLess(release_block.index('run_release_stage "release_wheels"'), release_block.index('run_release_stage "cubin_guard"'))
        self.assertLess(release_block.index('run_release_stage "cubin_guard"'), release_block.index('"release_workloads" \\'))

    def test_release_mode_skips_downstream_release_stages_after_upstream_failure(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")
        release_block = text.split('if [[ "$release_mode" == "1" ]]; then', 1)[1]

        self.assertIn("skip_stage_upstream_failed()", text)
        self.assertIn("skipped (upstream failed)", text)
        self.assertIn("run_release_stage()", text)
        for stage in (
            "release_wheels",
            "cubin_guard",
            "relocatability",
            "sbom",
            "release_dir",
        ):
            self.assertIn(f'run_release_stage "{stage}"', release_block)
        self.assertIn('run_release_stage \\\n    "release_workloads"', release_block)
        self.assertIn('run_release_stage \\\n      "release_gates"', release_block)
        self.assertIn('if [[ "$release_pipeline_failed" == "0" ]]; then', release_block)

    def test_upstream_native_prefix_failure_reports_release_stages_skipped(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
            root = Path(tmp)
            (root / "scripts/agents").mkdir(parents=True)
            (root / "tools").mkdir()
            gate_copy = root / "scripts/agents/triumvirate-gate-insula.sh"
            shutil.copy2(INSULA_GATE, gate_copy)
            gate_text = gate_copy.read_text(encoding="utf-8")
            gate_text = gate_text.replace(
                "/vaso/lines/$line/state/native",
                "${VASO_TEST_LINE_STATE_ROOT:?}/$line/state/native",
            )
            gate_copy.write_text(gate_text, encoding="utf-8")
            shutil.copy2(NATIVE_DETERMINISM_ARGS_SH, root / "tools/native_determinism_args.sh")
            shutil.copy2(EXPERIMENT_ROOT / "tools/triumvirate_acceptance.py", root / "tools/triumvirate_acceptance.py")

            for tool in (
                "one_llvm_graph_guard.py",
                "single_copy_cuda_graph_guard.py",
                "profile_guard.py",
                "compiler_pathway_guard.py",
                "compiler_provenance.py",
                "runtime_compiler_assertions.py",
                "abi_invariants.py",
                "jaxlib_deps_import_check.py",
            ):
                _write_executable(root / "tools" / tool, "#!/usr/bin/env python3\nraise SystemExit(0)\n")
            _write_executable(root / "native/pytorch/gates.py", "#!/usr/bin/env python3\nraise SystemExit(0)\n")
            _write_executable(root / "workloads/run_workloads.py", "#!/usr/bin/env python3\nraise SystemExit(0)\n")
            (root / "workloads/__init__.py").write_text("", encoding="utf-8")
            (root / "rootfs/cuda_ecosystem.lock.json").parent.mkdir(parents=True, exist_ok=True)
            (root / "rootfs/cuda_ecosystem.lock.json").write_text("{}\n", encoding="utf-8")
            rootfs_manifest = root / "rootfs-bundle.json"
            rootfs_manifest.write_text("{}\n", encoding="utf-8")
            (root / "acceptance-waivers.json").write_text("[]\n", encoding="utf-8")
            _write_executable(root / "rootfs/verify_cuda_ecosystem.py", "#!/usr/bin/env python3\nraise SystemExit(0)\n")

            fake_bin = root / "bin"
            fake_log = root / "fake-bazel.log"
            _write_executable(
                fake_bin / "bazel",
                """
                #!/usr/bin/env bash
                printf '%s\n' "$*" >> "$FAKE_BAZEL_LOG"
                echo native prefix build failed >&2
                exit 42
                """,
            )
            python_prefix = root / "prefixes/python"
            _write_executable(python_prefix / "bin/python3", "#!/usr/bin/env bash\nexec /usr/bin/python3 \"$@\"\n")
            prefixes = {
                "torch": root / "prefixes/torch",
                "torchvision": root / "prefixes/torchvision",
                "torchaudio": root / "prefixes/torchaudio",
                "triton": root / "prefixes/triton",
                "runtime": root / "prefixes/runtime",
            }
            for prefix in prefixes.values():
                prefix.mkdir(parents=True)
            out_dir = root / "out"
            env = {
                **os.environ,
                "FAKE_BAZEL_LOG": str(fake_log),
                "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
                "VASO_BAZEL_EXE": str(fake_bin / "bazel"),
                "VASO_BAZEL_OB": str(root / "bazel-ob"),
                "VASO_ESTATE_ROOT": str(root / "estate"),
                "VASO_IN_INSULA": "1",
                "VASO_REPO_ROOT": str(root),
                "VASO_ROOTFS_BUNDLE_MANIFEST": str(rootfs_manifest),
                "VASO_TEST_LINE_STATE_ROOT": str(root / "line-state"),
                "VASO_NATIVE_PYTORCH_TOKEN": "build-native-pytorch",
                "VASO_NATIVE_TORCHVISION_TOKEN": "build-native-torchvision",
                "VASO_NATIVE_TORCHAUDIO_TOKEN": "build-native-torchaudio",
                "VASO_NATIVE_TRITON_TOKEN": "build-native-triton",
            }

            result = subprocess.run(
                [
                    "bash",
                    str(root / "scripts/agents/triumvirate-gate-insula.sh"),
                    "--profile",
                    "torch",
                    "--line",
                    "cu130",
                    "--commit",
                    COMMIT,
                    "--source-date-epoch",
                    "0",
                    "--out-dir",
                    str(out_dir),
                    "--acceptance-out",
                    str(out_dir / "acceptance.json"),
                    "--release",
                    "--python-prefix",
                    str(python_prefix),
                    "--torch-prefix",
                    str(prefixes["torch"]),
                    "--torchvision-prefix",
                    str(prefixes["torchvision"]),
                    "--torchaudio-prefix",
                    str(prefixes["torchaudio"]),
                    "--triton-prefix",
                    str(prefixes["triton"]),
                    "--runtime-prefix",
                    f"runtime={prefixes['runtime']}",
                ],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            results = [
                json.loads(line)
                for line in (out_dir / "sub-results.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            by_name = {item["name"]: item for item in results}
            self.assertEqual(by_name["native_prefixes"]["returncode"], 42)
            for stage in (
                "release_wheels",
                "cubin_guard",
                "relocatability",
                "sbom",
                "release_dir",
                "release_workloads",
                "release_gates",
            ):
                self.assertEqual(by_name[stage]["verdict"], "skipped (upstream failed)")
                self.assertIn("skipped (upstream failed)", Path(by_name[stage]["stderr"]).read_text(encoding="utf-8"))
            self.assertNotIn("release_wheel", fake_log.read_text(encoding="utf-8"))
            acceptance_doc = json.loads((out_dir / "acceptance.json").read_text(encoding="utf-8"))
            acceptance_by_name = {item["name"]: item for item in acceptance_doc["sub_results"]}
            self.assertEqual(acceptance_by_name["release_wheels"]["verdict"], "skipped (upstream failed)")
            self.assertNotIn("release", acceptance_doc)

    def test_default_action_prefixes_are_resolved_from_transitioned_cquery_outputs(self) -> None:
        for profile in ("torch", "jax"):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
                root = Path(tmp)
                (root / "scripts/agents").mkdir(parents=True)
                (root / "tools").mkdir()
                shutil.copy2(INSULA_GATE, root / "scripts/agents/triumvirate-gate-insula.sh")

                helper = NATIVE_DETERMINISM_ARGS_SH.read_text(encoding="utf-8")
                helper = helper.replace(
                    'printf "%s\\n" "/vaso-bazel-ob"',
                    'printf "%s\\n" "${VASO_TEST_BAZEL_OB:?}"',
                )
                (root / "tools/native_determinism_args.sh").write_text(helper, encoding="utf-8")

                stale_marker = root / "stale-prefix-used"
                stage_log = root / "stage-args.log"
                fake_bazel_log = root / "fake-bazel.log"
                ob = root / "bazel-ob"
                stale_bin = ob / "execroot/_main/bazel-out/k8-fastbuild/bin/native"
                transitioned_bin = ob / "execroot/_main/bazel-out/k8-fastbuild-ST-feedface/bin/native"
                package_to_prefix = {
                    "pytorch": "pytorch_action_prefix",
                    "torchvision": "torchvision_action_prefix",
                    "torchaudio": "torchaudio_action_prefix",
                    "triton": "triton_action_prefix",
                    "jaxlib": "jaxlib_action_prefix",
                }
                for package, prefix_name in package_to_prefix.items():
                    (stale_bin / package / prefix_name).mkdir(parents=True)
                    (stale_bin / package / prefix_name / "STALE").write_text("", encoding="utf-8")
                    (transitioned_bin / package / prefix_name).mkdir(parents=True)

                python_prefix = ob / "external/+python_native+python_313_native/prefix"
                _write_executable(python_prefix / "bin/python3", "#!/usr/bin/env bash\nexec /usr/bin/python3 \"$@\"\n")

                stale_check_tool = f"""
                #!/usr/bin/env python3
                import os
                import sys

                args = sys.argv[1:]
                with open(os.environ["STAGE_ARG_LOG"], "a", encoding="utf-8") as fh:
                    fh.write(os.path.basename(sys.argv[0]) + "\\t" + "\\t".join(args) + "\\n")
                if any("k8-fastbuild/bin/native" in arg for arg in args):
                    with open(os.environ["STALE_PREFIX_MARKER"], "w", encoding="utf-8") as fh:
                        fh.write("\\n".join(args))
                    print("stale native action prefix was used", file=sys.stderr)
                    raise SystemExit(23)
                raise SystemExit(0)
                """
                for tool in (
                    "one_llvm_graph_guard.py",
                    "single_copy_cuda_graph_guard.py",
                    "profile_guard.py",
                    "compiler_pathway_guard.py",
                    "compiler_provenance.py",
                    "runtime_compiler_assertions.py",
                    "abi_invariants.py",
                    "jaxlib_deps_import_check.py",
                ):
                    _write_executable(root / "tools" / tool, stale_check_tool)
                _write_executable(root / "native/pytorch/gates.py", stale_check_tool)
                _write_executable(root / "workloads/run_workloads.py", stale_check_tool)
                (root / "workloads/__init__.py").write_text("", encoding="utf-8")
                _write_executable(
                    root / "tools/triumvirate_acceptance.py",
                    """
                    #!/usr/bin/env python3
                    import os
                    import sys

                    if os.path.exists(os.environ["STALE_PREFIX_MARKER"]):
                        print("stale prefix marker exists", file=sys.stderr)
                        raise SystemExit(1)
                    out = None
                    for index, arg in enumerate(sys.argv):
                        if arg == "--out" and index + 1 < len(sys.argv):
                            out = sys.argv[index + 1]
                            break
                    if out:
                        with open(out, "w", encoding="utf-8") as fh:
                            fh.write("{}\\n")
                    raise SystemExit(0)
                    """,
                )
                (root / "rootfs/cuda_ecosystem.lock.json").parent.mkdir(parents=True, exist_ok=True)
                (root / "rootfs/cuda_ecosystem.lock.json").write_text("{}\n", encoding="utf-8")
                rootfs_manifest = root / "rootfs-bundle.json"
                rootfs_manifest.write_text("{}\n", encoding="utf-8")
                (root / "acceptance-waivers.json").write_text("[]\n", encoding="utf-8")
                _write_executable(root / "rootfs/verify_cuda_ecosystem.py", stale_check_tool)

                fake_bin = root / "bin"
                _write_executable(
                    fake_bin / "bazel",
                    """
                    #!/usr/bin/env bash
                    set -euo pipefail
                    printf '%s\n' "$*" >> "$FAKE_BAZEL_LOG"
                    command_name=""
                    for arg in "$@"; do
                      case "$arg" in
                        build|cquery) command_name="$arg" ;;
                      esac
                    done
                    if [[ "$command_name" == "build" ]]; then
                      echo "unexpected build in fast-mode prefix resolution" >&2
                      exit 44
                    fi
                    if [[ "$command_name" != "cquery" ]]; then
                      echo "unexpected bazel command: $*" >&2
                      exit 45
                    fi
                    target="${@: -1}"
                    case "$target" in
                      //tools:pytorch_action_prefix)
                        echo "$FAKE_BAZEL_OB/execroot/_main/bazel-out/k8-fastbuild-ST-feedface/bin/native/pytorch/pytorch_action_prefix" ;;
                      //tools:torchvision_action_prefix)
                        echo "$FAKE_BAZEL_OB/execroot/_main/bazel-out/k8-fastbuild-ST-feedface/bin/native/torchvision/torchvision_action_prefix" ;;
                      //tools:torchaudio_action_prefix)
                        echo "$FAKE_BAZEL_OB/execroot/_main/bazel-out/k8-fastbuild-ST-feedface/bin/native/torchaudio/torchaudio_action_prefix" ;;
                      //tools:triton_action_prefix)
                        echo "$FAKE_BAZEL_OB/execroot/_main/bazel-out/k8-fastbuild-ST-feedface/bin/native/triton/triton_action_prefix" ;;
                      //tools:jaxlib_action_prefix)
                        echo "$FAKE_BAZEL_OB/execroot/_main/bazel-out/k8-fastbuild-ST-feedface/bin/native/jaxlib/jaxlib_action_prefix" ;;
                      *)
                        echo "unknown cquery target: $target" >&2
                        exit 46 ;;
                    esac
                    """,
                )

                out_dir = root / f"out-{profile}"
                env = {
                    **os.environ,
                    "FAKE_BAZEL_LOG": str(fake_bazel_log),
                    "FAKE_BAZEL_OB": str(ob),
                    "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
                    "STAGE_ARG_LOG": str(stage_log),
                    "STALE_PREFIX_MARKER": str(stale_marker),
                    "VASO_BAZEL_EXE": str(fake_bin / "bazel"),
                    "VASO_ESTATE_ROOT": str(root / "estate"),
                    "VASO_IN_INSULA": "1",
                    "VASO_REPO_ROOT": str(root),
                    "VASO_ROOTFS_BUNDLE_MANIFEST": str(rootfs_manifest),
                    "VASO_TEST_BAZEL_OB": str(ob),
                }

                result = subprocess.run(
                    [
                        "bash",
                        str(root / "scripts/agents/triumvirate-gate-insula.sh"),
                        "--profile",
                        profile,
                        "--line",
                        "cu130",
                        "--commit",
                        COMMIT,
                        "--source-date-epoch",
                        "0",
                        "--out-dir",
                        str(out_dir),
                        "--acceptance-out",
                        str(out_dir / "acceptance.json"),
                    ],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(stale_marker.exists())
                fake_bazel_text = fake_bazel_log.read_text(encoding="utf-8")
                self.assertIn("cquery", fake_bazel_text)
                self.assertIn("--nofetch", fake_bazel_text)
                self.assertIn("--repository_disable_download", fake_bazel_text)
                self.assertNotIn(" build ", f" {fake_bazel_text} ")
                logged_stage_args = stage_log.read_text(encoding="utf-8")
                self.assertIn("k8-fastbuild-ST-feedface", logged_stage_args)
                self.assertNotIn("k8-fastbuild/bin/native", logged_stage_args)

    def test_release_bazel_builds_forward_m2b_determinism_inputs(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")
        profile_args = _bash_function_body(text, "bazel_profile_command_args")

        self.assertIn('native_determinism_source_date_epoch "$REPO_ROOT" "$commit"', text)
        self.assertIn("--repo_env=SOURCE_DATE_EPOCH=$(native_repo_source_date_epoch)", profile_args)
        self.assertIn(
            'append_native_determinism_bazel_args out_args "$line" "$REPO_ROOT" "$commit" "$release_bazel_ob" "/vaso/lines/$line/work" "$source_date_epoch"',
            profile_args,
        )
        self.assertNotIn("--//native/common:deterministic_seed=", profile_args)
        self.assertNotIn("--//native/common:native_action_cuda_home=", profile_args)

    def test_torch_release_dir_includes_bazel_owned_runtime_dependency_wheelhouse(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")
        torch_case = _torch_profile_block(text)

        self.assertIn("//native/common:torch_runtime_release_wheelhouse", torch_case)
        self.assertIn("release_runtime_wheel_manifests=(", torch_case)
        self.assertIn(
            "torch-runtime-deps=$release_bazel_bin/native/common/torch_runtime_release_wheelhouse_manifest.json",
            torch_case,
        )
        self.assertIn("@py_setuptools_native//:prefix", torch_case)
        self.assertIn('"py-setuptools=$ob/external/+py_setuptools_native+py_setuptools_native/prefix"', text)

    def test_jax_release_dir_includes_bazel_owned_runtime_dependency_wheelhouse(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")
        jax_case = _jax_profile_block(text)

        self.assertIn("//native/common:jax_runtime_release_wheelhouse", jax_case)
        self.assertIn("release_runtime_wheel_manifests=(", jax_case)
        self.assertIn(
            "jax-runtime-deps=$release_bazel_bin/native/common/jax_runtime_release_wheelhouse_manifest.json",
            jax_case,
        )
        for package in (
            "py-absl-py",
            "py-ml-dtypes",
            "py-numpy",
            "py-opt-einsum",
            "py-scipy",
        ):
            self.assertIn(f'"{package}=', _bash_array_body(text, "jaxlib_deps_prefixes"))

    def test_release_mode_builds_native_prefixes_before_fast_runtime_checks(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")
        torch_case = _torch_profile_block(text)
        jax_case = _jax_profile_block(text)

        self.assertIn('"native_prefixes"', torch_case)
        self.assertIn('"native_prefixes"', jax_case)
        for target in (
            "//native/pytorch:pytorch_action",
            "//native/torchvision:torchvision_action",
            "//native/torchaudio:torchaudio_action",
            "//native/triton:triton_action",
        ):
            self.assertIn(target, torch_case)
        self.assertIn("//native/jaxlib:jaxlib_action", jax_case)
        self.assertLess(torch_case.index('"native_prefixes"'), torch_case.index('"compiler_provenance_torch"'))
        self.assertLess(torch_case.index('"native_prefixes"'), torch_case.index('"runtime_compilers"'))
        self.assertLess(jax_case.index('"native_prefixes"'), jax_case.index('"compiler_provenance_jaxlib"'))
        self.assertLess(jax_case.index('"native_prefixes"'), jax_case.index('"runtime_compilers"'))

    def test_torch_release_native_prefixes_cover_jaxlib_dependency_import_prefixes(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")
        torch_case = _torch_profile_block(text)
        native_targets = set(re.findall(r"@([A-Za-z0-9_]+)//:prefix", _bash_array_body(torch_case, "native_prefix_targets")))
        jaxlib_deps_repos = {
            match.group(1)
            for match in re.finditer(r"\+[A-Za-z0-9_]+\+(?P<repo>[A-Za-z0-9_]+)/prefix", _bash_array_body(text, "jaxlib_deps_prefixes"))
        }

        self.assertTrue(jaxlib_deps_repos)
        self.assertEqual(sorted(jaxlib_deps_repos - native_targets), [])
        self.assertNotIn("py_jax_native", native_targets)
        self.assertNotIn("//native/jaxlib:jaxlib_action", torch_case)

    def test_release_mode_preflights_native_build_tokens_before_prefix_builds(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")
        torch_case = _torch_profile_block(text)
        jax_case = text.split("else\n  jax_runtime_args=()", 1)[1].split("\n  acceptance_prefix_args=(", 1)[0]
        preflight = text.split("require_release_tokens() {", 1)[1].split("\nbazel_profile_build_stage() {", 1)[0]

        self.assertIn("require_release_tokens()", text)
        for env_name, token in (
            ("VASO_NATIVE_PYTORCH_TOKEN", "build-native-pytorch"),
            ("VASO_NATIVE_TORCHVISION_TOKEN", "build-native-torchvision"),
            ("VASO_NATIVE_TORCHAUDIO_TOKEN", "build-native-torchaudio"),
            ("VASO_NATIVE_TRITON_TOKEN", "build-native-triton"),
        ):
            self.assertIn(f'require_token {env_name} "{token}"', preflight)
        self.assertIn('require_token jaxlib_token "build-native-llvm"', preflight)
        self.assertLess(torch_case.index("require_release_tokens"), torch_case.index('"native_prefixes"'))
        self.assertLess(jax_case.index("require_release_tokens"), jax_case.index('"native_prefixes"'))

    def test_tokened_release_builds_include_native_determinism_args(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")
        helper = NATIVE_DETERMINISM_ARGS_SH.read_text(encoding="utf-8")
        profile_args = _bash_function_body(text, "bazel_profile_command_args")

        self.assertIn('. "$EXPERIMENT_ROOT/tools/native_determinism_args.sh"', text)
        self.assertIn("--source-date-epoch)", text)
        self.assertIn(
            'append_native_determinism_bazel_args out_args "$line" "$REPO_ROOT" "$commit" "$release_bazel_ob" "/vaso/lines/$line/work" "$source_date_epoch"',
            profile_args,
        )
        self.assertIn("--//native/common:source_date_epoch=", helper)
        self.assertIn("--//native/common:deterministic_seed=", helper)
        self.assertIn("--//native/common:deterministic_prefix_map_roots=", helper)
        self.assertLess(
            profile_args.index("append_native_determinism_bazel_args"),
            profile_args.index("add_release_tokens out_args"),
        )

    def test_release_mode_caps_default_max_jobs_before_bazel_flags(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")
        release_jobs = re.search(
            r"release_jobs\(\) \{\n(?P<body>.*?)\n\}",
            text,
            re.S,
        )
        self.assertIsNotNone(release_jobs)
        assert release_jobs is not None
        body = release_jobs.group("body")
        profile_args = _bash_function_body(text, "bazel_profile_command_args")
        release_build = _bash_function_body(text, "bazel_profile_build_stage")
        write_job_state = _bash_function_body(text, "write_native_job_state")

        self.assertIn("RELEASE_MAX_JOBS_CAP=96", text)
        self.assertIn("jobs > RELEASE_MAX_JOBS_CAP", body)
        release_max_jobs = re.search(
            r"release_max_jobs\(\) \{\n(?P<body>.*?)\n\}",
            text,
            re.S,
        )
        self.assertIsNotNone(release_max_jobs)
        assert release_max_jobs is not None
        max_jobs_body = release_max_jobs.group("body")
        self.assertIn("requested > RELEASE_MAX_JOBS_CAP", max_jobs_body)

        self.assertIn('jobs="$(release_jobs)"', profile_args)
        self.assertIn('pytorch_jobs="$(release_max_jobs VASO_PYTORCH_MAX_JOBS)"', profile_args)
        self.assertIn('jaxlib_jobs="$(release_max_jobs VASO_JAXLIB_MAX_JOBS)"', profile_args)
        self.assertIn('torchvision_jobs="$(release_max_jobs VASO_TORCHVISION_MAX_JOBS)"', profile_args)
        self.assertIn('torchaudio_jobs="$(release_max_jobs VASO_TORCHAUDIO_MAX_JOBS)"', profile_args)
        self.assertIn('write_native_job_state || return', release_build)
        self.assertIn('printf \'%s\\n\' "$pytorch_jobs" > "/vaso/lines/$line/state/native/pytorch_jobs.txt"', write_job_state)
        for flag in (
            "--//native/pytorch:max_jobs=${VASO_PYTORCH_MAX_JOBS:-$jobs}",
            "--//native/torchvision:max_jobs=${VASO_TORCHVISION_MAX_JOBS:-$jobs}",
            "--//native/torchaudio:max_jobs=${VASO_TORCHAUDIO_MAX_JOBS:-$jobs}",
        ):
            self.assertNotIn(flag, profile_args)
        self.assertNotIn("--//native/pytorch:max_jobs=$pytorch_jobs", profile_args)
        self.assertNotIn("--action_env=VASO_PYTORCH_MAX_JOBS=$pytorch_jobs", profile_args)
        self.assertNotIn("--//native/torchvision:max_jobs=", profile_args)
        self.assertNotIn("--//native/torchaudio:max_jobs=", profile_args)
        self.assertIn("--action_env=VASO_TORCHVISION_MAX_JOBS=$torchvision_jobs", profile_args)
        self.assertIn("--action_env=VASO_TORCHAUDIO_MAX_JOBS=$torchaudio_jobs", profile_args)
        self.assertIn("bazel_profile_build_stage release_wheel_targets", text)
        self.assertIn("bazel_profile_build_stage native_prefix_targets", text)

    def test_release_mode_runs_profile_specific_workloads_from_the_release_venv(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")
        release_block = text.split('if [[ "$release_mode" == "1" ]]; then', 1)[1]
        torch_case = text.split('if [[ "$profile" == "torch" ]]; then', 1)[1].split("else\n  jax_runtime_args=()", 1)[0]
        jax_case = _jax_profile_block(text)

        self.assertIn("release_gate_workloads=(\"${torch_workloads[@]}\")", torch_case)
        self.assertIn("release_gate_workloads=(\"${jax_workloads[@]}\" \"${jax_model_workloads[@]}\")", jax_case)
        self.assertIn("release_gate_names=(jax_psum_2gpu)", jax_case)
        self.assertRegex(release_block, r'--only "\$\(join_csv "\$\{release_gate_workloads\[@\]\}"\)"')
        self.assertIn("--from-release", release_block)

    def test_consumer_conformance_runs_profile_specific_python_consumers_from_release_dir(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")
        release_block = text.split('if [[ "$release_mode" == "1" ]]; then', 1)[1]

        self.assertIn('"consumer_conformance"', release_block)
        self.assertIn("examples/consumers/run.sh", release_block)
        self.assertIn("--release-dir \"$release_dir\"", release_block)
        self.assertIn("VASO_PROFILE=\"$profile\"", release_block)
        self.assertIn("INSULA_RELEASE_PYTHON_PREFIX_HOST=$release_python_prefix", release_block)
        self.assertNotIn('env VASO_GPUS=0 "$EXPERIMENT_ROOT/examples/consumers/run.sh"', release_block)
        self.assertLess(
            release_block.index('run_release_stage "release_dir"'),
            release_block.index('"consumer_conformance"'),
        )

    def test_torch_jax_and_jax_model_workload_lists_are_split_and_w4_is_absent(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")
        torch_match = re.search(r"torch_workloads=\((?P<body>[^)]*)\)", text, re.S)
        jax_match = re.search(r"jax_workloads=\((?P<body>[^)]*)\)", text, re.S)
        jax_model_match = re.search(r"jax_model_workloads=\((?P<body>[^)]*)\)", text, re.S)
        self.assertIsNotNone(torch_match)
        self.assertIsNotNone(jax_match)
        self.assertIsNotNone(jax_model_match)
        assert torch_match is not None
        assert jax_match is not None
        assert jax_model_match is not None
        torch_body = torch_match.group("body")
        jax_body = jax_match.group("body")
        jax_model_body = jax_model_match.group("body")

        for workload in ("W1a", "W1b", "W1c", "W1d", "W2a", "W2b", "W2c"):
            self.assertIn(workload, torch_body)
        for workload in ("W6a", "W6b", "W6c", "W6d", "W6e", "W6f", "W6g", "W6h", "W6i", "W6j", "W6w"):
            self.assertIn(workload, torch_body)
        torch_workloads = re.findall(r"\bW[0-9][A-Za-z0-9]*\b", torch_body)
        self.assertEqual(len(torch_workloads), len(set(torch_workloads)))
        self.assertNotIn("W3", torch_body)
        self.assertNotIn("W4", torch_body)
        self.assertIn("W3a", jax_body)
        self.assertIn("W3b", jax_body)
        self.assertIn("W3c", jax_body)
        self.assertNotIn("W5", jax_body)
        self.assertNotIn("W4", jax_body)
        for workload in ("W5a", "W5b", "W5c", "W5d", "W5e", "W5f", "W5g"):
            self.assertIn(workload, jax_model_body)
        self.assertNotIn("W3", jax_model_body)
        self.assertNotIn("W4", jax_model_body)

    def test_jax_profile_runs_real_model_workloads_as_their_own_stage(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")

        self.assertIn('"jax_model_workloads" \\', text)
        self.assertIn('--out "$out_dir/jax-model-workloads"', text)
        self.assertIn('--only "$(join_csv "${jax_model_workloads[@]}")"', text)

    def test_torch_profile_np13_gates_receive_vision_audio_runtime_prefixes(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")
        runtime_body = _bash_array_body(_bash_function_body(text, "configure_default_runtime_prefixes"), "torch_runtime_prefixes")

        self.assertIn("py-pillow=", runtime_body)
        self.assertIn("libjpeg-turbo=", runtime_body)
        self.assertIn("libpng=", runtime_body)
        self.assertIn("torchvision=$torchvision_prefix", runtime_body)
        self.assertIn("torchaudio=$torchaudio_prefix", runtime_body)

    def test_torch_profile_np13_gates_receive_triton_runtime_prefix(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")
        runtime_body = _bash_array_body(_bash_function_body(text, "configure_default_runtime_prefixes"), "torch_runtime_prefixes")

        self.assertIn("triton=$triton_prefix", runtime_body)

    def test_jax_profile_workloads_receive_absl_runtime_prefix(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")
        runtime_match = re.search(
            r"(?ms)^  jaxlib_deps_prefixes=\(\n(?P<body>.*?)^  \)",
            text,
        )
        self.assertIsNotNone(runtime_match)
        assert runtime_match is not None
        runtime_body = runtime_match.group("body")

        self.assertIn("py-absl-py=", runtime_body)
        self.assertIn("+py_absl_py_native+py_absl_py_native/prefix", runtime_body)
        self.assertRegex(text, r'jax_runtime_prefixes=\("\$\{jaxlib_deps_prefixes\[@\]\}"\)')

    def test_jax_profile_runs_runtime_and_abi_gap_stages(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")
        jax_gate = _jax_profile_block(text)

        self.assertIn('"runtime_compilers" \\', jax_gate)
        self.assertIn("tools/runtime_compiler_assertions.py", jax_gate)
        self.assertIn("--profile jax", jax_gate)
        self.assertIn('"abi_invariants_static" \\', jax_gate)
        self.assertIn("tools/abi_invariants.py", jax_gate)
        self.assertIn("--profile jax", jax_gate)
        self.assertIn("--prefix \"jaxlib=$jaxlib_prefix\"", jax_gate)
        self.assertIn('"abi_invariants_probe" \\', jax_gate)
        self.assertIn("--probe", jax_gate)

    def test_compiler_pathway_gate_does_not_mask_expected_failures(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")

        self.assertNotIn("--expect-failure", text)


if __name__ == "__main__":
    unittest.main()
