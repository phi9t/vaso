"""Test-only plan_executor fixture for leak scanning inside a Bazel action."""

def _leak_scan_real_action_fixture_impl(ctx):
    source_archive = ctx.actions.declare_file(ctx.label.name + ".source.tar.gz")
    rootfs_identity = ctx.actions.declare_file(ctx.label.name + ".rootfs.identity.json")
    plan = ctx.actions.declare_file(ctx.label.name + ".plan.json")
    prefix = ctx.actions.declare_directory(ctx.label.name + "_prefix")
    generator = ctx.actions.declare_file(ctx.label.name + "_gen.py")

    ctx.actions.write(
        output = generator,
        is_executable = True,
        content = """#!/usr/bin/env python3
import gzip
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

source_archive = Path(sys.argv[1])
rootfs_identity = Path(sys.argv[2])
plan = Path(sys.argv[3])

payload = b"fixture source\\n"
tar_buffer = io.BytesIO()
with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
    info = tarfile.TarInfo("zlib-ng-2.3.3/payload.txt")
    info.size = len(payload)
    info.mtime = 0
    info.mode = 0o644
    tar.addfile(info, io.BytesIO(payload))
compressed = gzip.compress(tar_buffer.getvalue(), mtime=0)
source_archive.write_bytes(compressed)

rootfs_doc = {
    "line": "cu130",
    "live_checks": {
        "commands": [
            {
                "name": "fixture-rootfs",
                "argv": ["python3", "-c", "print('fixture-rootfs-ok')"],
                "expect_stdout": "fixture-rootfs-ok",
            },
        ],
    },
}
rootfs_identity.write_text(json.dumps(rootfs_doc, sort_keys=True) + "\\n", encoding="utf-8")

toolchain_env = {}
toolchain_env["C" + "C"] = "/usr/bin/" + "gcc"
toolchain_env["C" + "XX"] = "/usr/bin/" + "g++"
toolchain_env["C" + "FLAGS"] = ""
toolchain_env["C" + "XXFLAGS"] = ""
toolchain_env["CUDA" + "FLAGS"] = ""
toolchain_env["NV" + "C" + "C" + "_" + "FLAGS"] = "-forward-unknown-to-host-compiler"

plan_doc = {
    "schema": 1,
    "profile": "torch",
    "line": "cu130",
    "package": {"name": "zlib-ng", "version": "fixture"},
    "sources": [
        {
            "name": "main",
            "archive": "{source_archive}",
            "sha256": hashlib.sha256(compressed).hexdigest(),
            "strip_prefix": "zlib-ng-2.3.3",
            "urls": [],
            "patches": [],
        },
    ],
    "inputs": [],
    "toolchain": {
        "cc": "toolchain:gcc_13_3",
        "cxx": "toolchain:gcc_13_3",
        "cuda": "rootfs:cuda@13.0.3",
        "env": toolchain_env,
        "python": "python_313_native",
        "rootfs_identity": {
            "label": "//plans:torch/cu130/rootfs.identity.json",
            "sha256": hashlib.sha256(rootfs_identity.read_bytes()).hexdigest(),
        },
    },
    "deps": {},
    "configure": {"cwd": "{src}", "argv": ["true"], "env": {}},
    "build": {"cwd": "{src}", "argv": ["true"], "env": {}},
    "install": {
        "cwd": "{src}",
        "argv": [
            "bash",
            "-c",
            "mkdir -p \\"$1\\" && printf '%s\\\\n%s\\\\n%s\\\\n%s\\\\n' \\"$PWD\\" \\"$1\\" 'bazel-out/k8-fastbuild/bin/native/pkg' '/vaso/lines/cu130/cache/bazel/output-base' > \\"$1/leak.txt\\"",
            "install",
            "{out}",
        ],
        "env": {},
    },
    "outputs": [{"kind": "prefix", "path": "{out}"}],
    "gpu_archs": [],
    "determinism": {
        "seed": "sha256:fixture-seed",
        "source_date_epoch": 1770153003,
        "prefix_map": {"{work}": ".", "{out}": "."},
    },
    "live_checks": rootfs_doc["live_checks"],
}
plan.write_text(json.dumps(plan_doc, sort_keys=True) + "\\n", encoding="utf-8")
""",
    )

    ctx.actions.run(
        inputs = [generator],
        outputs = [source_archive, rootfs_identity, plan],
        executable = "python3",
        arguments = [generator.path, source_archive.path, rootfs_identity.path, plan.path],
        env = {
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "PYTHONHASHSEED": "0",
        },
        mnemonic = "PlanExecutorLeakFixtureGen",
        use_default_shell_env = False,
    )

    ctx.actions.run(
        inputs = [
            ctx.file._executor,
            ctx.file._executor_schema,
            ctx.file._schema,
            source_archive,
            rootfs_identity,
            plan,
        ],
        outputs = [prefix],
        executable = "python3",
        arguments = [
            ctx.file._executor.path,
            "--plan",
            plan.path,
            "--schema",
            ctx.file._schema.path,
            "--source-archive",
            "main=" + source_archive.path,
            "--rootfs-identity",
            rootfs_identity.path,
            "--prefix-out",
            prefix.path,
        ],
        env = {
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "PYTHONHASHSEED": "0",
            "VASO_ACTION_RESERVED_CPUS": "4",
        },
        mnemonic = "PlanExecutorLeakFixture",
        progress_message = "Running plan_executor leak-scan fixture %{label}",
        use_default_shell_env = False,
    )

    return [DefaultInfo(files = depset([prefix]))]

leak_scan_real_action_fixture = rule(
    implementation = _leak_scan_real_action_fixture_impl,
    attrs = {
        "_executor": attr.label(
            default = Label("//native/common:plan_executor.py"),
            allow_single_file = True,
        ),
        "_executor_schema": attr.label(
            default = Label("//native/common:plan_schema.py"),
            allow_single_file = True,
        ),
        "_schema": attr.label(
            default = Label("//plans:schema/v1.json"),
            allow_single_file = True,
        ),
    },
)
