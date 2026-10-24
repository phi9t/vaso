from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
INSULA_SH = ROOT / "foundry" / "insula.sh"
DRIVER_BIND_DESTINATIONS = {
    "/run/nvidia-driver/lib/libcuda.so.1",
    "/run/nvidia-driver/lib/libcuda.so.580.105.08",
    "/run/nvidia-driver/lib/libnvidia-ml.so.1",
    "/run/nvidia-driver/lib/libnvidia-ml.so.580.105.08",
    "/run/nvidia-driver/lib/libnvidia-ptxjitcompiler.so.1",
    "/run/nvidia-driver/lib/libnvidia-ptxjitcompiler.so.580.105.08",
    "/run/nvidia-driver/lib/libnvidia-nvvm.so.4",
    "/run/nvidia-driver/lib/libnvidia-nvvm.so.580.105.08",
    "/run/nvidia-driver/lib/libnvidia-gpucomp.so.580.105.08",
}
RUNTIME_SCRIPTS = [
    ROOT / "foundry" / "run.sh",
    ROOT / "foundry" / "verify_languages.sh",
    ROOT / "foundry" / "bootstrap_insula.sh",
    ROOT / "foundry" / "seal_insula.sh",
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
EXP_DIR=/checkout/foundry
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


def _make_fake_driver_libs(driver: Path) -> None:
    driver.mkdir(parents=True)
    for stem in ("libcuda", "libnvidia-ml", "libnvidia-ptxjitcompiler", "libnvidia-nvvm"):
        real = driver / f"{stem}.so.580.105.08"
        real.write_bytes(b"fake driver dso\n")
        soname = "libnvidia-nvvm.so.4" if stem == "libnvidia-nvvm" else f"{stem}.so.1"
        (driver / soname).symlink_to(real.name)
    (driver / "libnvidia-gpucomp.so.580.105.08").write_bytes(b"fake driver compiler dso\n")


def _render_driver_binds(driver: Path) -> list[str]:
    harness = f"""
set -euo pipefail
source {str(INSULA_SH)!r}
INSULA_DRIVER_LIB_DIRS=({str(driver)!r})
CUDA_DRIVER_BINDS=()
insula_append_cuda_driver_binds CUDA_DRIVER_BINDS
insula_print_argv_json "${{CUDA_DRIVER_BINDS[@]}}"
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


def _make_fake_release_estate(tmp_path: Path, *, line: str = "cu129") -> tuple[Path, Path]:
    estate = tmp_path / "estate"
    rootfs = estate / "rootfs-lines" / line / "rootfs"
    (rootfs / "usr" / "bin").mkdir(parents=True)
    (rootfs / "bin").mkdir(parents=True)
    (rootfs / "usr" / "bin" / "python3").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    (rootfs / "bin" / "bash").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    manifest = estate / "rootfs-lines" / line / "rootfs-bundle.json"
    manifest.write_text(json.dumps({"schema_version": 2, "line": line}) + "\n", encoding="utf-8")
    (estate / "agents" / "leases").mkdir(parents=True)

    release = tmp_path / "release"
    wheels = release / "wheels"
    deps = release / "wheelhouse"
    wheels.mkdir(parents=True)
    deps.mkdir(parents=True)
    wheel_paths = [
        wheels / "torch-2.14.0+vaso.cu129.gabc12345-py3-none-any.whl",
        deps / "numpy-2.3.0-py3-none-any.whl",
    ]
    for path in wheel_paths:
        path.write_bytes(path.name.encode("utf-8"))
    requirement_lines = []
    sha_lines = []
    for path in wheel_paths:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rel = path.relative_to(release).as_posix()
        requirement_lines.append(f"./{rel} --hash=sha256:{digest}")
        sha_lines.append(f"{digest}  {rel}")
    requirements = release / "requirements.txt"
    requirements.write_text("\n".join(requirement_lines) + "\n", encoding="utf-8")
    sha_lines.append(f"{hashlib.sha256(requirements.read_bytes()).hexdigest()}  requirements.txt")
    (release / "SHA256SUMS").write_text("\n".join(sha_lines) + "\n", encoding="utf-8")
    return estate, release


def _write_fake_rootfs_bundle(tmp_path: Path, line: str = "cu129") -> Path:
    source = tmp_path / "bundle-source"
    rootfs = source / "rootfs"
    (rootfs / "usr" / "bin").mkdir(parents=True)
    (rootfs / "bin").mkdir(parents=True)
    (rootfs / "usr" / "bin" / "python3").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    (rootfs / "bin" / "bash").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (source / "rootfs-bundle.json").write_text(json.dumps({"schema_version": 2, "line": line}) + "\n", encoding="utf-8")
    tmp_bundle = tmp_path / f"rootfs-{line}.tar.zst.tmp"
    result = subprocess.run(
        [
            "tar",
            "--use-compress-program=zstd -q",
            "-C",
            str(source),
            "-cf",
            str(tmp_bundle),
            "rootfs",
            "rootfs-bundle.json",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    digest = hashlib.sha256(tmp_bundle.read_bytes()).hexdigest()
    bundle = tmp_path / f"rootfs-{line}-{digest}.tar.zst"
    tmp_bundle.rename(bundle)
    return bundle


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
        "/checkout/foundry",
        "/workspace/experiment",
        "--ro-bind",
        str(ROOT / "insula"),
        "/workspace/insula",
        "--ro-bind",
        str(ROOT / "foundry"),
        "/workspace/foundry",
        "--ro-bind",
        str(ROOT),
        "/workspace/repo",
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
        assert "foundry/insula.sh" in text, script
        assert not re.search(r"(?m)^\\s*bwrap\\b", text), script


def _has_bind(argv: list[str], flag: str, source: str, dest: str) -> bool:
    return any(argv[i : i + 3] == [flag, source, dest] for i in range(len(argv) - 2))


def test_insula_run_mounts_third_party_read_only_beside_the_experiment(tmp_path: Path) -> None:
    estate, _release = _make_fake_release_estate(tmp_path)
    env = {**os.environ, "VASO_ESTATE_ROOT": str(estate), "VASO_REQUIRED_GIB": "0", "PYTHONDONTWRITEBYTECODE": "1"}

    result = subprocess.run(
        [str(INSULA_SH), "run", "--line", "cu129", "--print-argv", "--", "true"],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    argv = json.loads(result.stdout)
    assert _has_bind(argv, "--ro-bind-try", str(ROOT / "third_party"), "/workspace/third_party")


def test_insula_release_run_mounts_third_party_read_only_beside_the_experiment(tmp_path: Path) -> None:
    estate, release = _make_fake_release_estate(tmp_path)

    result = subprocess.run(
        [
            str(INSULA_SH),
            "run",
            "--line",
            "cu129",
            "--release",
            str(release),
            "--print-argv",
            "--",
            "true",
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "VASO_ESTATE_ROOT": str(estate),
            "INSULA_RELEASE_RUN_ID": "test-third-party",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    argv = json.loads(result.stdout)
    assert _has_bind(argv, "--ro-bind-try", str(ROOT / "third_party"), "/workspace/third_party")


def test_direct_insula_bazel_entrypoint_stops_on_placement_failure(tmp_path: Path) -> None:
    estate, _release = _make_fake_release_estate(tmp_path)
    env = {**os.environ, "VASO_ESTATE_ROOT": str(estate), "VASO_REQUIRED_GIB": "1000000"}
    env.pop("VASO_BAZEL_OB", None)
    result = subprocess.run(
        [str(INSULA_SH), "run", "--line", "cu129", "--print-argv", "--", "true"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "bytes free, need" in result.stderr
    assert not result.stdout.strip()


def test_direct_insula_bazel_entrypoint_mounts_output_base_at_fixed_path(tmp_path: Path) -> None:
    estate, _release = _make_fake_release_estate(tmp_path)
    env = {**os.environ, "VASO_ESTATE_ROOT": str(estate), "VASO_REQUIRED_GIB": "0", "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("VASO_BAZEL_OB", None)

    for cache in (None, tmp_path / "other-volume-cache"):
        env.pop("VASO_BAZEL_DISK_CACHE", None)
        if cache is not None:
            env["VASO_BAZEL_DISK_CACHE"] = str(cache)
        result = subprocess.run(
            [
                str(INSULA_SH),
                "run",
                "--line",
                "cu129",
                "--print-argv",
                "--",
                "/vaso/tools/bin/bazel",
                "--output_base=/vaso-bazel-ob",
                "--batch",
                "build",
                "--repository_cache=/vaso/cache/bazel/repository-cache",
                "//:target",
            ],
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr
        argv = json.loads(result.stdout)
        assert "--bind" in argv
        assert str(estate / "vaso" / "lines" / "cu129" / "cache" / "bazel" / "output-base") in argv
        assert any(
            argv[index : index + 3]
            == [
                "--bind",
                str(estate / "vaso" / "lines" / "cu129" / "cache" / "bazel" / "output-base"),
                "/vaso-bazel-ob",
            ]
            for index, arg in enumerate(argv[:-2])
            if arg == "--bind"
        )
        env_pairs = {
            argv[index + 1]: argv[index + 2]
            for index, arg in enumerate(argv[:-2])
            if arg == "--setenv"
        }
        assert env_pairs["VASO_BAZEL_OB"] == "/vaso-bazel-ob"
        command = argv[argv.index("--") + 1 :]
        assert "--output_base=/vaso-bazel-ob" in command
        cache_source = cache or estate / "vaso/lines/cu129/cache/bazel/disk-cache"
        assert any(argv[index:index + 3] == ["--bind", str(cache_source),
                   "/vaso/lines/cu129/cache/bazel/disk-cache"]
                   for index in range(len(argv) - 2))


def test_insula_run_forwards_held_lease_ids_for_nested_release_runs(tmp_path: Path) -> None:
    estate, _release = _make_fake_release_estate(tmp_path)

    result = subprocess.run(
        [
            str(INSULA_SH),
            "run",
            "--line",
            "cu129",
            "--print-argv",
            "--",
            "bash",
            "-lc",
            "echo ok",
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "VASO_ESTATE_ROOT": str(estate),
            "VASO_REQUIRED_GIB": "0",
            "VASO_LEASES_HELD": "gpu:lease-outer,host-cpu:lease-cpu",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    argv = json.loads(result.stdout)
    env_pairs = {
        argv[index + 1]: argv[index + 2]
        for index, arg in enumerate(argv[:-2])
        if arg == "--setenv"
    }
    assert env_pairs["VASO_LEASES_HELD"] == "gpu:lease-outer,host-cpu:lease-cpu"


def test_insula_binds_cuda_runtime_compiler_driver_closure(tmp_path: Path) -> None:
    driver = tmp_path / "driver"
    _make_fake_driver_libs(driver)

    argv = _render_driver_binds(driver)

    destinations = {argv[index + 2] for index, arg in enumerate(argv[:-2]) if arg == "--ro-bind"}
    assert DRIVER_BIND_DESTINATIONS <= destinations


def test_non_fetch_bazel_phase_injects_repository_disable_download() -> None:
    for executable in ("/vaso/tools/bin/bazel", "/opt/vaso/bin/bazel-real"):
        argv = _render_phase_argv(
            "build",
            executable,
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


def test_release_run_refuses_sha256sum_mismatch(tmp_path: Path) -> None:
    estate, release = _make_fake_release_estate(tmp_path)
    (release / "wheels" / "torch-2.14.0+vaso.cu129.gabc12345-py3-none-any.whl").write_bytes(b"tampered")

    result = subprocess.run(
        [
            str(INSULA_SH),
            "run",
            "--line",
            "cu129",
            "--release",
            str(release),
            "--print-argv",
            "--",
            "python3",
            "-c",
            "print('unreachable')",
        ],
        cwd=ROOT,
        env={**os.environ, "VASO_ESTATE_ROOT": str(estate), "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "SHA256SUMS" in result.stderr
    assert "FAILED" in result.stderr


def test_release_run_renders_consumer_bindings_without_build_prefixes(tmp_path: Path) -> None:
    estate, release = _make_fake_release_estate(tmp_path)

    result = subprocess.run(
        [
            str(INSULA_SH),
            "run",
            "--line",
            "cu129",
            "--release",
            str(release),
            "--gpus",
            "2",
            "--print-argv",
            "--",
            "python3",
            "-c",
            "print('ok')",
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "VASO_ESTATE_ROOT": str(estate),
            "INSULA_RELEASE_RUN_ID": "test-release-run",
            "PYTHONPATH": "/vaso/lines/cu129/cache/bazel/output-base/execroot/_main",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    argv = json.loads(result.stdout)
    assert "--ro-bind" in argv
    assert "--unshare-net" in argv
    assert str(release.resolve()) in argv
    assert "/run/vaso/release" in argv
    assert "--bind" in argv
    assert str(estate / "run" / "test-release-run") in argv
    assert "/run/vaso/run" in argv
    assert "/workspace/experiment" in argv
    command = argv[argv.index("--") + 1 :]
    assert command[:1] == ["/run/vaso/run/release-entrypoint.sh"]
    assert command[-3:] == ["python3", "-c", "print('ok')"]
    env_pairs = {
        argv[index + 1]: argv[index + 2]
        for index, arg in enumerate(argv[:-2])
        if arg == "--setenv"
    }
    setenv_keys = set(env_pairs)
    assert "PYTHONPATH" not in setenv_keys
    assert env_pairs["CUDA_HOME"] == "/usr/local/cuda"
    assert env_pairs["CUDA_PATH"] == "/usr/local/cuda"
    assert env_pairs["VASO_CUDA_HOME"] == "/usr/local/cuda"
    assert "/usr/local/cuda/targets/x86_64-linux/lib" in env_pairs["LD_LIBRARY_PATH"].split(":")
    assert env_pairs["TRITON_PTXAS_PATH"] == "/usr/local/cuda/bin/ptxas"
    assert env_pairs["TRITON_PTXAS_BLACKWELL_PATH"] == "/usr/local/cuda/bin/ptxas"
    assert env_pairs["TRITON_CUOBJDUMP_PATH"] == "/usr/local/cuda/bin/cuobjdump"
    assert env_pairs["TRITON_NVDISASM_PATH"] == "/usr/local/cuda/bin/nvdisasm"
    assert env_pairs["TRITON_CUDACRT_PATH"] == "/usr/local/cuda/include"
    assert env_pairs["TRITON_CUDART_PATH"] == "/usr/local/cuda/include"
    assert env_pairs["TRITON_LIBCUDA_PATH"] == "/run/nvidia-driver/lib"
    assert env_pairs["TRITON_LIBDEVICE_PATH"] == "/usr/local/cuda/nvvm/libdevice/libdevice.10.bc"
    assert env_pairs["TRITON_CUPTI_LIB_PATH"] == "/usr/local/cuda/targets/x86_64-linux/lib"
    assert env_pairs["TRITON_CUPTI_LIB_BLACKWELL_PATH"] == "/usr/local/cuda/targets/x86_64-linux/lib"
    assert all("output-base" not in item for item in argv)
    assert all("/cache/bazel" not in item for item in argv)
    for index, arg in enumerate(argv[:-2]):
        if arg in {"--bind", "--ro-bind"}:
            assert argv[index + 2] != "/vaso"


def test_release_run_uses_bundled_python_without_build_prefix(tmp_path: Path) -> None:
    estate, release = _make_fake_release_estate(tmp_path)
    bundled = estate / "rootfs-lines/cu129/rootfs/opt/vaso/python/bin/python3"
    old_build = estate / "vaso/lines/cu129/cache/bazel/output-base/external/+python_native+python_313_native/prefix/bin/python3"
    for python in (bundled, old_build):
        python.parent.mkdir(parents=True)
        python.write_text("#!/bin/sh\n")
        python.chmod(0o755)
    result = subprocess.run([
        str(INSULA_SH), "--line", "cu129", "--release", str(release),
        "--print-argv", "--", "python3", "-V",
    ], env={**os.environ, "VASO_ESTATE_ROOT": str(estate), "INSULA_RELEASE_RUN_ID": "bundled-python"},
        cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    argv = json.loads(result.stdout)
    assert not any("/cache/bazel/" in arg for arg in argv)
    pairs = {argv[i + 1]: argv[i + 2] for i, token in enumerate(argv) if token == "--setenv"}
    assert "/opt/vaso/python/bin" in pairs["PATH"].split(":")
    assert "/opt/vaso/python/lib" in pairs["LD_LIBRARY_PATH"].split(":")


def test_release_run_preflights_release_layout_with_bound_driver_dir(tmp_path: Path) -> None:
    estate, release = _make_fake_release_estate(tmp_path)
    (estate / "agents" / "leases").rmdir()
    log = tmp_path / "bwrap-log.jsonl"
    doctor_log = tmp_path / "doctor-args.json"
    bwrap = tmp_path / "fake-bwrap.sh"
    bwrap.write_text(
        "#!/usr/bin/env bash\n"
        "python3 - \"$INSULA_BWRAP_LOG\" \"$@\" <<'PY'\n"
        "import json\n"
        "import sys\n"
        "with open(sys.argv[1], 'a', encoding='utf-8') as fh:\n"
        "    fh.write(json.dumps(sys.argv[2:]) + '\\n')\n"
        "PY\n",
        encoding="utf-8",
    )
    bwrap.chmod(0o755)
    doctor = tmp_path / "doctor.sh"
    doctor.write_text(
        "#!/usr/bin/env bash\n"
        "python3 - \"$DOCTOR_LOG\" \"$@\" <<'PY'\n"
        "import json\n"
        "import sys\n"
        "open(sys.argv[1], 'w', encoding='utf-8').write(json.dumps(sys.argv[2:]))\n"
        "PY\n",
        encoding="utf-8",
    )
    doctor.chmod(0o755)

    result = subprocess.run(
        [
            str(INSULA_SH),
            "run",
            "--line",
            "cu129",
            "--release",
            str(release),
            "--gpus",
            "1",
            "--",
            "python3",
            "-c",
            "print('ok')",
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "VASO_ESTATE_ROOT": str(estate),
            "INSULA_BWRAP_BIN": str(bwrap),
            "INSULA_BWRAP_LOG": str(log),
            "INSULA_DOCTOR": str(doctor),
            "DOCTOR_LOG": str(doctor_log),
            "INSULA_RELEASE_RUN_ID": "preflight-run",
            "VASO_IN_INSULA": "1",
            "VASO_GPU_COUNT": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    doctor_args = json.loads(doctor_log.read_text(encoding="utf-8"))
    assert (estate / "agents" / "leases").is_dir()
    assert "--layout" in doctor_args
    assert doctor_args[doctor_args.index("--layout") + 1] == "release"
    assert "--driver-lib-dir" in doctor_args
    assert doctor_args[doctor_args.index("--driver-lib-dir") + 1] == "/run/nvidia-driver/lib"
    assert "--require-driver" in doctor_args
    assert "--no-require-driver" not in doctor_args


def test_release_run_accepts_rootfs_bundle(tmp_path: Path) -> None:
    estate, release = _make_fake_release_estate(tmp_path)
    rootfs_line = estate / "rootfs-lines" / "cu129"
    for path in sorted(rootfs_line.rglob("*"), reverse=True):
        if path.is_file() or path.is_symlink():
            path.unlink()
        else:
            path.rmdir()
    rootfs_line.rmdir()
    bundle = _write_fake_rootfs_bundle(tmp_path)

    result = subprocess.run(
        [
            str(INSULA_SH),
            "run",
            "--line",
            "cu129",
            "--release",
            str(release),
            "--bundle",
            str(bundle),
            "--print-argv",
            "--",
            "python3",
            "-c",
            "print('ok')",
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "VASO_ESTATE_ROOT": str(estate),
            "INSULA_RELEASE_RUN_ID": "bundle-run",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    argv = json.loads(result.stdout)
    unpacked = estate / "rootfs-bundles" / "cu129"
    assert (unpacked / "rootfs" / "usr" / "bin" / "python3").is_file()
    assert str(unpacked / "rootfs-bundle.json") in argv
    assert str(estate / "rootfs-lines" / "cu129") not in argv


def test_release_run_acquires_gpu_lease_for_final_command(tmp_path: Path) -> None:
    estate, release = _make_fake_release_estate(tmp_path)
    log = tmp_path / "bwrap-log.jsonl"
    bwrap = tmp_path / "fake-bwrap.sh"
    bwrap.write_text(
        "#!/usr/bin/env bash\n"
        "python3 - \"$INSULA_BWRAP_LOG\" \"$@\" <<'PY'\n"
        "import json\n"
        "import sys\n"
        "with open(sys.argv[1], 'a', encoding='utf-8') as fh:\n"
        "    fh.write(json.dumps(sys.argv[2:]) + '\\n')\n"
        "PY\n",
        encoding="utf-8",
    )
    bwrap.chmod(0o755)

    result = subprocess.run(
        [
            str(INSULA_SH),
            "run",
            "--line",
            "cu129",
            "--release",
            str(release),
            "--gpus",
            "2",
            "--",
            "python3",
            "-c",
            "print('ok')",
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "VASO_ESTATE_ROOT": str(estate),
            "INSULA_BWRAP_BIN": str(bwrap),
            "INSULA_BWRAP_LOG": str(log),
            "INSULA_RELEASE_RUN_ID": "leased-run",
            "INSULA_SKIP_PREFLIGHT": "1",
            "VASO_GPU_COUNT": "4",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    invocations = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(invocations) == 3
    final_argv = invocations[-1]
    env_pairs = {
        final_argv[index + 1]: final_argv[index + 2]
        for index, arg in enumerate(final_argv[:-2])
        if arg == "--setenv"
    }
    assert env_pairs["CUDA_VISIBLE_DEVICES"] == "0,1"
    assert env_pairs["VASO_GPU_SET"] == "0,1"
    assert env_pairs["VASO_RELEASE_DIR"] == "/run/vaso/release"
    assert env_pairs["VIRTUAL_ENV"] == "/run/vaso/run/venv"
    state = json.loads((estate / "agents" / "leases" / "gpu.json").read_text(encoding="utf-8"))
    assert state["leases"] == []


def test_release_run_reuses_inherited_gpu_lease(tmp_path: Path) -> None:
    estate, release = _make_fake_release_estate(tmp_path)
    lease = subprocess.run(
        [
            "python3",
            str(ROOT / "insula" / "execution" / "lease.py"),
            "acquire",
            "--resource",
            "gpu",
            "--amount",
            "2",
            "--holder",
            "pytest",
            "--pid",
            str(os.getpid()),
        ],
        cwd=ROOT,
        env={**os.environ, "VASO_ESTATE_ROOT": str(estate), "VASO_GPU_COUNT": "4"},
        check=True,
        capture_output=True,
        text=True,
    )
    lease_doc = json.loads(lease.stdout)
    log = tmp_path / "bwrap-log.jsonl"
    bwrap = tmp_path / "fake-bwrap.sh"
    bwrap.write_text(
        "#!/usr/bin/env bash\n"
        "python3 - \"$INSULA_BWRAP_LOG\" \"$@\" <<'PY'\n"
        "import json\n"
        "import sys\n"
        "with open(sys.argv[1], 'a', encoding='utf-8') as fh:\n"
        "    fh.write(json.dumps(sys.argv[2:]) + '\\n')\n"
        "PY\n",
        encoding="utf-8",
    )
    bwrap.chmod(0o755)

    result = subprocess.run(
        [
            str(INSULA_SH),
            "run",
            "--line",
            "cu129",
            "--release",
            str(release),
            "--gpus",
            "2",
            "--",
            "python3",
            "-c",
            "print('ok')",
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "VASO_ESTATE_ROOT": str(estate),
            "INSULA_BWRAP_BIN": str(bwrap),
            "INSULA_BWRAP_LOG": str(log),
            "INSULA_RELEASE_RUN_ID": "inherited-run",
            "INSULA_SKIP_PREFLIGHT": "1",
            "VASO_GPU_COUNT": "4",
            "VASO_LEASES_HELD": f"gpu:{lease_doc['id']}",
            "CUDA_VISIBLE_DEVICES": lease_doc["env"]["CUDA_VISIBLE_DEVICES"],
            "VASO_GPU_SET": lease_doc["env"]["VASO_GPU_SET"],
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    invocations = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(invocations) == 3
    final_argv = invocations[-1]
    env_pairs = {
        final_argv[index + 1]: final_argv[index + 2]
        for index, arg in enumerate(final_argv[:-2])
        if arg == "--setenv"
    }
    assert env_pairs["CUDA_VISIBLE_DEVICES"] == lease_doc["env"]["CUDA_VISIBLE_DEVICES"]
    assert env_pairs["VASO_GPU_SET"] == lease_doc["env"]["VASO_GPU_SET"]
    state = json.loads((estate / "agents" / "leases" / "gpu.json").read_text(encoding="utf-8"))
    assert [item["id"] for item in state["leases"]] == [lease_doc["id"]]


def test_release_run_rejects_inherited_gpu_lease_that_is_too_small(tmp_path: Path) -> None:
    estate, release = _make_fake_release_estate(tmp_path)
    lease = subprocess.run(
        [
            "python3",
            str(ROOT / "insula" / "execution" / "lease.py"),
            "acquire",
            "--resource",
            "gpu",
            "--amount",
            "1",
            "--holder",
            "pytest",
            "--pid",
            str(os.getpid()),
        ],
        cwd=ROOT,
        env={**os.environ, "VASO_ESTATE_ROOT": str(estate), "VASO_GPU_COUNT": "4"},
        check=True,
        capture_output=True,
        text=True,
    )
    lease_doc = json.loads(lease.stdout)
    log = tmp_path / "bwrap-log.jsonl"
    bwrap = tmp_path / "fake-bwrap.sh"
    bwrap.write_text(
        "#!/usr/bin/env bash\n"
        "python3 - \"$INSULA_BWRAP_LOG\" \"$@\" <<'PY'\n"
        "import json\n"
        "import sys\n"
        "with open(sys.argv[1], 'a', encoding='utf-8') as fh:\n"
        "    fh.write(json.dumps(sys.argv[2:]) + '\\n')\n"
        "PY\n",
        encoding="utf-8",
    )
    bwrap.chmod(0o755)

    result = subprocess.run(
        [
            str(INSULA_SH),
            "run",
            "--line",
            "cu129",
            "--release",
            str(release),
            "--gpus",
            "2",
            "--",
            "python3",
            "-c",
            "print('ok')",
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "VASO_ESTATE_ROOT": str(estate),
            "INSULA_BWRAP_BIN": str(bwrap),
            "INSULA_BWRAP_LOG": str(log),
            "INSULA_RELEASE_RUN_ID": "small-inherited-run",
            "INSULA_SKIP_PREFLIGHT": "1",
            "VASO_GPU_COUNT": "4",
            "VASO_LEASES_HELD": f"gpu:{lease_doc['id']}",
            "CUDA_VISIBLE_DEVICES": lease_doc["env"]["CUDA_VISIBLE_DEVICES"],
            "VASO_GPU_SET": lease_doc["env"]["VASO_GPU_SET"],
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 75
    assert "inherited GPU lease is too small" in result.stderr
    invocations = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(invocations) == 2
    state = json.loads((estate / "agents" / "leases" / "gpu.json").read_text(encoding="utf-8"))
    assert [item["id"] for item in state["leases"]] == [lease_doc["id"]]


_ROOT_DERIVING_SCRIPTS = (
    "foundry/bootstrap_insula.sh",
    "foundry/seal_insula.sh",
    "foundry/verify_languages.sh",
    "insula/rootfs/bundle.sh",
    "foundry/examples/consumers/run.sh",
)


@pytest.mark.parametrize("relative", _ROOT_DERIVING_SCRIPTS)
def test_entry_scripts_derive_the_repository_root(relative: str) -> None:
    script = ROOT / relative
    header = [
        line
        for line in script.read_text(encoding="utf-8").splitlines()
        if re.match(r"^(SCRIPT_DIR|EXP_DIR|EXPERIMENT_ROOT|REPO_ROOT)=", line)
    ]
    assert any(line.startswith("REPO_ROOT=") for line in header), relative

    derivation = "\n".join(header).replace("${BASH_SOURCE[0]}", '"$1"')
    result = subprocess.run(
        ["bash", "-c", f'{derivation}\nprintf "%s" "$REPO_ROOT"', "bash", str(script)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert Path(result.stdout) == ROOT, relative
