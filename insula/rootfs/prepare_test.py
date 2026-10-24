"""Public rootfs preparation using local pinned bundles and external-tool fixtures."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile

import pytest

from insula.rootfs.verify_cuda_ecosystem_test import make_matching_rootfs

HERE = Path(__file__).resolve().parent
COMMAND = HERE / "prepare.sh"
LOCK = HERE / "cuda_ecosystem.lock.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def inputs(tmp_path: Path):
    def make(line="cu129", shell_kind="regular"):
        source = tmp_path / f"source-{line}"
        rootfs = source / "rootfs"
        make_matching_rootfs(rootfs, line)
        (rootfs / "bin").mkdir()
        (rootfs / "bin/sh").write_text("fixture shell\n")
        (rootfs / "bin/sh").chmod(0o755)
        if shell_kind != "regular":
            (rootfs / "bin/sh").rename(rootfs / "usr/bin/vaso-shell")
            if shell_kind == "merged-usr":
                (rootfs / "bin").rmdir()
                (rootfs / "bin").symlink_to("/usr/bin")
                (rootfs / "usr/bin/sh").symlink_to("/usr/bin/vaso-shell")
            else:
                target = "/usr/bin/true" if shell_kind == "host-only-alias" else "/usr/bin/vaso-shell"
                (rootfs / "bin/sh").symlink_to(target)
                if shell_kind == "nonexecutable-alias":
                    (rootfs / "usr/bin/vaso-shell").chmod(0o644)
        manifest = {"schema_version": 2, "line": line, "lock": {"sha256": sha(LOCK)}}
        (source / "rootfs-bundle.json").write_text(json.dumps(manifest))
        archive = tmp_path / f"{line}.tar.zst"
        with tarfile.open(tmp_path / f"{line}.tar", "w") as stream:
            stream.add(rootfs, arcname="rootfs")
            stream.add(source / "rootfs-bundle.json", arcname="rootfs-bundle.json")
        subprocess.run(["zstd", "-q", str(tmp_path / f"{line}.tar"), "-o", str(archive)], check=True)
        cache = tmp_path / "cache"
        cache.mkdir(exist_ok=True)
        tool = cache / hashlib.sha256(b"fixture bazel\n").hexdigest()
        tool.write_bytes(b"fixture bazel\n")
        tooling = tmp_path / "tools.lock.json"
        tooling.write_text(json.dumps({"tools": [{"name": "bazel", "version": "fixture", "kind": "binary", "path": "opt/vaso/bin/bazel-real", "sha256": sha(tool), "url": tool.as_uri()}]}))
        return ["restore", "--line", line, "--bundle", str(archive), "--digest", sha(archive), "--tools-lock", str(tooling), "--tools-cache", str(cache)], source
    return make


def run(tmp_path, args, **kwargs):
    return subprocess.run(["bash", str(COMMAND), *args], env={**os.environ, "VASO_ESTATE_ROOT": str(tmp_path / "estate")}, capture_output=True, text=True, **kwargs)


@pytest.mark.parametrize("line", ["cu129", "cu130"])
def test_restore_prepares_complete_rootfs_and_reuses_matching_destination(tmp_path, inputs, line):
    args, source = inputs(line)
    result = run(tmp_path, args)
    assert result.returncode == 0, result.stderr
    destination = tmp_path / "estate/rootfs-lines" / line
    assert (destination / "rootfs/opt/vaso/bin/bazel-real").read_bytes() == b"fixture bazel\n"
    assert json.loads((destination / "rootfs-bundle.json").read_text())["line"] == line
    assert not (source / "rootfs/opt/vaso").exists()
    inode = (destination / "rootfs").stat().st_ino
    again = run(tmp_path, args)
    assert again.returncode == 0, again.stderr
    assert json.loads(again.stdout)["status"] == "reused"
    assert (destination / "rootfs").stat().st_ino == inode


@pytest.mark.parametrize("line", ["cu129", "cu130"])
def test_build_requires_acquisition_authority_and_publishes_complete_rootfs(tmp_path, inputs, line):
    from insula.rootfs.build_rootfs_test import write_fake_docker
    import shutil

    restore_args, source = inputs(line)
    args = ["build", *restore_args[1:3], *restore_args[7:]]
    # The constructor's boundary is Docker, not a replacement preparation owner.
    fixture = source / "rootfs"
    (fixture / "etc/passwd").write_text("root:x:0:0:root:/root:/bin/bash\n")
    (fixture / "etc/group").write_text("root:x:0:\n")
    cache_plan = subprocess.run(["python3", str(HERE / "llvm_rootfs_cache.py"), "--lock", str(LOCK), "--estate-root", str(tmp_path / "estate"), "--format", "json"], capture_output=True, text=True, check=True)
    llvm_cache = Path(json.loads(cache_plan.stdout)["cache_dir"])
    shutil.copytree(fixture / "usr/lib/llvm-23", llvm_cache)
    (llvm_cache / ".vaso-llvm-cache-complete").write_text("ok\n")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    write_fake_docker(binaries / "docker", fixture, tmp_path / "Dockerfile", calls_log=tmp_path / "docker.log")
    docker = binaries / "docker"
    # External constructor tools have no input contract: caller stdin stays private.
    capture = tmp_path / "constructor-stdin"
    docker.write_text(docker.read_text().replace(
        "set -euo pipefail", f"set -euo pipefail\ncat >> {str(capture)!r}", 1))
    env = {**os.environ, "PATH": f"{binaries}:{os.environ['PATH']}", "VASO_ESTATE_ROOT": str(tmp_path / "estate")}
    no_authority = subprocess.run(["bash", str(COMMAND), *args], env=env, capture_output=True, text=True)
    assert no_authority.returncode != 0
    assert "--fetch" in no_authority.stderr
    assert not (tmp_path / "docker.log").exists()
    result = subprocess.run(["bash", str(COMMAND), *args, "--fetch"], env=env, input="private caller input\n", capture_output=True, text=True)
    assert capture.read_text() == ""
    assert result.returncode == 0, result.stderr
    destination = tmp_path / "estate/rootfs-lines" / line
    assert (destination / "rootfs/opt/vaso/bin/bazel-real").is_file()
    assert json.loads((destination / "rootfs-bundle.json").read_text())["line"] == line
    assert json.loads((destination / "rootfs-preparation.json").read_text())["mode"] == "build"
    assert "pull " in (tmp_path / "docker.log").read_text()


def test_refuses_symlink_destination_without_touching_published_tree(tmp_path, inputs):
    args, _ = inputs()
    assert run(tmp_path, args).returncode == 0
    destination = tmp_path / "estate/rootfs-lines/cu129"
    alias = tmp_path / "alias"
    alias.symlink_to(destination, target_is_directory=True)
    result = run(tmp_path, [*args, "--dest", str(alias)])
    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert (destination / "rootfs/opt/vaso/bin/bazel-real").read_bytes() == b"fixture bazel\n"


@pytest.mark.parametrize("damage", ["archive-selection", "tool", "manifest", "ecosystem-file", "receipt", "shell-missing", "shell-permissions", "shell-cycle"])
def test_refuses_mismatched_or_damaged_existing_state_and_retains_failure(tmp_path, inputs, damage):
    args, _ = inputs()
    assert run(tmp_path, args).returncode == 0
    destination = tmp_path / "estate/rootfs-lines/cu129"
    if damage == "archive-selection":
        args[6] = "0" * 64
    elif damage == "tool":
        (destination / "rootfs/opt/vaso/bin/bazel-real").chmod(0o755)
        (destination / "rootfs/opt/vaso/bin/bazel-real").write_text("damaged")
    elif damage == "manifest":
        manifest = destination / "rootfs-bundle.json"
        data = json.loads(manifest.read_text())
        data["line"] = "cu130"
        manifest.write_text(json.dumps(data))
    elif damage == "ecosystem-file":
        (destination / "rootfs/usr/local/cuda-12.9/include/cudnn_version.h").unlink()
    elif damage == "shell-missing":
        (destination / "rootfs/bin/sh").unlink()
    elif damage == "shell-permissions":
        (destination / "rootfs/bin/sh").chmod(0o644)
    elif damage == "shell-cycle":
        shell = destination / "rootfs/bin/sh"
        shell.unlink()
        shell.symlink_to("sh")
    else:
        (destination / "rootfs-preparation.json").unlink()
    inode = destination.stat().st_ino
    result = run(tmp_path, args)
    assert result.returncode != 0
    outcome = json.loads(result.stdout)
    assert outcome["status"] == "failed"
    assert outcome["exit_code"] != 0
    assert "Traceback" not in result.stderr
    assert "fresh --dest" in outcome["error"]
    assert json.loads((Path(outcome["attempt"]) / "result.json").read_text()) == outcome
    assert destination.stat().st_ino == inode
    assert not list(destination.parent.glob(".cu129.prepare-*/"))


@pytest.mark.parametrize("failure", ["digest", "line", "lock", "tool-input"])
def test_failed_staging_preserves_destination_and_retries_safely(tmp_path, inputs, failure):
    args, _ = inputs()
    bad = args.copy()
    if failure == "digest":
        bad[6] = "0" * 64
    elif failure == "line":
        bad[2] = "cu130"
    elif failure == "lock":
        lock = tmp_path / "other.lock.json"
        lock.write_text(LOCK.read_text() + "\n")
        bad.extend(["--lock", str(lock)])
    else:
        bad.extend(["--tools-cache", str(tmp_path / "missing-cache")])
    result = run(tmp_path, bad)
    assert result.returncode != 0
    outcome = json.loads(result.stdout)
    assert outcome["status"] == "failed"
    assert not (tmp_path / "estate/rootfs-lines" / bad[2]).exists()
    assert not list((tmp_path / "estate/rootfs-lines").glob(".*.prepare-*/"))
    assert run(tmp_path, args).returncode == 0
    assert json.loads((Path(outcome["attempt"]) / "result.json").read_text())["status"] == "failed"


def delayed_extractor(tmp_path):
    import shutil
    binaries = tmp_path / "external-bin"
    binaries.mkdir()
    real_zstd = shutil.which("zstd")
    script = binaries / "zstd"
    script.write_text(f'''#!/usr/bin/env python3
import os, time
from pathlib import Path
Path({str(tmp_path / 'ready')!r}).touch()
while not Path({str(tmp_path / 'release')!r}).exists():
    time.sleep(0.02)
os.execv({real_zstd!r}, [{real_zstd!r}, *__import__('sys').argv[1:]])
''')
    script.chmod(0o755)
    return {**os.environ, "PATH": f"{binaries}:{os.environ['PATH']}", "VASO_ESTATE_ROOT": str(tmp_path / "estate")}


def wait_ready(tmp_path):
    import time
    deadline = time.monotonic() + 10
    while not (tmp_path / "ready").exists():
        assert time.monotonic() < deadline, "extractor did not start"
        time.sleep(0.02)


def test_publication_refuses_destination_created_during_staging(tmp_path, inputs):
    args, _ = inputs()
    proc = subprocess.Popen(["bash", str(COMMAND), *args], env=delayed_extractor(tmp_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        wait_ready(tmp_path)
        destination = tmp_path / "estate/rootfs-lines/cu129"
        destination.mkdir()
        inode = destination.stat().st_ino
    finally:
        (tmp_path / "release").touch()
        out, err = proc.communicate(timeout=15)
    assert proc.returncode != 0, (out, err)
    assert destination.stat().st_ino == inode
    assert not list(destination.iterdir())
    assert json.loads(out)["status"] == "failed"


def test_interruption_retains_outcome_cleans_only_owned_staging_and_allows_retry(tmp_path, inputs):
    import time
    args, _ = inputs()
    unrelated = tmp_path / "estate/rootfs-lines/.other-staging"
    unrelated.mkdir(parents=True)
    (unrelated / "keep").write_text("other owner\n")
    proc = subprocess.Popen(["bash", str(COMMAND), *args], env=delayed_extractor(tmp_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        wait_ready(tmp_path)
        proc.terminate()
        time.sleep(0.15)
    finally:
        (tmp_path / "release").touch()
        out, err = proc.communicate(timeout=15)
    assert proc.returncode == 143, (out, err)
    outcome = json.loads(out)
    assert outcome["status"] == "interrupted"
    assert json.loads((Path(outcome["attempt"]) / "result.json").read_text()) == outcome
    assert not list((tmp_path / "estate/rootfs-lines").glob(".cu129.prepare-*/"))
    assert (unrelated / "keep").read_text() == "other owner\n"
    assert run(tmp_path, args).returncode == 0


def test_concurrent_preparations_publish_once_and_verify_reuse(tmp_path, inputs):
    args, _ = inputs()
    env = delayed_extractor(tmp_path)
    first = subprocess.Popen(["bash", str(COMMAND), *args], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    second = None
    try:
        wait_ready(tmp_path)
        second = subprocess.Popen(["bash", str(COMMAND), *args], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    finally:
        (tmp_path / "release").touch()
        out1, err1 = first.communicate(timeout=15)
        if second:
            out2, err2 = second.communicate(timeout=15)
    assert first.returncode == second.returncode == 0, (err1, err2)
    assert {json.loads(out1)["status"], json.loads(out2)["status"]} == {"prepared", "reused"}


@pytest.mark.parametrize("bad_manifest", [[], {"line": "cu129", "lock": []}])
def test_malformed_manifest_retains_contextual_failure(tmp_path, inputs, bad_manifest):
    args, source = inputs()
    manifest = source / "rootfs-bundle.json"
    manifest.write_text(json.dumps(bad_manifest))
    archive = Path(args[4])
    with tarfile.open(tmp_path / "malformed.tar", "w") as stream:
        stream.add(source / "rootfs", arcname="rootfs")
        stream.add(manifest, arcname="rootfs-bundle.json")
    subprocess.run(["zstd", "-q", "-f", str(tmp_path / "malformed.tar"), "-o", str(archive)], check=True)
    args[6] = sha(archive)
    result = run(tmp_path, args)
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert json.loads(result.stdout)["status"] == "failed"


def test_empty_estate_is_rejected_before_creating_state(tmp_path, inputs):
    args, _ = inputs()
    result = subprocess.run(["bash", str(COMMAND), *args], cwd=tmp_path, env={**os.environ, "VASO_ESTATE_ROOT": ""}, capture_output=True, text=True)
    assert result.returncode != 0
    assert "estate" in result.stderr
    assert not (tmp_path / "agents").exists()


@pytest.mark.parametrize("linked", ["rootfs", "rootfs/opt"])
def test_restore_refuses_external_tree_links_before_installing_tools(tmp_path, inputs, linked):
    import shutil
    args, source = inputs()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel").write_text("untouched")
    target = source / linked
    if target.exists():
        shutil.rmtree(target)
    target.symlink_to(outside)
    with tarfile.open(tmp_path / "unsafe.tar", "w") as stream:
        stream.add(source / "rootfs", arcname="rootfs")
        stream.add(source / "rootfs-bundle.json", arcname="rootfs-bundle.json")
    archive = Path(args[4])
    subprocess.run(["zstd", "-q", "-f", str(tmp_path / "unsafe.tar"), "-o", str(archive)], check=True)
    args[6] = sha(archive)
    result = run(tmp_path, args)
    assert result.returncode != 0
    assert list(outside.iterdir()) == [outside / "sentinel"]
    assert json.loads(result.stdout)["status"] == "failed"


def test_killed_preparation_is_reconciled_on_safe_retry(tmp_path, inputs):
    args, _ = inputs()
    proc = subprocess.Popen(["bash", str(COMMAND), *args], env=delayed_extractor(tmp_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        wait_ready(tmp_path)
        proc.kill()
    finally:
        (tmp_path / "release").touch()
        proc.communicate(timeout=15)
    attempts = list((tmp_path / "estate/agents/rootfs-prepare").glob("*/result.json"))
    assert len(attempts) == 1
    again = run(tmp_path, args)
    assert again.returncode == 0, again.stderr
    abandoned = json.loads(attempts[0].read_text())
    assert abandoned["status"] == "interrupted"
    assert abandoned["exit_code"] == 137
    assert not list((tmp_path / "estate/rootfs-lines").glob(".cu129.prepare-*/"))


def test_complete_bundle_restores_without_tool_cache_or_acquisition(tmp_path, inputs):
    import shutil
    args, _ = inputs()
    assert run(tmp_path, args).returncode == 0
    prepared = tmp_path / "estate/rootfs-lines/cu129"
    archive = tmp_path / "complete.tar.zst"
    with tarfile.open(tmp_path / "complete.tar", "w") as stream:
        stream.add(prepared / "rootfs", arcname="rootfs")
        stream.add(prepared / "rootfs-bundle.json", arcname="rootfs-bundle.json")
    subprocess.run(["zstd", "-q", str(tmp_path / "complete.tar"), "-o", str(archive)], check=True)
    shutil.rmtree(tmp_path / "cache")
    args[4], args[6] = str(archive), sha(archive)
    result = run(tmp_path, [*args, "--dest", str(tmp_path / "offline-line")])
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "offline-line/rootfs/opt/vaso/bin/bazel-real").read_bytes() == b"fixture bazel\n"


def test_help_names_the_supported_preparation_command(tmp_path):
    result = run(tmp_path, ["--help"])
    assert result.returncode == 0
    assert result.stdout.startswith("usage: insula/rootfs/prepare.sh ")


@pytest.mark.parametrize("shell_kind", ["missing", "bash-only", "nonexecutable", "cycle"])
def test_restore_rejects_unusable_shell_before_publication(tmp_path, inputs, shell_kind):
    args, source = inputs()
    shell = source / "rootfs/bin/sh"
    if shell_kind == "nonexecutable":
        shell.chmod(0o644)
    elif shell_kind == "bash-only":
        shell.rename(shell.with_name("bash"))
    elif shell_kind == "cycle":
        shell.unlink()
        shell.symlink_to("sh")
    else:
        shell.unlink()
    with tarfile.open(tmp_path / "shell.tar", "w") as stream:
        stream.add(source / "rootfs", arcname="rootfs")
        stream.add(source / "rootfs-bundle.json", arcname="rootfs-bundle.json")
    subprocess.run(["zstd", "-q", "-f", str(tmp_path / "shell.tar"), "-o", args[4]], check=True)
    args[6] = sha(Path(args[4]))
    result = run(tmp_path, args)
    assert result.returncode != 0
    outcome = json.loads(result.stdout)
    assert outcome["status"] == "failed"
    assert outcome["exit_code"] != 0
    assert "Traceback" not in result.stderr
    assert "fresh --dest" in outcome["error"]
    assert json.loads((Path(outcome["attempt"]) / "result.json").read_text()) == outcome
    assert not list((tmp_path / "estate/rootfs-lines").glob(".cu129.prepare-*/"))
    assert not (tmp_path / "estate/rootfs-lines/cu129").exists()
    if shell_kind != "cycle":
        assert "executable /bin/sh" in outcome["error"]


@pytest.mark.parametrize("shell_kind", ["absolute-alias", "merged-usr", "host-only-alias", "nonexecutable-alias"])
def test_restore_resolves_shell_in_rootfs_namespace(tmp_path, inputs, shell_kind):
    args, _ = inputs(shell_kind=shell_kind)
    result = run(tmp_path, args)
    destination = tmp_path / "estate/rootfs-lines/cu129"
    if shell_kind in {"absolute-alias", "merged-usr"}:
        assert result.returncode == 0, result.stderr
        again = run(tmp_path, args)
        assert again.returncode == 0, again.stderr
        assert json.loads(again.stdout)["status"] == "reused"
    else:
        assert result.returncode != 0
        assert "executable /bin/sh" in json.loads(result.stdout)["error"]
        assert not destination.exists()
