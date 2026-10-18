from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from vaso.bwrap import BwrapPlan, plan_to_json_dict
from vaso.config import VasoConfig


def new_run_id(now: datetime | None = None) -> str:
    value = now or datetime.now(UTC)
    return value.strftime("%Y%m%dT%H%M%S%fZ")


def create_run_dir(config: VasoConfig, run_id: str) -> Path:
    run_dir = config.state_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def write_plan(run_dir: Path, plan: BwrapPlan) -> Path:
    path = run_dir / "bwrap-plan.json"
    path.write_text(json.dumps(plan_to_json_dict(plan), indent=2, sort_keys=True) + "\n")
    return path


def write_log(run_dir: Path, name: str, text: str) -> Path:
    if name not in {"stdout.log", "stderr.log"}:
        raise ValueError(f"unsupported log name: {name}")
    path = run_dir / name
    path.write_text(text)
    return path


def write_result(
    run_dir: Path,
    *,
    ok: bool,
    exit_code: int,
    kind: str,
    extra: dict[str, object] | None = None,
) -> Path:
    data: dict[str, object] = {"exit_code": exit_code, "kind": kind, "ok": ok}
    if extra:
        data.update(extra)
    path = run_dir / "result.json"
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return path
