#!/usr/bin/env python3
"""Build nightly release CI summaries and prune stale estate artifacts."""

from __future__ import annotations

import argparse
import errno
import json
import os
import shutil
import socket
import stat
import time
from pathlib import Path
from typing import Any


# Per release gate (one profile on one line), not summed across lines. A gate
# that takes hours means a native package rebuilt.
SLO_LIMITS_SECONDS = {"torch": 45 * 60, "jaxlib": 2 * 60 * 60}
PROFILE_ORDER = {"torch": 0, "jax": 1}
LINE_ORDER = {"cu130": 0, "cu129": 1}
REQUIRED_RELEASE_PROFILES = ("torch", "jax")
REQUIRED_RELEASE_LINES = ("cu130", "cu129")


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise SystemExit(f"{path}: expected a JSON object")
    return value


def try_load_json(path: Path) -> dict[str, Any]:
    try:
        return load_json(path)
    except (json.JSONDecodeError, SystemExit):
        return {}


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def result_verdict(doc: dict[str, Any]) -> str:
    verdict = str(doc.get("verdict") or "").lower()
    if verdict in {"passed", "failed", "blocked"}:
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


def first_result_stage(doc: dict[str, Any]) -> dict[str, Any]:
    stages = doc.get("stages", [])
    if isinstance(stages, list) and stages and isinstance(stages[0], dict):
        return stages[0]
    return {}


def identity_from_result(path: Path, prefix: str) -> tuple[str, str]:
    stem = path.name.removesuffix(".result.json")
    parts = stem.split("-")
    if len(parts) != 3 or parts[0] != prefix:
        raise SystemExit(f"{path}: expected {prefix}-<profile>-<line>.result.json")
    return parts[1], parts[2]


def acceptance_refs(path: Path) -> list[str]:
    doc = load_json(path)
    refs = doc.get("referenced_paths", [])
    if isinstance(refs, list):
        return [str(item) for item in refs if isinstance(item, str)]
    return []


def release_gate_stage(path: Path, acceptance_path: Path) -> dict[str, Any]:
    profile, line = identity_from_result(path, "release")
    doc = load_json(path)
    first_stage = first_result_stage(doc)
    stage: dict[str, Any] = {
        "kind": "release_gate",
        "name": f"release:{profile}:{line}",
        "profile": profile,
        "line": line,
        "release": True,
        "verdict": result_verdict(doc),
        "exit_code": result_exit_code(doc),
        "duration_seconds": stage_duration(doc),
        "result_path": str(path),
        "log_path": str(first_stage.get("log_path") or doc.get("log_path") or ""),
        "output_base": str(path.parents[1] / "output-bases" / f"release-{profile}-{line}"),
    }
    if isinstance(doc.get("leases"), list):
        stage["leases"] = doc["leases"]
    if stage["verdict"] == "blocked" and doc.get("blocked_reason"):
        stage["blocked_reason"] = str(doc["blocked_reason"])
    if acceptance_path.exists():
        acceptance = load_json(acceptance_path)
        stage["acceptance_path"] = str(acceptance_path)
        stage["acceptance_verdict"] = str(acceptance.get("verdict") or "")
        refs = acceptance_refs(acceptance_path)
        if refs:
            stage["referenced_paths"] = refs
    return stage


def determinism_stage(path: Path) -> dict[str, Any]:
    profile, line = identity_from_result(path, "determinism")
    doc = load_json(path)
    first_stage = first_result_stage(doc)
    report_path = path.with_name(f"determinism-{profile}-{line}.json")
    stage: dict[str, Any] = {
        "kind": "determinism",
        "name": f"determinism:{profile}:{line}",
        "profile": profile,
        "line": line,
        "verdict": result_verdict(doc),
        "exit_code": result_exit_code(doc),
        "duration_seconds": stage_duration(doc),
        "result_path": str(path),
        "log_path": str(first_stage.get("log_path") or doc.get("log_path") or ""),
        "report_path": str(report_path),
        "work_dir": str(path.parents[1] / "determinism" / f"{profile}-{line}"),
    }
    if report_path.exists():
        report = load_json(report_path)
        stage["identical"] = bool(report.get("identical"))
        differing = report.get("differing_files")
        if isinstance(differing, list):
            stage["differing_files"] = differing
    return stage


