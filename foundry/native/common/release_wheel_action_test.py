#!/usr/bin/env python3
"""Static checks for release-wheel action wiring."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from nested_bazel_fixture import local_override_block, write, write_module_override_stubs


class ReleaseWheelActionTest(unittest.TestCase):
    def test_repair_declares_cached_torch_runtime_through_forwarded_providers(self) -> None:
        bazel = os.environ.get("BAZEL_BIN") or os.environ.get("BAZEL") or "/opt/vaso/bin/bazel-real"
        self.assertTrue(Path(bazel).is_file(), "run this integration check with rootfs Bazel inside insula")
        with tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR")) as tmp:
            root = Path(tmp)
            overrides = root / "overrides"
            write_module_override_stubs(overrides)
            write(root / "MODULE.bazel", 'module(name="release_input_fixture")\n' + local_override_block(overrides))
            write(root / "BUILD.bazel", '''
load(":fixture.bzl", "torch_native", "vision_native", "jax_native", "forward")
load("//native/common:release_wheel_action.bzl", "release_wheel_action")
exports_files(["rootfs/cuda_ecosystem.lock.json"])
torch_native(name="torch_impl")
forward(name="torch", actual=":torch_impl")
vision_native(name="vision_impl", torch_prefix=":torch")
forward(name="vision", actual=":vision_impl")
release_wheel_action(name="release", input=":vision", profile="torch")
jax_native(name="jax")
release_wheel_action(name="jax_release", input=":jax", profile="jax", wheelhouse=True)
''')
            write(root / "rootfs/cuda_ecosystem.lock.json", "{}")
            write(root / "locks/BUILD.bazel", 'exports_files(["torch.lock.json"])')
            write(root / "locks/torch.lock.json", "{}")
            providers = {
                "pytorch": "PytorchNativePrefixInfo", "torchvision": "TorchvisionNativePrefixInfo",
                "torchaudio": "TorchaudioNativePrefixInfo", "triton": "TritonNativePrefixInfo",
                "jaxlib": "JaxlibNativePrefixInfo",
            }
            for package, provider in providers.items():
                write(root / f"native/{package}/BUILD.bazel", "")
                write(root / f"native/{package}/{package}_action.bzl",
                      f'{provider}=provider(fields=["prefix","wheel","wheelhouse","provider_metadata"])\n')
            write(root / "native/common/extension_info.bzl", 'VasoTorchExtensionInfo=provider(fields=["wheel","provider_metadata"])\n')
            write(root / "native/common/native_action_toolchain.bzl", '''
def native_action_determinism_attrs():
    return {}
def native_action_deterministic_env(ctx, compiler_flags=True):
    return struct(env={"PATH":"/usr/bin:/bin", "LC_ALL":"C"})
''')
            write(root / "native/common/build_settings.bzl", '''
ReleaseCudaLineInfo=provider(fields=["value"])
ReleaseGitShaInfo=provider(fields=["value"])
def _line(ctx):
    return [ReleaseCudaLineInfo(value="cu129")]
def _sha(ctx):
    return [ReleaseGitShaInfo(value="abcdef1234567890")]
release_cuda_line=rule(implementation=_line)
release_git_sha=rule(implementation=_sha)
''')
            write(root / "native/common/BUILD.bazel", '''
load(":build_settings.bzl", "release_cuda_line", "release_git_sha")
load("//:fixture.bzl", "driver")
package(default_visibility=["//visibility:public"])
release_cuda_line(name="release_line")
release_git_sha(name="release_git_sha")
driver(name="repair_wheel")
''')
            write(root / "fixture.bzl", '''
load("//native/pytorch:pytorch_action.bzl", "PytorchNativePrefixInfo")
load("//native/torchvision:torchvision_action.bzl", "TorchvisionNativePrefixInfo")
load("//native/jaxlib:jaxlib_action.bzl", "JaxlibNativePrefixInfo")
def _torch(ctx):
    prefix=ctx.actions.declare_directory(ctx.label.name+"_prefix")
    ctx.actions.run_shell(outputs=[prefix],command="mkdir -p "+prefix.path)
    return [DefaultInfo(files=depset([prefix])),PytorchNativePrefixInfo(prefix=prefix)]
torch_native=rule(implementation=_torch)
def _vision(ctx):
    wheel=ctx.actions.declare_file("raw.whl")
    metadata=ctx.actions.declare_file("metadata.json")
    ctx.actions.write(wheel,"raw wheel")
    ctx.actions.write(metadata,json.encode({"input_prefixes":{"torch":ctx.attr.torch_prefix[PytorchNativePrefixInfo].prefix.path}}))
    return [DefaultInfo(files=depset([wheel,metadata])),TorchvisionNativePrefixInfo(wheel=wheel,provider_metadata=metadata)]
vision_native=rule(implementation=_vision,attrs={"torch_prefix":attr.label()})
def _jax(ctx):
    wheelhouse=ctx.actions.declare_directory("jax_wheels")
    metadata=ctx.actions.declare_file("jax_metadata.json")
    ctx.actions.run_shell(outputs=[wheelhouse],command="mkdir -p "+wheelhouse.path)
    ctx.actions.write(metadata,"{}")
    return [DefaultInfo(files=depset([wheelhouse,metadata])),JaxlibNativePrefixInfo(wheelhouse=wheelhouse,provider_metadata=metadata)]
jax_native=rule(implementation=_jax)
def _transition(settings, attr):
    return {}
_same=transition(implementation=_transition,inputs=[],outputs=[])
def _forward(ctx):
    actual=ctx.attr.actual[0] if type(ctx.attr.actual)=="list" else ctx.attr.actual
    info=PytorchNativePrefixInfo if PytorchNativePrefixInfo in actual else TorchvisionNativePrefixInfo
    return [actual[DefaultInfo],actual[info]]
forward=rule(implementation=_forward,attrs={"actual":attr.label(cfg=_same),"_allowlist_function_transition":attr.label(default="@bazel_tools//tools/allowlists/function_transition_allowlist")})
def _driver(ctx):
    out=ctx.actions.declare_file(ctx.label.name+".sh")
    ctx.actions.write(out,"#!/bin/sh\\nexit 0\\n",is_executable=True)
    return [DefaultInfo(executable=out)]
driver=rule(implementation=_driver,executable=True)
''')
            shutil.copyfile(ROOT / "native/common/release_wheel_action.bzl", root / "native/common/release_wheel_action.bzl")
            result = subprocess.run(
                [bazel, "--batch", f"--output_base={root / 'ob'}", "aquery",
                 'mnemonic("VasoReleaseWheelRepair", set(//:release //:jax_release))', "--output=jsonproto",
                 "--registry=file:///nonexistent", "--repository_disable_download", "--lockfile_mode=off"],
                cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            graph = json.loads(result.stdout)
            fragments = {str(f["id"]): f for f in graph["pathFragments"]}
            def path(fragment_id):
                fragment = fragments[str(fragment_id)]
                parent = fragment.get("parentId")
                return (path(parent) + "/" if parent else "") + fragment["label"]
            artifacts = {str(a["id"]): path(a["pathFragmentId"]) for a in graph["artifacts"]}
            deps = {str(d["id"]): d for d in graph["depSetOfFiles"]}
            def inputs(dep_id):
                dep = deps[str(dep_id)]
                return {artifacts[str(a)] for a in dep.get("directArtifactIds", [])} | {
                    p for child in dep.get("transitiveDepSetIds", []) for p in inputs(child)
                }
            targets = {str(t["id"]): t["label"].rsplit(":", 1)[-1] for t in graph["targets"]}
            self.assertEqual(len(graph["actions"]), 2)
            for action in graph["actions"]:
                declared = {p for d in action.get("inputDepSetIds", []) for p in inputs(d)}
                self.assertEqual(
                    any(p.endswith("/torch_impl_prefix") for p in declared),
                    targets[str(action["targetId"])] == "release",
                    sorted(declared),
                )

    def test_common_rule_exposes_line_and_sha_build_settings(self) -> None:
        text = (ROOT / "native/common/release_wheel_action.bzl").read_text(encoding="utf-8")

        self.assertIn("release_cuda_line", text)
        self.assertIn("release_git_sha", text)
        self.assertIn("--line", text)
        self.assertIn("--git-sha", text)
        self.assertIn("--profile", text)
        self.assertIn("--spack-lock", text)
        self.assertIn("//locks:torch.lock.json", text)
        self.assertIn("//native/common:repair_wheel", text)
        self.assertIn("VasoTorchExtensionInfo", text)
        self.assertIn("native_action_determinism_attrs", text)
        self.assertIn("native_action_deterministic_env(ctx, compiler_flags = False)", text)
        self.assertIn("env = determinism.env", text)

    def test_release_targets_are_wired_for_single_wheel_packages(self) -> None:
        packages = {
            "pytorch": "pytorch_release_wheel",
            "torchvision": "torchvision_release_wheel",
            "torchaudio": "torchaudio_release_wheel",
            "triton": "triton_release_wheel",
        }
        for package, target in packages.items():
            text = (ROOT / "native" / package / "BUILD.bazel").read_text(encoding="utf-8")
            with self.subTest(package=package):
                self.assertIn('load("//native/common:release_wheel_action.bzl"', text)
                self.assertIn(f'name = "{target}"', text)
                self.assertIn(f'input = ":{package}_action"', text)
                self.assertIn('profile = "torch"', text)

    def test_jaxlib_release_wheelhouse_is_wired(self) -> None:
        text = (ROOT / "native/jaxlib/BUILD.bazel").read_text(encoding="utf-8")

        self.assertIn('load("//native/common:release_wheel_action.bzl"', text)
        self.assertIn('name = "jaxlib_release_wheels"', text)
        self.assertIn('input = ":jaxlib_action"', text)
        self.assertIn('profile = "jax"', text)
        self.assertIn('spack_lock = "//locks:jax.lock.json"', text)
        self.assertIn('wheelhouse = True', text)

    def test_torch_runtime_release_wheelhouse_is_wired_to_native_prefixes(self) -> None:
        build = (ROOT / "native/common/BUILD.bazel").read_text(encoding="utf-8")
        rule = (ROOT / "native/common/runtime_wheelhouse_action.bzl").read_text(encoding="utf-8")

        self.assertIn('load(":runtime_wheelhouse_action.bzl", "runtime_wheelhouse_action")', build)
        self.assertIn('name = "torch_runtime_release_wheelhouse"', build)
        for repo in (
            "py_filelock_native",
            "py_fsspec_native",
            "py_jinja2_native",
            "py_markupsafe_native",
            "py_mpmath_native",
            "py_networkx_native",
            "py_numpy_native",
            "py_pillow_native",
            "py_setuptools_native",
            "py_sympy_native",
            "py_typing_extensions_native",
        ):
            self.assertIn(f"@{repo}//:prefix_path.txt", build)
            self.assertIn(f"@{repo}//:prefix", build)
        self.assertIn("RuntimeWheelhouseInfo", rule)
        self.assertIn("//native/common:runtime_wheelhouse", rule)
        self.assertIn("native_action_determinism_attrs", rule)
        self.assertIn("native_action_deterministic_env(ctx, compiler_flags = False)", rule)
        self.assertIn("env = determinism.env", rule)
        self.assertIn('export PATCHELF="${PATCHELF:-/usr/bin/patchelf}"', rule)
        self.assertIn('export READELF="${READELF:-/usr/bin/readelf}"', rule)

    def test_jax_runtime_release_wheelhouse_is_wired_to_native_prefixes(self) -> None:
        build = (ROOT / "native/common/BUILD.bazel").read_text(encoding="utf-8")

        self.assertIn('name = "jax_runtime_release_wheelhouse"', build)
        for repo in (
            "py_absl_py_native",
            "py_ml_dtypes_native",
            "py_numpy_native",
            "py_opt_einsum_native",
            "py_scipy_native",
        ):
            self.assertIn(f"@{repo}//:prefix_path.txt", build)
            self.assertIn(f"@{repo}//:prefix", build)
        self.assertIn("provider_prefix_files", build)
        self.assertIn("provider_prefix_trees", build)
        self.assertIn("@openblas_native//:prefix_path.txt", build)
        self.assertIn("@openblas_native//:prefix", build)


if __name__ == "__main__":
    unittest.main()
