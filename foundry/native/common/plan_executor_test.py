#!/usr/bin/env python3
"""Tests for the committed native build plan executor."""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


def scratch_parent() -> Path:
    return Path(os.environ.get("TEST_TMPDIR") or os.environ.get("VASO_AGENT_IO_ROOT") or Path.cwd())


def minimal_plan() -> dict[str, object]:
    return {
        "schema": 1,
        "profile": "torch",
        "line": "cu130",
        "package": {"name": "zlib-ng", "version": "2.3.3"},
        "sources": [
            {
                "name": "main",
                "archive": "{source_archive}",
                "sha256": "filled-by-test",
                "strip_prefix": "zlib-ng-2.3.3",
                "urls": ["https://github.com/zlib-ng/zlib-ng/archive/2.3.3.tar.gz"],
                "patches": [],
            }
        ],
        "inputs": [],
        "toolchain": {
            "cc": "toolchain:gcc_13_3",
            "cxx": "toolchain:gcc_13_3",
            "cuda": "rootfs:cuda@13.0.3",
            "env": {
                "CC": "/fixture/toolchain/gcc",
                "CXX": "/fixture/toolchain/g++",
                "CFLAGS": "-fbase-c",
                "CXXFLAGS": "-fbase-cxx",
                "CUDAFLAGS": "--base-cuda",
                "NVCC_FLAGS": "-forward-unknown-to-host-compiler",
            },
            "python": "python_313_native",
            "rootfs_identity": {
                "label": "//plans:torch/cu130/rootfs.identity.json",
                "sha256": "filled-by-test",
            },
        },
        "deps": {},
        "configure": {"cwd": "{src}", "argv": ["{src}/configure", "--prefix={out}"], "env": {}},
        "build": {"cwd": "{src}", "argv": ["true"], "env": {}},
        "install": {"cwd": "{src}", "argv": ["true"], "env": {}},
        "outputs": [{"kind": "prefix", "path": "{out}"}],
        "gpu_archs": [],
        "determinism": {
            "seed": "sha256:unused-in-test",
            "source_date_epoch": 1770153003,
            "prefix_map": {"{work}": ".", "{out}": "."},
        },
        "live_checks": {
            "commands": [
                {
                    "name": "fixture-plan",
                    "argv": [sys.executable, "-c", "print('fixture-plan-ok')"],
                    "expect_stdout": "fixture-plan-ok",
                }
            ]
        },
    }


def live_rootfs_identity() -> dict[str, object]:
    return {
        "line": "cu130",
        "live_checks": {
            "commands": [
                {
                    "name": "fixture-rootfs",
                    "argv": [sys.executable, "-c", "print('fixture-rootfs-ok')"],
                    "expect_stdout": "fixture-rootfs-ok",
                }
            ]
        },
    }


def write_rootfs_identity(path: Path, doc: dict[str, object] | None = None) -> None:
    path.write_text(json.dumps(doc or live_rootfs_identity(), sort_keys=True) + "\n", encoding="utf-8")