def duration_for_slo(stages: list[dict[str, Any]], name: str) -> float:
    profile = "torch" if name == "torch" else "jax"
    durations = [
        float(stage.get("duration_seconds", 0.0))
        for stage in stages
        if stage.get("kind") == "release_gate" and stage.get("profile") == profile
    ]
    return round(max(durations, default=0.0), 3)


def build_slos(stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, limit in SLO_LIMITS_SECONDS.items():
        duration = duration_for_slo(stages, name)
        rows.append(
            {
                "name": name,
                "duration_seconds": duration,
                "limit_seconds": limit,
                "within_slo": duration <= limit,
            }
        )
    return rows


def release_stage_passed(stage: dict[str, Any]) -> bool:
    acceptance_verdict = str(stage.get("acceptance_verdict") or "").lower()
    return (
        stage.get("kind") == "release_gate"
        and stage.get("release") is True
        and stage.get("verdict") == "passed"
        and acceptance_verdict in {"", "passed"}
    )


def release_matrix(stages: list[dict[str, Any]]) -> dict[str, Any]:
    required = []
    for profile in REQUIRED_RELEASE_PROFILES:
        for line in REQUIRED_RELEASE_LINES:
            matches = [
                stage
                for stage in stages
                if stage.get("kind") == "release_gate"
                and stage.get("profile") == profile
                and stage.get("line") == line
                and stage.get("release") is True
            ]
            status = "missing"
            if matches:
                if any(release_stage_passed(stage) for stage in matches):
                    status = "passed"
                elif all(stage.get("verdict") == "blocked" for stage in matches):
                    status = "blocked"
                else:
                    status = "red"
            required.append(
                {
                    "profile": profile,
                    "line": line,
                    "stage": "release_gate",
                    "status": status,
                }
            )
    return {
        "schema_version": 1,
        "verdict": "passed" if all(item["status"] == "passed" for item in required) else "failed",
        "required": required,
    }


# Absolute floor: the estate shares a multi-terabyte disk with other work, so a
# percentage floor overshoots what a nightly actually needs.
REQUIRED_FREE_GIB = 42.0


def disk_headroom(path: Path, fake_free_gib: str | None = None) -> dict[str, Any]:
    if fake_free_gib is not None:
        free_gib = float(fake_free_gib)
    else:
        free_gib = round(shutil.disk_usage(path).free / 2**30, 1)
    return {
        "free_gib": free_gib,
        "required_free_gib": REQUIRED_FREE_GIB,
        "verdict": "passed" if free_gib >= REQUIRED_FREE_GIB else "failed",
    }


def collect_path_strings(value: Any) -> set[str]:
    paths: set[str] = set()
    if isinstance(value, dict):
        for item in value.values():
            paths.update(collect_path_strings(item))
    elif isinstance(value, list):
        for item in value:
            paths.update(collect_path_strings(item))
    elif isinstance(value, str) and value.startswith("/"):
        paths.add(value)
    return paths


def collect_json_strings(value: Any) -> set[str]:
    strings: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            strings.add(str(key))
            strings.update(collect_json_strings(item))
    elif isinstance(value, list):
        for item in value:
            strings.update(collect_json_strings(item))
    elif isinstance(value, str):
        strings.add(value)
    return strings


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def is_lstat_dir(path: Path) -> bool:
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except FileNotFoundError:
        return False


def path_exists_lstat(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def json_files_under(root: Path) -> list[Path]:
    if not is_lstat_dir(root):
        return []
    paths: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [name for name in dirnames if not (Path(dirpath) / name).is_symlink()]
        for name in filenames:
            path = Path(dirpath) / name
            if path.suffix == ".json" and not path.is_symlink():
                paths.append(path)
    return sorted(paths)


def resolve_inside_estate(path: Path, estate_resolved: Path) -> tuple[Path | None, str | None]:
    try:
        resolved = path.resolve(strict=False)
    except OSError as exc:
        return None, str(exc)
    if not is_relative_to(resolved, estate_resolved):
        return resolved, f"resolves outside estate: {resolved}"
    return resolved, None


def tree_size_bytes(path: Path) -> int:
    try:
        root_stat = path.lstat()
    except FileNotFoundError:
        return 0
    total = int(root_stat.st_size)
    if not stat.S_ISDIR(root_stat.st_mode):
        return total
    for root, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            try:
                total += int((Path(root) / name).lstat().st_size)
            except FileNotFoundError:
                continue
    return total


def tree_mtime(path: Path) -> float:
    try:
        root_stat = path.lstat()
    except FileNotFoundError:
        return 0.0
    latest = float(root_stat.st_mtime)
    if not stat.S_ISDIR(root_stat.st_mode):
        return latest
    for root, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            try:
                latest = max(latest, float((Path(root) / name).lstat().st_mtime))
            except FileNotFoundError:
                continue
    return latest


def _pid_alive(pid: int, host: str) -> bool:
    if pid <= 0:
        return False
    if host and host != socket.gethostname():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno != errno.ESRCH
    return True


def _active_lease_records(estate: Path, now: float) -> list[dict[str, Any]]:
    agents_root = estate / "agents"
    if not is_lstat_dir(agents_root):
        return []
    leases_root = agents_root / "leases"
    if leases_root.is_symlink() or not is_lstat_dir(leases_root):
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(leases_root.iterdir()):
        if path.is_symlink() or path.suffix != ".json":
            continue
        state = try_load_json(path)
        raw_leases = state.get("leases", [])
        if not isinstance(raw_leases, list):
            continue
        for lease in raw_leases:
            if not isinstance(lease, dict):
                continue
            try:
                pid = int(lease.get("pid", 0))
                expiry = float(lease.get("expiry_time", 0))
            except (TypeError, ValueError):
                continue
            if expiry <= now:
                continue
            if not _pid_alive(pid, str(lease.get("host", ""))):
                continue
            records.append(lease)
    return records


def holder_agent_names(holder: str) -> set[str]:
    names: set[str] = set()
    if holder and "/" not in holder and holder not in {".", ".."}:
        names.add(holder)
        for marker in ("-nightly-", "-ci-"):
            if marker in holder:
                prefix = holder.split(marker, 1)[0]
                if prefix and "/" not in prefix and prefix not in {".", ".."}:
                    names.add(prefix)
    return names


def active_lease_paths(estate: Path, now: float) -> set[Path]:
    estate_resolved = estate.resolve(strict=False)
    paths: set[Path] = set()
    for lease in _active_lease_records(estate, now):
        for raw in collect_path_strings(lease):
            resolved, error = resolve_inside_estate(Path(raw), estate_resolved)
            if error is None and resolved is not None:
                paths.add(resolved)
        for agent in holder_agent_names(str(lease.get("holder") or "")):
            resolved, error = resolve_inside_estate(estate / "agents" / agent, estate_resolved)
            if error is None and resolved is not None:
                paths.add(resolved)
    return paths


def recent_result_strings(estate: Path, cutoff_epoch: float) -> set[str]:
    agents_root = estate / "agents"
    if not is_lstat_dir(agents_root):
        return set()
    strings: set[str] = set()
    for agent_root in sorted(agents_root.iterdir()):
        if agent_root.is_symlink() or not is_lstat_dir(agent_root):
            continue
        results_root = agent_root / "results"
        if results_root.is_symlink() or not is_lstat_dir(results_root):
            continue
        for path in json_files_under(results_root):
            if tree_mtime(path) < cutoff_epoch:
                continue
            strings.update(collect_json_strings(try_load_json(path)))
    return strings


def digest_in_recent_results(digest: str, strings: set[str]) -> bool:
    return any(digest == item or digest in item for item in strings)


def referenced_paths(estate: Path, keep_summaries: list[Path]) -> set[Path]:
    refs: set[Path] = set()
    estate_resolved = estate.resolve(strict=False)
    for path in keep_summaries:
        for raw in collect_path_strings(load_json(path)):
            resolved, error = resolve_inside_estate(Path(raw), estate_resolved)
            if error is None and resolved is not None:
                refs.add(resolved)
    releases = estate / "releases"
    if releases.exists():
        for path in json_files_under(releases):
            for raw in collect_path_strings(try_load_json(path)):
                resolved, error = resolve_inside_estate(Path(raw), estate_resolved)
                if error is None and resolved is not None:
                    refs.add(resolved)
    return refs


def path_is_referenced(path: Path, refs: set[Path]) -> bool:
    resolved = path.resolve()
    return any(resolved == ref or is_relative_to(ref, resolved) or is_relative_to(resolved, ref) for ref in refs)


def nightly_summaries(nightly_root: Path) -> list[Path]:
    if not is_lstat_dir(nightly_root):
        return []
    summaries: list[Path] = []
    for run_dir in sorted(nightly_root.iterdir()):
        if run_dir.is_symlink() or not is_lstat_dir(run_dir):
            continue
        summary = run_dir / "summary.json"
        if summary.exists() and not summary.is_symlink():
            summaries.append(summary)
    return summaries


def summary_sort_key(path: Path) -> str:
    doc = load_json(path)
    return str(doc.get("finished_utc") or path.parent.name)


def delete_path(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
        return
    shutil.rmtree(path)


def gc_estate(
    estate: Path,
    current_run_dir: Path,
    keep_green: int = 3,
    *,
    apply: bool = False,
    min_age_days: float = 7.0,
) -> dict[str, Any]:
    now = time.time()
    cutoff_epoch = now - (min_age_days * 24 * 60 * 60)
    estate_resolved = estate.resolve(strict=False)
    ci_root = estate / "ci"
    nightly_root = ci_root / "nightly"
    green = []
    if not ci_root.is_symlink():
        green = [
            path
            for path in nightly_summaries(nightly_root)
            if load_json(path).get("verdict") == "passed"
        ]
    current_summary = current_run_dir / "summary.json"
    current_resolved, current_error = resolve_inside_estate(current_run_dir, estate_resolved)
    if (
        current_error is None
        and current_resolved is not None
        and not current_run_dir.is_symlink()
        and is_lstat_dir(current_run_dir)
        and current_summary.exists()
        and not current_summary.is_symlink()
        and load_json(current_summary).get("verdict") == "passed"
    ):
        green.append(current_summary)
    green = sorted(set(green), key=summary_sort_key)
    keep = set(green[-keep_green:])
    refs = referenced_paths(estate, sorted(keep))
    lease_refs = active_lease_paths(estate, now)
    refs.update(lease_refs)
    recent_strings = recent_result_strings(estate, cutoff_epoch)
    candidates: list[tuple[Path, int]] = []
    candidate_paths: list[str] = []
    removed_paths: list[str] = []
    kept_ci: list[str] = []
    skipped_paths: list[dict[str, Any]] = []
    refused_paths: list[dict[str, str]] = []

    def consider(path: Path, reason_kind: str) -> None:
        resolved, error = resolve_inside_estate(path, estate_resolved)
        if error is not None:
            refused_paths.append({"path": str(path), "reason": error})
            return
        if path_is_referenced(path, refs):
            skipped_paths.append({"path": str(path), "reason": "referenced"})
            return
        if reason_kind == "work" and digest_in_recent_results(path.name, recent_strings):
            skipped_paths.append({"path": str(path), "reason": "recent_result_digest"})
            return
        modified_at = tree_mtime(path)
        if modified_at >= cutoff_epoch:
            skipped_paths.append(
                {
                    "path": str(path),
                    "reason": "recent_mtime",
                    "modified_at_epoch": modified_at,
                }
            )
            return
        size = tree_size_bytes(path)
        candidates.append((path, size))
        candidate_paths.append(str(path))

    def note_symlink_container(path: Path) -> None:
        _resolved, error = resolve_inside_estate(path, estate_resolved)
        if error is not None:
            refused_paths.append({"path": str(path), "reason": error})
        else:
            skipped_paths.append({"path": str(path), "reason": "symlink_container"})

    if ci_root.is_symlink():
        note_symlink_container(ci_root)
    elif is_lstat_dir(ci_root):
        for child in sorted(ci_root.iterdir()):
            if child == current_run_dir:
                kept_ci.append(str(child))
                continue
            if child == nightly_root:
                if child.is_symlink():
                    consider(child, "ci")
                    continue
                if not is_lstat_dir(child):
                    continue
                for run_dir in sorted(nightly_root.iterdir()):
                    if run_dir.is_symlink():
                        consider(run_dir, "ci")
                        continue
                    if not is_lstat_dir(run_dir):
                        continue
                    summary = run_dir / "summary.json"
                    if summary in keep or path_is_referenced(run_dir, refs):
                        kept_ci.append(str(run_dir))
                    else:
                        consider(run_dir, "ci")
                continue
            if child.is_symlink():
                consider(child, "ci")
            elif is_lstat_dir(child) and (child / "summary.json").exists():
                if path_is_referenced(child, refs):
                    kept_ci.append(str(child))
                else:
                    consider(child, "ci")

    for lines_root in (estate / "lines", estate / "vaso" / "lines"):
        if lines_root.is_symlink():
            note_symlink_container(lines_root)
            continue
        if not is_lstat_dir(lines_root):
            continue
        for line_dir in sorted(lines_root.iterdir()):
            if line_dir.is_symlink():
                note_symlink_container(line_dir)
                continue
            if not is_lstat_dir(line_dir):
                continue
            work_root = line_dir / "work"
            if work_root.is_symlink():
                note_symlink_container(work_root)
                continue
            if not is_lstat_dir(work_root):
                continue
            for package_dir in sorted(work_root.iterdir()):
                if package_dir.is_symlink():
                    note_symlink_container(package_dir)
                    continue
                if not is_lstat_dir(package_dir):
                    continue
                for digest_dir in sorted(package_dir.iterdir()):
                    if digest_dir.is_symlink() or is_lstat_dir(digest_dir):
                        consider(digest_dir, "work")

    for root in (estate, current_run_dir):
        if not path_exists_lstat(root):
            continue
        _resolved, error = resolve_inside_estate(root, estate_resolved)
        if error is not None:
            refused_paths.append({"path": str(root), "reason": error})

    candidate_bytes = sum(size for _path, size in candidates)
    if not green and apply:
        return {
            "schema_version": 1,
            "apply": apply,
            "mode": "apply",
            "verdict": "refused",
            "message": "GC refused: no green nightly summaries found; no paths removed.",
            "green_nightly_count": 0,
            "kept_green_nightlies": [],
            "candidate_paths": candidate_paths,
            "candidate_bytes": candidate_bytes,
            "would_reclaim_bytes": candidate_bytes,
            "removed_paths": [],
            "reclaimed_bytes": 0,
            "skipped_paths": skipped_paths,
            "refused_paths": refused_paths,
            "kept_ci": sorted(kept_ci),
            "active_lease_paths": sorted(str(path) for path in lease_refs),
            "min_age_days": min_age_days,
        }
    if refused_paths and apply:
        return {
            "schema_version": 1,
            "apply": apply,
            "mode": "apply",
            "verdict": "refused",
            "message": "GC refused: at least one path resolves outside the estate; no paths removed.",
            "green_nightly_count": len(green),
            "kept_green_nightlies": sorted(str(path.parent) for path in keep),
            "candidate_paths": candidate_paths,
            "candidate_bytes": candidate_bytes,
            "would_reclaim_bytes": candidate_bytes,
            "removed_paths": [],
            "reclaimed_bytes": 0,
            "skipped_paths": skipped_paths,
            "refused_paths": refused_paths,
            "kept_ci": sorted(kept_ci),
            "active_lease_paths": sorted(str(path) for path in lease_refs),
            "min_age_days": min_age_days,
        }

    reclaimed_bytes = 0
    if apply:
        for path, size in candidates:
            resolved, error = resolve_inside_estate(path, estate_resolved)
            if error is not None:
                refused_paths.append({"path": str(path), "reason": error})
                continue
            if resolved is None or not path_exists_lstat(path):
                continue
            delete_path(path)
            removed_paths.append(str(path))
            reclaimed_bytes += size

    if refused_paths and apply:
        verdict = "refused"
        message = "GC refused: at least one candidate path became unsafe during deletion."
    elif apply:
        verdict = "passed"
        message = f"GC applied; removed {len(removed_paths)} candidate path(s)."
    else:
        verdict = "passed"
        message = f"GC report-only; set NIGHTLY_GC_APPLY=1 to remove {len(candidate_paths)} candidate path(s)."

    return {
        "schema_version": 1,
        "apply": apply,
        "mode": "apply" if apply else "report-only",
        "verdict": verdict,
        "message": message,
        "green_nightly_count": len(green),
        "kept_green_nightlies": sorted(str(path.parent) for path in keep),
        "candidate_paths": candidate_paths,
        "candidate_bytes": candidate_bytes,
        "would_reclaim_bytes": candidate_bytes,
        "removed_paths": removed_paths,
        "reclaimed_bytes": reclaimed_bytes,
        "skipped_paths": skipped_paths,
        "refused_paths": refused_paths,
        "kept_ci": sorted(kept_ci),
        "active_lease_paths": sorted(str(path) for path in lease_refs),
        "min_age_days": min_age_days,
    }


def cmd_headroom(args: argparse.Namespace) -> int:
    write_json(Path(args.out), disk_headroom(Path(args.estate_root), args.fake_free_gib))
    return 0


def cmd_gc(args: argparse.Namespace) -> int:
    doc = gc_estate(
        Path(args.estate_root),
        Path(args.current_run_dir),
        args.keep_green,
        apply=args.apply,
        min_age_days=args.min_age_days,
    )
    write_json(Path(args.out), doc)
    return 0 if doc.get("verdict") == "passed" else 1


def cmd_summary(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    results_dir = run_dir / "results"
    stages: list[dict[str, Any]] = []
    release_paths = []
    for result_path in results_dir.glob("release-*-*.result.json"):
        profile, line = identity_from_result(result_path, "release")
        release_paths.append((PROFILE_ORDER.get(profile, 99), LINE_ORDER.get(line, 99), result_path))
    for _profile_order, _line_order, result_path in sorted(release_paths):
        profile, line = identity_from_result(result_path, "release")
        stages.append(
            release_gate_stage(
                result_path,
                results_dir / f"release-{profile}-{line}.acceptance.json",
            )
        )
    determinism_paths = []
    for result_path in results_dir.glob("determinism-*-*.result.json"):
        profile, line = identity_from_result(result_path, "determinism")
        determinism_paths.append((PROFILE_ORDER.get(profile, 99), LINE_ORDER.get(line, 99), result_path))
    for _profile_order, _line_order, result_path in sorted(determinism_paths):
        stages.append(determinism_stage(result_path))

    build_slo_rows = build_slos(stages)
    release_matrix_doc = release_matrix(stages)
    headroom = load_json(Path(args.disk_headroom))
    gc = load_json(Path(args.gc_report)) if args.gc_report else {}
    all_stages_passed = bool(stages) and all(stage["verdict"] == "passed" for stage in stages)
    release_matrix_passed = release_matrix_doc["verdict"] == "passed"
    slos_passed = all(row["within_slo"] for row in build_slo_rows)
    headroom_passed = headroom.get("verdict") == "passed"
    gc_passed = not gc or gc.get("verdict") == "passed"
    verdict = (
        "passed"
        if all_stages_passed and release_matrix_passed and slos_passed and headroom_passed and gc_passed
        else "failed"
    )

    referenced: set[str] = set()
    for stage in stages:
        for raw in stage.get("referenced_paths", []):
            if isinstance(raw, str):
                referenced.add(raw)

    doc = {
        "schema_version": 1,
        "run_id": args.run_id,
        "commit": args.commit,
        "target": args.target,
        "started_utc": args.started_utc,
        "finished_utc": args.finished_utc,
        "verdict": verdict,
        "host_facts": load_json(Path(args.host_facts)),
        "disk_headroom": headroom,
        "release_matrix": release_matrix_doc,
        "build_slos": build_slo_rows,
        "stages": stages,
        "referenced_paths": sorted(referenced),
        "gc": gc,
    }
    write_json(Path(args.out), doc)
    print(args.out)
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(description=__doc__)
    sub = top.add_subparsers(dest="cmd", required=True)

    headroom = sub.add_parser("headroom")
    headroom.add_argument("--estate-root", required=True)
    headroom.add_argument("--fake-free-gib")
    headroom.add_argument("--out", required=True)
    headroom.set_defaults(func=cmd_headroom)

    gc = sub.add_parser("gc")
    gc.add_argument("--estate-root", required=True)
    gc.add_argument("--current-run-dir", required=True)
    gc.add_argument("--keep-green", type=int, default=3)
    gc.add_argument("--apply", action="store_true")
    gc.add_argument("--min-age-days", type=float, default=7.0)
    gc.add_argument("--out", required=True)
    gc.set_defaults(func=cmd_gc)

    summary = sub.add_parser("summary")
    summary.add_argument("--run-dir", required=True)
    summary.add_argument("--run-id", required=True)
    summary.add_argument("--commit", required=True)
    summary.add_argument("--target", required=True)
    summary.add_argument("--started-utc", required=True)
    summary.add_argument("--finished-utc", required=True)
    summary.add_argument("--host-facts", required=True)
    summary.add_argument("--disk-headroom", required=True)
    summary.add_argument("--gc-report")
    summary.add_argument("--out", required=True)
    summary.set_defaults(func=cmd_summary)

    return top


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
