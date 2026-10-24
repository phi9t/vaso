#!/usr/bin/env python3
"""Restore a pinned candidate with existing acquisition and insula entrypoints.

Use a new estate. Credentials and bootstrap tools may already be provisioned;
downloads, rootfs lines, and runtime state must not exist. Failed attempts are
retained. This verifies restoration and generic execution, not GPU qualification.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import shutil
import sys

import candidate
from evidence import replace_json
from process_runner import interrupt_on_sigterm, run_command


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def restoration_plan(doc: dict, checkout: Path, estate: Path) -> list[dict]:
    candidate.validate(doc)
    stages = [
        {
            "name": "acquisition-tool",
            "command": [str(checkout / "tools/publish/install_oras.sh")],
        }
    ]
    for item in doc["components"]:
        repository, digest = item["reference"].split("@", 1)
        registry, package = repository.rsplit("/", 1)
        expected = "vaso-" + candidate.component_name(item)
        if package != expected:
            raise ValueError(f"component reference must name {expected}")
        command = [
            sys.executable,
            str(checkout / "tools/publish/artifact_store.py"),
            "pull",
            "--artifact",
            item["kind"],
            "--line",
            item["line"],
            "--registry",
            registry,
            "--digest",
            digest,
            "--target-dir",
            str(candidate.component_dir(estate, item)),
            "--json",
        ]
        if item.get("profile"):
            command += ["--profile", item["profile"]]
        stages.append({"name": "pull-" + package, "command": command})
    for item in doc["components"]:
        if item["kind"] != "rootfs":
            continue
        stages.append(
            {
                "name": "materialize-" + item["line"],
                "command": [
                    str(checkout / "insula/rootfs/prepare.sh"),
                    "restore",
                    "--bundle",
                    str(candidate.component_dir(estate, item) / item["archive"]),
                    "--digest",
                    item["archive_sha256"],
                    "--line",
                    item["line"],
                    "--estate-root",
                    str(estate),
                ],
            }
        )
    return stages


def prerequisites(estate: Path, minimum_free_bytes: int) -> None:
    for name in ("downloads", "rootfs-lines", "vaso", "run", "home", "sources"):
        if os.path.lexists(estate / name):
            raise ValueError(
                f"fresh estate required: {name} already exists; use a new estate and retain the previous attempt"
            )
    missing = [
        tool
        for tool in ("bash", "git", "curl", "tar", "zstd", "sha256sum", "bwrap")
        if not shutil.which(tool)
    ]
    if missing:
        raise ValueError("install host bootstrap tools: " + ", ".join(missing))
    if not os.environ.get("GHCR_TOKEN"):
        token = estate / "publish/ghcr-token"
        if (
            not token.is_file()
            or token.stat().st_mode & 0o777 != 0o600
            or not token.read_text().strip()
        ):
            raise ValueError(
                "set GHCR_TOKEN or provision publish/ghcr-token with mode 600 before restoring"
            )
    free = shutil.disk_usage(estate).free
    if free < minimum_free_bytes:
        raise ValueError(
            f"insufficient restore space: {free} bytes free, {minimum_free_bytes} required"
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--candidate-sha256", required=True)
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--estate-root", required=True, type=Path)
    parser.add_argument(
        "--out", required=True, type=Path, help="new private evidence directory"
    )
    parser.add_argument("--stage-timeout", type=float, default=3600)
    parser.add_argument("--min-free-gib", type=float, default=100)
    args = parser.parse_args(argv)
    for value in (args.stage_timeout, args.min_free_gib):
        if not math.isfinite(value) or value <= 0:
            parser.error(
                "deadlines and storage requirements must be finite and positive"
            )
    for name in ("candidate", "checkout", "estate_root", "out"):
        setattr(args, name, getattr(args, name).resolve())

    with interrupt_on_sigterm("restore interrupted"):
        return _run_attempt(args)


def _run_attempt(args: argparse.Namespace) -> int:
    """Own one evidence directory and retain the outcome of its execution."""
    try:
        args.out.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        print(f"cannot create attempt directory {args.out}: {exc}", file=sys.stderr)
        return 2
    summary = {
        "schema_version": 1,
        "candidate_sha256": args.candidate_sha256,
        "started_at": now(),
        "status": "blocked-prerequisite",
        "stages": [],
        "runner_sha256": candidate.sha256(Path(__file__)),
        "runner_files_sha256": {
            name: candidate.sha256(Path(__file__).with_name(name))
            for name in ("restore.py", "candidate.py", "evidence.py", "process_runner.py")
        },
        "qualification": "not performed",
    }

    def save_state():
        replace_json(args.out / "state.json", summary)

    try:
        doc = candidate.load_verified(args.candidate, args.candidate_sha256)
        candidate.verify_source(doc, args.checkout)
        plan = restoration_plan(doc, args.checkout, args.estate_root)
        candidate.write_new(args.out / "plan.json", plan)
        args.estate_root.mkdir(parents=True, exist_ok=True)
        with (args.estate_root / ".restore.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            prerequisites(args.estate_root, int(args.min_free_gib * 1024**3))
            env = {
                k: v
                for k, v in os.environ.items()
                if not k.startswith(("VASO_", "INSULA_", "CUDA_", "PYTHON", "LD_"))
            }
            env.update(
                VASO_ESTATE_ROOT=str(args.estate_root),
                VASO_AGENT="candidate-restore",
                TMPDIR=str(args.estate_root / "bootstrap-tmp"),
                ORAS_BIN=str(args.estate_root / "opt-vaso/bin/oras"),
            )
            Path(env["TMPDIR"]).mkdir(exist_ok=True)
            probes = [
                {
                    "name": "enter-" + line,
                    "command": [
                        str(args.checkout / "insula/run.sh"),
                        "--line",
                        line,
                        "--workspace",
                        str(args.checkout),
                        "--",
                        "/bin/sh",
                        "-c",
                        'set -eu; test -x /bin/sh; printf "insula rootfs execution passed\\n"; pwd',
                    ],
                }
                for line in ("cu129", "cu130")
            ]
            for stage in plan + [{"name": "verify-payloads"}] + probes:
                candidate.verify_source(doc, args.checkout)
                record = {
                    "name": stage["name"],
                    "started_at": now(),
                    "status": "running",
                }
                summary.update(status="running", current_stage=stage["name"])
                summary["stages"].append(record)
                save_state()
                if stage["name"] == "verify-payloads":
                    report = candidate.verify_payloads(doc, args.estate_root)
                    candidate.write_new(args.out / "payload-verification.json", report)
                    record.update(
                        returncode=0 if report["status"] == "passed" else 1,
                        status=report["status"],
                        finished_at=now(),
                    )
                else:
                    record.update(
                        run_command(
                            stage["command"],
                            env,
                            args.out / (stage["name"] + ".log"),
                            args.stage_timeout,
                        )
                    )
                save_state()
                print(f"{stage['name']}: {record['status']}", flush=True)
                if record["status"] != "passed":
                    summary.update(
                        status="failed",
                        recovery="inspect the failed stage log; retain this estate and retry in a new estate",
                    )
                    return 1
            candidate.verify_source(doc, args.checkout)
            summary["status"] = "restored-and-entered"
            return 0
    except (Exception, KeyboardInterrupt) as exc:
        if summary["stages"] and summary["stages"][-1]["status"] == "running":
            summary["stages"][-1].update(
                status="failed", error=str(exc), finished_at=now()
            )
        summary.update(
            status="failed" if summary["stages"] else "blocked-prerequisite",
            error=str(exc),
        )
        return 2
    finally:
        summary["finished_at"] = now()
        candidate.write_new(args.out / "summary.json", summary)
        save_state()
        print(
            json.dumps({"status": summary["status"], "stages": len(summary["stages"])}),
            flush=True,
        )


if __name__ == "__main__":
    raise SystemExit(main())
