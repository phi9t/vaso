# Vaso Rootfs Validation Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first executable Vaso slice: a Python CLI that creates deterministic bwrap plans, validates the host/rootfs projection, and runs Tier 0 and Tier 1 fixture validation.

**Architecture:** Implement a small Python package under `src/vaso/` with focused modules for configuration, plan construction, host layout, bwrap probing, validation tiers, and CLI dispatch. Use pytest for unit tests and fixture tests; keep actual bwrap execution behind one boundary so most behavior is testable without entering a sandbox.

**Tech Stack:** Python 3.11+, pytest, standard-library `argparse`, `dataclasses`, `json`, `hashlib`, `subprocess`, `pathlib`, and Bubblewrap as an external runtime.

**Spec:** `docs/rootfs-container-infra-design.md`

## Global Constraints

- Runtime insulation is a `bwrap` rootfs, not Docker.
- Sandbox repo paths are always `/workspace/<repo-name>`.
- The user home inside the rootfs is `/home/kvothe`.
- Default runtime identity maps to the host uid/gid and names that identity `kvothe`.
- `/opt/vaso` is read-only runtime/toolchain material.
- Writable Vaso state is under `/vaso/{state,cache,runs,traces,tmp}`.
- Host CUDA driver projection uses `/run/nvidia-driver`, but GPU validation is not part of this first slice.
- Bazel output bases and caches must be sandbox-relative under `/vaso/cache/bazel/<repo-name>/...`.
- Astral GPU wheels are named explicit uv indexes, not Python's global default index.
- Every validation run writes a run directory with `bwrap-plan.json`, `stdout.log`, `stderr.log`, `result.json`, and validation report JSON.

---

## File Structure

- Create `pyproject.toml`: Python package metadata, console script, pytest config.
- Create `src/vaso/__init__.py`: package version.
- Create `src/vaso/cli.py`: `vaso` command dispatcher.
- Create `src/vaso/config.py`: typed host/runtime configuration and `repo://self` resolution.
- Create `src/vaso/paths.py`: host layout creation and sandbox path constants.
- Create `src/vaso/bwrap.py`: bwrap binary probing, plan dataclasses, deterministic argv generation and digesting.
- Create `src/vaso/run_records.py`: run-id creation, run directory creation, result writing.
- Create `src/vaso/doctor.py`: operator checks that do not run fixture builds.
- Create `src/vaso/validation.py`: Tier 0 and Tier 1 validation orchestration.
- Create `fixtures/vaso-fixtures/`: tiny Bazel workspace for C++, Rust, Go, and Python smoke targets.
- Create `tests/`: pytest coverage for config, plan determinism, target validation, doctor checks, and validation report shape.
- Create `.gitignore`: ignore `.vaso/`, Python caches, and generated validation outputs.

---

### Task 1: Python Package and CLI Skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `src/vaso/__init__.py`
- Create: `src/vaso/cli.py`
- Create: `tests/test_cli.py`
- Create: `.gitignore`

**Interfaces:**
- Produces: `vaso.cli.main(argv: list[str] | None = None) -> int`
- Produces console script: `vaso = "vaso.cli:main"`

- [ ] **Step 1: Write the failing CLI tests**

Create `tests/test_cli.py`:

```python
from vaso.cli import main


def test_main_help_returns_zero(capsys):
    assert main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "vaso" in out
    assert "doctor" in out
    assert "validate" in out


def test_unknown_command_returns_two(capsys):
    assert main(["missing-command"]) == 2
    err = capsys.readouterr().err
    assert "invalid choice" in err
```

- [ ] **Step 2: Run the failing test**

Run: `python -m pytest tests/test_cli.py -q`

Expected: import failure for `vaso`.

- [ ] **Step 3: Add package metadata and CLI**

Create `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[project]
name = "vaso"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = []

[project.scripts]
vaso = "vaso.cli:main"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

Create `src/vaso/__init__.py`:

```python
__version__ = "0.1.0"
```

Create `src/vaso/cli.py`:

```python
from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vaso")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor", help="check host and rootfs readiness")
    validate = subparsers.add_parser("validate", help="run validation tiers")
    validate.add_argument("--tier", choices=["0", "1"], required=True)
    validate.add_argument("--repo", default="vaso-fixtures")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    return 0
