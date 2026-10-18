#!/usr/bin/env python3
"""Tests for rebuild_impact.py."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock
from pathlib import Path


SCRIPT = Path(__file__).with_name("rebuild_impact.py")
RUNFILES_GRAPH_ROOT = Path(__file__).resolve().parents[1]
RUNFILES_MANIFEST = RUNFILES_GRAPH_ROOT / "plans" / "native-inputs.json"
SPEC = importlib.util.spec_from_file_location("rebuild_impact", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
rebuild_impact = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rebuild_impact
SPEC.loader.exec_module(rebuild_impact)


def scratch_parent() -> str | None:
    return os.environ.get("TEST_TMPDIR") or os.environ.get("VASO_AGENT_IO_ROOT") or str(Path.cwd())


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def git_with_env(repo: Path, *args: str, env: dict[str, str]) -> str:
    merged = os.environ.copy()
    merged.update(env)
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=merged,
    ).stdout.strip()


def init_repo(root: Path, graph_dir: str = "graph") -> Path:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    graph_root = repo / graph_dir
    write(graph_root / "plans/native-inputs.json", json.dumps({
        "schema_version": 1,
        "common_inputs": [
            {"path": "tools/native_determinism_args.sh", "cause": "other", "status": "may-rebuild"},
            {"path": "scripts/agents/triumvirate-gate.sh", "cause": "flags", "status": "may-rebuild"},
            {"path": "scripts/agents/triumvirate-gate-insula.sh", "cause": "flags", "status": "may-rebuild"},
        ],
        "packages": {
            "zlib-ng": {
                "mnemonic": "ZlibNgActionPrefix",
                "inputs": [
                    {"path": "native/zlib_ng/zlib_ng_action.bzl", "cause": "other"},
                    {"path": "native/zlib_ng/source.sha256", "cause": "source"},
                    {"path": "native/common/native_action_toolchain.bzl", "cause": "toolchain"},
                    {"path": "run.sh", "cause": "flags"},
                ],
            },
            "pytorch": {
                "mnemonic": "PytorchNativePrefix",
                "inputs": [
                    {"path": "native/pytorch/action_driver.py", "cause": "other"},
                    {"path": "native/pytorch/plan.py", "cause": "flags"},
                    {"path": "run.sh", "cause": "flags"},
                ],
            },
        },
    }, indent=2) + "\n")
    write(graph_root / "native/zlib_ng/zlib_ng_action.bzl", "old\n")
    write(graph_root / "native/zlib_ng/source.sha256", "old\n")
    write(graph_root / "native/common/native_action_toolchain.bzl", "old\n")
    write(graph_root / "native/pytorch/action_driver.py", "old\n")
    write(graph_root / "native/pytorch/plan.py", "old\n")
    write(
        graph_root / "run.sh",
        'EXP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        + ('REPO_ROOT="$(cd "$EXP_DIR/../.." && pwd)"\n' if graph_dir.startswith("experiments/")
           else 'REPO_ROOT="$(cd "$EXP_DIR/.." && pwd)"\n')
        + "\n".join(f"# stable fixture line {index}" for index in range(20))
        + "\n",
    )
    write(graph_root / "tools/native_determinism_args.sh", "old\n")
    write(repo / "scripts/agents/triumvirate-gate.sh", 'EXP_DIR="$ROOT/experiments/spack-bazel-graph"\n')
    write(graph_root / "scripts/agents/triumvirate-gate-insula.sh", "old\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    return repo


def init_legacy_graph_repo(root: Path) -> Path:
    return init_repo(root, "experiments/spack-bazel-graph")


def init_repo_rule_native_fixture_repo(root: Path) -> Path:
    repo = init_repo(root)
    write(
        repo / "graph/dev.MODULE.bazel",
        textwrap.dedent(
            """\
            protobuf_native = use_repo_rule("//native/protobuf:protobuf.bzl", "protobuf_native")
            py_protobuf_native = use_repo_rule("//native/py_protobuf:py_protobuf.bzl", "py_protobuf_native")

            protobuf_native(
                name = "protobuf_native",
            )

            py_protobuf_native(
                name = "py_protobuf_native",
                protobuf_prefix_file = "@protobuf_native//:prefix_path.txt",
            )
            """
        ),
    )
    write(repo / "graph/native/protobuf/BUILD.bazel", 'exports_files(["protobuf.bzl"])\n')
    write(repo / "graph/native/protobuf/protobuf.bzl", "old\n")
    write(repo / "graph/native/py_protobuf/BUILD.bazel", 'exports_files(["py_protobuf.bzl"])\n')
    write(repo / "graph/native/py_protobuf/py_protobuf.bzl", "old\n")
    write(
        repo / "graph/native/pytorch/BUILD.bazel",
        textwrap.dedent(
            """\
            pytorch_action_prefix(
                name = "pytorch_action",
                protobuf_prefix_file = "@protobuf_native//:prefix_path.txt",
                py_protobuf_prefix_file = "@py_protobuf_native//:prefix_path.txt",
            )
            """
        ),
    )
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "repo-rule natives")
    return repo


def init_n1_5_fixture_repo(root: Path) -> tuple[Path, str, str]:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    write(
        repo / "graph/plans/native-inputs.json",
        json.dumps(rebuild_impact.default_manifest(), indent=2, sort_keys=True) + "\n",
    )
    write(
        repo / "graph/dev.MODULE.bazel",
        textwrap.dedent(
            """\
            module(name = "spack_graph")

            http_archive(
                name = "torchvision_v0_29_0_source",
                sha256 = "24be57d922927d8a2ac2e8f076f07c3447ddf8f1d25ddbb7b65578f36c9ab8e3",
                urls = ["https://example.invalid/vaso/torchvision-v0.29.0.tar.gz"],
            )
            """
        ),
    )
    git(repo, "add", ".")
    base_env = {
        "GIT_AUTHOR_DATE": "2026-10-07T07:56:09 +0000",
        "GIT_COMMITTER_DATE": "2026-10-07T07:56:09 +0000",
    }
    git_with_env(repo, "commit", "-q", "-m", "base", env=base_env)
    base_ref = git(repo, "rev-parse", "HEAD")

    write(
        repo / "graph/dev.MODULE.bazel",
        textwrap.dedent(
            """\
            module(name = "spack_graph")

            http_archive(
                name = "torchvision_v0_29_0_source",
                sha256 = "24be57d922927d8a2ac2e8f076f07c3447ddf8f1d25ddbb7b65578f36c9ab8e3",
                urls = ["https://example.invalid/vaso/torchvision-v0.29.0.tar.gz"],
            )

            http_file(
                name = "torchvision_v0_29_0_source_archive",
                downloaded_file_path = "torchvision-v0.29.0.tar.gz",
                sha256 = "24be57d922927d8a2ac2e8f076f07c3447ddf8f1d25ddbb7b65578f36c9ab8e3",
                urls = ["https://example.invalid/vaso/torchvision-v0.29.0.tar.gz"],
                dev_dependency = True,
            )
            """
        ),
    )
    git(repo, "add", ".")
    head_env = {
        "GIT_AUTHOR_DATE": "2026-10-07T09:35:53 +0000",
        "GIT_COMMITTER_DATE": "2026-10-07T09:35:53 +0000",
    }
    git_with_env(repo, "commit", "-q", "-m", "head", env=head_env)
    head_ref = git(repo, "rev-parse", "HEAD")
    return repo, base_ref, head_ref


def checked_in_manifest() -> Path:
    if RUNFILES_MANIFEST.exists():
        return RUNFILES_MANIFEST
    return Path.cwd() / "graph/plans/native-inputs.json"


class RepoRootTest(unittest.TestCase):
    def test_repo_root_is_the_parent_of_a_top_level_workspace(self) -> None:
        for name in ("foundry", "graph"):
            with self.subTest(name=name):
                self.assertEqual(rebuild_impact.repo_root_from_graph(Path("/r") / name), Path("/r"))

    def test_repo_root_is_two_levels_up_for_the_legacy_workspace(self) -> None:
        legacy = Path("/r/experiments/spack-bazel-graph")
        self.assertEqual(rebuild_impact.repo_root_from_graph(legacy), Path("/r"))


class RebuildImpactFastTest(unittest.TestCase):
    def test_gate_only_assignment_cannot_hide_a_run_script_change(self) -> None:
        """Independent review: gate EXP_DIR assignments are not run.sh root fixes."""
        for with_mount in (False, True):
            for mode in ("--worktree", "--staged", "--head"):
                with self.subTest(with_mount=with_mount, mode=mode), tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
                    repo = init_repo(Path(tmp), "foundry")
                    base = git(repo, "rev-parse", "HEAD")
                    target = repo / "foundry/run.sh"
                    invalid = target.read_text() + 'EXP_DIR="$ROOT/experiments/spack-bazel-graph"\n'
                    if with_mount:
                        invalid += "MP_ARGS+=(--mountpoint /workspace/third_party)\n"
                    write(target, invalid)
                    compare = [mode]
                    if mode != "--worktree":
                        git(repo, "add", ".")
                    if mode == "--head":
                        git(repo, "commit", "-q", "-m", "add gate-only assignment to run script")
                        compare.append("HEAD")
                    rc, payload, stderr = self.run_tool(repo, "--fast", "--base", base, *compare)
                    self.assertEqual(rc, 10, stderr)
                    self.assertEqual(payload["summary"]["highest_cause_class"], "flags")

    def test_reverse_foundry_root_is_not_exempt(self) -> None:
        for mode in ("--worktree", "--staged", "--head"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
                repo = init_repo(Path(tmp), "foundry")
                base = git(repo, "rev-parse", "HEAD")
                git(repo, "mv", "foundry", "graph")
                compare = [mode]
                if mode == "--head":
                    git(repo, "commit", "-q", "-m", "reverse foundry migration")
                    compare.append("HEAD")
                rc, payload, stderr = self.run_tool(repo, "--fast", "--base", base, *compare)
                self.assertEqual(rc, 10, stderr)
                self.assertEqual(payload["summary"]["highest_cause_class"], "flags")

    def test_identical_relocation_cannot_bypass_final_script_location_and_depth(self) -> None:
        """Independent review: byte equality alone hid the landing's root-depth hazard."""
        for reverse in (False, True):
            for mode in ("--worktree", "--staged", "--head"):
                with self.subTest(reverse=reverse, mode=mode), tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
                    repo = init_repo(Path(tmp)) if reverse else init_legacy_graph_repo(Path(tmp))
                    base = git(repo, "rev-parse", "HEAD")
                    if reverse:
                        (repo / "experiments").mkdir()
                        git(repo, "mv", "graph", "experiments/spack-bazel-graph")
                    else:
                        git(repo, "mv", "experiments/spack-bazel-graph", "foundry")
                    compare = [mode]
                    if mode == "--head":
                        git(repo, "commit", "-q", "-m", "move without updating script root")
                        compare.append("HEAD")
                    rc, payload, stderr = self.run_tool(repo, "--fast", "--base", base, *compare)
                    self.assertEqual(rc, 10, stderr)
                    self.assertEqual(payload["summary"]["highest_cause_class"], "flags")

    def test_valid_forward_relocation_controls_in_every_mode(self) -> None:
        for case in ("forward-capture", "graph-root-with-mount"):
            for mode in ("--worktree", "--staged", "--head"):
                with self.subTest(case=case, mode=mode), tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
                    repo = init_repo(Path(tmp)) if case == "forward-capture" else init_legacy_graph_repo(Path(tmp))
                    if case == "forward-capture":
                        target = repo / "graph/scripts/agents/triumvirate-gate-insula.sh"
                        write(target, "py_jax_0102_cu130_build_graph.json\n")
                        git(repo, "add", ".")
                        git(repo, "commit", "-q", "-m", "before forward relocation")
                    base = git(repo, "rev-parse", "HEAD")
                    if case == "forward-capture":
                        write(target, "graphs/py_jax_0102_cu130_build_graph.json\n")
                    else:
                        git(repo, "mv", "experiments/spack-bazel-graph", "foundry")
                        target = repo / "foundry/run.sh"
                        write(target, target.read_text().replace('$EXP_DIR/../.."', '$EXP_DIR/.."') + (
                            "MP_ARGS+=(--mountpoint /workspace/third_party)\n"
                            '  --ro-bind "$REPO_ROOT/third_party" /workspace/third_party\n'
                        ))
                    compare = [mode]
                    if mode != "--worktree":
                        git(repo, "add", ".")
                    if mode == "--head":
                        git(repo, "commit", "-q", "-m", "valid relocation")
                        compare.append("HEAD")
                    rc, payload, stderr = self.run_tool(repo, "--fast", "--base", base, *compare)
                    self.assertEqual(rc, 0, stderr)
                    self.assertEqual(payload["summary"]["highest_status"], "unchanged")

    def test_historical_relocation_exemptions_reject_reverse_bad_depth_and_wrong_script(self) -> None:
        """Replay .scratch/landing/follow-ups.md item 2; valid controls follow below."""
        for case in ("reverse-capture", "bad-depth-with-mount", "wrong-script"):
            for mode in ("--worktree", "--staged", "--head"):
                with self.subTest(case=case, mode=mode), tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
                    repo = init_repo(Path(tmp))
                    if case == "reverse-capture":
                        target = repo / "graph/scripts/agents/triumvirate-gate-insula.sh"
                        before = "graphs/py_jax_0102_cu130_build_graph.json\n"
                        after = "py_jax_0102_cu130_build_graph.json\n"
                    elif case == "bad-depth-with-mount":
                        target = repo / "graph/run.sh"
                        before = target.read_text()
                        after = before.replace('$EXP_DIR/.."', '$EXP_DIR/../.."') + (
                            "MP_ARGS+=(--mountpoint /workspace/third_party)\n"
                            '  --ro-bind "$REPO_ROOT/third_party" /workspace/third_party\n'
                        )
                    else:
                        target = repo / "run.sh"
                        before = 'REPO_ROOT="$(cd "$EXP_DIR/../.." && pwd)"\n'
                        after = 'REPO_ROOT="$(cd "$EXP_DIR/.." && pwd)"\n'
                    write(target, before)
                    git(repo, "add", ".")
                    git(repo, "commit", "-q", "--allow-empty", "-m", "before relocation")
                    base = git(repo, "rev-parse", "HEAD")
                    write(target, after)
                    compare = [mode]
                    if mode != "--worktree":
                        git(repo, "add", ".")
                    if mode == "--head":
                        git(repo, "commit", "-q", "-m", "invalid relocation")
                        compare.append("HEAD")
                    rc, payload, stderr = self.run_tool(repo, "--fast", "--base", base, *compare)
                    self.assertEqual(rc, 10, stderr)
                    self.assertEqual(payload["summary"]["highest_cause_class"], "flags")

    def test_historical_native_input_rename_keeps_deleted_side_in_every_mode(self) -> None:
        """Replay .scratch/landing/follow-ups.md item 1 without native builds."""
        for mode in ("--worktree", "--staged", "--head"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
                repo = init_repo(Path(tmp))
                base = git(repo, "rev-parse", "HEAD")
                (repo / "docs").mkdir()
                git(repo, "mv", "graph/native/zlib_ng/source.sha256", "docs/retired.sha256")
                compare = [mode]
                if mode == "--head":
                    git(repo, "commit", "-q", "-m", "retire native input")
                    compare.append("HEAD")
                rc, payload, stderr = self.run_tool(repo, "--fast", "--base", base, *compare)
                self.assertEqual(rc, 10, stderr)
                self.assertEqual(payload["packages"]["zlib-ng"]["status"], "rebuild")
                self.assertIn("graph/native/zlib_ng/source.sha256", payload["changed_paths"])

    def run_tool(self, repo: Path, *args: str) -> tuple[int, dict[str, object], str]:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            out = Path(tmp) / "impact.json"
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--repo-root", str(repo), "--format", "json", *args],
                capture_output=True,
                text=True,
            )
            if proc.stdout.strip():
                payload = json.loads(proc.stdout)
            else:
                payload = {}
            return proc.returncode, payload, proc.stderr

    def run_tool_text(self, repo: Path, *args: str) -> tuple[int, str, str]:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo-root", str(repo), *args],
            capture_output=True,
            text=True,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def test_text_output_omits_unchanged_packages(self) -> None:
        report = {
            "mode": "fast",
            "base": "HEAD",
            "head": "worktree",
            "summary": {
                "exit_code": 10,
                "highest_status": "rebuild",
                "highest_cause_class": "source",
            },
            "packages": {
                "pytorch": {"status": "unchanged"},
                "triton": {
                    "status": "rebuild",
                    "causes": [
                        {
                            "class": "source",
                            "path": "native/triton/upstream_pins.json",
                            "detail": "input content changed",
                        }
                    ],
                },
            },
        }

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rebuild_impact.print_text(report)

        self.assertNotIn("PACKAGE pytorch unchanged", out.getvalue())
        self.assertIn("PACKAGE triton rebuild", out.getvalue())

    def test_default_manifest_models_triton_committed_plan_native_inputs(self) -> None:
        manifest = rebuild_impact.default_manifest()
        triton_inputs = manifest["packages"]["triton"]["inputs"]
        by_path = {
            item["path"]: item
            for item in triton_inputs
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        }
        by_glob = {
            item["glob"]: item
            for item in triton_inputs
            if isinstance(item, dict) and isinstance(item.get("glob"), str)
        }

        self.assertNotIn("native/triton/action_driver.py", by_path)
        self.assertNotIn("native/triton/plan.py", by_path)
        self.assertEqual(by_path["native/triton/triton_action.bzl"]["cause"], "flags")
        self.assertEqual(by_path["native/triton/upstream_pins.json"]["cause"], "source")
        self.assertEqual(by_glob["plans/torch/*/triton.plan.json"]["cause"], "flags")
        self.assertEqual(by_glob["plans/torch/*/triton.plan.json"]["status"], "rebuild")

    def test_default_manifest_models_leaf_action_deps(self) -> None:
        manifest = rebuild_impact.default_manifest()
        packages = manifest["packages"]

        for package, mnemonic, target in (
            ("bzip2", "Bzip2ActionPrefix", "//native/bzip2:bzip2_action_prefix"),
            ("xz", "XzActionPrefix", "//native/xz:xz_action_prefix"),
            ("zstd", "ZstdActionPrefix", "//native/zstd:zstd_action_prefix"),
        ):
            with self.subTest(package=package):
                item = packages[package]
                self.assertEqual(item["mnemonic"], mnemonic)
                self.assertTrue(item["host_exact"])
                self.assertEqual(item["host_targets"], [target])
                self.assertEqual(item["insula_targets"], [target])
                paths = {
                    entry["path"]
                    for entry in item["inputs"]
                    if isinstance(entry, dict) and isinstance(entry.get("path"), str)
                }
                self.assertIn(f"native/{package}/BUILD.bazel", paths)
                self.assertIn(f"native/{package}/{package}_action.bzl", paths)

    def test_fast_treats_valid_graph_root_relocation_as_unchanged(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_legacy_graph_repo(Path(tmp))
            git(repo, "mv", "experiments/spack-bazel-graph", "graph")
            run_sh = repo / "graph/run.sh"
            write(run_sh, run_sh.read_text().replace('$EXP_DIR/../.."', '$EXP_DIR/.."'))

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")

            self.assertEqual(rc, 0)
            self.assertEqual(payload["summary"]["highest_status"], "unchanged")

    def test_fast_treats_graph_to_foundry_rename_as_unchanged(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            git(repo, "mv", "graph", "foundry")

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")

            self.assertEqual(rc, 0)
            self.assertEqual(payload["summary"]["highest_status"], "unchanged")

    def test_fast_keeps_renamed_foundry_native_content_changes_as_impact(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            git(repo, "mv", "graph", "foundry")
            write(repo / "foundry/native/zlib_ng/source.sha256", "new\n")

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")

            self.assertEqual(rc, 10)
            self.assertEqual(payload["packages"]["zlib-ng"]["status"], "rebuild")

    def test_fast_keeps_relocated_native_content_changes_as_impact(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_legacy_graph_repo(Path(tmp))
            git(repo, "mv", "experiments/spack-bazel-graph", "graph")
            run_sh = repo / "graph/run.sh"
            write(run_sh, run_sh.read_text().replace('$EXP_DIR/../.."', '$EXP_DIR/.."'))
            write(repo / "graph/native/zlib_ng/source.sha256", "new\n")

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")

            self.assertEqual(rc, 10)
            self.assertEqual(payload["packages"]["zlib-ng"]["status"], "rebuild")
            self.assertEqual(payload["summary"]["highest_cause_class"], "source")

    def test_fast_treats_graph_root_script_path_updates_as_unchanged(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_legacy_graph_repo(Path(tmp))
            git(repo, "mv", "experiments/spack-bazel-graph", "graph")
            run_sh = repo / "graph/run.sh"
            run_sh.write_text(
                run_sh.read_text(encoding="utf-8").replace(
                    'REPO_ROOT="$(cd "$EXP_DIR/../.." && pwd)"',
                    'REPO_ROOT="$(cd "$EXP_DIR/.." && pwd)"',
                ),
                encoding="utf-8",
            )
            gate = repo / "scripts/agents/triumvirate-gate.sh"
            gate.write_text(
                gate.read_text(encoding="utf-8").replace(
                    'EXP_DIR="$ROOT/experiments/spack-bazel-graph"',
                    'EXP_DIR="$ROOT/graph"',
                ),
                encoding="utf-8",
            )

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")

            self.assertEqual(rc, 0)
            self.assertEqual(payload["summary"]["highest_status"], "unchanged")

    def test_fast_treats_third_party_sandbox_mount_lines_as_unchanged(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            run_sh = repo / "graph/run.sh"
            run_sh.write_text(
                run_sh.read_text(encoding="utf-8")
                + "MP_ARGS+=(--mountpoint /workspace/third_party)\n"
                + '  --ro-bind "$REPO_ROOT/third_party" /workspace/third_party\n',
                encoding="utf-8",
            )

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")

            self.assertEqual(rc, 0)
            self.assertEqual(payload["summary"]["highest_status"], "unchanged")

    def test_fast_treats_relocation_and_third_party_mount_lines_together_as_unchanged(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_legacy_graph_repo(Path(tmp))
            git(repo, "mv", "experiments/spack-bazel-graph", "foundry")
            run_sh = repo / "foundry/run.sh"
            run_sh.write_text(
                run_sh.read_text(encoding="utf-8").replace(
                    'REPO_ROOT="$(cd "$EXP_DIR/../.." && pwd)"',
                    'REPO_ROOT="$(cd "$EXP_DIR/.." && pwd)"',
                )
                + "MP_ARGS+=(--mountpoint /workspace/third_party)\n"
                + '  --ro-bind "$REPO_ROOT/third_party" /workspace/third_party\n',
                encoding="utf-8",
            )

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")

            self.assertEqual(rc, 0)
            self.assertEqual(payload["summary"]["highest_status"], "unchanged")

    def test_fast_rejects_other_sandbox_mount_lines_in_run_script(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            run_sh = repo / "graph/run.sh"
            run_sh.write_text(
                run_sh.read_text(encoding="utf-8")
                + '  --ro-bind "$REPO_ROOT/third_party" /workspace/third_party\n'
                + '  --ro-bind "$REPO_ROOT/elsewhere" /workspace/elsewhere\n',
                encoding="utf-8",
            )

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")

            self.assertEqual(rc, 10)
            self.assertEqual(payload["summary"]["highest_cause_class"], "flags")

    def test_fast_rejects_nearby_graph_root_script_updates(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_legacy_graph_repo(Path(tmp))
            git(repo, "mv", "experiments/spack-bazel-graph", "graph")
            run_sh = repo / "graph/run.sh"
            run_sh.write_text(
                run_sh.read_text(encoding="utf-8").replace(
                    'REPO_ROOT="$(cd "$EXP_DIR/../.." && pwd)"',
                    'REPO_ROOT="$(cd "$EXP_DIR/../../.." && pwd)"',
                ),
                encoding="utf-8",
            )

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")

            self.assertEqual(rc, 10)
            self.assertEqual(payload["summary"]["highest_cause_class"], "flags")

    def test_fast_treats_gate_graph_capture_path_relocation_as_unchanged(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            gate = repo / "graph/scripts/agents/triumvirate-gate-insula.sh"
            gate.write_text(
                textwrap.dedent(
                    """\
                    torch_graphs=(
                      py_torch_214_full_cu130_build_graph.json
                      py_torch_214_full_cu129_build_graph.json
                      py_triton_pytorch_pin_cu130_build_graph.json
                      py_triton_pytorch_pin_cu129_build_graph.json
                    )
                    jax_graphs=(
                      py_jax_0102_cu130_build_graph.json
                      py_jax_0102_cu129_build_graph.json
                    )
                    """
                ),
                encoding="utf-8",
            )
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "add graph capture gate list")
            updated = gate.read_text(encoding="utf-8")
            for graph in (
                "py_torch_214_full_cu130_build_graph.json",
                "py_torch_214_full_cu129_build_graph.json",
                "py_triton_pytorch_pin_cu130_build_graph.json",
                "py_triton_pytorch_pin_cu129_build_graph.json",
                "py_jax_0102_cu130_build_graph.json",
                "py_jax_0102_cu129_build_graph.json",
            ):
                updated = updated.replace(f"  {graph}", f"  graphs/{graph}")
            gate.write_text(updated, encoding="utf-8")

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")

            self.assertEqual(rc, 0)
            self.assertEqual(payload["summary"]["highest_status"], "unchanged")

    def test_fast_rejects_nearby_gate_graph_capture_path_updates(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            gate = repo / "graph/scripts/agents/triumvirate-gate-insula.sh"
            gate.write_text(
                textwrap.dedent(
                    """\
                    torch_graphs=(
                      py_torch_214_full_cu130_build_graph.json
                    )
                    """
                ),
                encoding="utf-8",
            )
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "add graph capture gate list")
            gate.write_text(
                textwrap.dedent(
                    """\
                    torch_graphs=(
                      graphs/py_torch_lean_nx_build_graph.json
                    )
                    """
                ),
                encoding="utf-8",
            )

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")

            self.assertEqual(rc, 10)
            self.assertEqual(payload["summary"]["highest_cause_class"], "flags")

    def test_fast_reports_unchanged_source_and_other_causes_with_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")
            self.assertEqual(rc, 0)
            self.assertEqual(payload["summary"]["highest_status"], "unchanged")

            write(repo / "graph/native/zlib_ng/source.sha256", "new\n")
            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")
            self.assertEqual(rc, 10)
            zlib = payload["packages"]["zlib-ng"]
            self.assertEqual(zlib["status"], "rebuild")
            self.assertEqual(zlib["causes"][0]["class"], "source")
            self.assertEqual(payload["summary"]["highest_cause_class"], "source")

            git(repo, "restore", "--", ".")
            write(repo / "graph/native/pytorch/action_driver.py", "driver refactor\n")
            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")
            self.assertEqual(rc, 20)
            pytorch = payload["packages"]["pytorch"]
            self.assertEqual(pytorch["status"], "rebuild")
            self.assertEqual(pytorch["causes"][0]["class"], "other")

    def test_fast_flags_action_and_repo_env_edits_as_other(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            write(
                repo / "graph/run.sh",
                textwrap.dedent(
                    """\
                    BAZEL_ACTION_ENV=(
                      "--action_env=CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-}"
                    )
                    BAZEL_REPO_ENV=(
                      "--repo_env=SOURCE_DATE_EPOCH=$VASO_SOURCE_DATE_EPOCH"
                    )
                    """
                ),
            )

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")

            self.assertEqual(rc, 20)
            causes = payload["packages"]["zlib-ng"]["causes"]
            self.assertTrue(any(cause["class"] == "other" and "--action_env" in cause["detail"] for cause in causes))
            self.assertTrue(any(cause["class"] == "other" and "--repo_env" in cause["detail"] for cause in causes))

    def test_fast_treats_estate_only_run_script_edits_as_unchanged(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            run_sh = repo / "graph/run.sh"
            run_sh.write_text(
                run_sh.read_text(encoding="utf-8")
                + ': "${VASO_ESTATE_ROOT:?set VASO_ESTATE_ROOT}"\n'
                + '  --root "$VASO_ESTATE_ROOT" \\\n',
                encoding="utf-8",
            )

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")

            self.assertEqual(rc, 0)
            self.assertEqual(payload["summary"]["highest_status"], "unchanged")

    def test_fast_treats_host_gate_copyback_only_edits_as_unchanged(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            gate = repo / "scripts/agents/triumvirate-gate.sh"
            gate.write_text(
                textwrap.dedent(
                    """\
                    args=()

                    map_estate_path_for_insula() {
                      local raw_path="$1"
                      local estate_root="${VASO_ESTATE_ROOT:-}"
                      [[ -n "$estate_root" && "$raw_path" == /* ]] || { printf '%s\\n' "$raw_path"; return 0; }

                      raw_path="$(readlink -m "$raw_path")"
                      local estate_agents_root
                      estate_agents_root="$(readlink -m "$estate_root/agents")"
                      if [[ "$raw_path" == "$estate_agents_root" ]]; then
                        printf '/vaso/agents\\n'
                      elif [[ "$raw_path" == "$estate_agents_root/"* ]]; then
                        printf '/vaso/agents/%s\\n' "${raw_path#"$estate_agents_root/"}"
                      else
                        printf '%s\\n' "$raw_path"
                      fi
                    }

                    while [[ "$#" -gt 0 ]]; do
                      case "$1" in
                        --out-dir|--acceptance-out)
                          [[ "$#" -ge 2 ]] || { echo "$1 requires a value" >&2; exit 2; }
                          args+=("$1" "$(map_estate_path_for_insula "$2")")
                          shift 2
                          ;;
                      esac
                    done

                    cd "$EXP_DIR"
                    exec ./run.sh --insula-cmd env VASO_ESTATE_ROOT=/vaso bash scripts/agents/triumvirate-gate-insula.sh "${args[@]}"
                    """
                ),
                encoding="utf-8",
            )
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "host gate base")

            gate.write_text(
                textwrap.dedent(
                    """\
                    args=()
                    stage_root_host=""
                    stage_root_insula=""
                    acceptance_out_host=""
                    acceptance_out_stage_host=""
                    out_dir_host=""
                    out_dir_stage_host=""

                    ensure_stage_root() {
                      [[ -n "$stage_root_host" ]] && return 0

                      local agent="${VASO_AGENT:-trae}"
                      if [[ ! "$agent" =~ ^[A-Za-z0-9_.-]+$ ]]; then
                        echo "VASO_AGENT must contain only letters, numbers, dot, underscore or dash, got: $agent" >&2
                        exit 2
                      fi

                      local tmpdir="$VASO_ESTATE_ROOT/agents/$agent/tmp"
                      mkdir -p "$tmpdir"
                      local TMPDIR="$tmpdir"
                      stage_root_host="$(mktemp -d -p "$TMPDIR" triumvirate-gate.XXXXXX)"
                      stage_root_insula="/vaso/agents/$agent/tmp/$(basename "$stage_root_host")"
                    }

                    while [[ "$#" -gt 0 ]]; do
                      case "$1" in
                        --out-dir|--acceptance-out)
                          [[ "$#" -ge 2 ]] || { echo "$1 requires a value" >&2; exit 2; }
                          ensure_stage_root
                          case "$1" in
                            --acceptance-out)
                              acceptance_out_host="$(readlink -m "$2")"
                              acceptance_out_stage_host="$stage_root_host/acceptance.json"
                              args+=("$1" "$stage_root_insula/acceptance.json")
                              ;;
                            --out-dir)
                              out_dir_host="$(readlink -m "$2")"
                              out_dir_stage_host="$stage_root_host/out-dir"
                              args+=("$1" "$stage_root_insula/out-dir")
                              ;;
                          esac
                          shift 2
                          ;;
                      esac
                    done

                    cd "$EXP_DIR"
                    set +e
                    ./run.sh --insula-cmd env VASO_ESTATE_ROOT=/vaso bash scripts/agents/triumvirate-gate-insula.sh "${args[@]}"
                    run_rc=$?
                    set -e

                    copy_rc=0
                    if [[ -n "$acceptance_out_host" ]]; then
                      if [[ -f "$acceptance_out_stage_host" ]]; then
                        mkdir -p "$(dirname "$acceptance_out_host")"
                        cp "$acceptance_out_stage_host" "$acceptance_out_host" || copy_rc=$?
                      elif [[ "$run_rc" == "0" ]]; then
                        echo "acceptance output was not produced inside the insula: $stage_root_insula/acceptance.json" >&2
                        copy_rc=1
                      fi
                    fi
                    if [[ -n "$out_dir_host" ]]; then
                      if [[ -d "$out_dir_stage_host" ]]; then
                        mkdir -p "$out_dir_host"
                        cp -a "$out_dir_stage_host"/. "$out_dir_host"/ || copy_rc=$?
                      elif [[ "$run_rc" == "0" ]]; then
                        echo "output directory was not produced inside the insula: $stage_root_insula/out-dir" >&2
                        copy_rc=1
                      fi
                    fi
                    if [[ "$copy_rc" == "0" && -n "$stage_root_host" ]]; then
                      rm -rf "$stage_root_host"
                    fi
                    if [[ "$run_rc" == "0" && "$copy_rc" != "0" ]]; then
                      exit "$copy_rc"
                    fi
                    exit "$run_rc"
                    """
                ),
                encoding="utf-8",
            )

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")

            self.assertEqual(rc, 0)
            self.assertEqual(payload["summary"]["highest_status"], "unchanged")

    def test_fast_keeps_non_copyback_host_gate_edits_as_native_impact(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            gate = repo / "scripts/agents/triumvirate-gate.sh"
            gate.write_text(gate.read_text(encoding="utf-8") + "echo native flags changed\n", encoding="utf-8")

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")

            self.assertEqual(rc, 10)
            self.assertEqual(payload["summary"]["highest_cause_class"], "flags")

    def test_fast_treats_only_exact_disk_cache_gc_lines_as_unchanged(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            helper = repo / "graph/tools/native_determinism_args.sh"
            helper.write_text(
                helper.read_text(encoding="utf-8")
                + 'native_bazel_disk_cache_gc_max_size() { printf "%s\\n" "80G"; }\n'
                + 'native_bazel_disk_cache_gc_max_age() { printf "%s\\n" "72h"; }\n'
                + 'append_native_bazel_disk_cache_gc_args() { local -n out="$1"; out+=("--experimental_disk_cache_gc_max_size=$(native_bazel_disk_cache_gc_max_size)" "--experimental_disk_cache_gc_max_age=$(native_bazel_disk_cache_gc_max_age)"); }\n',
                encoding="utf-8",
            )
            run_sh = repo / "graph/run.sh"
            run_sh.write_text(
                run_sh.read_text(encoding="utf-8")
                + "bazel_disk_cache_gc_args=()\n"
                + "  append_native_bazel_disk_cache_gc_args bazel_disk_cache_gc_args\n"
                + '    "${bazel_disk_cache_gc_args[@]}" \\\n',
                encoding="utf-8",
            )
            gate = repo / "graph/scripts/agents/triumvirate-gate-insula.sh"
            gate.write_text(
                gate.read_text(encoding="utf-8")
                + "  append_native_bazel_disk_cache_gc_args args\n",
                encoding="utf-8",
            )

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")

            self.assertEqual(rc, 0)
            self.assertEqual(payload["summary"]["highest_status"], "unchanged")

    def test_fast_rejects_nearby_disk_cache_gc_edits_outside_allowlist(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            helper = repo / "graph/tools/native_determinism_args.sh"
            helper.write_text(
                helper.read_text(encoding="utf-8")
                + 'append_native_bazel_disk_cache_gc_args() { local -n out="$1"; out+=("--experimental_disk_cache_gc_max_size=$VASO_CACHE_LIMIT"); }\n',
                encoding="utf-8",
            )

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")

            self.assertEqual(rc, 20)
            self.assertEqual(payload["summary"]["highest_cause_class"], "other")

    def test_fast_treats_exact_coreutils_offline_fetch_lines_as_unchanged(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            run_sh = repo / "graph/run.sh"
            run_sh.write_text(
                run_sh.read_text(encoding="utf-8")
                + '  "@coreutils_native"\n'
                + '    "@coreutils_native")\n'
                + '      FETCH_SOURCE_URL="https://ftpmirror.gnu.org/coreutils/coreutils-9.10.tar.xz"\n'
                + '      FETCH_SOURCE_SHA256="16535a9adf0b10037364e2d612aad3d9f4eca3a344949ced74d12faf4bd51d25"\n'
                + '      FETCH_SOURCE_FILENAME="coreutils-9.10.tar.xz"\n'
                + '      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"\n'
                + '      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"\n'
                + "      ;;\n"
                + 'if [[ "${1:-}" == "--cubin-guard-offline-check" ]]; then\n'
                + "  shift\n"
                + '  echo "== cubin_guard offline check (inside insula, downloads disabled) =="\n'
                + '  bazel_insula test //tools:cubin_guard_test --test_output=all --announce_rc "$@"\n'
                + '  echo "cubin-guard-offline-check: ok line=$VASO_CUDA_LINE"\n'
                + "  exit 0\n"
                + "fi\n",
                encoding="utf-8",
            )

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")

            self.assertEqual(rc, 0)
            self.assertEqual(payload["summary"]["highest_status"], "unchanged")

    def test_fast_treats_exact_leaf_action_offline_fetch_lines_as_unchanged(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            run_sh = repo / "graph/run.sh"
            run_sh.write_text(
                run_sh.read_text(encoding="utf-8")
                + '  "@bzip2_action_archive"\n'
                + '  "@xz_action_archive"\n'
                + '  "@zstd_action_archive"\n'
                + '    "@bzip2_action_archive")\n'
                + '      FETCH_SOURCE_URL="https://sourceware.org/pub/bzip2/bzip2-1.0.8.tar.gz"\n'
                + '      FETCH_SOURCE_SHA256="ab5a03176ee106d3f0fa90e381da478ddae405918153cca248e682cd0c4a2269"\n'
                + '      FETCH_SOURCE_FILENAME="bzip2-1.0.8.tar.gz"\n'
                + '      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"\n'
                + '      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"\n'
                + "      ;;\n"
                + '    "@xz_action_archive")\n'
                + '      FETCH_SOURCE_URL="https://github.com/tukaani-project/xz/releases/download/v5.8.3/xz-5.8.3.tar.gz"\n'
                + '      FETCH_SOURCE_SHA256="3d3a1b973af218114f4f889bbaa2f4c037deaae0c8e815eec381c3d546b974a0"\n'
                + '      FETCH_SOURCE_FILENAME="xz-5.8.3.tar.gz"\n'
                + '      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"\n'
                + '      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"\n'
                + "      ;;\n"
                + '    "@zstd_action_archive")\n'
                + '      FETCH_SOURCE_URL="https://github.com/facebook/zstd/archive/v1.5.7.tar.gz"\n'
                + '      FETCH_SOURCE_SHA256="37d7284556b20954e56e1ca85b80226768902e2edabd3b649e9e72c0c9012ee3"\n'
                + '      FETCH_SOURCE_FILENAME="zstd-1.5.7.tar.gz"\n'
                + '      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"\n'
                + '      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"\n'
                + "      ;;\n",
                encoding="utf-8",
            )

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")

            self.assertEqual(rc, 0)
            self.assertEqual(payload["summary"]["highest_status"], "unchanged")

    def test_fast_treats_exact_consumer_conformance_gate_stage_as_unchanged(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            gate = repo / "graph/scripts/agents/triumvirate-gate-insula.sh"
            gate.write_text(
                gate.read_text(encoding="utf-8")
                + "  run_release_stage \\\n"
                + '    "consumer_conformance" \\\n'
                + '    "consumer_conformance" \\\n'
                + '    env VASO_PROFILE="$profile" "INSULA_RELEASE_PYTHON_PREFIX_HOST=$release_python_prefix" "$EXPERIMENT_ROOT/examples/consumers/run.sh" \\\n'
                + '      --line "$line" \\\n'
                + '      --release-dir "$release_dir"\n',
                encoding="utf-8",
            )

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")

            self.assertEqual(rc, 0)
            self.assertEqual(payload["summary"]["highest_status"], "unchanged")

    def test_fast_treats_native_prefix_cquery_gate_refactor_as_unchanged(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            gate = repo / "graph/scripts/agents/triumvirate-gate-insula.sh"
            gate.write_text(
                gate.read_text(encoding="utf-8")
                + "bazel_cquery_single_file() {\n"
                + '  local target="$1"\n'
                + "  local args=()\n"
                + "  bazel_profile_command_args args cquery\n"
                + "  args+=(--nofetch --repository_disable_download --output=files)\n"
                + '  append_native_determinism_bazel_args out_args "$line" "$REPO_ROOT" "$commit" "$release_bazel_ob" "/vaso/lines/$line/work" "$source_date_epoch"\n'
                + "}\n"
                + 'run_stage "native_prefix_paths" "native_prefixes" resolve_default_action_prefixes\n',
                encoding="utf-8",
            )

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")

            self.assertEqual(rc, 0)
            self.assertEqual(payload["summary"]["highest_status"], "unchanged")

    def test_fast_treats_native_prefix_path_query_build_targets_as_unchanged(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            manifest_path = repo / "graph/plans/native-inputs.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["packages"]["pytorch"]["inputs"].append({
                "path": "native/pytorch/BUILD.bazel",
                "cause": "flags",
                "status": "may-rebuild",
            })
            write(manifest_path, json.dumps(manifest, indent=2) + "\n")
            write(repo / "graph/native/pytorch/BUILD.bazel", "old\n")
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "build-query-base")

            build_file = repo / "graph/native/pytorch/BUILD.bazel"
            build_file.write_text(
                build_file.read_text(encoding="utf-8")
                + 'load("//native/common:native_output_path_query.bzl", "native_action_prefix_path")\n'
                + "\n"
                + "native_action_prefix_path(\n"
                + '    name = "pytorch_action_prefix_path",\n'
                + '    output_basename = "pytorch_action",\n'
                + '    profile = "torch",\n'
                + '    tags = ["manual"],\n'
                + ")\n",
                encoding="utf-8",
            )

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")

            self.assertEqual(rc, 0)
            self.assertEqual(payload["summary"]["highest_status"], "unchanged")

    def test_fast_rejects_nearby_coreutils_offline_fetch_edits_outside_allowlist(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            run_sh = repo / "graph/run.sh"
            run_sh.write_text(
                run_sh.read_text(encoding="utf-8")
                + '  "@coreutils_native"\n'
                + '  bazel test //tools:cubin_guard_test --test_output=all --announce_rc "$@"\n',
                encoding="utf-8",
            )

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")

            self.assertEqual(rc, 10)
            self.assertEqual(payload["summary"]["highest_cause_class"], "flags")

    def test_fast_keeps_non_estate_run_script_edits_as_native_impact(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            run_sh = repo / "graph/run.sh"
            run_sh.write_text(run_sh.read_text(encoding="utf-8") + "echo native flags changed\n", encoding="utf-8")

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")

            self.assertEqual(rc, 10)
            self.assertNotEqual(payload["packages"]["zlib-ng"]["status"], "unchanged")

    def test_fast_flags_new_use_default_shell_env_as_other(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            write(
                repo / "graph/native/zlib_ng/zlib_ng_action.bzl",
                "ctx.actions.run_shell(use_default_shell_env = True)\n",
            )

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")

            self.assertEqual(rc, 20)
            causes = payload["packages"]["zlib-ng"]["causes"]
            self.assertTrue(
                any(cause["class"] == "other" and "use_default_shell_env" in cause["detail"] for cause in causes)
            )

    def test_fast_ignores_use_default_shell_env_text_outside_bazel_sources(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            write(repo / "graph/tools/example_test.py", '"use_default_shell_env=True"\n')

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--worktree")

            self.assertEqual(rc, 0)
            self.assertEqual(payload["summary"]["highest_status"], "unchanged")

    def test_fast_staged_mode_checks_only_the_index(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            write(repo / "graph/native/pytorch/action_driver.py", "unstaged\n")
            write(repo / "graph/native/zlib_ng/source.sha256", "staged\n")
            git(repo, "add", "graph/native/zlib_ng/source.sha256")

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", "HEAD", "--staged")

            self.assertEqual(rc, 10)
            self.assertEqual(payload["packages"]["zlib-ng"]["status"], "rebuild")
            self.assertEqual(payload["packages"]["pytorch"]["status"], "unchanged")

    def test_fast_reports_repository_rule_native_and_transitive_dependents(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo_rule_native_fixture_repo(Path(tmp))
            write(repo / "graph/native/protobuf/protobuf.bzl", "new\n")

            rc, stdout, stderr = self.run_tool_text(repo, "--fast", "--base", "HEAD", "--worktree")

        self.assertEqual(rc, 10, stderr)
        self.assertIn("PACKAGE protobuf rebuild", stdout)
        self.assertIn("PACKAGE py_protobuf rebuild", stdout)
        self.assertIn("PACKAGE pytorch rebuild", stdout)

    def test_fast_reports_n1_5_generated_repo_manifest_rebuilds(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo, base_ref, head_ref = init_n1_5_fixture_repo(Path(tmp))

            rc, payload, _ = self.run_tool(repo, "--fast", "--base", base_ref, "--head", head_ref)

        self.assertEqual(rc, 20)
        for package in ("pytorch", "triton"):
            item = payload["packages"][package]
            self.assertEqual(item["status"], "rebuild")
            self.assertIn(
                {
                    "path": "graph/dev.MODULE.bazel",
                    "class": "other",
                    "detail": (
                        "Bzlmod source-repo changes can rematerialize native repository-rule prefixes; "
                        "their generated prefix_manifest.json files are declared action inputs"
                    ),
                },
                item["causes"],
            )


class RebuildImpactExactTest(unittest.TestCase):
    def test_live_aquery_refuses_host_execution_before_launching_bazel(self) -> None:
        with mock.patch.dict(os.environ, {"VASO_IN_INSULA": "0"}), mock.patch.object(subprocess, "run") as run:
            with self.assertRaisesRegex(SystemExit, "Bazel aquery must run under insula"):
                rebuild_impact.run_aquery(Path("."), Path("owned-ob"), Path("query.json"), None, {})
            run.assert_not_called()

    def test_empty_exact_snapshots_cannot_prove_unchanged_native_inputs(self) -> None:
        manifest = {"packages": {"zlib-ng": {"mnemonic": "ZlibNgActionPrefix"}}}
        for insula in (False, True):
            report = rebuild_impact.compare_exact_actions({}, {}, manifest, insula=insula)
            self.assertEqual(report["summary"]["exit_code"], 20)
            self.assertIn("missing from both snapshots", report["packages"]["zlib-ng"]["causes"][0]["detail"])

    def test_retained_json_checks_materialized_inputs_even_when_action_key_is_equal(self) -> None:
        for tree in (False, True):
            with tempfile.TemporaryDirectory(dir=scratch_parent()) as temp:
                root = Path(temp)
                repo = init_repo(root)
                ref = git(repo, "rev-parse", "HEAD")
                input_path = "external/probe/prefix" if tree else "external/probe/input.txt"
                content_path = input_path + "/include/value.h" if tree else input_path
                query = root / "aquery.json"
                query.write_text(json.dumps({
                    "actions": [{"targetId": 1, "mnemonic": "ZlibNgActionPrefix", "actionKey": "fixed",
                                 "arguments": ["tool"], "inputDepSetIds": [1]}],
                    "targets": [{"id": 1, "label": "//native/zlib_ng:prefix"}],
                    "depSetOfFiles": [{"id": 1, "directArtifactIds": [1]}],
                    "artifacts": [{"id": 1, "execPath": input_path}],
                }))
                before = root / "base-ob"
                after = root / "head-ob"
                write(before / content_path, "original")
                for contents, expected in (("original", 0), ("changed", 20)):
                    write(after / content_path, contents)
                    evidence = root / f"evidence-{expected}"
                    result = subprocess.run([
                        sys.executable, str(SCRIPT), "--repo-root", str(repo), "--exact",
                        "--base", ref, "--head", ref, "--package", "zlib-ng",
                        "--aquery-json-base", str(query), "--aquery-json-head", str(query),
                        "--aquery-output-base-base", str(before), "--aquery-output-base-head", str(after),
                        "--snapshot-dir", str(evidence), "--format", "json",
                    ], capture_output=True, text=True)
                    self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                    report = json.loads(result.stdout)
                    self.assertEqual(list(report["packages"]), ["zlib-ng"])
                    self.assertEqual(report["packages"]["zlib-ng"]["action_key"], {"base": "fixed", "head": "fixed"})
                    snapshot = json.loads((evidence / "head-actions.json").read_text())["ZlibNgActionPrefix"]
                    self.assertFalse(snapshot["unverified_inputs"])
                    self.assertIn(input_path, snapshot["input_digests"])

    def test_git_source_input_identity_tracks_executable_mode(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as temp:
            repo = init_repo(Path(temp))
            source = repo / "graph/native/zlib_ng/source.sha256"
            source.chmod(0o644)
            ref = git(repo, "rev-parse", "HEAD")
            before = rebuild_impact.InputContentContext(repo_root=repo, ref=ref)
            name = "native/zlib_ng/source.sha256"
            expected, reason = before.digest_input(name)
            self.assertIsNone(reason)
            head = rebuild_impact.InputContentContext(repo_root=repo, tree=repo / "graph")
            self.assertEqual(head.digest_input(name), (expected, None))
            source.chmod(0o755)
            head = rebuild_impact.InputContentContext(repo_root=repo, tree=repo / "graph")
            self.assertNotEqual(head.digest_input(name)[0], expected)
            source.chmod(0o644)
            head = rebuild_impact.InputContentContext(repo_root=repo, tree=repo / "graph")
            self.assertEqual(head.digest_input(name), (expected, None))

    def test_git_source_links_use_snapshot_referents_and_refuse_cycles(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as temp:
            repo = init_repo(Path(temp))
            source = repo / "graph/native/zlib_ng/probe.py"
            write(source, "original")
            source.chmod(0o755)
            alias = source.with_name("alias.py")
            alias.symlink_to("probe.py")
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "source link")
            ref = git(repo, "rev-parse", "HEAD")
            before = rebuild_impact.InputContentContext(repo_root=repo, ref=ref)
            name = "native/zlib_ng/alias.py"
            expected, reason = before.digest_input(name)
            self.assertIsNone(reason)
            head = rebuild_impact.InputContentContext(repo_root=repo, tree=repo / "graph")
            self.assertEqual(head.digest_input(name), (expected, None))
            write(source, "changed")
            head = rebuild_impact.InputContentContext(repo_root=repo, tree=repo / "graph")
            self.assertNotEqual(head.digest_input(name)[0], expected)
            alias.unlink()
            alias.symlink_to("cycle.py")
            source.with_name("cycle.py").symlink_to("alias.py")
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "cyclic source")
            before = rebuild_impact.InputContentContext(repo_root=repo, ref=git(repo, "rev-parse", "HEAD"))
            self.assertEqual(before.digest_input(name), (None, "unverified-content"))

    def test_materialized_output_base_overrides_stale_checkout_views(self) -> None:
        for artifact in ("/usr/bin/tool", "../repo/value", "./../repo/value", "external/repo/value", "bazel-out/k8/bin/value"):
            self.assertEqual(rebuild_impact.source_repo_candidates(artifact), ())
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as temp:
            root = Path(temp)
            view, output_base = root / "checkout", root / "output-base"
            for artifact in ("external/probe/value", "bazel-out/k8/bin/value", "../sibling/value"):
                source = output_base / ("external/" + artifact[3:] if artifact.startswith("../") else artifact)
                stale = view / artifact
                write(source, "snapshot")
                write(stale, "stale checkout")
                context = rebuild_impact.InputContentContext(repo_root=root, tree=view, output_base=output_base)
                actual, reason = context.digest_input(artifact)
                self.assertIsNone(reason)
                self.assertEqual(actual, rebuild_impact.materialized_input_digest(source))
                source.unlink()
                context = rebuild_impact.InputContentContext(repo_root=root, tree=view, output_base=output_base)
                self.assertEqual(context.digest_input(artifact), (None, "unverified-content"))

    def test_tree_input_identity_tracks_names_modes_and_content_without_host_metadata(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as temp:
            root = Path(temp)
            first, second = root / "first", root / "relocated"
            for directory in (first, second):
                write(directory / "nested/tool", "same bytes")
                (directory / "empty").mkdir()
            digest = rebuild_impact.materialized_input_digest
            baseline = digest(first)
            os.utime(second / "nested/tool", (100, 100))
            self.assertEqual(baseline, digest(second))
            (second / "nested/tool").chmod(0o755)
            self.assertNotEqual(baseline, digest(second))
            (second / "nested/tool").chmod(0o644)
            self.assertEqual(baseline, digest(second))
            (second / "nested/tool").rename(second / "nested/renamed")
            self.assertNotEqual(baseline, digest(second))
            (second / "nested/renamed").rename(second / "nested/tool")
            (second / "empty").rmdir()
            self.assertNotEqual(baseline, digest(second))

    def test_tree_input_tracks_symlink_closure_and_refuses_cycles(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as temp:
            root = Path(temp)
            tree = root / "ob/external/probe/prefix"
            tree.mkdir(parents=True)
            write(tree.parent / "library", "initial")
            (tree / "link").symlink_to("../library")
            digest = rebuild_impact.materialized_input_digest
            baseline = digest(tree)
            write(tree.parent / "library", "changed")
            self.assertNotEqual(baseline, digest(tree))
            write(tree.parent / "library", "initial")
            write(tree.parent / "other-library", "initial")
            (tree / "link").unlink()
            (tree / "link").symlink_to("../other-library")
            self.assertNotEqual(baseline, digest(tree))
            (tree / "cycle").symlink_to(".")
            context = rebuild_impact.InputContentContext(repo_root=root, output_base=root / "ob")
            value, reason = context.digest_input("external/probe/prefix")
            self.assertIsNone(value)
            self.assertEqual(reason, "unverified-content")

    def test_exact_aquery_uses_estate_and_explicit_distdirs(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            estate = root / "estate"
            for relative in (
                "sources/bazel/distdir",
                "sources/native/distdir",
                "sources/triton/distdir",
                "sources/jax/distdir",
            ):
                (estate / relative).mkdir(parents=True)
            repository_cache = estate / "vaso/cache/bazel/repository-cache"
            repository_cache.mkdir(parents=True)
            explicit = root / "extra-distdir"
            explicit.mkdir()
            old_estate = os.environ.get("VASO_ESTATE_ROOT")
            os.environ["VASO_ESTATE_ROOT"] = str(estate)
            try:
                args = rebuild_impact.argparse.Namespace(
                    bazel=Path("/usr/bin/bazel"),
                    distdir=[explicit, estate / "sources/native/distdir"],
                    insula=False,
                    line="cu129",
                    registry=None,
                    repository_cache=None,
                    repository_disable_download=True,
                    release_metadata="fixed",
                )

                command = rebuild_impact.aquery_command(root / "ob", args, rebuild_impact.default_manifest())
            finally:
                if old_estate is None:
                    os.environ.pop("VASO_ESTATE_ROOT", None)
                else:
                    os.environ["VASO_ESTATE_ROOT"] = old_estate

        distdir_flags = [arg for arg in command if arg.startswith("--distdir=")]
        self.assertEqual(distdir_flags, [
            f"--distdir={estate / 'sources/bazel/distdir'}",
            f"--distdir={estate / 'sources/native/distdir'}",
            f"--distdir={estate / 'sources/triton/distdir'}",
            f"--distdir={estate / 'sources/jax/distdir'}",
            f"--distdir={explicit}",
        ])
        self.assertIn(f"--repository_cache={repository_cache}", command)
        self.assertIn("--repository_disable_download", command)
        self.assertIn("//native/bzip2:bzip2_action_prefix", command[-1])
        self.assertIn("//native/xz:xz_action_prefix", command[-1])
        self.assertIn("//native/zstd:zstd_action_prefix", command[-1])
        self.assertIn("//native/zlib_ng:zlib_ng_action_prefix", command[-1])

    def test_exact_aquery_omits_build_settings_absent_from_materialized_tree(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            write(
                root / "native/common/BUILD.bazel",
                textwrap.dedent(
                    """\
                    native_action_string_flag(name = "source_date_epoch")
                    native_action_string_flag(name = "deterministic_seed")
                    native_action_string_flag(name = "deterministic_prefix_map_roots")
                    release_cuda_line(name = "release_line")
                    release_git_sha(name = "release_git_sha")
                    """
                ),
            )
            write(root / "native/pytorch/BUILD.bazel", 'pytorch_max_jobs_flag(name = "max_jobs")\n')
            write(root / "native/torchvision/BUILD.bazel", 'torchvision_max_jobs_flag(name = "max_jobs")\n')
            write(root / "native/torchaudio/BUILD.bazel", 'torchaudio_max_jobs_flag(name = "max_jobs")\n')
            args = rebuild_impact.argparse.Namespace(
                bazel=Path("/usr/bin/bazel"),
                distdir=[],
                insula=False,
                line="cu129",
                registry=None,
                repository_cache=None,
                repository_disable_download=True,
                release_metadata="fixed",
            )

            command = rebuild_impact.aquery_command(root / "ob", args, rebuild_impact.default_manifest(), tree=root)

        self.assertTrue(any(arg.startswith("--//native/common:source_date_epoch=") for arg in command))
        self.assertFalse(any(arg.startswith("--//native/common:native_action_cuda_home=") for arg in command))

    def test_exact_aquery_uses_fixed_release_metadata_placeholders(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            write(repo / "graph/native/zlib_ng/source.sha256", "new\n")
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "head")
            args = rebuild_impact.argparse.Namespace(
                repo_root=repo,
                bazel=Path("/usr/bin/bazel"),
                distdir=[],
                insula=False,
                line="cu129",
                registry=None,
                repository_cache=None,
                repository_disable_download=True,
                release_metadata="fixed",
            )

            base_settings = rebuild_impact.fixed_native_settings(args, ref="HEAD~1")
            head_settings = rebuild_impact.fixed_native_settings(args, ref="HEAD")

        self.assertEqual(base_settings, head_settings)
        self.assertIn("--//native/common:release_git_sha=0000000000000000000000000000000000000000", base_settings)
        self.assertIn("--//native/common:source_date_epoch=1700000000", base_settings)

    def test_exact_aquery_can_use_compared_ref_metadata_for_calibration(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            base_sha = git(repo, "rev-parse", "HEAD")
            base_epoch = git(repo, "show", "-s", "--format=%ct", "HEAD")
            write(repo / "graph/native/zlib_ng/source.sha256", "new\n")
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "head")
            head_sha = git(repo, "rev-parse", "HEAD")
            head_epoch = git(repo, "show", "-s", "--format=%ct", "HEAD")
            args = rebuild_impact.argparse.Namespace(
                repo_root=repo,
                bazel=Path("/usr/bin/bazel"),
                distdir=[],
                insula=False,
                line="cu129",
                registry=None,
                repository_cache=None,
                repository_disable_download=True,
                release_metadata="ref",
            )

            base_settings = rebuild_impact.fixed_native_settings(args, ref=base_sha)
            head_settings = rebuild_impact.fixed_native_settings(args, ref=head_sha)

        self.assertIn(f"--//native/common:release_git_sha={base_sha}", base_settings)
        self.assertIn(f"--//native/common:source_date_epoch={base_epoch}", base_settings)
        self.assertIn(f"--//native/common:release_git_sha={head_sha}", head_settings)
        self.assertIn(f"--//native/common:source_date_epoch={head_epoch}", head_settings)

    def test_exact_json_comparison_reports_key_arg_and_env_changes(self) -> None:
        before = {
            "actions": [
                {
                    "targetId": 1,
                    "mnemonic": "ZlibNgActionPrefix",
                    "actionKey": "old-key",
                    "arguments": ["tool", "--old"],
                    "environmentVariables": [{"key": "SOURCE_DATE_EPOCH", "value": "1"}],
                    "inputDepSetIds": [1],
                }
            ],
            "targets": [{"id": 1, "label": "//native/zlib_ng:zlib_ng_action_prefix"}],
            "depSetOfFiles": [{"id": 1, "directArtifactIds": [1]}],
            "artifacts": [{"id": 1, "execPath": "native/zlib_ng/source.sha256"}],
        }
        after = {
            "actions": [
                {
                    "targetId": 1,
                    "mnemonic": "ZlibNgActionPrefix",
                    "actionKey": "new-key",
                    "arguments": ["tool", "--new"],
                    "environmentVariables": [{"key": "SOURCE_DATE_EPOCH", "value": "2"}],
                    "inputDepSetIds": [2],
                }
            ],
            "targets": [{"id": 1, "label": "//native/zlib_ng:zlib_ng_action_prefix"}],
            "depSetOfFiles": [{"id": 2, "directArtifactIds": [2]}],
            "artifacts": [{"id": 2, "execPath": "native/common/native_action_toolchain.bzl"}],
        }
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            base_json = root / "base.json"
            head_json = root / "head.json"
            base_json.write_text(json.dumps(before), encoding="utf-8")
            head_json.write_text(json.dumps(after), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repo-root",
                    str(Path.cwd()),
                    "--exact",
                    "--aquery-json-base",
                    str(base_json),
                    "--aquery-json-head",
                    str(head_json),
                    "--manifest",
                    str(checked_in_manifest()),
                    "--format",
                    "json",
                ],
                capture_output=True,
                text=True,
            )

        self.assertEqual(proc.returncode, 20)
        payload = json.loads(proc.stdout)
        zlib = payload["packages"]["zlib-ng"]
        self.assertEqual(zlib["status"], "rebuild")
        self.assertEqual(zlib["action_key"], {"base": "old-key", "head": "new-key"})
        details = {cause["detail"] for cause in zlib["causes"]}
        self.assertIn("args changed: - --old; + --new", details)
        self.assertIn("env SOURCE_DATE_EPOCH changed: 1 -> 2", details)
        self.assertIn("input changed: native/zlib_ng/source.sha256", details)
        self.assertIn("input changed: native/common/native_action_toolchain.bzl", details)

    def test_exact_json_comparison_reports_source_content_changes(self) -> None:
        doc = {
            "actions": [
                {
                    "targetId": 1,
                    "mnemonic": "ZlibNgActionPrefix",
                    "actionKey": "same-key",
                    "arguments": ["tool", "--same"],
                    "environmentVariables": [{"key": "SOURCE_DATE_EPOCH", "value": "1"}],
                    "inputDepSetIds": [1],
                }
            ],
            "targets": [{"id": 1, "label": "//native/zlib_ng:zlib_ng_action_prefix"}],
            "depSetOfFiles": [{"id": 1, "directArtifactIds": [1]}],
            "artifacts": [{"id": 1, "execPath": "native/zlib_ng/source.sha256"}],
        }
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            base_ref = git(repo, "rev-parse", "HEAD")
            write(repo / "graph/native/zlib_ng/source.sha256", "new\n")
            git(repo, "add", "graph/native/zlib_ng/source.sha256")
            git(repo, "commit", "-q", "-m", "head")
            head_ref = git(repo, "rev-parse", "HEAD")
            base_json = Path(tmp) / "base.json"
            head_json = Path(tmp) / "head.json"
            base_json.write_text(json.dumps(doc), encoding="utf-8")
            head_json.write_text(json.dumps(doc), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repo-root",
                    str(repo),
                    "--exact",
                    "--base",
                    base_ref,
                    "--head",
                    head_ref,
                    "--aquery-json-base",
                    str(base_json),
                    "--aquery-json-head",
                    str(head_json),
                    "--manifest",
                    str(repo / "graph/plans/native-inputs.json"),
                    "--format",
                    "json",
                ],
                capture_output=True,
                text=True,
            )

        self.assertEqual(proc.returncode, 10, proc.stderr)
        payload = json.loads(proc.stdout)
        zlib = payload["packages"]["zlib-ng"]
        self.assertEqual(zlib["status"], "rebuild")
        self.assertEqual(zlib["action_key"], {"base": "same-key", "head": "same-key"})
        self.assertIn(
            {
                "path": "native/zlib_ng/source.sha256",
                "class": "source",
                "detail": "input content changed: native/zlib_ng/source.sha256",
            },
            zlib["causes"],
        )

    def test_exact_json_comparison_reports_unverified_external_inputs(self) -> None:
        doc = {
            "actions": [
                {
                    "targetId": 1,
                    "mnemonic": "ZlibNgActionPrefix",
                    "actionKey": "same-key",
                    "arguments": ["tool"],
                    "environmentVariables": [],
                    "inputDepSetIds": [1],
                }
            ],
            "targets": [{"id": 1, "label": "//native/zlib_ng:zlib_ng_action_prefix"}],
            "depSetOfFiles": [{"id": 1, "directArtifactIds": [1]}],
            "artifacts": [{"id": 1, "execPath": "external/+repo+tool/bin/tool"}],
        }
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            base_ref = git(repo, "rev-parse", "HEAD")
            base_json = Path(tmp) / "base.json"
            head_json = Path(tmp) / "head.json"
            base_json.write_text(json.dumps(doc), encoding="utf-8")
            head_json.write_text(json.dumps(doc), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repo-root",
                    str(repo),
                    "--exact",
                    "--base",
                    base_ref,
                    "--head",
                    base_ref,
                    "--aquery-json-base",
                    str(base_json),
                    "--aquery-json-head",
                    str(head_json),
                    "--manifest",
                    str(repo / "graph/plans/native-inputs.json"),
                    "--format",
                    "json",
                ],
                capture_output=True,
                text=True,
            )

        self.assertEqual(proc.returncode, 20, proc.stderr)
        payload = json.loads(proc.stdout)
        causes = payload["packages"]["zlib-ng"]["causes"]
        self.assertEqual(payload["packages"]["zlib-ng"]["status"], "may-rebuild")
        self.assertEqual(causes[0]["class"], "other")
        self.assertIn("unverified-content: 1 input(s)", causes[0]["detail"])
        self.assertIn("external/+repo+tool/bin/tool", causes[0]["detail"])

    def test_exact_json_comparison_multi_action_digest_includes_input_digests(self) -> None:
        doc = {
            "actions": [
                {
                    "targetId": 1,
                    "mnemonic": "ZlibNgActionPrefix",
                    "actionKey": "same-key",
                    "arguments": ["tool", "one"],
                    "environmentVariables": [],
                    "inputDepSetIds": [1],
                },
                {
                    "targetId": 2,
                    "mnemonic": "ZlibNgActionPrefix",
                    "actionKey": "same-key",
                    "arguments": ["tool", "two"],
                    "environmentVariables": [],
                    "inputDepSetIds": [2],
                },
            ],
            "targets": [
                {"id": 1, "label": "//native/zlib_ng:first_action"},
                {"id": 2, "label": "//native/zlib_ng:second_action"},
            ],
            "depSetOfFiles": [
                {"id": 1, "directArtifactIds": [1]},
                {"id": 2, "directArtifactIds": [2]},
            ],
            "artifacts": [
                {"id": 1, "execPath": "native/zlib_ng/source.sha256"},
                {"id": 2, "execPath": "native/zlib_ng/zlib_ng_action.bzl"},
            ],
        }
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            repo = init_repo(Path(tmp))
            base_ref = git(repo, "rev-parse", "HEAD")
            write(repo / "graph/native/zlib_ng/source.sha256", "new\n")
            git(repo, "add", "graph/native/zlib_ng/source.sha256")
            git(repo, "commit", "-q", "-m", "head")
            head_ref = git(repo, "rev-parse", "HEAD")
            base_json = Path(tmp) / "base.json"
            head_json = Path(tmp) / "head.json"
            base_json.write_text(json.dumps(doc), encoding="utf-8")
            head_json.write_text(json.dumps(doc), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repo-root",
                    str(repo),
                    "--exact",
                    "--base",
                    base_ref,
                    "--head",
                    head_ref,
                    "--aquery-json-base",
                    str(base_json),
                    "--aquery-json-head",
                    str(head_json),
                    "--manifest",
                    str(repo / "graph/plans/native-inputs.json"),
                    "--format",
                    "json",
                ],
                capture_output=True,
                text=True,
            )

        self.assertEqual(proc.returncode, 10, proc.stderr)
        payload = json.loads(proc.stdout)
        zlib = payload["packages"]["zlib-ng"]
        self.assertNotEqual(zlib["action_key"]["base"], zlib["action_key"]["head"])
        self.assertIn(
            {
                "path": "native/zlib_ng/source.sha256",
                "class": "source",
                "detail": "input content changed: native/zlib_ng/source.sha256",
            },
            zlib["causes"],
        )

    def test_exact_host_marks_big_native_packages_needs_insula(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            base_json = Path(tmp) / "base.json"
            head_json = Path(tmp) / "head.json"
            doc = {"actions": [], "targets": [], "depSetOfFiles": [], "artifacts": []}
            base_json.write_text(json.dumps(doc), encoding="utf-8")
            head_json.write_text(json.dumps(doc), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repo-root",
                    str(Path.cwd()),
                    "--exact",
                    "--aquery-json-base",
                    str(base_json),
                    "--aquery-json-head",
                    str(head_json),
                    "--manifest",
                    str(checked_in_manifest()),
                    "--format",
                    "json",
                ],
                capture_output=True,
                text=True,
            )

        self.assertEqual(proc.returncode, 20)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["packages"]["pytorch"]["status"], "needs-insula")
        self.assertEqual(payload["packages"]["triton"]["status"], "needs-insula")


class PlanKeyInputMergeTest(unittest.TestCase):
    def test_keys_json_inputs_become_definite_rebuild_rules(self) -> None:
        # native-inputs.json went stale: plan_executor.py was missing for every package (2026-10-07).
        import tempfile, json as _json
        from pathlib import Path as _Path
        with tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMPDIR") or None) as td:
            root = _Path(td)
            (root / "native-inputs.json").write_text(_json.dumps({"schema_version": 1, "packages": {"pytorch": {"inputs": []}}}))
            (root / "keys.json").write_text(_json.dumps({"packages": {"torch/cu130/py-torch": {"inputs": [
                {"kind": "executor", "path": "native/common/plan_executor.py", "sha256": "x"},
                {"kind": "source", "path": "source:pytorch.tar.gz", "sha256": "y"},
            ]}}}))
            doc = rebuild_impact.load_manifest(root / "native-inputs.json")
            paths = [item["path"] for item in doc["packages"]["pytorch"]["inputs"]]
            self.assertIn("native/common/plan_executor.py", paths)
            self.assertNotIn("source:pytorch.tar.gz", paths)


if __name__ == "__main__":
    unittest.main()
