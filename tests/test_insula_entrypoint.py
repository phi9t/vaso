from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSULA_SH = ROOT / "scripts" / "insula" / "insula.sh"
RUNTIME_SCRIPTS = [
    ROOT / "experiments" / "spack-bazel-graph" / "run.sh",
    ROOT / "experiments" / "spack-bazel-graph" / "verify_languages.sh",
    ROOT / "experiments" / "spack-bazel-graph" / "bootstrap_insula.sh",
    ROOT / "experiments" / "spack-bazel-graph" / "seal_insula.sh",
]


def _render_sourceable_argv() -> list[str]:
    harness = f"""
set -euo pipefail
source {str(INSULA_SH)!r}
ROOT_PLAN=(--ro-bind /estate/rootfs / --dir /workspace --dir /vaso)
ROOTFS_MANIFEST_BIND=(--ro-bind /estate/rootfs-lines/cu129/rootfs-bundle.json /run/vaso/rootfs-bundle.json)
CUDA_DRIVER_BINDS=(--ro-bind /driver/libcuda.so.1 /run/nvidia-driver/lib/libcuda.so.1 --dev-bind-try /dev/nvidia0 /dev/nvidia0)
INSULA_TMPFS_SIZE=123456
VASO_HOST=/estate/vaso
SOURCES_HOST=/estate/sources
BAZEL_OUTPUT_BASE_HOST=/estate/vaso/lines/cu129/cache/bazel/output-base-cache
BAZEL_OUTPUT_BASE_SB=/vaso/lines/cu129/cache/bazel/output-base-cache
BAZEL_DISK_CACHE_HOST=/estate/vaso/lines/cu129/cache/bazel/disk-cache
BAZEL_DISK_CACHE_SB=/vaso/lines/cu129/cache/bazel/disk-cache
VASO_NATIVE_STATE_HOST=/estate/vaso/lines/cu129/state/native
VASO_NATIVE_STATE_SB=/vaso/lines/cu129/state/native
VASO_NATIVE_STAMPS_HOST=/estate/vaso/lines/cu129/state/stamps
VASO_NATIVE_STAMPS_SB=/vaso/lines/cu129/state/stamps
TOOLS_BIN_HOST=/estate/vaso/tools/bin
ESTATE_ROOT=/estate
HOME_HOST=/estate/home/kvothe
HOME_SB=/home/kvothe
EXP_DIR=/checkout/experiments/spack-bazel-graph
EXP_SB=/workspace/experiment
INNER_PATH=/vaso/tools/bin:/usr/local/cuda/bin:/usr/bin:/bin
INSULA_LIBRARY_PATH=/run/nvidia-driver/lib:/usr/local/cuda/lib64
INSULA_TMP_SB=/vaso/tmp/insula-123
VASO_CUDA_HOME_SB=/usr/local/cuda
VASO_CUDA_LINE=cu129
CUDA_VISIBLE_DEVICES=2,3
VASO_GPU_SET=2,3
VASO_HOST_CPU_JOBS=12
VASO_JAXLIB_MAX_JOBS_RESOLVED=9
ROOTFS_BUNDLE_MANIFEST_SB=/run/vaso/rootfs-bundle.json
INSULA_DIR_ARGS=(--dir /run/vaso --dir /run/nvidia-driver --dir /run/nvidia-driver/lib)
INSULA_BIND_ARGS=(
  --bind "$VASO_HOST" /vaso
  --ro-bind "$SOURCES_HOST" /vaso/sources
  --bind "$BAZEL_OUTPUT_BASE_HOST" "$BAZEL_OUTPUT_BASE_SB"
  --bind "$BAZEL_DISK_CACHE_HOST" "$BAZEL_DISK_CACHE_SB"
  --bind "$VASO_NATIVE_STATE_HOST" "$VASO_NATIVE_STATE_SB"
  --bind "$VASO_NATIVE_STAMPS_HOST" "$VASO_NATIVE_STAMPS_SB"
  --ro-bind "$TOOLS_BIN_HOST" /vaso/tools/bin
  --ro-bind "$ESTATE_ROOT/opt-vaso" /opt/vaso
  --bind "$ESTATE_ROOT/workspace" /workspace
  --bind "$HOME_HOST" "$HOME_SB"
  --bind "$EXP_DIR" "$EXP_SB"
)
INSULA_ENV_ARGS=(
  --setenv PATH "$INNER_PATH"
  --setenv LD_LIBRARY_PATH "$INSULA_LIBRARY_PATH"
  --setenv HOME "$HOME_SB"
  --setenv TMPDIR "$INSULA_TMP_SB"
  --setenv USER kvothe
  --setenv LOGNAME kvothe
  --setenv TERM "${{TERM:-xterm}}"
  --setenv XDG_CACHE_HOME "$HOME_SB/.cache"
  --setenv XDG_CONFIG_HOME "$HOME_SB/.config"
  --setenv VASO_HOME /vaso
  --setenv VASO_BAZEL_OB "$BAZEL_OUTPUT_BASE_SB"
  --setenv VASO_CUDA_HOME "$VASO_CUDA_HOME_SB"
  --setenv VASO_CUDA_LINE "$VASO_CUDA_LINE"
  --setenv CUDA_VISIBLE_DEVICES "${{CUDA_VISIBLE_DEVICES:-}}"
  --setenv VASO_GPU_SET "${{VASO_GPU_SET:-}}"
  --setenv VASO_HOST_CPU_JOBS "${{VASO_HOST_CPU_JOBS:-}}"
  --setenv VASO_JAXLIB_MAX_JOBS "$VASO_JAXLIB_MAX_JOBS_RESOLVED"
  --setenv VASO_IN_INSULA 1
  --setenv VASO_NATIVE_STATE "$VASO_NATIVE_STATE_SB"
  --setenv VASO_NATIVE_STAMPS "$VASO_NATIVE_STAMPS_SB"
  --setenv VASO_ROOTFS_BUNDLE_MANIFEST "$ROOTFS_BUNDLE_MANIFEST_SB"
  --setenv VASO_WORKSPACE_ROOT /workspace
  --setenv VASO_PREFIX /opt/vaso
)
INSULA_CHDIR="$EXP_SB"
insula --print-argv -- bash -lc 'echo hi'
"""
    result = subprocess.run(
        ["bash", "-c", harness],
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "TERM": "xterm"},
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _render_phase_argv(phase: str, *command: str) -> list[str]:
    harness = f"""
set -euo pipefail
source {str(INSULA_SH)!r}
ROOT_PLAN=()
INSULA_DIR_ARGS=()
INSULA_BIND_ARGS=()
INSULA_ENV_ARGS=()
INSULA_SKIP_PREFLIGHT=1
insula --phase {phase!r} --print-argv -- {' '.join(repr(arg) for arg in command)}
"""
    result = subprocess.run(
        ["bash", "-c", harness],
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _run_fetch_phase(tmp_path: Path, *, pinned: bool) -> subprocess.CompletedProcess[str]:
    bwrap = tmp_path / "fake-bwrap.sh"
    bwrap.write_text(
        "#!/usr/bin/env bash\n"
        "test -s \"$VASO_ESTATE_ROOT/agents/leases/network-fetch.json\"\n",
        encoding="utf-8",
    )
    bwrap.chmod(0o755)
    harness = f"""
set -euo pipefail
source {str(INSULA_SH)!r}
ROOT_PLAN=()
INSULA_DIR_ARGS=()
INSULA_BIND_ARGS=()
INSULA_ENV_ARGS=()
INSULA_SKIP_PREFLIGHT=1
INSULA_BWRAP_BIN={str(bwrap)!r}
VASO_ESTATE_ROOT={str(tmp_path / 'estate')!r}
export VASO_ESTATE_ROOT
ESTATE_ROOT="$VASO_ESTATE_ROOT"
VASO_AGENT=pytest
{("INSULA_FETCH_SHA256_PINNED=1" if pinned else "")}
insula --phase fetch -- bash -lc 'echo fetch'
"""
    return subprocess.run(
        ["bash", "-c", harness],
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
        capture_output=True,
        text=True,
    )


def test_sourceable_insula_renders_legacy_run_sh_bwrap_argv() -> None:
    assert _render_sourceable_argv() == [
        "bwrap",
        "--die-with-parent",
        "--unshare-user",
        "--unshare-ipc",
        "--unshare-uts",
        "--clearenv",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--size",
        "123456",
        "--tmpfs",
        "/tmp",
        "--tmpfs",
        "/run",
        "--ro-bind",
        "/estate/rootfs",
        "/",
        "--dir",
        "/workspace",
        "--dir",
        "/vaso",
        "--dir",
        "/run/vaso",
        "--dir",
        "/run/nvidia-driver",
        "--dir",
        "/run/nvidia-driver/lib",
        "--ro-bind",
        "/estate/rootfs-lines/cu129/rootfs-bundle.json",
        "/run/vaso/rootfs-bundle.json",
        "--bind",
        "/estate/vaso",
        "/vaso",
        "--ro-bind",
        "/estate/sources",
        "/vaso/sources",
        "--bind",
        "/estate/vaso/lines/cu129/cache/bazel/output-base-cache",
        "/vaso/lines/cu129/cache/bazel/output-base-cache",
        "--bind",
        "/estate/vaso/lines/cu129/cache/bazel/disk-cache",
        "/vaso/lines/cu129/cache/bazel/disk-cache",
        "--bind",
        "/estate/vaso/lines/cu129/state/native",
        "/vaso/lines/cu129/state/native",
        "--bind",
        "/estate/vaso/lines/cu129/state/stamps",
        "/vaso/lines/cu129/state/stamps",
        "--ro-bind",
        "/estate/vaso/tools/bin",
        "/vaso/tools/bin",
        "--ro-bind",
        "/estate/opt-vaso",
        "/opt/vaso",
        "--bind",
        "/estate/workspace",
        "/workspace",
        "--bind",
        "/estate/home/kvothe",
        "/home/kvothe",
        "--bind",
        "/checkout/experiments/spack-bazel-graph",
        "/workspace/experiment",
        "--ro-bind",
        "/driver/libcuda.so.1",
        "/run/nvidia-driver/lib/libcuda.so.1",
        "--dev-bind-try",
        "/dev/nvidia0",
        "/dev/nvidia0",
        "--setenv",
        "PATH",
        "/vaso/tools/bin:/usr/local/cuda/bin:/usr/bin:/bin",
        "--setenv",
        "LD_LIBRARY_PATH",
        "/run/nvidia-driver/lib:/usr/local/cuda/lib64",
        "--setenv",
        "HOME",
        "/home/kvothe",
        "--setenv",
        "TMPDIR",
        "/vaso/tmp/insula-123",
        "--setenv",
        "USER",
        "kvothe",
        "--setenv",
        "LOGNAME",
        "kvothe",
        "--setenv",
        "TERM",
        "xterm",
        "--setenv",
        "XDG_CACHE_HOME",
        "/home/kvothe/.cache",
        "--setenv",
        "XDG_CONFIG_HOME",
        "/home/kvothe/.config",
        "--setenv",
        "VASO_HOME",
        "/vaso",
        "--setenv",
        "VASO_BAZEL_OB",
        "/vaso/lines/cu129/cache/bazel/output-base-cache",
        "--setenv",
        "VASO_CUDA_HOME",
        "/usr/local/cuda",
        "--setenv",
        "VASO_CUDA_LINE",
        "cu129",
        "--setenv",
        "CUDA_VISIBLE_DEVICES",
        "2,3",
        "--setenv",
        "VASO_GPU_SET",
        "2,3",
        "--setenv",
        "VASO_HOST_CPU_JOBS",
        "12",
        "--setenv",
        "VASO_JAXLIB_MAX_JOBS",
        "9",
        "--setenv",
        "VASO_IN_INSULA",
        "1",
        "--setenv",
        "VASO_NATIVE_STATE",
        "/vaso/lines/cu129/state/native",
        "--setenv",
        "VASO_NATIVE_STAMPS",
        "/vaso/lines/cu129/state/stamps",
        "--setenv",
        "VASO_ROOTFS_BUNDLE_MANIFEST",
        "/run/vaso/rootfs-bundle.json",
        "--setenv",
        "VASO_WORKSPACE_ROOT",
        "/workspace",
        "--setenv",
        "VASO_PREFIX",
        "/opt/vaso",
        "--chdir",
        "/workspace/experiment",
        "--",
        "bash",
        "-lc",
        "echo hi",
    ]


def test_runtime_scripts_use_shared_insula_entrypoint() -> None:
    for script in RUNTIME_SCRIPTS:
        text = script.read_text(encoding="utf-8")
        assert "scripts/insula/insula.sh" in text, script
        assert not re.search(r"(?m)^\\s*bwrap\\b", text), script


def test_non_fetch_bazel_phase_injects_repository_disable_download() -> None:
    argv = _render_phase_argv(
        "build",
        "/vaso/tools/bin/bazel",
        "--output_base=/vaso/ob",
        "--batch",
        "build",
        "--distdir=/vaso/sources/native/distdir",
        "--repository_cache=/vaso/cache/bazel/repository-cache",
        "//:target",
    )
    command = argv[argv.index("--") + 1 :]
    assert command.count("--repository_disable_download") == 1
    assert command.index("--repository_disable_download") < command.index("--repository_cache=/vaso/cache/bazel/repository-cache")


def test_fetch_phase_does_not_inject_repository_disable_download() -> None:
    argv = _render_phase_argv(
        "fetch",
        "/vaso/tools/bin/bazel",
        "--output_base=/vaso/ob",
        "--batch",
        "fetch",
        "--repository_cache=/vaso/cache/bazel/repository-cache",
        "--repo=@pinned",
    )
    assert "--repository_disable_download" not in argv[argv.index("--") + 1 :]


def test_fetch_phase_refuses_without_sha256_pin_proof(tmp_path: Path) -> None:
    result = _run_fetch_phase(tmp_path, pinned=False)

    assert result.returncode == 2
    assert "sha256-pinned" in result.stderr


def test_fetch_phase_acquires_network_fetch_lease(tmp_path: Path) -> None:
    result = _run_fetch_phase(tmp_path, pinned=True)

    assert result.returncode == 0, result.stderr
    state = json.loads((tmp_path / "estate" / "agents" / "leases" / "network-fetch.json").read_text(encoding="utf-8"))
    assert state["resource"] == "network-fetch"
    assert state["leases"] == []
