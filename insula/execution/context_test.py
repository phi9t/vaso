"""Generic execution works without Foundry or host tool prefixes."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess


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
        "bash", str(checkout / "insula/execution/insula.sh"),
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
        "bash", str(checkout / "insula/execution/insula.sh"),
        "--rootfs", str(rootfs / "missing"), "--print-argv", "--", "true",
    ], env=env, cwd=checkout, capture_output=True, text=True)
    assert proc.returncode == 2
    assert "rootfs is missing" in proc.stderr


def test_generic_context_preserves_rootfs_tool_prefix(tmp_path: Path) -> None:
    checkout, rootfs, env = minimal_checkout(tmp_path)
    tools = rootfs / "opt/vaso"
    tools.mkdir(parents=True)
    proc = subprocess.run([
        "bash", str(checkout / "insula/execution/insula.sh"),
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
