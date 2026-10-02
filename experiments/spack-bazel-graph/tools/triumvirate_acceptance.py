#!/usr/bin/env python3
"""Build the triumvirate acceptance JSON document."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any


WORKLOAD_SUMMARY_DIRS = {
    "workloads": "workloads",
    "jax_model_workloads": "jax-model-workloads",
}


def file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def prefix_digest(path: Path) -> dict[str, Any]:
    if not path.is_dir():
        return {"path": str(path), "exists": False, "sha256": None}

    digest = hashlib.sha256()
    for root, dirs, files in os.walk(path):
        dirs.sort()
        files.sort()
        root_path = Path(root)
        for name in dirs + files:
            item = root_path / name
            rel = item.relative_to(path).as_posix()
            digest.update(rel.encode())
            digest.update(b"\0")
            try:
                if item.is_symlink():
                    digest.update(b"symlink\0")
                    digest.update(os.readlink(item).encode())
                elif item.is_file():
                    digest.update(b"file\0")
                    with item.open("rb") as fh:
                        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                            digest.update(chunk)
                elif item.is_dir():
                    digest.update(b"dir\0")
                else:
                    digest.update(b"other\0")
            except OSError as exc:
                digest.update(f"error:{exc}".encode())
            digest.update(b"\0")
    return {"path": str(path), "exists": True, "sha256": "sha256:" + digest.hexdigest()}


def load_waivers(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    if not path.is_file():
        return [], [f"{path.name} is missing"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [], [f"{path.name} is not valid JSON: {exc}"]

    if not isinstance(data, list):
        return [], [f"{path.name} must be a JSON list"]

    errors: list[str] = []
    waivers: list[dict[str, Any]] = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            errors.append(f"waiver {idx} must be an object")
            continue
        if not item.get("human_decision"):
            errors.append(f"waiver {idx} must include human_decision")
        waivers.append(item)
    return waivers, errors


def load_sub_results(path: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if not path.is_file():
        return [
            {
                "name": "acceptance_inputs",
                "stage": "acceptance",
                "command": ["read", str(path)],
                "returncode": 1,
                "stdout": "",
                "stderr": f"{path} is missing",
                "started_utc": "",
                "finished_utc": "",
                "verdict": "failed",
            }
        ]
    for line_number, line_text in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line_text.strip():
            continue
        try:
            item = json.loads(line_text)
        except json.JSONDecodeError as exc:
            results.append(
                {
                    "name": f"acceptance_inputs_line_{line_number}",
                    "stage": "acceptance",
                    "command": ["parse", str(path)],
                    "returncode": 1,
                    "stdout": "",
                    "stderr": str(exc),
                    "started_utc": "",
                    "finished_utc": "",
                    "verdict": "failed",
                }
            )
            continue
        if isinstance(item, dict):
            results.append(item)
        else:
            results.append(
                {
                    "name": f"acceptance_inputs_line_{line_number}",
                    "stage": "acceptance",
                    "command": ["parse", str(path)],
                    "returncode": 1,
                    "stdout": "",
                    "stderr": "sub-result must be a JSON object",
                    "started_utc": "",
                    "finished_utc": "",
                    "verdict": "failed",
                }
            )
    return results


def _waiver_for(
    result: dict[str, Any],
    waivers: list[dict[str, Any]],
    *,
    profile: str,
    line: str,
    commit: str,
) -> dict[str, Any] | None:
    for waiver in waivers:
        if not waiver.get("human_decision"):
            continue
        if waiver.get("profile") not in (None, profile):
            continue
        if waiver.get("line") not in (None, line):
            continue
        if waiver.get("commit") not in (None, commit):
            continue
        if waiver.get("name") and waiver.get("name") != result.get("name"):
            continue
        if waiver.get("stage") and waiver.get("stage") != result.get("stage"):
            continue
        if not waiver.get("name") and not waiver.get("stage"):
            continue
        return waiver
    return None


def _sub_result_failed(result: dict[str, Any]) -> bool:
    return int(result.get("returncode", 1)) != 0 or result.get("verdict") != "passed"


def _load_summary_file(summary_path: Path) -> Any:
    if not summary_path.is_file():
        return None
    try:
        return json.loads(summary_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"error": str(exc), "path": str(summary_path)}


def _load_workload_summary(run_dir: Path) -> Any:
    return _load_summary_file(run_dir / "workloads" / "summary.json")


def _load_workload_summaries(run_dir: Path) -> dict[str, Any]:
    return {
        name: _load_summary_file(run_dir / dirname / "summary.json")
        for name, dirname in WORKLOAD_SUMMARY_DIRS.items()
    }


def build_acceptance_doc(
    *,
    profile: str,
    line: str,
    commit: str,
    run_dir: Path,
    rootfs_manifest: Path,
    lock: Path,
    prefixes: dict[str, Path],
    sub_results: list[dict[str, Any]],
    waivers: list[dict[str, Any]],
    waiver_errors: list[str],
) -> dict[str, Any]:
    normalized_results = [dict(result) for result in sub_results]
    failed_sub_results: list[str] = []

    for result in normalized_results:
        if not _sub_result_failed(result):
            result["verdict"] = "passed"
            continue
        waiver = _waiver_for(result, waivers, profile=profile, line=line, commit=commit)
        if waiver is not None:
            result["waived_by"] = waiver
            result["verdict"] = "waived"
            continue
        result["verdict"] = "failed"
        failed_sub_results.append(str(result.get("name", "<unnamed>")))

    prefix_digests = {name: prefix_digest(path) for name, path in prefixes.items()}
    for name, digest in prefix_digests.items():
        if not digest["exists"]:
            failed_sub_results.append(f"missing_prefix_digest:{name}")

    if waiver_errors:
        failed_sub_results.append("waivers")

    rootfs_manifest_digest = file_sha256(rootfs_manifest)
    lock_sha256 = file_sha256(lock)
    if rootfs_manifest_digest is None:
        failed_sub_results.append("missing_rootfs_manifest_digest")
    if lock_sha256 is None:
        failed_sub_results.append("missing_lock_sha256")

    workload_summaries = _load_workload_summaries(run_dir)
    doc = {
        "schema_version": 1,
        "profile": profile,
        "line": line,
        "commit": commit,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run_dir": str(run_dir),
        "rootfs_manifest": str(rootfs_manifest),
        "rootfs_manifest_digest": rootfs_manifest_digest,
        "lock": str(lock),
        "lock_sha256": lock_sha256,
        "prefix_digests": prefix_digests,
        "waivers_file": "",
        "waivers": waivers,
        "waiver_errors": waiver_errors,
        "sub_results": normalized_results,
        "workload_summary": workload_summaries["workloads"],
        "workload_summaries": workload_summaries,
        "failed_sub_results": failed_sub_results,
    }
    doc["verdict"] = "failed" if failed_sub_results else "passed"
    return doc


def _parse_prefix(text: str) -> tuple[str, Path]:
    if "=" not in text:
        raise argparse.ArgumentTypeError("--prefix entries must be NAME=PATH")
    name, path = text.split("=", 1)
    if not name:
        raise argparse.ArgumentTypeError("--prefix entries must include a non-empty name")
    return name, Path(path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--profile", choices=("torch", "jax"), required=True)
    parser.add_argument("--line", choices=("cu129", "cu130"), required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--rootfs-manifest", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--waivers", type=Path, required=True)
    parser.add_argument("--sub-results-jsonl", type=Path, required=True)
    parser.add_argument("--prefix", action="append", type=_parse_prefix, default=[])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    waivers, waiver_errors = load_waivers(args.waivers)
    prefixes = dict(args.prefix)
    doc = build_acceptance_doc(
        profile=args.profile,
        line=args.line,
        commit=args.commit,
        run_dir=args.run_dir,
        rootfs_manifest=args.rootfs_manifest,
        lock=args.lock,
        prefixes=prefixes,
        sub_results=load_sub_results(args.sub_results_jsonl),
        waivers=waivers,
        waiver_errors=waiver_errors,
    )
    doc["waivers_file"] = str(args.waivers)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"acceptance: verdict={doc['verdict']} profile={args.profile} line={args.line} out={args.out}")
    return 0 if doc["verdict"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
