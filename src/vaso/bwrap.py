from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from vaso.config import VasoConfig


@dataclass(frozen=True)
class MountSpec:
    name: str
    operation: str
    host_path: Path
    sandbox_path: str
    mode: str


@dataclass(frozen=True)
class BwrapPlan:
    run_id: str
    target_repo: str
    cwd: str
    uid: int
    gid: int
    rootfs: Path
    mounts: list[MountSpec]
    environment: dict[str, str]
    command_argv: list[str]
    network_mode: str = "online"
    scratch_mode: str = "bind"
    bazel_sandbox_strategy: str = "processwrapper-sandboxed"
    worker_sandboxing: bool = False


def _mount_sort_key(mount: MountSpec) -> tuple[int, str]:
    if mount.sandbox_path.startswith("/workspace/"):
        group = 0
    elif mount.sandbox_path.startswith("/vaso/"):
        group = 1
    elif mount.sandbox_path == "/opt/vaso":
        group = 2
    else:
        group = 3
    return (group, mount.sandbox_path)


def build_bwrap_argv(plan: BwrapPlan) -> list[str]:
    argv = [
        "bwrap",
        "--die-with-parent",
        "--unshare-user",
        "--uid",
        str(plan.uid),
        "--gid",
        str(plan.gid),
        "--unshare-ipc",
        "--unshare-pid",
        "--unshare-uts",
        "--unshare-cgroup-try",
        "--as-pid-1",
    ]
    if plan.network_mode == "offline":
        argv.append("--unshare-net")
    argv.extend(
        [
            "--clearenv",
            "--ro-bind",
            str(plan.rootfs),
            "/",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--ro-bind-try",
            "/sys",
            "/sys",
            "--tmpfs",
            "/tmp",
            "--tmpfs",
            "/run",
            "--dir",
            "/run/vaso",
            "--dir",
            "/run/nvidia-driver",
        ]
    )
    for mount in sorted(plan.mounts, key=_mount_sort_key):
        op = "--ro-bind" if mount.mode == "ro" else "--bind"
        argv.extend([op, str(mount.host_path), mount.sandbox_path])
    for key in sorted(plan.environment):
        argv.extend(["--setenv", key, plan.environment[key]])
    argv.extend(["--chdir", plan.cwd, "--", *plan.command_argv])
    return argv


def argv_sha256(argv: list[str]) -> str:
    payload = json.dumps(argv, ensure_ascii=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def plan_to_json_dict(plan: BwrapPlan) -> dict[str, object]:
    argv = build_bwrap_argv(plan)
    return {
        "schema_version": 1,
        "run_id": plan.run_id,
        "target_repo": plan.target_repo,
        "cwd": plan.cwd,
        "network_mode": plan.network_mode,
        "uid": plan.uid,
        "gid": plan.gid,
        "identity_mode": "host-user",
        "rootfs": {
            "host_path": str(plan.rootfs),
            "sandbox_path": "/",
            "mode": "ro",
        },
        "mounts": [
            {
                "name": mount.name,
                "operation": mount.operation,
                "host_path": str(mount.host_path),
                "sandbox_path": mount.sandbox_path,
                "mode": mount.mode,
            }
            for mount in sorted(plan.mounts, key=_mount_sort_key)
        ],
        "scratch": {"sandbox_path": "/vaso/tmp", "mode": plan.scratch_mode},
        "bazel": {
            "sandbox_strategy": plan.bazel_sandbox_strategy,
            "worker_sandboxing": plan.worker_sandboxing,
            "output_base_mode": "per-repo",
        },
        "environment": dict(sorted(plan.environment.items())),
        "command_argv": plan.command_argv,
        "bwrap_argv_sha256": argv_sha256(argv),
    }


def validation_plan(config: VasoConfig, run_id: str, tier: str) -> BwrapPlan:
    repo = config.repos["vaso"]
    return BwrapPlan(
        run_id=run_id,
        target_repo=repo.name,
        cwd=repo.sandbox,
        uid=Path(config.repo_root).stat().st_uid,
        gid=Path(config.repo_root).stat().st_gid,
        rootfs=config.state_root / "rootfs",
        mounts=[
            MountSpec("workspace:vaso", "bind", repo.host, repo.sandbox, repo.mode),
            MountSpec("vaso:state", "bind", config.state_root / "state", "/vaso/state", "rw"),
            MountSpec("vaso:cache", "bind", config.state_root / "cache", "/vaso/cache", "rw"),
            MountSpec("vaso:runs", "bind", config.state_root / "runs", "/vaso/runs", "rw"),
            MountSpec("vaso:traces", "bind", config.state_root / "traces", "/vaso/traces", "rw"),
            MountSpec("vaso:tmp", "bind", config.state_root / "tmp", "/vaso/tmp", "rw"),
        ],
        environment={"HOME": "/home/kvothe", "USER": "kvothe", "VASO_VALIDATION_TIER": tier},
        command_argv=["/bin/true"],
    )