```

Create `.gitignore`:

```gitignore
.vaso/
.pytest_cache/
__pycache__/
*.pyc
```

- [ ] **Step 4: Run the test**

Run: `python -m pytest tests/test_cli.py -q`

Expected: 2 passed.

---

### Task 2: Configuration, Host Layout, and `repo://self`

**Files:**
- Create: `src/vaso/config.py`
- Create: `src/vaso/paths.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Produces: `RepoMount(name: str, host: Path, sandbox: str, mode: Literal["ro", "rw"])`
- Produces: `VasoConfig(host_root: Path, repo_root: Path, state_root: Path, repos: dict[str, RepoMount])`
- Produces: `resolve_repo_self(start: Path, host_root: Path) -> Path`
- Produces: `default_config(repo_root: Path | None = None) -> VasoConfig`
- Produces: `ensure_host_layout(config: VasoConfig) -> None`

- [ ] **Step 1: Write failing tests**

Create `tests/test_config.py`:

```python
from pathlib import Path

import pytest

from vaso.config import default_config, resolve_repo_self
from vaso.paths import ensure_host_layout


def test_default_config_uses_workspace_repo_and_vaso_state(tmp_path):
    repo = tmp_path / "workspace" / "vaso"
    repo.mkdir(parents=True)
    config = default_config(repo)
    assert config.host_root == tmp_path / "workspace"
    assert config.state_root == repo / ".vaso"
    assert config.repos["vaso"].sandbox == "/workspace/vaso"
    assert config.repos["vaso"].mode == "rw"


def test_repo_self_must_stay_under_host_root(tmp_path):
    host_root = tmp_path / "workspace"
    repo = host_root / "vaso"
    repo.mkdir(parents=True)
    assert resolve_repo_self(repo, host_root) == repo.resolve()
    with pytest.raises(ValueError, match="outside host_root"):
        resolve_repo_self(tmp_path / "elsewhere", host_root)


def test_ensure_host_layout_creates_backing_dirs(tmp_path):
    repo = tmp_path / "workspace" / "vaso"
    repo.mkdir(parents=True)
    config = default_config(repo)
    ensure_host_layout(config)
    for rel in ["rootfs", "home/kvothe", "state", "cache", "runs", "traces", "tmp"]:
        assert (config.state_root / rel).is_dir()
```

- [ ] **Step 2: Run failing tests**

Run: `python -m pytest tests/test_config.py -q`

Expected: import failure for `vaso.config`.

- [ ] **Step 3: Implement config and path helpers**

Create `src/vaso/config.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class RepoMount:
    name: str
    host: Path
    sandbox: str
    mode: Literal["ro", "rw"] = "ro"


@dataclass(frozen=True)
class VasoConfig:
    host_root: Path
    repo_root: Path
    state_root: Path
    repos: dict[str, RepoMount]


def resolve_repo_self(start: Path, host_root: Path) -> Path:
    resolved = start.resolve()
    root = host_root.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"repo://self resolved outside host_root: {resolved} not under {root}")
    return resolved


def default_config(repo_root: Path | None = None) -> VasoConfig:
    repo = (repo_root or Path.cwd()).resolve()
    host_root = repo.parent
    self_repo = resolve_repo_self(repo, host_root)
    state_root = self_repo / ".vaso"
    return VasoConfig(
        host_root=host_root,
        repo_root=self_repo,
        state_root=state_root,
        repos={
            "vaso": RepoMount(
                name="vaso",
                host=self_repo,
                sandbox="/workspace/vaso",
                mode="rw",
            )
        },
    )
```

Create `src/vaso/paths.py`:

```python
from __future__ import annotations

from vaso.config import VasoConfig


HOST_LAYOUT_DIRS = (
    "rootfs",
    "home/kvothe",
    "state",
    "cache",
    "runs",
    "traces",
    "tmp",
)


def ensure_host_layout(config: VasoConfig) -> None:
    for rel in HOST_LAYOUT_DIRS:
        (config.state_root / rel).mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_config.py -q`

Expected: 3 passed.

---

### Task 3: bwrap Plan Model and Deterministic argv

**Files:**
- Create: `src/vaso/bwrap.py`
- Create: `tests/test_bwrap_plan.py`

**Interfaces:**
- Produces: `MountSpec`
- Produces: `BwrapPlan`
- Produces: `build_bwrap_argv(plan: BwrapPlan) -> list[str]`
- Produces: `argv_sha256(argv: list[str]) -> str`

- [ ] **Step 1: Write failing tests**

Create `tests/test_bwrap_plan.py`:

```python
from pathlib import Path

