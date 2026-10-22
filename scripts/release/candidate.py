#!/usr/bin/env python3
"""Freeze a complete preview selection and verify its restored payloads.

This records candidate identity, not promotion. Build and runtime verdicts remain
separate evidence. No mutable tags, implicit component selection, or native builds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Any

MATRIX = {
    (line, profile) for line in ("cu129", "cu130") for profile in ("torch", "jax")
}
REPO = Path(__file__).resolve().parents[2]


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical(doc: Any) -> bytes:
    return (json.dumps(doc, sort_keys=True, indent=2) + "\n").encode()


def write_new(path: Path, doc: Any) -> str:
    data = canonical(doc)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
    return hashlib.sha256(data).hexdigest()


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def safe_member(name: str) -> None:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or str(path) != name:
        raise ValueError(f"invalid artifact member: {name!r}")


def validate(doc: dict) -> None:
    if doc.get("schema_version") != 1 or doc.get("kind") != "vaso-release-candidate":
        raise ValueError("unsupported candidate schema")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", doc.get("name", "")):
        raise ValueError("invalid candidate name")
    for field in ("commit", "tree"):
        if not re.fullmatch("[0-9a-f]{40}", doc.get("source", {}).get(field, "")):
            raise ValueError(f"missing source {field}")
    seen = set()
    for item in doc.get("components", []):
        key = (item.get("kind"), item.get("line"), item.get("profile"))
        if key in seen:
            raise ValueError("duplicate candidate component")
        seen.add(key)
        if not re.fullmatch(
            r"ghcr\.io/[^\s@]+@sha256:[0-9a-f]{64}", item.get("reference", "")
        ):
            raise ValueError("component must use an immutable GHCR digest")
        if not item.get("files"):
            raise ValueError("component has no file hashes")
        for name, digest in item["files"].items():
            safe_member(name)
            if not re.fullmatch("[0-9a-f]{64}", digest):
                raise ValueError("invalid file hash")
        if item["kind"] == "rootfs":
            safe_member(item["archive"])
            if not re.fullmatch("[0-9a-f]{64}", item["archive_sha256"]):
                raise ValueError("invalid archive hash")
    expected = {("rootfs", line, None) for line in ("cu129", "cu130")}
    expected |= {("release", line, profile) for line, profile in MATRIX}
    if seen != expected:
        raise ValueError("candidate must contain two rootfs and four profile releases")
    policy = doc.get("qualification_policy", {})
    if hashlib.sha256(canonical(policy)).hexdigest() != doc.get(
        "qualification_policy_sha256"
    ):
        raise ValueError("qualification policy hash mismatch")


def freeze(
    repo: Path, source: str, inventory: Path, policy_path: Path, name: str
) -> dict:
    commit = git(repo, "rev-parse", source + "^{commit}")
    components = []
    for item in json.loads(inventory.read_text())["artifacts"]:
        # Observations in the inventory are historical evidence, not candidate verdicts.
        fields = (
            "kind",
            "line",
            "profile",
            "reference",
            "files",
            "archive",
            "archive_sha256",
            "source_commit",
            "rootfs_manifest_digest",
            "ecosystem_lock_sha256",
            "release_python",
        )
        components.append({key: item[key] for key in fields if key in item})
    policy = json.loads(policy_path.read_text())
    selections = {}
    for path in (
        "foundry/MODULE.bazel",
        "foundry/plans/keys.json",
        "foundry/rootfs/cuda_ecosystem.lock.json",
        "scripts/release/determinism_allowlist.json",
    ):
        data = subprocess.check_output(
            ["git", "-C", str(repo), "show", f"{commit}:{path}"]
        )
        selections[path] = hashlib.sha256(data).hexdigest()
    doc = {
        "schema_version": 1,
        "kind": "vaso-release-candidate",
        "name": name,
        "source": {
            "commit": commit,
            "tree": git(repo, "rev-parse", commit + "^{tree}"),
        },
        "selection_sha256": selections,
        "components": components,
        "inventory_sha256": sha256(inventory),
        "qualification_policy": policy,
        "qualification_policy_sha256": hashlib.sha256(canonical(policy)).hexdigest(),
    }
    validate(doc)
    return doc


def component_dir(estate: Path, item: dict) -> Path:
    name = "-".join(
        str(item[key]) for key in ("kind", "profile", "line") if key in item
    )
    return estate / "downloads" / name


def verify_payloads(doc: dict, estate: Path) -> dict:
    validate(doc)
    checks, errors = [], []
    for item in doc["components"]:
        directory = component_dir(estate, item)
        expected = dict(item["files"])
        if item["kind"] == "rootfs":
            expected[item["archive"]] = item["archive_sha256"]
        verified = 0
        omitted_transport_parts = []
        for name, digest in expected.items():
            path = directory / name
            if (
                item["kind"] == "rootfs"
                and ".tar.zst.part" in name
                and not path.exists()
            ):
                # ORAS may discard transport chunks after reassembly. The pinned
                # whole-archive checksum below remains mandatory.
                omitted_transport_parts.append(name)
                continue
            try:
                # Artifact symlinks may not escape the restored component.
                path.resolve().relative_to(directory.resolve())
                if sha256(path) != digest:
                    raise ValueError("checksum mismatch")
                verified += 1
            except (OSError, ValueError) as exc:
                errors.append(
                    {"component": directory.name, "file": name, "error": str(exc)}
                )
        if item["kind"] == "rootfs":
            path = estate / "rootfs-lines" / item["line"] / "rootfs-bundle.json"
            try:
                if sha256(path) != item["files"]["rootfs-bundle.json"]:
                    raise ValueError(
                        "materialized rootfs manifest differs from candidate"
                    )
            except (OSError, ValueError) as exc:
                errors.append(
                    {
                        "component": directory.name,
                        "file": "materialized/rootfs-bundle.json",
                        "error": str(exc),
                    }
                )
        checks.append(
            {
                "reference": item["reference"],
                "verified_files": verified,
                "expected_files": len(expected),
                "reassembled_transport_parts": omitted_transport_parts,
            }
        )
    return {
        "status": "passed" if not errors else "failed",
        "checks": checks,
        "errors": errors,
        "scope": "Restored archive and release-file checksums, plus materialized rootfs metadata; not full rootfs-tree or runtime qualification.",
    }


def verify_source(doc: dict, checkout: Path) -> None:
    if git(checkout, "rev-parse", "HEAD") != doc["source"]["commit"]:
        raise ValueError("checkout commit does not match candidate")
    if git(checkout, "rev-parse", "HEAD^{tree}") != doc["source"]["tree"]:
        raise ValueError("checkout tree does not match candidate")
    if git(checkout, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("candidate checkout must be clean")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("freeze")
    p.add_argument("--source", required=True)
    p.add_argument("--name", required=True)
    p.add_argument(
        "--inventory",
        type=Path,
        default=REPO / "tools/publish/published-artifacts.json",
    )
    p.add_argument(
        "--policy", type=Path, default=Path(__file__).with_name("preview-policy.json")
    )
    p.add_argument("--out", type=Path, required=True)
    p = commands.add_parser("verify")
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--candidate-sha256", required=True)
    p.add_argument("--checkout", type=Path, required=True)
    p.add_argument("--estate-root", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "freeze":
            doc = freeze(REPO, args.source, args.inventory, args.policy, args.name)
            print(
                json.dumps({"candidate": args.name, "sha256": write_new(args.out, doc)})
            )
            return 0
        if sha256(args.candidate) != args.candidate_sha256:
            raise ValueError("candidate checksum mismatch")
        doc = json.loads(args.candidate.read_text())
        validate(doc)
        verify_source(doc, args.checkout)
        report = verify_payloads(doc, args.estate_root)
        report.update(candidate_sha256=args.candidate_sha256, source=doc["source"])
        write_new(args.out, report)
        print(json.dumps({"status": report["status"], "errors": len(report["errors"])}))
        return 0 if report["status"] == "passed" else 1
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"candidate: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
