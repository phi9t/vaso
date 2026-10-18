#!/usr/bin/env python3
"""Build the scheduled CI loop summary from run result files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise SystemExit(f"{path}: expected a JSON object")
    return value


def first_result_stage(doc: dict[str, Any]) -> dict[str, Any]:
    stages = doc.get("stages", [])
    if isinstance(stages, list) and stages:
        first = stages[0]
        if isinstance(first, dict):
            return first
    return {}


def stage_duration(doc: dict[str, Any]) -> float:
    stages = doc.get("stages", [])
    if isinstance(stages, list) and stages:
        total = 0.0
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            try:
                total += float(stage.get("duration_seconds", 0.0))
            except (TypeError, ValueError):
                continue
        return round(total, 3)
    try:
        return round(float(doc.get("duration_seconds", 0.0)), 3)
    except (TypeError, ValueError):
        return 0.0


def result_verdict(doc: dict[str, Any]) -> str:
    verdict = str(doc.get("verdict") or "").lower()
    if verdict in {"passed", "failed"}:
        return verdict
    try:
        return "passed" if int(doc.get("exit_code", 1)) == 0 else "failed"
    except (TypeError, ValueError):
        return "failed"


def result_exit_code(doc: dict[str, Any]) -> int:
    try:
        return int(doc.get("exit_code", 1))
    except (TypeError, ValueError):
        return 1


def gate_identity(path: Path) -> tuple[str, str]:
    stem = path.name.removesuffix(".result.json")
    parts = stem.split("-")
    if len(parts) != 3 or parts[0] != "gate":
        raise SystemExit(f"{path}: expected gate-<profile>-<line>.result.json")
    return parts[1], parts[2]


def stage_from_result(
    *,
    kind: str,
    name: str,
    result_path: Path,
    profile: str | None = None,
    line: str | None = None,
    acceptance_path: Path | None = None,
) -> dict[str, Any]:
    result_doc = load_json(result_path)
    first_stage = first_result_stage(result_doc)
    stage: dict[str, Any] = {
        "kind": kind,
        "name": name,
        "verdict": result_verdict(result_doc),
        "exit_code": result_exit_code(result_doc),
        "duration_seconds": stage_duration(result_doc),
        "result_path": str(result_path),
        "log_path": str(first_stage.get("log_path") or result_doc.get("log_path") or ""),
    }
    if profile is not None:
        stage["profile"] = profile
    if line is not None:
        stage["line"] = line
    if isinstance(result_doc.get("leases"), list):
        stage["leases"] = result_doc["leases"]
    if acceptance_path is not None and acceptance_path.exists():
        acceptance = load_json(acceptance_path)
        stage["acceptance_path"] = str(acceptance_path)
        stage["acceptance_verdict"] = str(acceptance.get("verdict") or "")
        failed = acceptance.get("failed_sub_results")
        if isinstance(failed, list):
            stage["failed_sub_results"] = failed
    return stage


def build_summary(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run_dir)
    results_dir = run_dir / "results"
    host_facts = load_json(Path(args.host_facts))

    stages: list[dict[str, Any]] = []
    standing = results_dir / "standing-verify.result.json"
    if standing.exists():
        stages.append(
            stage_from_result(
                kind="standing_verify",
                name="standing_verify",
                result_path=standing,
            )
        )

    for result_path in sorted(results_dir.glob("gate-*-*.result.json")):
        profile, line = gate_identity(result_path)
        stages.append(
            stage_from_result(
                kind="gate",
                name=f"gate:{profile}:{line}",
                result_path=result_path,
                profile=profile,
                line=line,
                acceptance_path=results_dir / f"gate-{profile}-{line}.acceptance.json",
            )
        )

    verdict = "passed" if stages and all(stage["verdict"] == "passed" for stage in stages) else "failed"
    return {
        "schema_version": 1,
        "run_id": args.run_id,
        "commit": args.commit,
        "target": args.target,
        "started_utc": args.started_utc,
        "finished_utc": args.finished_utc,
        "verdict": verdict,
        "host_facts": host_facts,
        "stages": stages,
    }


def cmd_summary(args: argparse.Namespace) -> int:
    doc = build_summary(args)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.name}.tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(out)
    print(out)
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-dir", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--commit", required=True)
    p.add_argument("--target", required=True)
    p.add_argument("--started-utc", required=True)
    p.add_argument("--finished-utc", required=True)
    p.add_argument("--host-facts", required=True)
    p.add_argument("--out", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    return cmd_summary(parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
