"""Generic execution works without Foundry or host tool prefixes."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]


def minimal_checkout(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    checkout = tmp_path / "checkout"
    shutil.copytree(ROOT / "insula", checkout / "insula", symlinks=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    rootfs = tmp_path / "rootfs"
    (rootfs / "bin").mkdir(parents=True)
    shell = rootfs / "bin/sh"
    shell.write_text("#!/bin/sh\n")
    shell.chmod(0o755)
    return checkout, rootfs, {
        "PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
        "VASO_ESTATE_ROOT": str(tmp_path / "estate"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def test_generic_context_needs_no_foundry_and_preserves_command(tmp_path: Path) -> None:
    checkout, rootfs, env = minimal_checkout(tmp_path)
    literal = "$(touch unwanted); spaces"
    proc = subprocess.run([
        "bash", str(checkout / "insula/run.sh"),
        "--rootfs", str(rootfs), "--workspace", str(checkout),
        "--print-argv", "--", "/bin/sh", "-c", "printf '%s' \"$1\"", "sh", literal,
    ], env=env, cwd=checkout, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    argv = json.loads(proc.stdout)
    assert argv[argv.index("--") + 1:] == ["/bin/sh", "-c", "printf '%s' \"$1\"", "sh", literal]
    assert "--clearenv" in argv and "--unshare-net" in argv
    assert argv[argv.index("--chdir") + 1] == "/workspace/vaso"
    assert "--ro-bind" in argv and str(rootfs / "bin") in argv
    pairs = {argv[i + 1]: argv[i + 2] for i, token in enumerate(argv) if token == "--setenv"}
    assert pairs["PATH"] == "/opt/vaso/compat-bin:/opt/vaso/bin:/usr/local/cuda/bin:/usr/local/bin:/usr/bin:/bin"
    assert (tmp_path / "estate/sources").is_dir()
    assert (tmp_path / "estate/agents/leases").is_dir()
    bindings = [argv[i + 1:i + 3] for i, token in enumerate(argv) if token in {"--bind", "--ro-bind"}]
    assert not any(dest in {"/opt/vaso", "/vaso/tools/bin"} for _, dest in bindings)


def test_generic_context_refuses_missing_rootfs_before_execution(tmp_path: Path) -> None:
    checkout, rootfs, env = minimal_checkout(tmp_path)
    proc = subprocess.run([
        "bash", str(checkout / "insula/run.sh"),
        "--rootfs", str(rootfs / "missing"), "--print-argv", "--", "true",
    ], env=env, cwd=checkout, capture_output=True, text=True)
    assert proc.returncode == 2
    assert "rootfs is missing" in proc.stderr


def test_generic_context_preserves_rootfs_tool_prefix(tmp_path: Path) -> None:
    checkout, rootfs, env = minimal_checkout(tmp_path)
    tools = rootfs / "opt/vaso"
    tools.mkdir(parents=True)
    proc = subprocess.run([
        "bash", str(checkout / "insula/run.sh"),
        "--rootfs", str(rootfs), "--print-argv", "--", "/opt/vaso/python/bin/python3",
    ], env=env, cwd=checkout, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    argv = json.loads(proc.stdout)
    assert any(argv[i:i + 3] == ["--ro-bind", str(tools), "/opt/vaso"] for i in range(len(argv)))


def test_sourceable_launcher_stops_on_failed_preflight(tmp_path: Path) -> None:
    failed = tmp_path / "doctor.sh"
    failed.write_text("#!/bin/sh\nexit 37\n")
    failed.chmod(0o755)
    marker = tmp_path / "executed"
    proc = subprocess.run([
        "bash", "-c", 'source "$1"; INSULA_DOCTOR="$2"; insula /bin/touch "$3"',
        "sh", str(ROOT / "insula/execution/insula.sh"), str(failed), str(marker),
    ], env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}, capture_output=True, text=True)
    assert proc.returncode == 37, proc.stderr
    assert not marker.exists()


@pytest.mark.parametrize("workspace_kind", ["missing", "file", "empty"])
def test_run_rejects_invalid_workspace_before_materializing_estate(tmp_path: Path, workspace_kind: str) -> None:
    checkout, rootfs, env = minimal_checkout(tmp_path)
    workspace = tmp_path / workspace_kind
    if workspace_kind == "file":
        workspace.write_text("not a directory")
    value = "" if workspace_kind == "empty" else str(workspace)
    proc = subprocess.run([
        str(checkout / "insula/run.sh"), "--rootfs", str(rootfs),
        "--workspace", value, "--", "true",
    ], env=env, cwd=checkout, capture_output=True, text=True)
    assert proc.returncode == 2, proc.stderr
    assert "--workspace requires a readable, searchable directory" in proc.stderr
    assert "select --workspace DIR" in proc.stderr
    assert not Path(env["VASO_ESTATE_ROOT"]).exists()
    assert proc.stdout == ""


def test_run_retains_failed_command_output_and_allows_retry(tmp_path: Path) -> None:
    checkout, rootfs, env = minimal_checkout(tmp_path)
    # Boundary stand-in executes argv on the host; projection proof is live acceptance.
    bwrap = tmp_path / "bwrap"
    bwrap.write_text('#!/bin/bash\nwhile [[ "$1" != -- ]]; do shift; done\nshift\nexec "$@"\n')
    bwrap.chmod(0o755)
    env["INSULA_BWRAP_BIN"] = str(bwrap)
    command = [str(checkout / "insula/run.sh"), "--rootfs", str(rootfs), "--", "/bin/sh", "-c"]
    failed = subprocess.run([*command, "printf 'attempt failed\\n'; exit 23"], env=env, capture_output=True, text=True)
    assert failed.returncode == 23, failed.stderr
    assert failed.stdout == "attempt failed\n"
    passed = subprocess.run([*command, "printf 'retry passed\\n'"], env=env, capture_output=True, text=True)
    assert passed.returncode == 0, passed.stderr
    assert passed.stdout == "retry passed\n"
    assert failed.stdout == "attempt failed\n"
    assert not list((Path(env["VASO_ESTATE_ROOT"]) / "agents/leases").glob("*.json"))


def test_run_readiness_failure_preserves_invalid_lease_and_stops_child(tmp_path: Path) -> None:
    checkout, rootfs, env = minimal_checkout(tmp_path)
    lease = Path(env["VASO_ESTATE_ROOT"]) / "agents/leases/insula.json"
    lease.parent.mkdir(parents=True)
    lease.write_text("invalid lease evidence\n")
    marker = tmp_path / "executed"
    proc = subprocess.run([
        str(checkout / "insula/run.sh"), "--rootfs", str(rootfs), "--",
        "/bin/touch", str(marker),
    ], env=env, capture_output=True, text=True)
    assert proc.returncode == 1, proc.stderr
    assert "FAIL: lease_dir:json" in proc.stderr
    assert lease.read_text() == "invalid lease evidence\n"
    assert not marker.exists()
    assert proc.stdout == ""


@pytest.mark.parametrize("shell_kind", ["absolute-alias", "merged-usr", "host-only-alias", "nonexecutable-alias"])
def test_run_checks_shell_in_rootfs_namespace(tmp_path: Path, shell_kind: str) -> None:
    checkout, rootfs, env = minimal_checkout(tmp_path)
    shell = rootfs / "bin/sh"
    shell.unlink()
    (rootfs / "usr/bin").mkdir(parents=True)
    target = rootfs / "usr/bin/vaso-shell"
    target.write_text("#!/bin/sh\n")
    target.chmod(0o755)
    if shell_kind == "merged-usr":
        (rootfs / "bin").rmdir()
        (rootfs / "bin").symlink_to("/usr/bin")
        (rootfs / "usr/bin/sh").symlink_to("/usr/bin/vaso-shell")
    elif shell_kind == "host-only-alias":
        shell.symlink_to("/usr/bin/true")
    else:
        shell.symlink_to("/usr/bin/vaso-shell")
        if shell_kind == "nonexecutable-alias":
            target.chmod(0o644)
    proc = subprocess.run([
        str(checkout / "insula/run.sh"), "--rootfs", str(rootfs),
        "--workspace", str(checkout), "--print-argv", "--", "true",
    ], env=env, capture_output=True, text=True)
    if shell_kind in {"absolute-alias", "merged-usr"}:
        assert proc.returncode == 0, proc.stderr
    else:
        assert proc.returncode == 2, proc.stderr
        assert "no executable /bin/sh" in proc.stderr
        assert not Path(env["VASO_ESTATE_ROOT"]).exists()
