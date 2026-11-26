#!/usr/bin/env python3
"""Run repeated packed Inkling qualification in an immutable Insula capsule."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import traceback

from run_payload import Cancelled, SignalRelay

HERE = Path(__file__).resolve().parent
QUALIFICATION = "/opt/vaso/pascal/qualification"


def atomic_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def run_request(signals):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--insula", type=Path, required=True)
    p.add_argument("--supervisor", type=Path, required=True)
    p.add_argument("--bundle-store", type=Path, required=True)
    p.add_argument("--bundle-digest", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--lease-cli", type=Path, required=True)
    p.add_argument("--lease-estate", type=Path, required=True)
    p.add_argument("--gpus", required=True, help="Exact physical GPU indices, comma separated")
    p.add_argument("--checkpoint", type=Path, help="Existing pinned tiny checkpoint; copied as data")
    p.add_argument("--repeat", type=int, default=3)
    p.add_argument("--new-tokens", type=int, default=8)
    p.add_argument("--cache-mib", type=int, default=64)
    p.add_argument("--archive-mib", type=int, default=0,
                   help="Optional packed archive; requires exactly two admitted GPUs")
    p.add_argument("--payload-timeout", type=int, default=7200)
    a = p.parse_args()
    if a.repeat < 2 or a.new_tokens < 2 or a.cache_mib < 1:
        p.error("use at least two independent processes, two tokens, and one MiB cache")
    if a.archive_mib < 0:
        p.error("--archive-mib must be nonnegative")
    try:
        indices = [int(v) for v in a.gpus.split(",")]
    except ValueError:
        p.error("--gpus must contain integer indices")
    if not indices or min(indices) < 0 or len(set(indices)) != len(indices):
        p.error("--gpus must select distinct nonnegative physical indices")
    if a.archive_mib and len(indices) != 2:
        p.error("--archive-mib requires exactly two physical GPUs in the admitted set")
    if not a.output.is_absolute():
        p.error("--output must be absolute")
    if len(a.bundle_digest) != 64 or any(c not in "0123456789abcdef" for c in a.bundle_digest):
        p.error("--bundle-digest must be the exact lowercase generation key")
    for path in (a.insula, a.supervisor, a.bundle_store, a.lease_cli, a.lease_estate):
        path.resolve(strict=True)
    rootfs = a.bundle_store / "content" / a.bundle_digest / "rootfs"
    for name in ("packed_checkpoint.py", "packed_experts.py", "inkling_packed_inference.py",
                 "packed_experts_probe.py", "inkling_packed_generate.py"):
        (rootfs / QUALIFICATION.lstrip("/") / name).resolve(strict=True)

    a.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    estate = a.output / "estate"
    estate.mkdir(mode=0o700)
    workspace = estate / "workspace"
    workspace.mkdir(mode=0o700)
    (workspace / "receipts").mkdir(mode=0o700)
    control = a.output / "control"
    control.mkdir(mode=0o700)
    shutil.copy2(HERE / "run_payload.py", control / "run_payload.py")
    shutil.copy2(a.supervisor, control / "payload_owner.py")
    state = {
        "passed": False, "state": "preparing", "stages": {},
        "bundle_digest": a.bundle_digest, "bundle_store": str(a.bundle_store),
        "physical_gpu_indices": indices, "dependency_mounts": [],
        "archive_budget_bytes": a.archive_mib * 1024**2,
        "checkpoint_trained": False,
        "scope": "checkpoint-native NVFP4/MXFP8 experts and FP32 text inference",
        "started_utc": datetime.now(timezone.utc).isoformat(),
    }
    def save():
        state["updated_utc"] = datetime.now(timezone.utc).isoformat()
        atomic_json(a.output / "status.json", state)
    save()
    try:
        # UUID selection remains correct when the device namespace exposes only
        # a subset of physical GPUs. This runtime identity stays in private evidence.
        uuid_text = subprocess.check_output(
            ["nvidia-smi", "--id=" + ",".join(map(str, indices)),
             "--query-gpu=uuid", "--format=csv,noheader"], text=True, timeout=10)
        uuids = [line.strip() for line in uuid_text.splitlines() if line.strip()]
        if len(uuids) != len(indices) or any(not v.startswith("GPU-") for v in uuids):
            raise RuntimeError("Could not resolve the selected physical GPU UUIDs")
        common = [
            "version = 1", "bundle_store = " + json.dumps(str(a.bundle_store)),
            "bundle_digest = " + json.dumps(a.bundle_digest),
            "estate = " + json.dumps(str(estate)), 'host_bwrap = "/usr/bin/bwrap"',
            'network = "host"',
        ]
        (a.output / "cpu.toml").write_text("\n".join(common + ["devices = []"]) + "\n")
        (a.output / "gpu.toml").write_text("\n".join(common + ["gpus = " + json.dumps(indices)]) + "\n")
        base = [sys.executable, str(control / "run_payload.py"), "--insula", str(a.insula),
                "--supervisor", str(control / "payload_owner.py"),
                "--timeout", str(a.payload_timeout)]
        def stage(name, command, *, gpu=False, result=None):
            state["state"] = name
            state["stages"][name] = {"state": "running"}
            save()
            print(json.dumps({"stage": name, "state": "starting"}), flush=True)
            output = a.output / "runs" / name
            argv = base + ["--spec", str(a.output / ("gpu.toml" if gpu else "cpu.toml")),
                           "--output", str(output)]
            if gpu:
                argv += ["--gpu", "--lease-cli", str(a.lease_cli),
                         "--lease-estate", str(a.lease_estate)]
                command = ["/usr/bin/env", "CUDA_VISIBLE_DEVICES=" + ",".join(uuids), *command]
            rc = signals.run(argv + ["--", *command])
            status_path = output / "status.json"
            status = json.loads(status_path.read_text()) if status_path.is_file() else {}
            state["stages"][name] = status
            save()
            signals.check()
            if rc or status.get("state") != "passed" or status.get("cleanup_complete") is not True:
                raise RuntimeError(f"Stage {name} failed; see {output}")
            if gpu and status.get("lease_released") is not True:
                raise RuntimeError(f"Stage {name} did not release its selected GPU lease")
            data = None
            if result:
                data = json.loads((workspace / "receipts" / result).read_text())
                if data.get("passed") is not True:
                    raise RuntimeError(f"Stage {name} did not produce passing evidence")
                status["result"] = str(workspace / "receipts" / result)
            save()
            return data

        signals.check()
        stage("capsule-probe", ["/bin/bash", "/opt/vaso/pascal/bin/probe"])
        checkpoint = workspace / "checkpoints" / "tiny-inkling2-mini"
        if a.checkpoint:
            shutil.copytree(a.checkpoint.resolve(strict=True), checkpoint)
        else:
            stage("acquire-checkpoint", ["/bin/bash", "/opt/vaso/pascal/bin/inference-python",
                  QUALIFICATION + "/inkling_acquire_tiny.py",
                  "--output", "/workspace/checkpoints/tiny-inkling2-mini"])
        operator = stage(
            "packed-operators", ["/bin/bash", "/opt/vaso/pascal/bin/inference-python",
                                 QUALIFICATION + "/packed_experts_probe.py",
                                 "--output", "/workspace/receipts/operators.json", "--time-production"],
            gpu=True, result="operators.json")
        reports = []
        archive_args = ["--archive-mib", str(a.archive_mib)] if a.archive_mib else []
        for index in range(1, a.repeat + 1):
            name = f"packed-inference-{index:02d}"
            result_name = name + ".json"
            reports.append(stage(
                name, ["/bin/bash", "/opt/vaso/pascal/bin/inference-python",
                       QUALIFICATION + "/inkling_packed_inference.py",
                       "--checkpoint", "/workspace/checkpoints/tiny-inkling2-mini",
                       "--result", "/workspace/receipts/" + result_name,
                       "--process-label", str(index), "--new-tokens", str(a.new_tokens),
                       "--cache-mib", str(a.cache_mib), *archive_args],
                gpu=True, result=result_name))
        def tokens(report):
            return {(row["mode"], row["gpu"], row["repeat"], row["case"], row["use_cache"]):
                    row["new_token_ids"] for row in report["runs"]}
        expected = tokens(reports[0])
        if any(tokens(report) != expected for report in reports[1:]):
            raise AssertionError("Generation tokens changed across independent processes")
        if a.archive_mib:
            for report in reports:
                archive = report["archive_qualification"]
                if (archive["requested_budget_bytes"] != a.archive_mib * 1024**2
                        or set(archive["compute_roles"]) != {0, 1}
                        or report.get("all_store_closes_confirmed") is not True
                        or not all(archive[key] is True for key in (
                            "priority_derived_from_model_loop", "mixed_archive_cpu_source_coverage",
                            "clear_cache_preserved_archive", "both_device_stores_released"))):
                    raise AssertionError("Archive qualification did not cover both admitted device roles")
        demo_archive_args = (
            archive_args + ["--device", "cuda:0", "--archive-device", "cuda:1"]
            if a.archive_mib else [])
        demo = stage(
            "packed-generation-entrypoint",
            ["/bin/bash", "/opt/vaso/pascal/bin/inference-python",
             QUALIFICATION + "/inkling_packed_generate.py",
             "--checkpoint", "/workspace/checkpoints/tiny-inkling2-mini",
             "--prompt", "Explain why one plus one equals two.",
             "--max-new-tokens", str(a.new_tokens), "--cache-mib", str(a.cache_mib),
             "--result", "/workspace/receipts/generation-entrypoint.json", *demo_archive_args],
            gpu=True, result="generation-entrypoint.json")
        if a.archive_mib:
            if (demo["archive_budget_bytes"] != a.archive_mib * 1024**2
                    or demo.get("cache_released") is not True
                    or demo.get("archive_released") is not True):
                raise AssertionError("Standalone generation did not release its compute cache and archive")
            archive = demo["residency"]["archive"]
            if (archive["device"] != "cuda:1" or archive["compute_device"] != "cuda:0"
                    or archive["source_hits"] <= 0):
                raise AssertionError("Standalone generation did not use the requested archive role")
        rows = [row for report in reports for row in report["runs"] if row["mode"] == "packed_fp32"]
        summary = {
            "passed": True, "bundle_digest": a.bundle_digest,
            "independent_inference_processes": len(reports),
            "physical_gpu_indices": indices,
            "archive_budget_bytes": a.archive_mib * 1024**2,
            "packed_generations": len(rows),
            "dense_gpu_comparisons": sum(sum(r["mode"] == "dense_fp32" for r in x["runs"]) for x in reports),
            "cpu_reference_generations": sum(len(x["cpu_references"]) for x in reports),
            "forced_eviction_generations": sum(len(x["eviction_runs"]) for x in reports),
            "tokens_match_across_processes": True,
            "packed_max_logit_error_vs_cpu": max(row["vs_cpu"]["max_abs"] for row in rows),
            "packed_max_logit_error_vs_dense_gpu": max(row["vs_dense_gpu"]["max_abs"] for row in rows),
            "raw_expert_storage_bytes": reports[0]["packed_loads"][0]["raw_storage_bytes"],
            "avoided_dense_expert_bytes": reports[0]["packed_loads"][0]["avoided_dense_expert_bytes"],
            "all_cache_clears_confirmed": all(x["all_cache_clears_confirmed"] for x in reports),
            "all_payload_cleanup_complete": all(x.get("cleanup_complete") for x in state["stages"].values()),
            "all_gpu_leases_released": all(x.get("lease_released", True) for x in state["stages"].values()),
            "standalone_generation_entrypoint_passed": demo["passed"],
            "checkpoint_trained": False, "activation_quantization": False,
            "dependency_mounts": [],
        }
        if a.archive_mib:
            summary["archive_qualification"] = {
                "logical_roles": reports[0]["archive_roles"],
                "mixed_source_generations": sum(len(report["eviction_runs"]) for report in reports),
                "both_device_stores_released": True,
                "clear_cache_preserved_archive": True,
                "priority_derived_from_model_loop": True,
                "standalone_compute_logical_id": 0, "standalone_archive_logical_id": 1,
            }
        atomic_json(a.output / "summary.json", summary)
        state.update(passed=True, state="passed", summary=summary)
        save()
        print(json.dumps(summary), flush=True)
        return 0
    except BaseException as error:
        state.update(passed=False, state="failed", error=repr(error), traceback=traceback.format_exc())
        save()
        raise


def main():
    with SignalRelay() as signals:
        try:
            return run_request(signals)
        except Cancelled as error:
            return 128 + error.number


if __name__ == "__main__":
    raise SystemExit(main())
