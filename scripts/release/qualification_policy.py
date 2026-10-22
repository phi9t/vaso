"""Supported qualification recipes derived from a candidate and its pinned source.

This module selects coverage and commands. It neither acquires resources nor
executes workloads; the CLI owns the attempt lifecycle.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import candidate


def make_plan(doc: dict, checkout: Path, gpu_index: int | None = None) -> list[dict]:
    candidate.validate(doc)
    policy = doc["qualification_policy"]
    hardware = policy.get("hardware", {})
    if (
        hardware.get("gpu_count") not in (1, 8)
        or hardware.get("gpu_model") != "NVIDIA B200"
        or hardware.get("os") != "Linux"
        or hardware.get("arch") != "x86_64"
    ):
        raise ValueError("unsupported qualification hardware policy")
    for field in (
        "retain_all_attempts",
        "unexplained_failures_block_promotion",
        "sample_occupancy_during_execution",
        "co_residence_and_consumers_required",
    ):
        if policy.get(field) is not True:
            raise ValueError(f"unsupported qualification policy: {field}")
    if policy.get("launched_mpi_supported") is not False:
        raise ValueError("launched MPI is outside the supported policy")
    matrix = policy.get("matrix", [])
    if (
        len(matrix) != 4
        or {(r["line"], r["profile"]) for r in matrix} != candidate.MATRIX
    ):
        raise ValueError("unsupported qualification matrix policy")
    if hardware["gpu_count"] == 8:
        diagnostics = {
            "line": "cu130",
            "workload": "W5b",
            "isolated_fresh_process_cold_cache_attempts": 3,
            "isolated_fresh_process_reused_cache_attempts": 3,
            "full_profile_cold_cache_attempts": 1,
            "full_profile_reused_cache_attempts": 1,
            "stop_on_uncontaminated_timeout_recurrence": True,
        }
        if (
            policy.get("jax_diagnostics") != diagnostics
            or policy.get("exclusive_physical_gpu_reservation_required") is not True
        ):
            raise ValueError("unsupported eight-GPU diagnostic or reservation policy")
    elif (
        policy.get("supported_runtime_scope") != "single-gpu"
        or policy.get("selected_device_cooperative_lease_and_occupancy_checks_required")
        is not True
        or policy.get("jax_diagnostics", {}).get("status")
        != "deferred-with-multi-gpu-support"
    ):
        raise ValueError("unsupported single-GPU qualification policy")
    if policy.get("hardware", {}).get("gpu_count") == 1 and gpu_index is None:
        raise ValueError("single-GPU candidate requires an explicit --gpu-index")
    if policy.get("rounds") != [
        {"id": 1, "cache": "fresh"},
        {"id": 2, "cache": "verified-reuse"},
        {"id": 3, "cache": "verified-reuse"},
    ]:
        raise ValueError("runner requires the approved three-round policy")
    historical = json.loads(
        (checkout / "tools/publish/multigpu-qualification.json").read_text()
    )
    rows = {(r["line"], r["profile"]): r for r in historical["runs"]}
    if set(rows) != candidate.MATRIX:
        raise ValueError("incomplete qualification matrix recipe")
    stages = []

    def add(name, line, profile, cache_group, reuse, only=None):
        row = rows[(line, profile)] if profile != "coresidence" else None
        stages.append(
            {
                "name": name,
                "line": line,
                "profile": profile,
                "cache_group": cache_group,
                "reuse": reuse,
                "workloads": only or [w["id"] for w in row["workloads"]],
                "gates": [g["name"] for g in row["gates"]] if row and not only else [],
                "consumer": row["consumer"] if row and not only else None,
            }
        )

    for n in range(3):
        add(
            f"jax-diagnostic-cold-{n + 1}",
            "cu130",
            "jax",
            f"jax-cold-{n + 1}",
            False,
            ["W5b"],
        )
    for n in range(3):
        add(
            f"jax-diagnostic-reuse-{n + 1}", "cu130", "jax", "jax-cold-1", True, ["W5b"]
        )
    add("jax-profile-cold", "cu130", "jax", "jax-profile", False)
    add("jax-profile-reuse", "cu130", "jax", "jax-profile", True)
    for round_number in (1, 2, 3):
        for line in ("cu129", "cu130"):
            for profile in ("torch", "jax"):
                add(
                    f"round-{round_number}-{line}-{profile}",
                    line,
                    profile,
                    f"{line}-{profile}",
                    round_number > 1,
                )
            add(
                f"round-{round_number}-{line}-coresidence",
                line,
                "coresidence",
                f"{line}-coresidence",
                round_number > 1,
                ["W4"],
            )
    if gpu_index is not None:
        # Read resource declarations from the pinned source without importing it.
        tree = ast.parse((checkout / "foundry/workloads/run_workloads.py").read_text())
        workloads = next(
            node.value
            for node in tree.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "WORKLOADS"
        )
        minimum = {
            ast.literal_eval(key): ast.literal_eval(value.args[2])
            for key, value in zip(workloads.keys, workloads.values)
        }
        stages = [stage for stage in stages if stage["name"].startswith("round-")]
        for stage in stages:
            stage["omitted_workloads"] = [
                w for w in stage["workloads"] if minimum[w] > 1
            ]
            stage["workloads"] = [w for w in stage["workloads"] if minimum[w] <= 1]
            # Gloo uses CPU tensors with two processes and remains covered.
            stage["omitted_gates"] = [
                g for g in stage["gates"] if g in ("nccl_distributed", "jax_psum_2gpu")
            ]
            stage["gates"] = [
                g for g in stage["gates"] if g not in stage["omitted_gates"]
            ]
    for stage in stages:
        stage["gpu_count"] = 1 if gpu_index is not None else 8
    return stages


def stage_command(stage: dict, checkout: Path, release: Path) -> list[str]:
    import shlex

    base = [
        "python3",
        "-m",
        "workloads.run_workloads",
        "--from-release",
        "--line",
        stage["line"],
        "--gpus",
        str(stage["gpu_count"]),
        "--out",
        "/run/vaso/run/workloads",
        "--only",
        ",".join(stage["workloads"]),
    ]
    commands = [shlex.join(base)]
    if stage["gates"]:
        args = [
            "python3",
            "native/pytorch/gates.py",
            "--from-release",
            "--out-dir",
            "/run/vaso/run/gates",
            "--results-out",
            "/run/vaso/run/gates/results.json",
            "--only",
            ",".join(stage["gates"]),
            "--execute",
        ]
        for group in (
            ["core", "compile", "vision_audio"]
            if stage["profile"] == "torch"
            else ["jax"]
        ):
            args += ["--block-group", group]
        commands.append(shlex.join(args))
    if stage["consumer"]:
        commands.append(
            shlex.join(
                [
                    "env",
                    "PYTHONPATH=/workspace/experiment/examples/consumers:/workspace/experiment",
                    "python3",
                    "-m",
                    stage["consumer"],
                ]
            )
        )
    return [
        str(checkout / "foundry/insula.sh"),
        "--line",
        stage["line"],
        "--release",
        str(release),
        "--gpus",
        str(stage["gpu_count"]),
        "--",
        "/bin/bash",
        "-c",
        "set -euo pipefail\n" + "\n".join(commands),
    ]
