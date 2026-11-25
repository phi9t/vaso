#!/usr/bin/env python3
"""Run repeated Inkling reference comparisons in an immutable standalone Insula capsule."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import traceback


HERE = Path(__file__).resolve().parent


def write_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--insula", type=Path, required=True)
    p.add_argument("--supervisor", type=Path, required=True,
                   help="Pinned upstream Insula payload_owner.py for finite controlled workloads")
    p.add_argument("--bundle-store", type=Path, required=True)
    p.add_argument("--bundle-digest", required=True)
    p.add_argument("--host-bwrap", type=Path, default=Path("/usr/bin/bwrap"))
    p.add_argument("--output", type=Path, required=True, help="New private run directory")
    p.add_argument("--lease-cli", type=Path, required=True)
    p.add_argument("--lease-estate", type=Path, required=True)
    p.add_argument("--repeat", type=int, default=3)
    p.add_argument("--new-tokens", type=int, default=4)
    p.add_argument("--payload-timeout", type=int, default=7200)
    a = p.parse_args()
    if a.repeat < 2 or a.new_tokens < 2:
        p.error("repeated qualification needs at least two processes and two generated tokens")
    if not a.output.is_absolute():
        p.error("--output must be absolute")
    if len(a.bundle_digest) != 64 or any(c not in "0123456789abcdef" for c in a.bundle_digest):
        p.error("--bundle-digest must be the exact lowercase generation key")
    for path in (a.insula, a.supervisor, a.bundle_store, a.host_bwrap, a.lease_cli, a.lease_estate):
        path.resolve(strict=True)
    bundle = a.bundle_store / "content" / a.bundle_digest
    (bundle / "rootfs/opt/vaso/pascal/qualification/model_triton_adapter.py").resolve(strict=True)
    a.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    state = {"state": "preparing", "passed": False, "stages": {},
             "bundle_digest": a.bundle_digest, "bundle_store": str(a.bundle_store),
             "dependency_mounts": [], "repeat_processes": a.repeat,
             "scope": "FP32 Inkling text inference: reduced random graph and published untrained tiny checkpoint",
             "supervision": "upstream finite external owner; no production service qualification",
             "started_utc": datetime.now(timezone.utc).isoformat()}
    def save():
        state["updated_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(a.output / "status.json", state)
    save()
    try:
        control = a.output / "control"
        control.mkdir(mode=0o700)
        shutil.copy2(HERE / "run_payload.py", control / "run_payload.py")
        shutil.copy2(a.supervisor, control / "payload_owner.py")
        estates = {}
        for kind, files in {
            "reduced": ("inkling_repeated_inference.py",),
            "checkpoint": ("inkling_acquire_tiny.py", "inkling_checkpoint_inference.py"),
        }.items():
            estate = a.output / (kind + "-estate")
            estate.mkdir(mode=0o700)
            workspace = estate / "workspace"
            workspace.mkdir(mode=0o700)
            (workspace / "inputs").mkdir()
            (workspace / "receipts").mkdir()
            shutil.copy2(HERE / "inference-python.sh", workspace / "python.sh")
            for name in files:
                shutil.copy2(HERE / name, workspace / "inputs" / name)
            estates[kind] = workspace
            common = [
                "version = 1",
                "bundle_store = " + json.dumps(str(a.bundle_store)),
                "bundle_digest = " + json.dumps(a.bundle_digest),
                "estate = " + json.dumps(str(estate)),
                "host_bwrap = " + json.dumps(str(a.host_bwrap)),
                'network = "host"',
            ]
            for mode, selection in (("cpu", "devices = []"), ("gpu", "gpus = [0, 1]")):
                (a.output / f"{kind}-{mode}.toml").write_text("\n".join(common + [selection]) + "\n")
        base = [sys.executable, str(control / "run_payload.py"), "--insula", str(a.insula),
                "--supervisor", str(control / "payload_owner.py"), "--timeout", str(a.payload_timeout)]
        def stage(name, kind, command, *, gpu=False, result=None):
            state["state"] = name
            state["stages"][name] = {"state": "running"}
            save()
            print(json.dumps({"stage": name, "state": "starting"}), flush=True)
            run_output = a.output / "runs" / name
            spec = a.output / f"{kind}-{'gpu' if gpu else 'cpu'}.toml"
            argv = base + ["--spec", str(spec), "--output", str(run_output)]
            if gpu:
                argv += ["--gpu", "--lease-cli", str(a.lease_cli), "--lease-estate", str(a.lease_estate)]
            rc = subprocess.call(argv + ["--", "/bin/bash", "/workspace/python.sh", *command])
            run_state_path = run_output / "status.json"
            run_state = json.loads(run_state_path.read_text()) if run_state_path.exists() else {}
            state["stages"][name] = run_state
            if rc or run_state.get("state") != "passed" or not run_state.get("cleanup_complete"):
                raise RuntimeError(f"{name} failed; see {run_output}")
            if gpu and not run_state.get("lease_released"):
                raise RuntimeError(f"{name} did not prove GPU release")
            if result:
                data = json.loads((estates[kind] / "receipts" / result).read_text())
                if data.get("passed") is not True:
                    raise RuntimeError(f"{name} has no passing inference receipt")
                run_state["result"] = str(estates[kind] / "receipts" / result)
            save()
            print(json.dumps({"stage": name, "state": "passed"}), flush=True)
        stage("acquire-checkpoint", "checkpoint",
              ["/workspace/inputs/inkling_acquire_tiny.py", "--output",
               "/workspace/checkpoints/tiny-inkling2-mini"])
        acquired = json.loads((estates["checkpoint"] / "checkpoints/tiny-inkling2-mini/acquisition.json").read_text())
        if not acquired.get("complete"):
            raise RuntimeError("checkpoint acquisition is incomplete")
        all_reduced, all_checkpoint = [], []
        for iteration in range(1, a.repeat + 1):
            number = f"{iteration:02d}"
            reduced_result = f"reduced-{number}.json"
            stage("reduced-" + number, "reduced",
                  ["/workspace/inputs/inkling_repeated_inference.py", "--new-tokens", str(a.new_tokens),
                   "--output", "/workspace/receipts/" + reduced_result], gpu=True, result=reduced_result)
            all_reduced.append(json.loads((estates["reduced"] / "receipts" / reduced_result).read_text()))
            checkpoint_result = f"checkpoint-{number}.json"
            stage("checkpoint-" + number, "checkpoint",
                  ["/workspace/inputs/inkling_checkpoint_inference.py", "--checkpoint",
                   "/workspace/checkpoints/tiny-inkling2-mini", "--new-tokens", str(a.new_tokens),
                   "--process-label", number, "--result", "/workspace/receipts/" + checkpoint_result],
                  gpu=True, result=checkpoint_result)
            all_checkpoint.append(json.loads((estates["checkpoint"] / "receipts" / checkpoint_result).read_text()))
        def reduced_tokens(report):
            return {(row["seed"], row["case"], row["gpu"]): [g["generated_tokens"] for g in row["generations"]]
                    for row in report["results"]}
        def checkpoint_tokens(report):
            return {(row["case"], row["use_cache"], row["gpu"], row["repeat"]): row["new_token_ids"]
                    for row in report["runs"]}
        for reports, extract in ((all_reduced, reduced_tokens), (all_checkpoint, checkpoint_tokens)):
            baseline = extract(reports[0])
            if any(extract(report) != baseline for report in reports[1:]):
                raise AssertionError("generated tokens changed across independent processes")
        reduced_rows = [row for report in all_reduced for row in report["results"]]
        checkpoint_rows = [row for report in all_checkpoint for row in report["runs"]]
        summary = {
            "passed": True,
            "bundle_digest": a.bundle_digest,
            "independent_gpu_processes": 2 * a.repeat,
            "reduced_model_cases": len(reduced_rows),
            "reduced_generation_requests": sum(report["generation_requests"] for report in all_reduced),
            "reduced_generated_sequences": sum(report["generated_sequences"] for report in all_reduced),
            "checkpoint_gpu_generations": len(checkpoint_rows),
            "checkpoint_cpu_references": sum(report["completed_cpu_generations"] for report in all_checkpoint),
            "tokens_match_across_processes": True,
            "reduced_max_logit_error": max(max(row["full_logit_max_abs_error"],
                max(g["logit_max_abs_error"] for g in row["generations"])) for row in reduced_rows),
            "reduced_max_cache_tensor_error": max(row["cache_reference_max_abs_error"] for row in reduced_rows),
            "checkpoint_max_logit_error": max(row["vs_cpu"]["max_abs"] for row in checkpoint_rows),
            "checkpoint_max_cross_gpu_logit_error": max(
                (row["vs_gpu0"]["max_abs"] for row in checkpoint_rows if "vs_gpu0" in row), default=0.0),
            "checkpoint_max_warm_repeat_logit_error": max(
                (row["vs_fresh_repeat"]["max_abs"] for row in checkpoint_rows if "vs_fresh_repeat" in row), default=0.0),
            "checkpoint_peak_gpu_allocation_bytes": max(row["peak_allocated_bytes"] for row in checkpoint_rows),
            "checkpoint_repo": acquired["repo"], "checkpoint_revision": acquired["revision"],
            "checkpoint_trained": False, "precision": "float32",
            "all_payload_cleanup_complete": all(s.get("cleanup_complete") for s in state["stages"].values()),
            "all_gpu_leases_released": all(s.get("lease_released", True) for s in state["stages"].values()),
            "dependency_mounts": [],
        }
        write_json(a.output / "summary.json", summary)
        state.update(state="passed", passed=True, summary=summary)
        save()
        print(json.dumps(summary), flush=True)
        return 0
    except BaseException as exc:
        state.update(state="failed", passed=False, error=repr(exc), traceback=traceback.format_exc())
        save()
        raise


if __name__ == "__main__":
    sys.exit(main())
