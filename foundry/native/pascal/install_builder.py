"""Install an additive Bazel builder into a NEW mutable Pascal capsule stage.

The caller owns capsule construction/publication. Existing published bundles
are rejected. Existing file inodes are never edited or chmodded in place.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

from plans import (PROVIDERS, TORCH_ARCHIVE, TRITON_ARCHIVE, WHEEL_NAMES, build_plan,
                   pascal_schema, provider_contract, rootfs_identity, stable)

HERE = Path(__file__).resolve().parent
FOUNDRY = HERE.parents[1]
BAZEL_SHA256 = "fe7e799cbc9140f986b063e06800a3d4c790525075c877d00a7112669824acbf"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def replace_copy(source: Path, destination: Path, mode: int | None = None):
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix="." + destination.name + "-", dir=destination.parent)
    os.close(fd)
    try:
        shutil.copyfile(source, temp)
        os.chmod(temp, mode if mode is not None else source.stat().st_mode & 0o777)
        os.replace(temp, destination)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def create_builder(destination: Path, torch_profile_text: str, triton_profile_text: str,
                   patch_text: str, base_capsule: str):
    torch_profile = json.loads(torch_profile_text)
    triton_profile = json.loads(triton_profile_text)
    contract = provider_contract(base_capsule)
    identity = rootfs_identity(contract)
    contract_text = stable(contract)
    schema = pascal_schema(json.loads((FOUNDRY / "plans/schema/v1.json").read_text()))
    (destination / "WORKSPACE").write_text('workspace(name = "vaso_pascal")\n')
    files = {
        "torch.profile.json": torch_profile_text, "triton.profile.json": triton_profile_text,
        "signed-int8-dot.patch": patch_text, "capsule.providers.json": contract_text,
        "rootfs.identity.json": stable(identity), "schema.json": stable(schema),
    }
    for package in WHEEL_NAMES:
        files[package + ".plan.json"] = stable(build_plan(
            package, torch_profile, triton_profile, identity,
            files[package + ".profile.json"], contract_text, patch_text,
        ))
    for name, contents in files.items():
        (destination / name).write_text(contents)
    for name in ("native_wheel.bzl", "native_step.py", "build_native.py", "plans.py"):
        shutil.copyfile(HERE / name, destination / name)
    for source, name in (
        (FOUNDRY / "native/common/plan_executor.py", "plan_executor.py"),
        (FOUNDRY / "native/common/plan_schema.py", "plan_schema.py"),
        (FOUNDRY / "native/pytorch/runtime_jobs_executor.py", "runtime_jobs_executor.py"),
    ):
        shutil.copyfile(source, destination / name)
    common = dict(
        toolchain=":capsule", schema=":schema.json", executor=":plan_executor.py",
        executor_schema=":plan_schema.py", runtime_executor=":runtime_jobs_executor.py",
        native_step=":native_step.py",
        dependency_prefixes={name: provider["prefix"] for name, provider in PROVIDERS.items()},
    )
    lines = [
        'load(":native_wheel.bzl", "capsule_toolchain", "native_wheel")',
        'package(default_visibility = ["//visibility:public"])',
        'exports_files(glob(["*.json", "*.py", "*.patch", "inputs/*"]))',
        'capsule_toolchain(name = "capsule", identity = ":rootfs.identity.json", contract = ":capsule.providers.json")',
    ]
    for package, archive in (("torch", TORCH_ARCHIVE), ("triton", TRITON_ARCHIVE)):
        attrs = dict(common, name=package, package=package, wheel_filename=WHEEL_NAMES[package],
                     plan=":" + package + ".plan.json", source_archive=":inputs/" + archive,
                     extra_inputs={":" + package + ".profile.json": package + ".profile.json",
                                   ":capsule.providers.json": "capsule.providers.json"},
                     patches={":signed-int8-dot.patch": "signed-int8-dot.patch"} if package == "triton" else {})
        lines.append("native_wheel(\n" + "".join("    " + name + " = " + json.dumps(value, sort_keys=True) + ",\n" for name, value in attrs.items()) + ")")
    (destination / "BUILD.bazel").write_text("\n\n".join(lines) + "\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--rootfs", type=Path, required=True)
    parser.add_argument("--torch-archive", type=Path, required=True)
    parser.add_argument("--bazel", type=Path, required=True)
    parser.add_argument("--zstd-deb", type=Path, required=True)
    parser.add_argument("--base-capsule-sha256", required=True)
    args = parser.parse_args(argv)
    rootfs = args.rootfs.resolve(strict=True)
    if (rootfs.parent / "insula.json").exists() or (rootfs.parent / "SHA256SUMS").exists():
        raise ValueError("refusing to modify a published capsule; select a new mutable stage")
    prefix = rootfs / "opt/vaso/pascal"
    if not prefix.is_dir():
        raise ValueError("stage must already contain the complete Pascal capsule")
    torch_text = (FOUNDRY / "native/pytorch215/profiles/cu126-sm61-r4.json").read_text()
    triton_text = (FOUNDRY / "native/triton/pascal/profile.json").read_text()
    torch_profile, triton_profile = json.loads(torch_text), json.loads(triton_text)
    if sha256(args.torch_archive) != torch_profile["torch"]["archive_sha256"]:
        raise ValueError("Torch acquisition archive identity mismatch")
    if sha256(args.bazel) != BAZEL_SHA256:
        raise ValueError("Bazel 7.7.0 acquisition identity mismatch")
    if sha256(args.zstd_deb) != PROVIDERS["zstd"]["archive_sha256"]:
        raise ValueError("Zstd acquisition identity mismatch")
    triton_archive = prefix / TRITON_ARCHIVE
    if sha256(triton_archive) != triton_profile["source_sha256"]:
        raise ValueError("Triton acquisition archive identity mismatch")
    # Directory copies are distinct in a hardlinked rootfs stage. Never chmod
    # or overwrite a preexisting file inode shared with an older capsule.
    prefix.chmod(prefix.stat().st_mode | 0o200)
    binary_dir = prefix / "bin"
    binary_dir.chmod(binary_dir.stat().st_mode | 0o200)
    stage = Path(tempfile.mkdtemp(prefix=".foundry-stage-", dir=prefix))
    create_builder(stage, torch_text, triton_text,
                   (FOUNDRY / "native/triton/pascal/signed-int8-dot.patch").read_text(),
                   args.base_capsule_sha256)
    (stage / "inputs").mkdir()
    shutil.copyfile(args.torch_archive, stage / "inputs" / TORCH_ARCHIVE)
    shutil.copyfile(triton_archive, stage / "inputs" / TRITON_ARCHIVE)
    with tempfile.TemporaryDirectory(prefix="zstd-extract-", dir=rootfs.parent) as extracted:
        subprocess.run(["dpkg-deb", "-x", str(args.zstd_deb.resolve()), extracted], check=True)
        replace_copy(Path(extracted) / "usr/bin/zstd", prefix / "zstd/bin/zstd", 0o755)
    destination = prefix / "foundry"
    if destination.exists():
        destination.rename(rootfs.parent / ("foundry-builder-previous-" + uuid.uuid4().hex))
    stage.rename(destination)
    replace_copy(args.bazel, binary_dir / "bazel", 0o755)
    replace_copy(HERE / "build-native.sh", binary_dir / "build-native", 0o755)
    print(stable({"schema": "vaso.pascal-builder-install.v1", "workspace": str(destination),
                  "targets": list(WHEEL_NAMES), "base_capsule_sha256": args.base_capsule_sha256,
                  "bazel_version": "7.7.0", "bazel_sha256": BAZEL_SHA256,
                  "native_dependencies": "complete unchanged Pascal capsule"}), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
