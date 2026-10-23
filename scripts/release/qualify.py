#!/usr/bin/env python3
"""Execute the approved preview matrix with retained attempts and GPU monitoring.

A successful attempt is not promotion: native proof, restore proof, and
disposition of failures within the supported scope remain required evidence.
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
import signal
import uuid

import candidate
import gpu_monitor as gpu
import qualification_policy as policy
from evidence import replace_json
from process_runner import run_command


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def verify_reuse(doc: dict, previous: Path, attempt: Path, checkout: Path) -> dict:
    """Link retained evidence across a policy revision without rewriting a verdict."""
    old = json.loads(previous.read_text())
    candidate.validate(old)
    summary = json.loads((attempt / "summary.json").read_text())
    if candidate.sha256(previous) != summary["candidate_sha256"]:
        raise ValueError("attempt does not match its original candidate")
    if summary["status"] != "passed-attempt":
        raise ValueError(
            "only a completed passing attempt can satisfy the revised scope"
        )
    for field in ("source", "components", "selection_sha256"):
        if old.get(field) != doc.get(field):
            raise ValueError(f"evidence identity changed: {field}")
    index = summary.get("gpu_index")
    plan = policy.make_plan(doc, checkout, index)
    if plan != policy.make_plan(old, checkout, index) or plan != json.loads(
        (attempt / "plan.json").read_text()
    ):
        raise ValueError("execution plan or acceptance criteria changed")
    if len(summary["stages"]) != len(plan):
        raise ValueError("attempt has missing stages")
    hashes = {
        name: candidate.sha256(attempt / name)
        for name in ("summary.json", "plan.json", "payload-verification.json")
    }
    if (
        json.loads((attempt / "payload-verification.json").read_text())["status"]
        != "passed"
    ):
        raise ValueError("attempt payload verification failed")
    previous_caches = {}
    for stage, result in zip(plan, summary["stages"]):
        directory = attempt / stage["name"]
        retained = json.loads((directory / "result.json").read_text())
        if (
            result != retained
            or result["stage"] != stage
            or result["status"] != "passed"
            or result["returncode"] != 0
        ):
            raise ValueError("retained stage result differs or failed")
        before = json.loads((directory / "cache-before.json").read_text())
        after = json.loads((directory / "cache-after.json").read_text())
        if stage["reuse"] and (
            not before or before != previous_caches.get(stage["cache_group"])
        ):
            raise ValueError("retained cache reuse is unverified")
        previous_caches[stage["cache_group"]] = after
        for path in directory.rglob("*"):
            if path.is_file():
                path.resolve().relative_to(attempt.resolve())
                hashes[str(path.relative_to(attempt))] = candidate.sha256(path)
    return {
        "status": "compatible-runtime-evidence",
        "original_candidate_sha256": summary["candidate_sha256"],
        "scope": summary["scope"],
        "gpu_index": index,
        "coverage_limits": summary.get("coverage_limits", []),
        "stages": len(plan),
        "evidence_sha256": hashes,
        "promotion": "not evaluated",
        "original_attempt_preserved": True,
    }


def cache_inventory(run: Path) -> dict:
    result = {}
    for path in run.rglob("*"):
        rel = path.relative_to(run)
        if (
            path.is_file()
            and not path.is_symlink()
            and any("cache" in part.lower() for part in rel.parts)
            and "venv" not in rel.parts
        ):
            result[str(rel)] = candidate.sha256(path)
    return result


def compose(doc: dict, estate: Path, output: Path, line: str) -> Path:
    """Compose pinned wheels; JAX NumPy carries its own OpenBLAS dependency."""
    output.mkdir(parents=True)
    wheels = output / "wheels"
    wheels.mkdir()
    selected = {}
    # A single venv must support either import order. Torch's NumPy wheel
    # relies on Torch loading OpenBLAS; JAX's same-version wheel vendors it.
    for profile in ("jax", "torch"):
        item = next(
            c
            for c in doc["components"]
            if c["kind"] == "release" and c["line"] == line and c["profile"] == profile
        )
        root = candidate.component_dir(estate, item)
        for rel, digest in sorted(item["files"].items()):
            if not rel.endswith(".whl"):
                continue
            name = Path(rel).name
            distribution = name.split("-")[0].lower().replace("_", "-")
            if distribution in selected:
                previous_name, previous_digest = selected[distribution]
                if (name, digest) != (
                    previous_name,
                    previous_digest,
                ) and distribution != "numpy":
                    raise ValueError(f"profile wheel conflict: {distribution}")
                if distribution == "numpy" and name != previous_name:
                    raise ValueError("co-residence NumPy versions differ")
                continue
            # Copy: derived staging must never mutate a candidate input via a hard link.
            shutil.copy2(root / rel, wheels / name)
            if candidate.sha256(wheels / name) != digest:
                raise ValueError("composition input changed")
            selected[distribution] = (name, digest)
    requirements = "".join(
        f"/run/vaso/release/wheels/{name} --hash=sha256:{digest}\n"
        for name, digest in selected.values()
    )
    (output / "requirements.txt").write_text(requirements)
    sums = "".join(f"{digest}  wheels/{name}\n" for name, digest in selected.values())
    sums += f"{candidate.sha256(output / 'requirements.txt')}  requirements.txt\n"
    (output / "SHA256SUMS").write_text(sums)
    return output


def retain_results(run: Path, output: Path) -> None:
    output.mkdir()
    for parent in ("workloads", "gates"):
        for path in (run / parent).rglob("*.json"):
            rel = path.relative_to(run)
            if any("cache" in part.lower() for part in rel.parts):
                continue
            target = output / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidate", required=True, type=Path)
    p.add_argument("--candidate-sha256", required=True)
    p.add_argument("--checkout", required=True, type=Path)
    p.add_argument("--estate-root", required=True, type=Path)
    p.add_argument(
        "--out",
        required=True,
        type=Path,
        help="new attempt directory; never overwritten",
    )
    p.add_argument(
        "--reservation", type=Path, help="private exclusive reservation record"
    )
    p.add_argument("--execute", action="store_true")
    p.add_argument(
        "--gpu-index",
        type=int,
        help="physical device for single-GPU qualification; multi-GPU paths remain unqualified",
    )
    p.add_argument(
        "--reuse-attempt",
        type=Path,
        help="verify completed evidence against a policy-only candidate revision",
    )
    p.add_argument(
        "--evidence-candidate",
        type=Path,
        help="unchanged original candidate document for --reuse-attempt",
    )
    p.add_argument("--stage-timeout", type=float, default=10800)
    args = p.parse_args(argv)
    if not math.isfinite(args.stage_timeout) or args.stage_timeout <= 0:
        p.error("--stage-timeout must be finite and positive")
    if bool(args.reuse_attempt) != bool(args.evidence_candidate) or (
        args.reuse_attempt and args.execute
    ):
        p.error(
            "--reuse-attempt requires --evidence-candidate and cannot execute workloads"
        )
    if args.gpu_index is not None and args.gpu_index < 0:
        p.error("--gpu-index must be nonnegative")
    for name in ("candidate", "checkout", "estate_root", "out"):
        setattr(args, name, getattr(args, name).resolve())

    def interrupted(_signum, _frame):
        raise KeyboardInterrupt("qualification interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    args.out.mkdir(parents=True, exist_ok=False)
    summary = {
        "schema_version": 1,
        "candidate_sha256": args.candidate_sha256,
        "started_at": now(),
        "status": "blocked-prerequisite",
        "stages": [],
        "promotion": "not evaluated",
    }
    summary.update(
        scope="single-gpu" if args.gpu_index is not None else "eight-gpu",
        gpu_index=args.gpu_index,
        qualification_complete=False,
        runner_sha256=candidate.sha256(Path(__file__)),
        candidate_tool_sha256=candidate.sha256(Path(candidate.__file__)),
        runner_files_sha256={
            name: candidate.sha256(Path(__file__).with_name(name))
            for name in (
                "qualify.py",
                "candidate.py",
                "evidence.py",
                "qualification_policy.py",
                "gpu_monitor.py",
                "process_runner.py",
            )
        },
    )
    if args.gpu_index is not None:
        summary["coverage_limits"] = [
            "multi-GPU workloads and gates omitted as listed in plan",
            "CUDA 13 W5b cold/warm diagnostics not run",
            "cooperative single-device lease; no exclusive host reservation",
        ]
    rc = 2
    try:
        if candidate.sha256(args.candidate) != args.candidate_sha256:
            raise ValueError("candidate checksum mismatch")
        doc = json.loads(args.candidate.read_text())
        candidate.validate(doc)
        candidate.verify_source(doc, args.checkout)
        if args.reuse_attempt:
            reuse = verify_reuse(
                doc, args.evidence_candidate, args.reuse_attempt, args.checkout
            )
            candidate.write_new(args.out / "reuse-verification.json", reuse)
            summary.update(
                status=reuse["status"],
                scope=reuse["scope"],
                gpu_index=reuse["gpu_index"],
                coverage_limits=reuse["coverage_limits"],
                original_attempt_sha256=candidate.sha256(
                    args.reuse_attempt / "summary.json"
                ),
            )
            return 0
        plan = policy.make_plan(doc, args.checkout, args.gpu_index)
        candidate.write_new(args.out / "plan.json", plan)
        reservation = (
            json.loads(args.reservation.read_text()) if args.reservation else None
        )
        observed = gpu.gpu_snapshot()
        candidate.write_new(args.out / "gpu-preflight.json", observed)
        blockers = gpu.resource_blockers(observed, reservation, args.gpu_index)
        summary["blockers"] = blockers
        if blockers:
            return 2
        if not args.execute:
            summary["status"] = "preflight-passed"
            return 0
        # Cooperative lock supplements the declared physical reservation and live monitoring.
        (args.estate_root / "agents").mkdir(parents=True, exist_ok=True)
        indices = (
            [args.gpu_index]
            if args.gpu_index is not None
            else [d["index"] for d in observed["devices"]]
        )
        with (
            (args.estate_root / "agents" / "qualification.lock").open("w") as lock,
            gpu.gpu_lease(
                args.estate_root, indices, len(plan) * args.stage_timeout + 600
            ) as lease,
        ):
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            candidate.write_new(args.out / "gpu-lease.json", lease)
            payload = candidate.verify_payloads(doc, args.estate_root)
            candidate.write_new(args.out / "payload-verification.json", payload)
            if payload["status"] != "passed":
                summary["blockers"] = ["candidate payload verification failed"]
                return 2
            summary["status"] = "running"
            groups = {}
            group_inventories = {}
            composed = {}
            for stage in plan:
                candidate.verify_source(doc, args.checkout)
                blockers = gpu.resource_blockers(
                    gpu.gpu_snapshot(), reservation, args.gpu_index
                )
                if blockers:
                    summary.update(status="blocked-prerequisite", blockers=blockers)
                    return 2
                group = stage["cache_group"]
                if group not in groups:
                    groups[group] = f"qualification-{uuid.uuid4().hex}"
                run = args.estate_root / "run" / groups[group]
                stage_out = args.out / stage["name"]
                stage_out.mkdir()
                before = cache_inventory(run) if run.exists() else {}
                if stage["reuse"] and (
                    not before or before != group_inventories.get(group)
                ):
                    summary.update(
                        status="blocked-prerequisite",
                        blockers=["requested cache reuse was not established"],
                    )
                    return 2
                candidate.write_new(stage_out / "cache-before.json", before)
                if stage["profile"] == "coresidence":
                    if stage["line"] not in composed:
                        composed[stage["line"]] = compose(
                            doc,
                            args.estate_root,
                            args.out / ("composed-" + stage["line"]),
                            stage["line"],
                        )
                    release = composed[stage["line"]]
                else:
                    item = next(
                        c
                        for c in doc["components"]
                        if c["kind"] == "release"
                        and c["line"] == stage["line"]
                        and c["profile"] == stage["profile"]
                    )
                    release = candidate.component_dir(args.estate_root, item)
                # No inherited interpreter, device selection, or native-prefix overrides.
                env = {
                    k: v
                    for k, v in os.environ.items()
                    if not k.startswith(
                        ("VASO_", "INSULA_", "CUDA_", "JAX_", "XLA_", "PYTHON", "LD_")
                    )
                }
                env.update(
                    VASO_ESTATE_ROOT=str(args.estate_root),
                    VASO_AGENT="release-qualification",
                    INSULA_RELEASE_RUN_ID=groups[group],
                    INSULA_RELEASE_REUSE_RUN_DIR="1" if stage["reuse"] else "0",
                )
                env.update(VASO_LEASES_HELD="gpu:" + lease["id"], **lease["env"])
                state = {
                    "status": "running",
                    "stage": stage["name"],
                    "pid": os.getpid(),
                    "candidate_sha256": args.candidate_sha256,
                    "updated_at": now(),
                }
                replace_json(args.out / "state.json", state)
                result = run_command(
                    policy.stage_command(stage, args.checkout, release),
                    env,
                    stage_out / "stage.log",
                    args.stage_timeout,
                    monitor=gpu.Monitor(
                        stage_out / "gpu-observations.jsonl",
                        reservation,
                        args.gpu_index,
                    ),
                )
                retain_results(run, stage_out / "results")
                after = cache_inventory(run)
                group_inventories[group] = after
                candidate.write_new(stage_out / "cache-after.json", after)
                result.update(
                    stage=stage,
                    cache_before_files=len(before),
                    cache_after_files=len(after),
                )
                candidate.write_new(stage_out / "result.json", result)
                summary["stages"].append(result)
                with (args.out / "events.jsonl").open("a") as stream:
                    stream.write(json.dumps(result) + "\n")
                print(f"{stage['name']}: {result['status']}", flush=True)
                if result["status"] != "passed":
                    summary["status"] = "failed"
                    return 1
            candidate.verify_source(doc, args.checkout)
            summary["status"] = "passed-attempt"
            rc = 0
            return rc
    except (Exception, KeyboardInterrupt) as exc:
        summary.update(
            status="failed" if summary["stages"] else "blocked-prerequisite",
            error=str(exc),
        )
        return 2
    finally:
        summary["finished_at"] = now()
        candidate.write_new(args.out / "summary.json", summary)
        replace_json(
            args.out / "state.json",
            {
                "status": summary["status"],
                "updated_at": now(),
                "summary": "summary.json",
            },
        )
        print(
            json.dumps(
                {
                    "status": summary["status"],
                    "completed_stages": len(summary["stages"]),
                    "blockers": summary.get("blockers", []),
                }
            )
        )


if __name__ == "__main__":
    raise SystemExit(main())