from vaso.bwrap import BwrapPlan, MountSpec, argv_sha256, build_bwrap_argv


def test_bwrap_argv_orders_mounts_and_environment(tmp_path):
    plan = BwrapPlan(
        run_id="run-1",
        target_repo="vaso",
        cwd="/workspace/vaso",
        uid=1018,
        gid=1018,
        rootfs=tmp_path / "rootfs",
        mounts=[
            MountSpec("vaso-runs", "bind", tmp_path / "runs", "/vaso/runs", "rw"),
            MountSpec("workspace", "bind", tmp_path / "repo", "/workspace/vaso", "rw"),
        ],
        environment={"ZED": "last", "ALPHA": "first"},
        command_argv=["/bin/true"],
    )
    argv = build_bwrap_argv(plan)
    assert argv[:3] == ["bwrap", "--die-with-parent", "--unshare-user"]
    assert argv.index("--ro-bind") < argv.index("--proc")
    assert argv.index("/workspace/vaso") < argv.index("/vaso/runs")
    alpha_i = argv.index("ALPHA")
    zed_i = argv.index("ZED")
    assert alpha_i < zed_i
    assert argv[-2:] == ["--", "/bin/true"]


def test_argv_digest_changes_with_command(tmp_path):
    base = BwrapPlan(
        run_id="run-1",
        target_repo="vaso",
        cwd="/workspace/vaso",
        uid=1018,
        gid=1018,
        rootfs=tmp_path / "rootfs",
        mounts=[],
        environment={},
        command_argv=["/bin/true"],
    )
    other = BwrapPlan(
        run_id="run-1",
        target_repo="vaso",
        cwd="/workspace/vaso",
        uid=1018,
        gid=1018,
        rootfs=tmp_path / "rootfs",
        mounts=[],
        environment={},
        command_argv=["/bin/false"],
    )
    assert argv_sha256(build_bwrap_argv(base)) != argv_sha256(build_bwrap_argv(other))
```

- [ ] **Step 2: Run failing tests**

Run: `python -m pytest tests/test_bwrap_plan.py -q`

Expected: import failure for `vaso.bwrap`.

- [ ] **Step 3: Implement plan model**

Create `src/vaso/bwrap.py`:

```python
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path


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
    if plan.network_mode == "offline":
        argv.insert(13, "--unshare-net")
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
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_bwrap_plan.py -q`

Expected: 2 passed.

---

### Task 4: Run Records and Plan Serialization

**Files:**
- Create: `src/vaso/run_records.py`
- Create: `tests/test_run_records.py`
- Modify: `src/vaso/bwrap.py`

**Interfaces:**
- Produces: `new_run_id(now: datetime | None = None) -> str`
- Produces: `create_run_dir(config: VasoConfig, run_id: str) -> Path`
- Produces: `write_plan(run_dir: Path, plan: BwrapPlan) -> Path`
- Produces: `BwrapPlan.to_json_dict() -> dict[str, object]`

- [ ] **Step 1: Write failing tests**

Create `tests/test_run_records.py`:

```python
import json
from pathlib import Path

from vaso.bwrap import BwrapPlan
from vaso.config import default_config
from vaso.run_records import create_run_dir, new_run_id, write_plan


def test_run_id_is_filesystem_safe():
    run_id = new_run_id()
    assert "/" not in run_id
    assert ":" not in run_id
    assert run_id.endswith("Z")


def test_write_plan_includes_digest(tmp_path):
    config = default_config(tmp_path / "workspace" / "vaso")
    config.repo_root.mkdir(parents=True, exist_ok=True)
    run_dir = create_run_dir(config, "20260820T000000Z-test")
    plan = BwrapPlan(
        run_id="20260820T000000Z-test",
        target_repo="vaso",
        cwd="/workspace/vaso",
        uid=1018,
        gid=1018,
        rootfs=Path(".vaso/rootfs"),
        mounts=[],
        environment={},
        command_argv=["/bin/true"],
    )
    path = write_plan(run_dir, plan)
    data = json.loads(path.read_text())
    assert data["run_id"] == "20260820T000000Z-test"
    assert len(data["bwrap_argv_sha256"]) == 64