class PlanExecutorTest(unittest.TestCase):
    def run_executor(
        self,
        plan: dict[str, object],
        archive: Path,
        rootfs_identity: Path,
        prefix: Path | None,
        extra_args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        wheel_out: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            plan = json.loads(json.dumps(plan))
            plan["sources"][0]["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
            plan["toolchain"]["rootfs_identity"]["sha256"] = hashlib.sha256(
                rootfs_identity.read_bytes()
            ).hexdigest()
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")
            output_args: list[str] = []
            if prefix is not None:
                output_args.extend(["--prefix-out", str(prefix)])
            if wheel_out is not None:
                output_args.extend(["--wheel-out", str(wheel_out)])
            return subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("plan_executor.py")),
                    "--plan",
                    str(plan_path),
                    "--source-archive",
                    str(archive),
                    "--rootfs-identity",
                    str(rootfs_identity),
                    *output_args,
                    *(extra_args or []),
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=cwd,
            )

    def make_archive(self, tmp_path: Path, files: dict[str, str]) -> Path:
        src = tmp_path / "zlib-ng-2.3.3"
        src.mkdir()
        for relative, content in files.items():
            path = src / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            if relative == "configure" or relative.endswith(".sh"):
                path.chmod(0o755)
        archive = tmp_path / "source.tar.gz"
        subprocess.run(["tar", "-czf", str(archive), "-C", str(tmp_path), src.name], check=True)
        return archive

    def test_executor_refuses_existing_nonempty_work_dir(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / "source.tar.gz"
            archive.write_bytes(b"not reached")
            rootfs_identity = tmp_path / "rootfs.identity.json"
            write_rootfs_identity(rootfs_identity)
            prefix = tmp_path / "prefix"
            work = prefix / ".work"
            work.mkdir(parents=True)
            (work / "old-object.o").write_text("stale", encoding="utf-8")

            result = self.run_executor(minimal_plan(), archive, rootfs_identity, prefix)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("refusing non-empty work dir", result.stderr)

    def test_executor_rejects_unknown_placeholders_before_build(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / "source.tar.gz"
            archive.write_bytes(b"not reached")
            rootfs_identity = tmp_path / "rootfs.identity.json"
            write_rootfs_identity(rootfs_identity)
            plan = minimal_plan()
            plan["install"] = {
                "cwd": "{src}",
                "argv": ["bash", "-c", "echo {unsupported_placeholder}", "install"],
                "env": {},
            }

            result = self.run_executor(plan, archive, rootfs_identity, tmp_path / "prefix")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsupported placeholder {unsupported_placeholder}", result.stderr)

    def test_executor_sets_plan_env_and_runtime_prefix_maps(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            tmp_path = Path(tmp)
            archive = self.make_archive(
                tmp_path,
                {
                    "configure": textwrap.dedent(
                        """\
                        #!/usr/bin/env bash
                        set -euo pipefail
                        printf '%s\n' "$CC|$SOURCE_DATE_EPOCH|$CFLAGS|$CXXFLAGS|${NVCC_FLAGS:-}|${CUDAFLAGS:-}|$1" > "$PLAN_EXECUTOR_ENV_PROBE"
                        """
                    ),
                },
            )
            rootfs_identity = tmp_path / "rootfs.identity.json"
            write_rootfs_identity(rootfs_identity)
            probe = tmp_path / "probe.txt"
            plan = minimal_plan()
            plan["configure"] = {
                "cwd": "{src}",
                "argv": ["bash", "{src}/configure", "--prefix={out}"],
                "env": {
                    "PLAN_EXECUTOR_ENV_PROBE": str(probe),
                    "CFLAGS": "-O2",
                    "CXXFLAGS": "-O3",
                    "NVCC_FLAGS": "--existing-nvcc",
                    "CUDAFLAGS": "--existing-cuda",
                },
            }
            plan["build"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["install"] = {"cwd": "{src}", "argv": ["true"], "env": {}}

            result = self.run_executor(plan, archive, rootfs_identity, tmp_path / "prefix")

            self.assertEqual(result.returncode, 0, result.stderr)
            cc, epoch, cflags, cxxflags, nvcc_flags, cudaflags, prefix_arg = probe.read_text(encoding="utf-8").split("|")
            self.assertEqual(cc, "/fixture/toolchain/gcc")
            self.assertEqual(epoch, "1770153003")
            self.assertTrue(cflags.startswith("-O2 "), cflags)
            self.assertTrue(cxxflags.startswith("-O3 "), cxxflags)
            self.assertTrue(nvcc_flags.startswith("--existing-nvcc "), nvcc_flags)
            self.assertTrue(cudaflags.startswith("--existing-cuda "), cudaflags)
            self.assertIn("-ffile-prefix-map=", cflags)
            self.assertIn("-fdebug-prefix-map=", cxxflags)
            self.assertIn("--frandom-seed=unused-in-test", nvcc_flags)
            self.assertNotIn("-Xcompiler=-frandom-seed=unused-in-test", nvcc_flags)
            self.assertIn("--objdir-as-tempdir", nvcc_flags)
            self.assertIn("=.", cudaflags)
            self.assertEqual(prefix_arg, f"--prefix={tmp_path / 'prefix'}\n")

    def test_executor_applies_toolchain_env_from_plan(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            tmp_path = Path(tmp)
            archive = self.make_archive(
                tmp_path,
                {
                    "configure": textwrap.dedent(
                        """\
                        #!/usr/bin/env bash
                        set -euo pipefail
                        printf '%s\n' "$CC|$CXX|$CFLAGS|$CXXFLAGS|${NVCC_FLAGS:-}|${CUDAFLAGS:-}" > "$PLAN_EXECUTOR_ENV_PROBE"
                        """
                    ),
                },
            )
            rootfs_identity = tmp_path / "rootfs.identity.json"
            write_rootfs_identity(rootfs_identity)
            probe = tmp_path / "toolchain-env.txt"
            plan = minimal_plan()
            plan["configure"] = {
                "cwd": "{src}",
                "argv": ["bash", "{src}/configure"],
                "env": {"PLAN_EXECUTOR_ENV_PROBE": str(probe)},
            }
            plan["build"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["install"] = {"cwd": "{src}", "argv": ["true"], "env": {}}

            result = self.run_executor(plan, archive, rootfs_identity, tmp_path / "prefix")

            self.assertEqual(result.returncode, 0, result.stderr)
            cc, cxx, cflags, cxxflags, nvcc_flags, cudaflags = probe.read_text(encoding="utf-8").split("|")
            self.assertEqual(cc, "/fixture/toolchain/gcc")
            self.assertEqual(cxx, "/fixture/toolchain/g++")
            self.assertTrue(cflags.startswith("-fbase-c "), cflags)
            self.assertTrue(cxxflags.startswith("-fbase-cxx "), cxxflags)
            self.assertTrue(nvcc_flags.startswith("-forward-unknown-to-host-compiler "), nvcc_flags)
            self.assertTrue(cudaflags.startswith("--base-cuda "), cudaflags)

    def test_executor_applies_ordered_source_patches_before_phases(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            tmp_path = Path(tmp)
            archive = self.make_archive(tmp_path, {"message.txt": "before\n"})
            rootfs_identity = tmp_path / "rootfs.identity.json"
            write_rootfs_identity(rootfs_identity)
            patch = tmp_path / "message.patch"
            patch.write_text(
                textwrap.dedent(
                    """\
                    diff --git a/message.txt b/message.txt
                    --- a/message.txt
                    +++ b/message.txt
                    @@ -1 +1 @@
                    -before
                    +after
                    """
                ),
                encoding="utf-8",
            )
            plan = minimal_plan()
            plan["sources"][0]["patches"] = [
                {"path": "message.patch", "sha256": hashlib.sha256(patch.read_bytes()).hexdigest()}
            ]
            plan["configure"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["build"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["install"] = {
                "cwd": "{src}",
                "argv": ["bash", "-c", "mkdir -p \"$1\" && cp message.txt \"$1/message.txt\"", "install", "{out}"],
                "env": {},
            }

            result = self.run_executor(
                plan,
                archive,
                rootfs_identity,
                tmp_path / "prefix",
                extra_args=["--patch", f"message.patch={patch}"],
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((tmp_path / "prefix" / "message.txt").read_text(encoding="utf-8"), "after\n")

    def test_dep_prefix_placeholder_resolves_declared_prefix_tree_path(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            tmp_path = Path(tmp)
            archive = self.make_archive(tmp_path, {"payload.txt": "source\n"})
            rootfs_identity = tmp_path / "rootfs.identity.json"
            write_rootfs_identity(rootfs_identity)
            dep = tmp_path / "dep-prefix"
            (dep / "include").mkdir(parents=True)
            (dep / "include" / "dep.txt").write_text("dependency\n", encoding="utf-8")
            plan = minimal_plan()
            plan["deps"] = {"zdep": {"label": "//deps:zdep", "key": "sha256:fixture"}}
            plan["configure"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["build"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["install"] = {
                "cwd": "{src}",
                "argv": ["bash", "-c", "mkdir -p \"$1\" && cp \"$2/include/dep.txt\" \"$1/dep.txt\"", "install", "{out}", "{prefix:zdep}"],
                "env": {},
            }

            result = self.run_executor(
                plan,
                archive,
                rootfs_identity,
                tmp_path / "prefix",
                extra_args=["--dep-prefix", f"zdep={dep}"],
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((tmp_path / "prefix" / "dep.txt").read_text(encoding="utf-8"), "dependency\n")

    def test_dep_prefix_placeholder_resolves_execroot_symlink_to_real_path(self) -> None:
        # Post-N1-6 gate: execroot-relative dep prefixes leaked /vaso-bazel-ob/execroot/_main/external/...
        # into torch's RUNPATH. The executor now realpaths deps at run time: an execroot external/<repo>
        # symlink resolves to the real (fixed-mount) prefix, and the phase sees an absolute path that
        # still works after it changes directory into {src}.
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            tmp_path = Path(tmp)
            action_cwd = tmp_path / "ob" / "execroot" / "_main"
            action_cwd.mkdir(parents=True)
            archive = self.make_archive(tmp_path, {"payload.txt": "source\n"})
            rootfs_identity = tmp_path / "rootfs.identity.json"
            write_rootfs_identity(rootfs_identity)
            real_dep = tmp_path / "ob" / "external" / "+zdep+zdep" / "prefix"
            real_dep.mkdir(parents=True)
            (action_cwd / "external").mkdir()
            (action_cwd / "external" / "+zdep+zdep").symlink_to(real_dep.parent)
            probe = tmp_path / "dep-arg.txt"
            plan = minimal_plan()
            plan["deps"] = {"zdep": {"label": "//deps:zdep", "key": "sha256:fixture"}}
            plan["configure"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["build"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["install"] = {
                "cwd": "{src}",
                "argv": ["bash", "-c", "printf '%s\\n' \"$1\" > \"$2\"", "install", "{prefix:zdep}", str(probe)],
                "env": {},
            }

            result = self.run_executor(
                plan,
                archive,
                rootfs_identity,
                tmp_path / "prefix",
                extra_args=["--dep-prefix", "zdep=external/+zdep+zdep/prefix"],
                cwd=action_cwd,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            resolved = probe.read_text(encoding="utf-8").strip()
            self.assertEqual(resolved, str(real_dep.resolve()))
            self.assertNotIn(str(action_cwd), resolved)

    def test_phase_cwd_is_resolved_per_phase(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            tmp_path = Path(tmp)
            archive = self.make_archive(tmp_path, {"payload.txt": "source\n"})
            rootfs_identity = tmp_path / "rootfs.identity.json"
            write_rootfs_identity(rootfs_identity)
            probe = tmp_path / "cwd.txt"
            plan = minimal_plan()
            plan["configure"] = {"cwd": "{work}", "argv": ["bash", "-c", "pwd > \"$1\"", "probe", str(probe)], "env": {}}
            plan["build"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["install"] = {"cwd": "{src}", "argv": ["true"], "env": {}}

            result = self.run_executor(plan, archive, rootfs_identity, tmp_path / "prefix")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(probe.read_text(encoding="utf-8").strip(), str(tmp_path / "prefix" / ".work"))

    def test_rootfs_identity_without_live_checks_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            tmp_path = Path(tmp)
            archive = self.make_archive(tmp_path, {"payload.txt": "source\n"})
            rootfs_identity = tmp_path / "rootfs.identity.json"
            write_rootfs_identity(rootfs_identity, {"line": "cu130"})
            plan = minimal_plan()
            plan["configure"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["build"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["install"] = {"cwd": "{src}", "argv": ["true"], "env": {}}

            result = self.run_executor(plan, archive, rootfs_identity, tmp_path / "prefix")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("rootfs identity live_checks is required", result.stderr)

    def test_plan_without_live_checks_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            tmp_path = Path(tmp)
            archive = self.make_archive(tmp_path, {"payload.txt": "source\n"})
            rootfs_identity = tmp_path / "rootfs.identity.json"
            write_rootfs_identity(rootfs_identity)
            plan = minimal_plan()
            plan.pop("live_checks")
            plan["configure"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["build"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["install"] = {"cwd": "{src}", "argv": ["true"], "env": {}}

            result = self.run_executor(plan, archive, rootfs_identity, tmp_path / "prefix")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("plan is missing live_checks", result.stderr)

    def test_live_rootfs_identity_mismatch_fails_before_build(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            tmp_path = Path(tmp)
            archive = self.make_archive(tmp_path, {"payload.txt": "source\n"})
            rootfs_identity = tmp_path / "rootfs.identity.json"
            rootfs_identity.write_text(
                json.dumps(
                    {
                        "line": "cu130",
                        "live_checks": {
                            "commands": [
                                {
                                    "name": "fixture-rootfs",
                                    "argv": [sys.executable, "-c", "print('actual-rootfs')"],
                                    "expect_stdout": "expected-rootfs",
                                }
                            ]
                        },
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            plan = minimal_plan()
            plan["configure"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["build"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["install"] = {"cwd": "{src}", "argv": ["true"], "env": {}}

            result = self.run_executor(plan, archive, rootfs_identity, tmp_path / "prefix")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("live rootfs identity mismatch", result.stderr)

    def test_declared_extra_input_must_be_passed_by_the_action(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            tmp_path = Path(tmp)
            archive = self.make_archive(tmp_path, {"payload.txt": "source\n"})
            rootfs_identity = tmp_path / "rootfs.identity.json"
            write_rootfs_identity(rootfs_identity)
            declared = tmp_path / "declared-input.txt"
            declared.write_text("declared\n", encoding="utf-8")
            plan = minimal_plan()
            plan["inputs"] = [
                {"path": str(declared), "sha256": hashlib.sha256(declared.read_bytes()).hexdigest()}
            ]
            plan["configure"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["build"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["install"] = {"cwd": "{src}", "argv": ["true"], "env": {}}

            result = self.run_executor(plan, archive, rootfs_identity, tmp_path / "prefix")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing declared input binding", result.stderr)

    def test_nested_bazel_args_expand_from_declared_cache_manifests(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            tmp_path = Path(tmp)
            archive = self.make_archive(tmp_path, {"payload.txt": "source\n"})
            rootfs_identity = tmp_path / "rootfs.identity.json"
            write_rootfs_identity(rootfs_identity)
            manifest = tmp_path / "nested-cache-manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "package": "py-jaxlib",
                        "jaxlib_version": "0.10.2",
                        "entries": [
                            {
                                "kind": "archive",
                                "filename": "archive.tar.gz",
                                "expected_sha256": hashlib.sha256(b"archive").hexdigest(),
                            }
                        ],
                        "tree_sha256": hashlib.sha256(b"tree").hexdigest(),
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            probe = tmp_path / "nested-args.txt"
            plan = minimal_plan()
            plan["nested_bazel"] = {
                "archives": [
                    {
                        "name": "fixture",
                        "sha256": hashlib.sha256(b"archive").hexdigest(),
                        "urls": ["https://example.invalid/archive.tar.gz"],
                    }
                ],
                "distdir_manifest": {
                    "label": "@jaxlib_nested_cache_manifest//:manifest.json",
                    "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                },
                "repository_cache_manifest": {
                    "label": "@jaxlib_nested_cache_manifest//:manifest.json",
                    "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                },
                "build_py_args": [
                    "build/build.py",
                    "build",
                    "--bazel_options=--distdir={nested_distdir}",
                    "--bazel_options=--repository_cache={nested_repository_cache}",
                    "--bazel_options=--disk_cache={nested_disk_cache}",
                    "--bazel_options=--jobs={jobs}",
                    "--bazel_options=--local_resources=cpu={jobs}",
                ],
            }
            plan["configure"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["build"] = {
                "cwd": "{src}",
                "argv": [
                    "bash",
                    "-c",
                    "probe=$1; shift; printf '%s\n' \"$@\" > \"$probe\"",
                    "build",
                    str(probe),
                    "{nested_build_py_args}",
                ],
                "env": {},
            }
            plan["install"] = {"cwd": "{src}", "argv": ["true"], "env": {}}

            result = self.run_executor(
                plan,
                archive,
                rootfs_identity,
                tmp_path / "prefix",
                extra_args=[
                    "--nested-cache-manifest",
                    f"distdir={manifest}",
                    "--nested-cache-manifest",
                    f"repository_cache={manifest}",
                    "--nested-distdir",
                    "/vaso/sources/jax/distdir",
                    "--nested-repository-cache",
                    "/vaso/cache/bazel/repository-cache",
                    "--nested-disk-cache",
                    "/vaso/cache/bazel/jaxlib-disk-cache",
                ],
                env={"VASO_ACTION_RESERVED_CPUS": "7"},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                probe.read_text(encoding="utf-8").splitlines(),
                [
                    "build/build.py",
                    "build",
                    "--bazel_options=--distdir=/vaso/sources/jax/distdir",
                    "--bazel_options=--repository_cache=/vaso/cache/bazel/repository-cache",
                    "--bazel_options=--disk_cache=/vaso/cache/bazel/jaxlib-disk-cache",
                    "--bazel_options=--jobs=7",
                    "--bazel_options=--local_resources=cpu=7",
                ],
            )

    def test_wheel_only_output_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            tmp_path = Path(tmp)
            archive = self.make_archive(tmp_path, {"payload.txt": "source\n"})
            rootfs_identity = tmp_path / "rootfs.identity.json"
            write_rootfs_identity(rootfs_identity)
            wheel = tmp_path / "dist" / "fixture.whl"
            plan = minimal_plan()
            plan["outputs"] = [{"kind": "wheel", "path": "{wheel}"}]
            plan["configure"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["build"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["install"] = {
                "cwd": "{src}",
                "argv": ["bash", "-c", "mkdir -p \"$(dirname \"$1\")\" && printf 'wheel\\n' > \"$1\"", "install", "{wheel}"],
                "env": {},
            }

            result = self.run_executor(plan, archive, rootfs_identity, None, wheel_out=wheel)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(wheel.read_text(encoding="utf-8"), "wheel\n")

    def test_prefix_and_wheel_outputs_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            tmp_path = Path(tmp)
            archive = self.make_archive(tmp_path, {"payload.txt": "source\n"})
            rootfs_identity = tmp_path / "rootfs.identity.json"
            write_rootfs_identity(rootfs_identity)
            prefix = tmp_path / "prefix"
            wheel = tmp_path / "dist" / "fixture.whl"
            plan = minimal_plan()
            plan["outputs"] = [{"kind": "prefix", "path": "{out}"}, {"kind": "wheel", "path": "{wheel}"}]
            plan["configure"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["build"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["install"] = {
                "cwd": "{src}",
                "argv": [
                    "bash",
                    "-c",
                    "mkdir -p \"$1\" \"$(dirname \"$2\")\" && printf 'prefix\\n' > \"$1/prefix.txt\" && printf 'wheel\\n' > \"$2\"",
                    "install",
                    "{out}",
                    "{wheel}",
                ],
                "env": {},
            }

            result = self.run_executor(plan, archive, rootfs_identity, prefix, wheel_out=wheel)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((prefix / "prefix.txt").read_text(encoding="utf-8"), "prefix\n")
            self.assertEqual(wheel.read_text(encoding="utf-8"), "wheel\n")

    def test_post_install_rewrites_pkg_config_prefixes_relative_to_pcfiledir(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            tmp_path = Path(tmp)
            archive = self.make_archive(tmp_path, {"payload.txt": "source\n"})
            rootfs_identity = tmp_path / "rootfs.identity.json"
            write_rootfs_identity(rootfs_identity)
            prefix = tmp_path / "prefix"
            plan = minimal_plan()
            plan["configure"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["build"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["install"] = {
                "cwd": "{src}",
                "argv": [
                    "bash",
                    "-c",
                    "mkdir -p \"$1/lib/pkgconfig\" && printf 'prefix=/\\nName: zlib\\n' > \"$1/lib/pkgconfig/zlib.pc\"",
                    "install",
                    "{out}",
                ],
                "env": {},
            }

            result = self.run_executor(plan, archive, rootfs_identity, prefix)

            self.assertEqual(result.returncode, 0, result.stderr)
            pc_lines = (prefix / "lib" / "pkgconfig" / "zlib.pc").read_text(encoding="utf-8").splitlines()
            self.assertEqual(pc_lines[0], "prefix=${pcfiledir}/../..")

    def test_jobs_placeholder_matches_action_reserved_cpus(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            tmp_path = Path(tmp)
            archive = self.make_archive(tmp_path, {"payload.txt": "source\n"})
            rootfs_identity = tmp_path / "rootfs.identity.json"
            write_rootfs_identity(rootfs_identity)
            probe = tmp_path / "jobs.txt"
            plan = minimal_plan()
            plan["configure"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["build"] = {
                "cwd": "{src}",
                "argv": ["bash", "-c", "printf '%s\\n' \"$1\" > \"$2\"", "build", "-j{jobs}", str(probe)],
                "env": {},
            }
            plan["install"] = {"cwd": "{src}", "argv": ["true"], "env": {}}

            result = self.run_executor(
                plan,
                archive,
                rootfs_identity,
                tmp_path / "prefix",
                env={"VASO_ACTION_RESERVED_CPUS": "4"},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(probe.read_text(encoding="utf-8"), "-j4\n")

    def test_post_install_scan_rejects_action_pwd_execroot_output_root_and_fixed_patterns(self) -> None:
        leak_tokens = [
            ("pwd", "PWD"),
            ("execroot", "EXECROOT"),
            ("output-root", "OUTPUT_ROOT"),
            ("vaso-line", "/vaso/lines/cu130/cache/bazel/output-base"),
            ("estate", "$VASO_ESTATE_ROOT/.vaso-estate/agents/fc-n1-1"),
            ("bazel-out", "bazel-out/k8-fastbuild/bin/native/pkg"),
        ]
        for name, token in leak_tokens:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
                    tmp_path = Path(tmp)
                    output_root = tmp_path / "output-root"
                    execroot = output_root / "execroot"
                    action_cwd = execroot / "_main"
                    action_cwd.mkdir(parents=True)
                    archive = self.make_archive(tmp_path, {"payload.txt": "source\n"})
                    rootfs_identity = tmp_path / "rootfs.identity.json"
                    write_rootfs_identity(rootfs_identity)
                    prefix = tmp_path / "prefix"
                    plan = minimal_plan()
                    plan["configure"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
                    plan["build"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
                    actual_token = {
                        "PWD": str(action_cwd),
                        "EXECROOT": str(action_cwd),
                        "OUTPUT_ROOT": str(output_root),
                    }.get(token, token)
                    plan["install"] = {
                        "cwd": "{src}",
                        "argv": [
                            "bash",
                            "-c",
                            "mkdir -p \"$1\" && printf '%s\\n' \"$2\" > \"$1/leak.txt\"",
                            "install",
                            "{out}",
                            actual_token,
                        ],
                        "env": {},
                    }

                    result = self.run_executor(plan, archive, rootfs_identity, prefix, cwd=action_cwd)

                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("non-relocatable output leak", result.stderr)

    def test_post_install_scan_rejects_work_output_base_and_estate_paths(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            tmp_path = Path(tmp)
            archive = self.make_archive(tmp_path, {"payload.txt": "source\n"})
            rootfs_identity = tmp_path / "rootfs.identity.json"
            write_rootfs_identity(rootfs_identity)
            prefix = tmp_path / "prefix"
            plan = minimal_plan()
            plan["configure"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["build"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["install"] = {
                "cwd": "{src}",
                "argv": ["bash", "-c", "mkdir -p \"$1\" && printf '%s\\n' \"$1\" > \"$1/leak.txt\"", "install", "{out}"],
                "env": {},
            }

            result = self.run_executor(plan, archive, rootfs_identity, prefix)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("non-relocatable output leak", result.stderr)

    def test_post_install_scan_reports_every_prefix_and_wheel_leak(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            tmp_path = Path(tmp)
            archive = self.make_archive(tmp_path, {"payload.txt": "source\n"})
            rootfs_identity = tmp_path / "rootfs.identity.json"
            write_rootfs_identity(rootfs_identity)
            prefix = tmp_path / "prefix"
            wheel = tmp_path / "dist" / "fixture.whl"
            plan = minimal_plan()
            plan["outputs"] = [{"kind": "prefix", "path": "{out}"}, {"kind": "wheel", "path": "{wheel}"}]
            plan["configure"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["build"] = {"cwd": "{src}", "argv": ["true"], "env": {}}
            plan["install"] = {
                "cwd": "{src}",
                "argv": [
                    sys.executable,
                    "-c",
                    textwrap.dedent(
                        """\
                        import sys
                        import zipfile
                        from pathlib import Path

                        prefix = Path(sys.argv[1])
                        wheel = Path(sys.argv[2])
                        estate = sys.argv[3]
                        (prefix / "bin").mkdir(parents=True)
                        (prefix / "lib").mkdir(parents=True)
                        (prefix / "bin" / "prefix-leak.txt").write_text(str(prefix) + "/include\\n", encoding="utf-8")
                        (prefix / "lib" / "estate-link").symlink_to(estate + "/dep/libfixture.so")
                        wheel.parent.mkdir(parents=True, exist_ok=True)
                        with zipfile.ZipFile(wheel, "w") as archive:
                            archive.writestr("pkg/bazel-path.txt", "bazel-out/k8-fastbuild/bin/native/pkg\\n")
                            archive.writestr("pkg/prefix-path.txt", str(prefix) + "/inside-wheel\\n")
                        """
                    ),
                    "{out}",
                    "{wheel}",
                    "$VASO_ESTATE_ROOT/.vaso-estate/agents/fc-n1-6b",
                ],
                "env": {},
            }

            result = self.run_executor(plan, archive, rootfs_identity, prefix, wheel_out=wheel)

            self.assertNotEqual(result.returncode, 0)
            for expected in (
                "prefix-leak.txt",
                "estate-link",
                "fixture.whl!pkg/bazel-path.txt",
                "fixture.whl!pkg/prefix-path.txt",
            ):
                self.assertIn(expected, result.stderr)


class SafeExtractArchiveTest(unittest.TestCase):
    def _module(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("plan_executor_under_test", Path(__file__).with_name("plan_executor.py"))
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    def _archive(self, root: Path, members: list[tuple[str, str, str | None]]) -> Path:
        import io
        import tarfile
        archive = root / "src.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            for name, kind, target in members:
                info = tarfile.TarInfo(name)
                if kind == "file":
                    data = b"x"
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
                elif kind == "dir":
                    info.type = tarfile.DIRTYPE
                    info.mode = 0o755
                    tar.addfile(info)
                elif kind == "sym":
                    info.type = tarfile.SYMTYPE
                    info.linkname = target or ""
                    tar.addfile(info)
                elif kind == "hard":
                    info.type = tarfile.LNKTYPE
                    info.linkname = target or ""
                    tar.addfile(info)
        return archive

    def test_in_root_relative_symlinks_and_hard_links_extract(self) -> None:
        # torchvision 0.29.0 ships ../ symlinks in gallery/ (post-N1-3 gate red, 2026-10-07).
        module = self._module()
        with tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMPDIR") or None) as td:
            root = Path(td)
            archive = self._archive(root, [
                ("pkg", "dir", None),
                ("pkg/assets", "dir", None),
                ("pkg/assets/a.jpg", "file", None),
                ("pkg/gallery", "dir", None),
                ("pkg/gallery/a.jpg", "sym", "../assets/a.jpg"),
                ("pkg/gallery/b.jpg", "hard", "pkg/assets/a.jpg"),
            ])
            module.safe_extract_archive(archive, root / "out")
            self.assertTrue((root / "out" / "pkg" / "gallery" / "a.jpg").is_symlink())

    def test_links_escaping_the_root_fail(self) -> None:
        module = self._module()
        for kind, target in (("sym", "../../outside"), ("sym", "/etc/passwd"), ("hard", "../outside")):
            with self.subTest(kind=kind, target=target):
                with tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMPDIR") or None) as td:
                    root = Path(td)
                    archive = self._archive(root, [("pkg", "dir", None), ("pkg/link", kind, target)])
                    with self.assertRaises(SystemExit):
                        module.safe_extract_archive(archive, root / "out")


class ActionPathTokensTest(unittest.TestCase):
    def test_fixed_output_base_mount_is_not_a_leak_token(self) -> None:
        # post-N1-3 gate: torchvision RPATHs point at /vaso-bazel-ob/external/<dep>/prefix/lib.
        module = SafeExtractArchiveTest()._module()
        tokens = module.action_path_tokens([Path("/vaso-bazel-ob/execroot/_main/bazel-out/k8-fastbuild/bin/native/x")])
        self.assertNotIn("/vaso-bazel-ob", tokens)
        self.assertIn("/vaso-bazel-ob/execroot", tokens)
        self.assertIn("/vaso/lines/", tokens)

    def test_varying_output_base_root_stays_a_leak_token(self) -> None:
        module = SafeExtractArchiveTest()._module()
        tokens = module.action_path_tokens([Path("/vaso/lines/cu130/cache/bazel/output-base/execroot/_main/x")])
        self.assertIn("/vaso/lines/cu130/cache/bazel/output-base", tokens)


class MainArgvTest(unittest.TestCase):
    def test_main_accepts_argv_from_launchers(self) -> None:
        # post-N1-3 gate red: runtime_jobs_executor called executor.main(args) -> TypeError.
        module = SafeExtractArchiveTest()._module()
        with self.assertRaises((SystemExit, FileNotFoundError)) as cm:
            module.main(["--plan", "/nonexistent/plan.json", "--source-archive", "src=/nonexistent.tar.gz", "--rootfs-identity", "/nonexistent.json"])
        # The argv list reached argparse and the plan path was used (no TypeError on the call).
        self.assertIn("/nonexistent/plan.json", str(cm.exception))


class CommandEnvTmpdirTest(unittest.TestCase):
    def test_tmpdir_is_inside_the_action_work_tree(self) -> None:
        module = SafeExtractArchiveTest()._module()
        import inspect
        source = inspect.getsource(module.command_env)
        self.assertIn('action_tmp = work / "tmp"', source)
        for name in ("TMPDIR", "TMP", "TEMP"):
            self.assertIn(f'"{name}"', source)


if __name__ == "__main__":
    unittest.main()
