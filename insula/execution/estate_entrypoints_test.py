"""Entry points fail closed without an explicit VASO_ESTATE_ROOT."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "foundry"


def _fake_bazel(tmp_path: Path) -> Path:
    bazel = tmp_path / "bin" / "bazel-real"
    bazel.parent.mkdir(parents=True, exist_ok=True)
    bazel.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    bazel.chmod(0o755)
    return bazel


def _env_without_estate(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    scratch = tmp_path / "scratch"
    home.mkdir()
    scratch.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "TMPDIR": str(scratch),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for key in (
        "VASO_ESTATE_ROOT",
        "VASO_AGENT_IO_ROOT",
        "VASO_BAZEL_OB",
        "TRAE_BROKER_DIR",
    ):
        env.pop(key, None)
    return env


ENTRYPOINTS = [
    (
        "run.sh",
        ["bash", str(EXP / "run.sh"), "--print-config-for-test"],
        EXP,
        {"VASO_CUDA_LINE": "cu129", "VASO_REQUIRED_GIB": "0", "VASO_GPUS": "0"},
    ),
    (
        "triumvirate-gate.sh",
        [
            "bash",
            str(ROOT / "scripts/agents/triumvirate-gate.sh"),
            "--profile",
            "torch",
            "--line",
            "cu129",
            "--commit",
            "HEAD",
        ],
        ROOT,
        {"VASO_REQUIRED_GIB": "0", "VASO_GPUS": "0"},
    ),
    (
        "triumvirate-gate-insula.sh",
        [
            "bash",
            str(EXP / "scripts/agents/triumvirate-gate-insula.sh"),
            "--profile",
            "torch",
            "--line",
            "cu129",
            "--commit",
            "HEAD",
        ],
        EXP,
        {"VASO_IN_INSULA": "1", "VASO_REPO_ROOT": str(ROOT)},
    ),
    (
        "standing-verify.sh",
        ["bash", str(ROOT / "tools/agents/standing-verify.sh"), "--agent", "fixture"],
        ROOT,
        {},
    ),
    (
        "verified-commit.sh",
        [
            "bash",
            str(ROOT / "tools/agents/verified-commit.sh"),
            "-m",
            "Fixture",
            "--",
            "README.md",
        ],
        ROOT,
        {},
    ),
    (
        "insula.sh",
        [
            "bash",
            str(ROOT / "scripts/insula/insula.sh"),
            "run",
            "--line",
            "cu129",
            "--print-argv",
            "--",
            "true",
        ],
        ROOT,
        {"VASO_REQUIRED_GIB": "0"},
    ),
    (
        "build_rootfs.sh",
        ["bash", str(ROOT / "insula/rootfs/build_rootfs.sh"), "--line", "cu129"],
        ROOT,
        {},
    ),
    (
        "doctor.sh",
        [
            "bash",
            str(ROOT / "insula/execution/doctor.sh"),
            "preflight",
            "--line",
            "cu129",
        ],
        ROOT,
        {},
    ),
    (
        "last-result.sh",
        ["bash", str(ROOT / "insula/execution/last-result.sh")],
        ROOT,
        {},
    ),
    (
        "lease.py",
        [sys.executable, str(ROOT / "insula/execution/lease.py"), "list"],
        ROOT,
        {},
    ),
    (
        "result.py",
        [
            sys.executable,
            str(ROOT / "insula/execution/result.py"),
            "redact-args",
            "--",
            "ok",
        ],
        ROOT,
        {},
    ),
    (
        "cut.sh",
        [
            "bash",
            str(ROOT / "scripts/release/cut.sh"),
            "--profile",
            "torch",
            "--line",
            "cu129",
            "--sha",
            "0" * 40,
        ],
        ROOT,
        {},
    ),
    (
        "determinism_check.sh",
        [
            "bash",
            str(ROOT / "scripts/release/determinism_check.sh"),
            "--profile",
            "torch",
            "--line",
            "cu129",
        ],
        ROOT,
        {},
    ),
    (
        "determinism_compare.py",
        [
            sys.executable,
            str(ROOT / "scripts/release/determinism_compare.py"),
            "--profile",
            "torch",
            "--line",
            "cu129",
            "--first",
            ".",
            "--second",
            ".",
            "--allowlist",
            str(ROOT / "scripts/release/determinism_allowlist.json"),
            "--out",
            str(ROOT / ".scratch" / "unused-determinism.json"),
        ],
        ROOT,
        {},
    ),
    (
        "estate.py",
        [sys.executable, str(EXP / "tools/estate.py"), "--required-gib", "0"],
        EXP,
        {},
    ),
]


@pytest.mark.parametrize(
    ("name", "argv", "cwd", "extra_env"),
    ENTRYPOINTS,
    ids=[case[0] for case in ENTRYPOINTS],
)
def test_entrypoint_refuses_without_vaso_estate_root(
    tmp_path: Path,
    name: str,
    argv: list[str],
    cwd: Path,
    extra_env: dict[str, str],
) -> None:
    env = _env_without_estate(tmp_path)
    env["BAZEL_BIN"] = str(_fake_bazel(tmp_path))
    env.update(extra_env)

    proc = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode != 0, name
    assert "VASO_ESTATE_ROOT" in proc.stderr, proc.stdout + proc.stderr
