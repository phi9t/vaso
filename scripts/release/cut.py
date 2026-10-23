#!/usr/bin/env python3
"""Cut a release directory from an already verified nightly run."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / "foundry" / "tools"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(TOOLS_DIR))
from insula.estate.path_map import insula_to_host_path  # noqa: E402
from release_wheel_identity import validate_release_wheel_commit  # noqa: E402


PROFILE_PRIMARY_WHEEL = {
    "torch": "torch",
    "jax": "jaxlib",
}
RELEASE_LINES = ("cu129", "cu130")


def die(message: str, code: int = 1) -> None:
    raise SystemExit(f"{message}")


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        die(f"missing JSON file: {path}")
    except json.JSONDecodeError as exc:
        die(f"invalid JSON file: {path}: {exc}")
    if not isinstance(value, dict):
        die(f"{path}: expected a JSON object")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def strip_sha256_prefix(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    return text.removeprefix("sha256:")


def ensure_file(path: Path, label: str) -> Path:
    if not path.is_file():
        die(f"{label} not found: {path}")
    return path


def copy_file(
    src: Path, dst: Path, label: str, *, expected_sha256: str | None = None
) -> None:
    """Verify staged bytes against a pin or a source hash captured before copying."""
    ensure_file(src, label)
    expected = expected_sha256 if expected_sha256 is not None else sha256_file(src)
    shutil.copy2(src, dst)
    actual = sha256_file(dst)
    if actual != expected:
        die(f"copied {label} sha256 mismatch for {dst}: expected {expected}, got {actual}")


def host_path(path: Any, estate: Path, *, line: str) -> Path:
    # Acceptance JSON is authored inside the insula and remains source-faithful;
    # host-side release cutting translates only paths it opens.
    return insula_to_host_path(str(path or ""), estate, line=line, repo_root=REPO_ROOT)


def host_mapped_json(value: Any, estate: Path, *, line: str) -> Any:
    if isinstance(value, dict):
        return {key: host_mapped_json(item, estate, line=line) for key, item in value.items()}
    if isinstance(value, list):
        return [host_mapped_json(item, estate, line=line) for item in value]
    if isinstance(value, str) and value.startswith(("/vaso", "/opt/vaso", "/run/vaso", "/workspace")):
        return str(host_path(value, estate, line=line))
    return value


def normalize_name(name: str) -> str:
    return name.replace("_", "-").lower()


def wheel_distribution_and_version(name: str) -> tuple[str, str]:
    if not name.endswith(".whl"):
        die(f"release artifact is not a wheel: {name}")
    parts = name[:-4].split("-")
    if len(parts) < 5:
        die(f"invalid wheel filename: {name}")
    return normalize_name(parts[0]), parts[1]


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


def release_gate_candidates(stages: list[Any], profile: str, line: str) -> list[dict[str, Any]]:
    return [
        stage
        for stage in stages
        if isinstance(stage, dict)
        and stage.get("kind") == "release_gate"
        and stage.get("profile") == profile
        and stage.get("line") == line
        and stage.get("release") is True
    ]


def determinism_candidates(stages: list[Any], profile: str, line: str) -> list[dict[str, Any]]:
    return [
        stage
        for stage in stages
        if isinstance(stage, dict)
        and stage.get("kind") == "determinism"
        and stage.get("profile") == profile
        and stage.get("line") == line
    ]


def release_gate_issue(stages: list[Any], profile: str, line: str) -> tuple[dict[str, Any] | None, str | None]:
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


def determinism_issue(stages: list[Any], profile: str, line: str) -> tuple[dict[str, Any] | None, str | None]:
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


def find_green_nightly(estate: Path, profile: str, line: str, sha: str) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
    nightly_root = estate / "ci" / "nightly"
    matches: list[tuple[tuple[str, str], Path, dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    rejected: list[tuple[tuple[str, str], Path, list[str]]] = []
    for summary_path in sorted(nightly_root.glob("*/summary.json")):
        doc = load_json(summary_path)
        if doc.get("commit") != sha:
            continue
        stages = doc.get("stages", [])
        if not isinstance(stages, list):
            rejected.append((summary_sort_key(summary_path, doc), summary_path, ["stages missing"]))
            continue
        release_stages, determinism_stages, issues = required_stage_status(stages, profile)
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
        die(message)
    _sort_key, summary_path, doc, release_stage, determinism_stage = sorted(matches)[-1]
    return summary_path, doc, release_stage, determinism_stage


def release_wheels(
    acceptance: dict[str, Any],
    *,
    expected_commit: str,
    estate: Path,
    line: str,
    output_base: Any = None,
) -> list[dict[str, Any]]:
    release = acceptance.get("release")
    if not isinstance(release, dict):
        die("acceptance JSON has no release section")
    raw_wheels = release.get("wheels")
    if not isinstance(raw_wheels, list) or not raw_wheels:
        manifests = release.get("wheel_manifests")
        raw_wheels = []
        if isinstance(manifests, dict):
            for manifest in manifests.values():
                if isinstance(manifest, dict) and isinstance(manifest.get("wheels"), list):
                    raw_wheels.extend(manifest["wheels"])
    if not isinstance(raw_wheels, list) or not raw_wheels:
        die("acceptance JSON has no release wheels")

    wheels: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_wheels:
        if not isinstance(raw, dict):
            die("acceptance JSON contains an invalid release wheel entry")
        raw_path = str(raw.get("path") or "")
        if not raw_path:
            die("acceptance JSON contains a release wheel without a path")
        if raw_path.startswith("/"):
            src = host_path(raw_path, estate, line=line)
        else:
            # Wheel manifests record wheels relative to the gate's Bazel execroot.
            if not output_base:
                die(f"release wheel path is execroot-relative but the release gate stage has no output_base: {raw_path}")
            src = Path(str(output_base)) / "execroot" / "_main" / raw_path
        src = ensure_file(src, "release wheel")
        name = str(raw.get("name") or src.name)
        if "/" in name or name in {".", ".."}:
            die(f"invalid release wheel name: {name}")
        actual_sha = sha256_file(src)
        expected_digest = strip_sha256_prefix(raw.get("sha256"))
        if expected_digest is not None and expected_digest != actual_sha:
            die(f"release wheel sha256 mismatch for {src}: expected {expected_digest}, got {actual_sha}")
        if name in seen:
            die(f"duplicate release wheel name: {name}")
        try:
            validate_release_wheel_commit(src, expected_commit, wheel_name=name)
        except ValueError as exc:
            die(f"release wheel {name} does not match --sha {expected_commit}: {exc}")
        seen.add(name)
        dist, version = wheel_distribution_and_version(name)
        wheels.append(
            {
                "package": str(raw.get("package") or dist),
                "distribution": dist,
                "version": version,
                "name": name,
                "path": str(src),
                "sha256": actual_sha,
            }
        )
    return sorted(wheels, key=lambda item: item["name"])


def primary_version(profile: str, wheels: list[dict[str, Any]]) -> str:
    primary = PROFILE_PRIMARY_WHEEL[profile]
    for wheel in wheels:
        if wheel["distribution"] == primary:
            version = str(wheel["version"])
            if "+vaso" not in version:
                die(f"{primary} wheel version is not a vaso release version: {version}")
            return version
    die(f"no {primary} wheel found in release artifacts")


def release_sbom_path(acceptance: dict[str, Any], *, estate: Path, line: str) -> Path:
    release = acceptance.get("release")
    sbom = release.get("sbom") if isinstance(release, dict) else None
    if not isinstance(sbom, dict) or not sbom.get("path"):
        die("acceptance JSON has no release SBOM path")
    path = ensure_file(host_path(sbom["path"], estate, line=line), "release SBOM")
    expected_sha = strip_sha256_prefix(sbom.get("sha256"))
    if expected_sha is not None:
        actual_sha = sha256_file(path)
        if expected_sha != actual_sha:
            die(f"release SBOM sha256 mismatch for {path}: expected {expected_sha}, got {actual_sha}")
    return path


def workload_results(acceptance: dict[str, Any], *, estate: Path, line: str) -> dict[str, Any]:
    release = acceptance.get("release")
    if not isinstance(release, dict):
        die("acceptance JSON has no release section")
    raw = release.get("workload_summaries")
    if isinstance(raw, dict):
        mapped = host_mapped_json(raw, estate, line=line)
        if isinstance(mapped, dict):
            return mapped
    die("acceptance JSON has no release workload_summaries")


def build_provenance(sha: str, acceptance: dict[str, Any], wheels: list[dict[str, Any]]) -> dict[str, Any]:
    versions = {wheel["distribution"]: wheel["version"] for wheel in wheels}
    lock_digests: dict[str, str] = {}
    lock_sha = acceptance.get("lock_sha256")
    if isinstance(lock_sha, str) and lock_sha:
        lock_digests["cuda_ecosystem"] = lock_sha
    return {
        "schema_version": 1,
        "sha": sha,
        "versions": versions,
        "rootfs_bundle_digest": acceptance.get("rootfs_manifest_digest"),
        "lock_digests": lock_digests,
    }


def cut_release(args: argparse.Namespace) -> Path:
    estate = Path(os.environ.get("VASO_ESTATE_ROOT", ""))
    if not str(estate):
        die("set VASO_ESTATE_ROOT")
    estate = estate.resolve()
    summary_path, summary, release_stage, determinism_stage = find_green_nightly(
        estate, args.profile, args.line, args.sha
    )

    acceptance_path = ensure_file(host_path(release_stage.get("acceptance_path"), estate, line=args.line), "acceptance JSON")
    acceptance = load_json(acceptance_path)
    if acceptance.get("profile") != args.profile or acceptance.get("line") != args.line:
        die(f"acceptance JSON does not match {args.profile}/{args.line}: {acceptance_path}")
    if acceptance.get("commit") != args.sha:
        die(f"acceptance JSON does not match sha {args.sha}: {acceptance_path}")
    if acceptance.get("verdict") != "passed":
        die(f"acceptance JSON verdict is not passed: {acceptance_path}")

    determinism_path = ensure_file(host_path(determinism_stage.get("report_path"), estate, line=args.line), "determinism report")
    determinism = load_json(determinism_path)
    if determinism.get("profile") != args.profile:
        die(f"determinism report does not match profile {args.profile}: {determinism_path}")
    if determinism.get("line") != args.line:
        die(f"determinism report does not match line {args.line}: {determinism_path}")

    rootfs_manifest = ensure_file(
        host_path(acceptance.get("rootfs_manifest"), estate, line=args.line),
        "rootfs bundle manifest",
    )
    wheels = release_wheels(
        acceptance,
        expected_commit=args.sha,
        estate=estate,
        line=args.line,
        output_base=release_stage.get("output_base"),
    )
    version = primary_version(args.profile, wheels)
    release_dir = estate / "releases" / args.profile / args.line / version
    if release_dir.exists():
        die(f"release dir already exists: {release_dir}")

    parent = release_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent, prefix=f".{version}.tmp.") as staging:
        tmp_dir = Path(staging)
        for wheel in wheels:
            copy_file(
                Path(wheel["path"]), tmp_dir / wheel["name"], "release wheel",
                expected_sha256=wheel["sha256"],
            )
        sha_lines = [f"{wheel['sha256']}  {wheel['name']}\n" for wheel in wheels]
        (tmp_dir / "SHA256SUMS").write_text("".join(sha_lines), encoding="utf-8")
        copy_file(
            release_sbom_path(acceptance, estate=estate, line=args.line),
            tmp_dir / "sbom.json", "release SBOM",
            expected_sha256=strip_sha256_prefix(acceptance["release"]["sbom"].get("sha256")),
        )
        copy_file(acceptance_path, tmp_dir / "acceptance.json", "acceptance JSON")
        write_json(tmp_dir / "workload-results.json", workload_results(acceptance, estate=estate, line=args.line))
        copy_file(determinism_path, tmp_dir / "determinism.json", "determinism report")
        copy_file(rootfs_manifest, tmp_dir / "rootfs-bundle.json", "rootfs bundle manifest")
        write_json(tmp_dir / "provenance.json", build_provenance(args.sha, acceptance, wheels))
        cut_doc = {
            "schema_version": 1,
            "created_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "profile": args.profile,
            "line": args.line,
            "sha": args.sha,
            "version": version,
            "release_dir": str(release_dir),
            "nightly_run_id": summary.get("run_id"),
            "nightly_summary": str(summary_path),
            "nightly_finished_utc": summary.get("finished_utc"),
            "artifacts": {
                "wheels": [wheel["name"] for wheel in wheels],
                "sha256sums": "SHA256SUMS",
                "sbom": "sbom.json",
                "provenance": "provenance.json",
                "acceptance": "acceptance.json",
                "workload_results": "workload-results.json",
                "determinism": "determinism.json",
                "rootfs_bundle": "rootfs-bundle.json",
            },
        }
        write_json(tmp_dir / "cut.json", cut_doc)
        tmp_dir.rename(release_dir)
    return release_dir


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile", choices=("torch", "jax"), required=True)
    p.add_argument("--line", choices=("cu129", "cu130"), required=True)
    p.add_argument("--sha", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    release_dir = cut_release(parser().parse_args(argv))
    print(f"RELEASE_CUT {release_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
