"""Select complete nightly evidence without filesystem access or publication.

The caller supplies summary paths and decoded objects. Selection requires the
matching source revision and passing release/determinism stages on both CUDA
lines; artifact validation and copying belong to the release-cut workflow.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

RELEASE_LINES = ("cu129", "cu130")


def stage_passed(stage: dict[str, Any]) -> bool:
    return str(stage.get("verdict") or "").lower() == "passed"


def release_gate_matches(stage: dict[str, Any], profile: str, line: str) -> bool:
    return (
        stage.get("kind") == "release_gate"
        and stage.get("profile") == profile
        and stage.get("line") == line
        and stage.get("release") is True
        and stage_passed(stage)
        and str(stage.get("acceptance_verdict") or "passed").lower() == "passed"
    )


def determinism_matches(stage: dict[str, Any], profile: str, line: str) -> bool:
    return (
        stage.get("kind") == "determinism"
        and stage.get("profile") == profile
        and stage.get("line") == line
        and stage_passed(stage)
    )


def summary_sort_key(path: Path, doc: dict[str, Any]) -> tuple[str, str]:
    return (str(doc.get("finished_utc") or ""), str(path.parent.name))


def release_gate_candidates(
    stages: list[Any], profile: str, line: str
) -> list[dict[str, Any]]:
    return [
        stage
        for stage in stages
        if isinstance(stage, dict)
        and stage.get("kind") == "release_gate"
        and stage.get("profile") == profile
        and stage.get("line") == line
        and stage.get("release") is True
    ]


def determinism_candidates(
    stages: list[Any], profile: str, line: str
) -> list[dict[str, Any]]:
    return [
        stage
        for stage in stages
        if isinstance(stage, dict)
        and stage.get("kind") == "determinism"
        and stage.get("profile") == profile
        and stage.get("line") == line
    ]


def release_gate_issue(
    stages: list[Any], profile: str, line: str
) -> tuple[dict[str, Any] | None, str | None]:
    candidates = release_gate_candidates(stages, profile, line)
    for stage in candidates:
        if release_gate_matches(stage, profile, line):
            return stage, None
    if not candidates:
        return None, f"release_gate {profile}/{line} missing"
    details = []
    for field in ("verdict", "acceptance_verdict"):
        value = str(candidates[0].get(field) or "")
        if value and value.lower() != "passed":
            details.append(f"{field}={value}")
    suffix = f" ({', '.join(details)})" if details else ""
    return None, f"release_gate {profile}/{line} red{suffix}"


def determinism_issue(
    stages: list[Any], profile: str, line: str
) -> tuple[dict[str, Any] | None, str | None]:
    candidates = determinism_candidates(stages, profile, line)
    for stage in candidates:
        if determinism_matches(stage, profile, line):
            return stage, None
    if not candidates:
        return None, f"determinism {profile}/{line} missing"
    verdict = str(candidates[0].get("verdict") or "")
    suffix = f" (verdict={verdict})" if verdict else ""
    return None, f"determinism {profile}/{line} red{suffix}"


def required_stage_status(
    stages: list[Any], profile: str
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
    release_stages: dict[str, dict[str, Any]] = {}
    determinism_stages: dict[str, dict[str, Any]] = {}
    issues: list[str] = []
    for release_line in RELEASE_LINES:
        release_stage, issue = release_gate_issue(stages, profile, release_line)
        if release_stage is not None:
            release_stages[release_line] = release_stage
        if issue is not None:
            issues.append(issue)
        determinism_stage, issue = determinism_issue(stages, profile, release_line)
        if determinism_stage is not None:
            determinism_stages[release_line] = determinism_stage
        if issue is not None:
            issues.append(issue)
    return release_stages, determinism_stages, issues


def select_nightly(
    summaries: Iterable[tuple[Path, dict[str, Any]]], profile: str, line: str, sha: str
) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
    matches: list[
        tuple[tuple[str, str], Path, dict[str, Any], dict[str, Any], dict[str, Any]]
    ] = []
    rejected: list[tuple[tuple[str, str], Path, list[str]]] = []
    for summary_path, doc in summaries:
        if doc.get("commit") != sha:
            continue
        stages = doc.get("stages", [])
        if not isinstance(stages, list):
            rejected.append(
                (summary_sort_key(summary_path, doc), summary_path, ["stages missing"])
            )
            continue
        release_stages, determinism_stages, issues = required_stage_status(
            stages, profile
        )
        if doc.get("verdict") != "passed":
            issues.append(f"summary verdict {doc.get('verdict') or 'missing'}")
        if issues:
            rejected.append((summary_sort_key(summary_path, doc), summary_path, issues))
            continue
        matches.append(
            (
                summary_sort_key(summary_path, doc),
                summary_path,
                doc,
                release_stages[line],
                determinism_stages[line],
            )
        )
    if not matches:
        message = (
            f"no passed nightly for sha {sha} with release_gate and determinism {profile} "
            f"on {', '.join(RELEASE_LINES)}"
        )
        if rejected:
            _sort_key, summary_path, issues = sorted(rejected)[-1]
            message += f"; latest matching summary {summary_path}: " + "; ".join(issues)
        raise SystemExit(message)
    _sort_key, summary_path, doc, release_stage, determinism_stage = sorted(matches)[-1]
    return summary_path, doc, release_stage, determinism_stage