```

- [ ] **Step 2: Run failing tests**

Run: `python -m pytest tests/test_run_records.py -q`

Expected: import failure for `vaso.run_records`.

- [ ] **Step 3: Implement run records**

Modify `src/vaso/bwrap.py` to add:

```python
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
                "name": m.name,
                "operation": m.operation,
                "host_path": str(m.host_path),
                "sandbox_path": m.sandbox_path,
                "mode": m.mode,
            }
            for m in sorted(plan.mounts, key=_mount_sort_key)
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
```

Create `src/vaso/run_records.py`:

```python
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from vaso.bwrap import BwrapPlan, plan_to_json_dict
from vaso.config import VasoConfig


def new_run_id(now: datetime | None = None) -> str:
    value = now or datetime.now(UTC)
    return value.strftime("%Y%m%dT%H%M%SZ")


def create_run_dir(config: VasoConfig, run_id: str) -> Path:
    run_dir = config.state_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def write_plan(run_dir: Path, plan: BwrapPlan) -> Path:
    path = run_dir / "bwrap-plan.json"
    path.write_text(json.dumps(plan_to_json_dict(plan), indent=2, sort_keys=True) + "\n")
    return path
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_run_records.py tests/test_bwrap_plan.py -q`

Expected: 4 passed.

---

### Task 5: Doctor Checks for Host Layout and bwrap Capability

**Files:**
- Create: `src/vaso/doctor.py`
- Create: `tests/test_doctor.py`
- Modify: `src/vaso/cli.py`

**Interfaces:**
- Produces: `DoctorCheck(name: str, ok: bool, detail: str)`
- Produces: `run_doctor(config: VasoConfig) -> list[DoctorCheck]`
- CLI: `vaso doctor` prints JSON and exits nonzero if any check fails.

- [ ] **Step 1: Write failing tests**

Create `tests/test_doctor.py`:

```python
import json

from vaso.cli import main
from vaso.config import default_config
from vaso.doctor import run_doctor
from vaso.paths import ensure_host_layout


def test_doctor_reports_missing_rootfs(tmp_path):
    repo = tmp_path / "workspace" / "vaso"
    repo.mkdir(parents=True)
    config = default_config(repo)
    ensure_host_layout(config)
    checks = run_doctor(config)
    rootfs = [c for c in checks if c.name == "rootfs_exists"][0]
    assert not rootfs.ok
    assert ".vaso/rootfs" in rootfs.detail


