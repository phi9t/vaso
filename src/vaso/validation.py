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
    probe = path / ".vaso-write-probe"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok\n")
        ok = probe.read_text() == "ok\n"
        return {"name": name, "ok": ok, "path": str(path)}
    except OSError as exc:
        return {"name": name, "ok": False, "path": str(path), "error": str(exc)}
    finally:
        try:
            if probe.is_file():
                probe.unlink()
        except OSError:
            pass


def run_tier0(config: VasoConfig) -> ValidationResult:
    checks = [
        _writable_check("writable:/home/kvothe", config.state_root / "home" / "kvothe"),
        _writable_check("writable:/vaso/cache", config.state_root / "cache"),
        _writable_check("writable:/vaso/runs", config.state_root / "runs"),
        _writable_check("writable:/vaso/traces", config.state_root / "traces"),
        _writable_check("writable:/vaso/tmp", config.state_root / "tmp"),
    ]
    return ValidationResult("0", all(bool(check["ok"]) for check in checks), checks)


def write_validation_report(directory: Path, result: ValidationResult) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"tier{result.tier}.json"
    path.write_text(json.dumps(result.to_json(), indent=2, sort_keys=True) + "\n")
    return path


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