def test_cli_doctor_outputs_json(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "workspace" / "vaso"
    repo.mkdir(parents=True)
    monkeypatch.chdir(repo)
    code = main(["doctor"])
    assert code == 1
    data = json.loads(capsys.readouterr().out)
    assert "checks" in data
```

- [ ] **Step 2: Run failing tests**

Run: `python -m pytest tests/test_doctor.py -q`

Expected: import failure for `vaso.doctor`.

- [ ] **Step 3: Implement doctor**

Create `src/vaso/doctor.py`:

```python
from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass

from vaso.config import VasoConfig


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def run_doctor(config: VasoConfig) -> list[DoctorCheck]:
    rootfs = config.state_root / "rootfs"
    checks = [
        DoctorCheck("bwrap_available", shutil.which("bwrap") is not None, shutil.which("bwrap") or "not found"),
        DoctorCheck("rootfs_exists", rootfs.is_dir(), str(rootfs)),
        DoctorCheck("host_home_writable", (config.state_root / "home" / "kvothe").is_dir(), str(config.state_root / "home" / "kvothe")),
    ]
    return checks
```

Modify `src/vaso/cli.py`:

```python
import json
from vaso.config import default_config
from vaso.doctor import run_doctor
from vaso.paths import ensure_host_layout
```

In `main`, after parsing:

```python
    args = parser.parse_args(argv)
    if args.command == "doctor":
        config = default_config()
        ensure_host_layout(config)
        checks = run_doctor(config)
        print(json.dumps({"checks": [c.to_json() for c in checks]}, indent=2, sort_keys=True))
        return 0 if all(c.ok for c in checks) else 1
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_doctor.py tests/test_cli.py -q`

Expected: all tests pass.

---

### Task 6: Tier 0 Validation Report

**Files:**
- Create: `src/vaso/validation.py`
- Create: `tests/test_validation_tier0.py`
- Modify: `src/vaso/cli.py`

**Interfaces:**
- Produces: `ValidationResult(tier: str, ok: bool, checks: list[dict[str, object]])`
- Produces: `run_tier0(config: VasoConfig) -> ValidationResult`
- CLI: `vaso validate --tier 0` writes a validation report in `.vaso/runs/<run-id>/validation/tier0.json`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_validation_tier0.py`:

```python
import json

from vaso.config import default_config
from vaso.paths import ensure_host_layout
from vaso.validation import run_tier0, write_validation_report


def test_tier0_reports_writable_surfaces(tmp_path):
    repo = tmp_path / "workspace" / "vaso"
    repo.mkdir(parents=True)
    config = default_config(repo)
    ensure_host_layout(config)
    result = run_tier0(config)
    assert result.tier == "0"
    assert result.ok
    names = {check["name"] for check in result.checks}
    assert "writable:/vaso/cache" in names
    assert "writable:/vaso/runs" in names


def test_write_validation_report(tmp_path):
    repo = tmp_path / "workspace" / "vaso"
    repo.mkdir(parents=True)
    config = default_config(repo)
    ensure_host_layout(config)
    result = run_tier0(config)
    report = write_validation_report(config.state_root / "runs" / "run" / "validation", result)
    data = json.loads(report.read_text())
    assert data["tier"] == "0"
    assert data["ok"] is True
```

- [ ] **Step 2: Run failing tests**

Run: `python -m pytest tests/test_validation_tier0.py -q`

Expected: import failure for `vaso.validation`.

- [ ] **Step 3: Implement Tier 0 host-side probes**

Create `src/vaso/validation.py`:

```python
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from vaso.config import VasoConfig


@dataclass(frozen=True)
class ValidationResult:
    tier: str
    ok: bool
    checks: list[dict[str, object]]

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def _writable_check(name: str, path: Path) -> dict[str, object]:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".vaso-write-probe"
    probe.write_text("ok\n")
    ok = probe.read_text() == "ok\n"
    probe.unlink()
    return {"name": name, "ok": ok, "path": str(path)}


def run_tier0(config: VasoConfig) -> ValidationResult:
    checks = [
        _writable_check("writable:/home/kvothe", config.state_root / "home" / "kvothe"),
        _writable_check("writable:/vaso/cache", config.state_root / "cache"),
        _writable_check("writable:/vaso/runs", config.state_root / "runs"),
        _writable_check("writable:/vaso/traces", config.state_root / "traces"),
        _writable_check("writable:/vaso/tmp", config.state_root / "tmp"),
    ]
    return ValidationResult("0", all(bool(c["ok"]) for c in checks), checks)


def write_validation_report(directory: Path, result: ValidationResult) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"tier{result.tier}.json"
    path.write_text(json.dumps(result.to_json(), indent=2, sort_keys=True) + "\n")
    return path
```

Modify `src/vaso/cli.py` to call Tier 0:

```python
from vaso.run_records import create_run_dir, new_run_id
from vaso.validation import run_tier0, write_validation_report
```

In `main`, after `doctor` handling:

```python
    if args.command == "validate" and args.tier == "0":
        config = default_config()
        ensure_host_layout(config)
        run_dir = create_run_dir(config, new_run_id())
        result = run_tier0(config)
        write_validation_report(run_dir / "validation", result)
        print(json.dumps(result.to_json(), indent=2, sort_keys=True))
        return 0 if result.ok else 1
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_validation_tier0.py -q`

Expected: 2 passed.

---

### Task 7: Bazel Fixture Workspace and Tier 1 Plan

**Files:**
- Create: `fixtures/vaso-fixtures/README.md`
- Create: `fixtures/vaso-fixtures/MODULE.bazel`
- Create: `fixtures/vaso-fixtures/cpp/BUILD.bazel`
- Create: `fixtures/vaso-fixtures/cpp/hello.cc`
- Create: `fixtures/vaso-fixtures/python/BUILD.bazel`
- Create: `fixtures/vaso-fixtures/python/hello.py`
- Create: `fixtures/vaso-fixtures/integration/BUILD.bazel`
- Create: `fixtures/vaso-fixtures/integration/check.py`
- Create: `tests/test_fixture_files.py`

**Interfaces:**
- Produces a minimal Bazel fixture for C++ and Python in this slice.
- Rust and Go fixture targets remain in the design plan for the next slice because they require choosing and pinning Bazel rulesets.

- [ ] **Step 1: Write failing fixture file tests**

Create `tests/test_fixture_files.py`:

```python
from pathlib import Path


FIXTURE = Path("fixtures/vaso-fixtures")


def test_fixture_workspace_files_exist():
    for rel in [
        "MODULE.bazel",
        "cpp/BUILD.bazel",
        "cpp/hello.cc",
        "python/BUILD.bazel",
        "python/hello.py",
        "integration/BUILD.bazel",
        "integration/check.py",
    ]:
        assert (FIXTURE / rel).is_file()


def test_fixture_expected_outputs_are_stable():
    assert "vaso-cpp-ok" in (FIXTURE / "cpp/hello.cc").read_text()
    assert "vaso-python-ok" in (FIXTURE / "python/hello.py").read_text()
```

- [ ] **Step 2: Run failing tests**

Run: `python -m pytest tests/test_fixture_files.py -q`

Expected: missing fixture files.

- [ ] **Step 3: Add fixture files**

Create `fixtures/vaso-fixtures/MODULE.bazel`:

```python
module(name = "vaso_fixtures", version = "0.1.0")
```

Create `fixtures/vaso-fixtures/cpp/BUILD.bazel`:

```python
cc_binary(
    name = "hello_cpp",
    srcs = ["hello.cc"],
)
```

Create `fixtures/vaso-fixtures/cpp/hello.cc`:

```cpp
#include <iostream>

int main() {
  std::cout << "vaso-cpp-ok\\n";
  return 0;
}
```

Create `fixtures/vaso-fixtures/python/BUILD.bazel`:

```python
py_binary(
    name = "hello_python",
    srcs = ["hello.py"],
    main = "hello.py",
)
```

Create `fixtures/vaso-fixtures/python/hello.py`:

```python
def main() -> None:
    print("vaso-python-ok")


if __name__ == "__main__":
    main()
```

Create `fixtures/vaso-fixtures/integration/BUILD.bazel`:

```python
py_binary(
    name = "check",
    srcs = ["check.py"],
    data = ["//cpp:hello_cpp", "//python:hello_python"],
    main = "check.py",
)
```

Create `fixtures/vaso-fixtures/integration/check.py`:

```python
from __future__ import annotations

import subprocess
from pathlib import Path


def run(path: str) -> str:
    return subprocess.check_output([path], text=True).strip()


def main() -> None:
    root = Path.cwd()
    cpp = run(str(root / "cpp" / "hello_cpp"))
    py = run(str(root / "python" / "hello_python"))
    if cpp != "vaso-cpp-ok" or py != "vaso-python-ok":
        raise SystemExit(f"unexpected outputs: {cpp!r} {py!r}")
    print("vaso-integration-ok")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run fixture file tests**

Run: `python -m pytest tests/test_fixture_files.py -q`

Expected: 2 passed.

---

### Task 8: CLI Integration and Tier 1 Command Emission

**Files:**
- Modify: `src/vaso/validation.py`
- Modify: `src/vaso/cli.py`
- Create: `tests/test_validation_tier1.py`

**Interfaces:**
- Produces: `tier1_bazel_commands(repo: str) -> list[list[str]]`
- Produces: `run_tier1_plan(repo: str) -> ValidationResult`
- CLI: `vaso validate --tier 1` emits the Bazel commands it will run and marks them `not_run` until real bwrap execution lands.

- [ ] **Step 1: Write failing tests**

Create `tests/test_validation_tier1.py`:

```python
from vaso.validation import run_tier1_plan, tier1_bazel_commands


def test_tier1_bazel_commands_include_build_and_run():
    commands = tier1_bazel_commands("vaso-fixtures")
    joined = [" ".join(cmd) for cmd in commands]
    assert any("build //cpp:hello_cpp //python:hello_python" in item for item in joined)
    assert any("run //integration:all" in item for item in joined)


def test_tier1_plan_is_not_run_until_bwrap_executor_exists():
    result = run_tier1_plan("vaso-fixtures")
    assert result.tier == "1"
    assert not result.ok
    assert result.checks[0]["status"] == "not_run"
```

- [ ] **Step 2: Run failing tests**

Run: `python -m pytest tests/test_validation_tier1.py -q`

Expected: missing functions.

- [ ] **Step 3: Implement Tier 1 command plan**

Modify `src/vaso/validation.py`:

```python
def tier1_bazel_commands(repo: str) -> list[list[str]]:
    return [
        ["vaso", "bazel", "--repo", repo, "--", "build", "//cpp:hello_cpp", "//python:hello_python"],
        ["vaso", "bazel", "--repo", repo, "--", "run", "//integration:all"],
    ]


def run_tier1_plan(repo: str) -> ValidationResult:
    checks = [
        {
            "name": "tier1:bazel-command-plan",
            "ok": False,
            "status": "not_run",
            "commands": tier1_bazel_commands(repo),
        }
    ]
    return ValidationResult("1", False, checks)
```

Modify `src/vaso/cli.py` validate handling:

```python
from vaso.validation import run_tier0, run_tier1_plan, write_validation_report
```

```python
    if args.command == "validate" and args.tier == "1":
        config = default_config()
        ensure_host_layout(config)
        run_dir = create_run_dir(config, new_run_id())
        result = run_tier1_plan(args.repo)
        write_validation_report(run_dir / "validation", result)
        print(json.dumps(result.to_json(), indent=2, sort_keys=True))
        return 1
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_validation_tier1.py tests/test_validation_tier0.py -q`

Expected: all tests pass.

---

### Task 9: Final Verification for Slice

**Files:**
- Modify: none unless verification finds a bug.

**Interfaces:**
- Consumes all previous task outputs.
- Produces evidence that the initial implementation slice is internally consistent.

- [ ] **Step 1: Run unit tests**

Run: `python -m pytest -q`

Expected: all tests pass.

- [ ] **Step 2: Run CLI help**

Run: `python -m vaso.cli --help`

Expected: output includes `doctor` and `validate`.

- [ ] **Step 3: Run host-side doctor**

Run: `python -m vaso.cli doctor`

Expected: JSON output with `checks`; exit may be nonzero until `.vaso/rootfs` exists. Nonzero is acceptable only if the failing check is `rootfs_exists`.

- [ ] **Step 4: Run Tier 0 validation**

Run: `python -m vaso.cli validate --tier 0`

Expected: JSON output with `"tier": "0"` and `"ok": true`.

- [ ] **Step 5: Run Tier 1 command-plan validation**

Run: `python -m vaso.cli validate --tier 1 --repo vaso-fixtures`

Expected: JSON output with `"tier": "1"`, `"status": "not_run"`, and the two planned Bazel commands.

- [ ] **Step 6: Inspect generated run artifacts**

Run: `find .vaso/runs -maxdepth 3 -type f -print | sort`

Expected: Tier 0 and Tier 1 validation JSON reports exist under run directories.

---

## Self-Review

Spec coverage in this slice:

- Covered: host layout, `/home/kvothe`, `/workspace/<repo>`, `/vaso/*` backing dirs, deterministic bwrap argv construction, host uid/gid plan identity, bwrap plan digest serialization, doctor JSON, host-side Tier 0 writable-surface validation, validation run-record skeletons, and a Tier 1 fixture command plan.
- Partially covered: Tier 0 rootfs validation. This slice writes a bwrap plan and validates host backing paths, but does not yet execute bwrap, run user namespace probes, prove read-only rootfs enforcement, or validate network namespaces.
- Partially covered: Bazel fixture validation. This slice creates C++ and Python fixture targets and emits Bazel commands, but does not yet execute Bazel through bwrap.
- Deferred deliberately: Rust and Go Bazel rulesets, real rootfs materialization, actual bwrap execution, CUDA driver projection, uv/PyTorch/Astral Tier 2, Spack Tier 3, and agent Tier 4.

Placeholder scan: no task contains TBD/TODO/fill-in instructions. Deferred items are named explicitly as out of scope for this slice.

Type consistency:

- `ValidationResult` is reused by Tier 0 and Tier 1.
- `BwrapPlan` is the single input for argv generation and plan serialization.
- `VasoConfig` is the shared config object for path creation, doctor, and validation.
