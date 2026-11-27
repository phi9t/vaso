"""Common native artifact and lifecycle receipts for explicitly owned Insula builds."""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import uuid
from build_report import atomic_json, read_json, utc_now

SCHEMA = "vaso.foundry-build-result.v1"
NATIVE_SCHEMA = "vaso.native-build-result.v1"
BUILDER = "/opt/vaso/pascal/bin/build-native"
PAYLOAD_RUNNER = Path(__file__).resolve().parents[1] / "native/triton/pascal/capsule/run_payload.py"


def cpu_count():
    return len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)


def absolute(path, label):
    if path is None or not path.is_absolute():
        raise ValueError(label + " must be an explicit absolute path")
    return str(path)


def payload(a, request_id):
    command = [BUILDER, "--target", a.target.split("/")[-1], "--phase", a.phase,
               "--request-id", request_id]
    if a.jobs is not None:
        command += ["--jobs", str(a.jobs)]
    return command


def owner_command(a, command, digest=None):
    if a.supervisor is not None:
        result = [sys.executable, "-B", str(PAYLOAD_RUNNER),
                  "--insula", str(a.insula), "--spec", str(a.spec),
                  "--supervisor", str(a.supervisor), "--output", str(a.output / "lifecycle"),
                  "--timeout", str(a.max_seconds or 14400)]
    else:
        result = [str(a.insula), "run", "--state", str(a.state), "--run-id", a.run_id,
                  "-f", str(a.spec), "--timeout-seconds", str(a.max_seconds or 14400)]
    if digest is not None:
        result += ["--expected-plan-sha256", digest]
    return result + ["--", *command]


def admit(a, command):
    argv = [str(a.insula), "plan", "-f", str(a.spec)]
    if a.supervisor is None:
        if a.state:
            argv += ["--state", str(a.state)]
        argv += ["--timeout-seconds", str(a.max_seconds or 14400)]
    plan = json.loads(subprocess.check_output(argv + ["--", *command], text=True, timeout=85))
    main = plan["main"]
    if (plan.get("kind") != "single" or main["argv"] != command
            or main["devices"] or main["extra_mounts"] or main.get("unavailable_projections")):
        raise RuntimeError("native builds require an exact CPU-only Insula plan without external dependency mounts")
    if main["env"]:
        raise RuntimeError("build-native owns its environment; use --jobs instead of spec environment overrides")
    if main.get("ephemeral"):
        raise RuntimeError("native builds require a persistent estate for receipts and artifacts")
    if not re.fullmatch(r"[a-f0-9]{64}", plan["plan_sha256"]):
        raise RuntimeError("invalid Insula plan digest")
    prefix = Path(main["rootfs"]) / "opt/vaso/pascal"
    profile = read_json(prefix / "profile.json")
    if (profile["cuda"], profile["gpu_arch"], profile["python_abi"], profile["runtime"]) != (
            "12.6", "sm_61", "cp312", "standalone-insula"):
        raise RuntimeError("selected capsule contradicts the requested build target")
    if not (prefix / "bin/build-native").is_file():
        raise RuntimeError("selected capsule has no packaged unified Bazel builder; publish a builder capsule")
    workspaces = [mount for mount in main["mounts"] if mount.get("target") == "/workspace"]
    if len(workspaces) != 1 or workspaces[0].get("writable") is not True:
        raise RuntimeError("expected one writable /workspace estate projection")
    workspace = Path(workspaces[0]["source"])
    if not workspace.is_absolute() or not workspace.is_dir():
        raise RuntimeError("expected an existing absolute workspace projection")
    return plan, workspace


def _base(request):
    return {
        "schema": SCHEMA, "target": request["target"], "phase": request["phase"],
        "request_id": request["request_id"], "backend": "bazel",
        "runtime": "standalone-insula", "state": "admitted", "exit_code": None,
        "started_at": request["started_at"], "artifacts": [], "runtime_qualified": False,
        "execution": {"owner": request["owner"], "plan_sha256": request["plan_sha256"],
                      "bundle_digest": request.get("bundle_digest")},
        "lifecycle": {"state": "admitted", "cleanup_complete": None},
    }


def _request(output, target):
    request = read_json(output / "request.json")
    if request.get("schema") != "vaso.foundry-build-request.v1" or request.get("target") != target:
        raise ValueError("attempt request does not match the selected target")
    if request.get("phase") not in ("prepare", "build"):
        raise ValueError("invalid attempt phase")
    if not re.fullmatch(r"[a-f0-9]{32}", request.get("request_id", "")):
        raise ValueError("invalid build request identity")
    return request


def _native_result(request):
    short = request["target"].split("/")[-1]
    workspace = Path(request["workspace"]).resolve()
    path = workspace / "native-build" / short / "requests" / (request["request_id"] + ".json")
    result = read_json(path)
    for field, expected in (("schema", NATIVE_SCHEMA), ("target", short),
                            ("phase", request["phase"]), ("request_id", request["request_id"]),
                            ("backend", "bazel"), ("python_abi", "cp312"),
                            ("cuda", "12.6"), ("gpu_arch", "sm_61")):
        if result.get(field) != expected:
            raise ValueError("native receipt " + field + " does not match this build request")
    expected_state = "prepared" if request["phase"] == "prepare" else "built"
    if result.get("state") != expected_state or result.get("exit_code") != 0:
        raise RuntimeError("native request did not finish " + expected_state)
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("native receipt has no artifact inventory")
    if request["phase"] == "prepare" and artifacts:
        raise ValueError("preparation cannot claim wheel artifacts")
    if request["phase"] == "build" and len(artifacts) != 1:
        raise ValueError("native build must produce exactly one validated wheel")
    normalized = []
    for artifact in artifacts:
        guest = PurePosixPath(artifact["path"])
        owned = PurePosixPath("/workspace/native-build") / short / "artifacts"
        if not guest.is_absolute() or ".." in guest.parts or not guest.is_relative_to(owned):
            raise ValueError("artifact is outside the selected target's output directory")
        host = workspace / guest.relative_to("/workspace")
        if not host.resolve().is_relative_to(workspace):
            raise ValueError("artifact escapes the admitted workspace")
        size = artifact.get("bytes")
        if type(size) is not int or size <= 0 or not host.is_file() or host.stat().st_size != size:
            raise ValueError("native artifact is missing or its size differs from its receipt")
        if not re.fullmatch(r"[a-f0-9]{64}", artifact.get("sha256", "")):
            raise ValueError("native artifact has no completed output digest")
        if artifact.get("python_abi") != "cp312" or not host.name.endswith(".whl"):
            raise ValueError("native artifact contradicts the selected Python ABI")
        normalized.append({**artifact, "guest_path": str(guest), "path": str(host)})
    return result, normalized, str(path)


def _external_lifecycle(output, request):
    path = output / "lifecycle/status.json"
    if not path.exists():
        return {"state": "admitted", "cleanup_complete": None}, None
    status = read_json(path)
    state = status.get("state")
    if state not in ("planning", "running", "waiting-for-gpus", "passed", "failed"):
        raise ValueError("unsupported payload lifecycle state")
    lifecycle = {"state": state, "cleanup_complete": status.get("cleanup_complete"),
                 "status_path": str(path), "exit_code": status.get("exit_code")}
    if state not in ("passed", "failed"):
        return lifecycle, None
    if state == "failed":
        lifecycle["error"] = status.get("error", "Insula payload failed")
        return lifecycle, status.get("exit_code") or 1
    receipt_path = output / "lifecycle/owner/receipt.json"
    receipt = read_json(receipt_path)
    lifecycle.update(receipt_path=str(receipt_path), proof=receipt.get("cleanup"),
                     limitations=receipt.get("limitations", []))
    if (status.get("plan_sha256") != request["plan_sha256"]
            or status.get("command") != request["command"]
            or status.get("cleanup_complete") is not True or status.get("exit_code") != 0
            or receipt.get("exit_code") != 0
            or receipt.get("cleanup", {}).get("complete") is not True
            or receipt.get("lifecycle", {}).get("reason") != "completed"):
        lifecycle.update(state="failed", error="payload result lacks successful matching lifecycle cleanup")
        return lifecycle, 125
    lifecycle["cleanup_complete"] = True
    return lifecycle, 0


def _native_lifecycle(request):
    owner = json.loads(subprocess.check_output(
        [request["insula"], "status", "--state", request["state"], request["run_id"]],
        text=True, timeout=15))
    if owner.get("run_id") != request["run_id"] or owner.get("plan_sha256") != request["plan_sha256"]:
        raise ValueError("Insula status does not match the admitted run and plan")
    phase = owner.get("phase")
    cleanup = owner.get("cleanup") or {}
    process = cleanup.get("process_cleanup") or {}
    complete = (phase == "cleaned" and process.get("root_populated") is False
                and process.get("removed") is True)
    lifecycle = {"state": phase, "cleanup_complete": complete,
                 "proof": cleanup, "run_id": request["run_id"], "state_root": request["state"]}
    if phase in ("planned", "running"):
        return lifecycle, None
    if not complete:
        lifecycle["error"] = "native Insula owner has not proved cleanup"
        return lifecycle, 125
    outcome = (owner.get("exits") or {}).get("main") or {}
    rc = outcome.get("exit_code")
    if outcome.get("reason") != "exited" or type(rc) is not int:
        return lifecycle, 125
    lifecycle["exit_code"] = rc
    return lifecycle, rc


def status(output, target):
    """Read one exact attempt, binding success to native and owner receipts."""
    request = _request(output, target)
    summary = _base(request)
    saved_path = output / "summary.json"
    if saved_path.exists():
        saved = read_json(saved_path)
        if (saved.get("schema") != SCHEMA or saved.get("target") != target
                or saved.get("request_id") != request["request_id"]):
            raise ValueError("common result does not match this build request")
        # A terminal failure belongs to this immutable attempt. In particular,
        # cancellation after a child exits must not later become build success.
        if saved.get("state") == "failed":
            return saved
    if request["owner"] == "external":
        lifecycle, rc = _external_lifecycle(output, request)
    elif request["owner"] == "native":
        lifecycle, rc = _native_lifecycle(request)
    else:
        raise ValueError("invalid selected lifecycle owner")
    summary["lifecycle"] = lifecycle
    if rc is None:
        summary["state"] = "running" if lifecycle["state"] in ("running", "waiting-for-gpus") else "admitted"
        return summary
    if rc:
        summary.update(state="failed", exit_code=rc,
                       error=lifecycle.get("error", "payload exited unsuccessfully"))
        return summary
    try:
        native, artifacts, native_path = _native_result(request)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        summary.update(state="failed", exit_code=2, error=str(exc))
        return summary
    summary.update(state=native["state"], exit_code=0, artifacts=artifacts,
                   finished_at=native.get("finished_at"), native_receipt=native_path,
                   native_execution=native.get("native_execution", False),
                   cache_hit=native.get("cache_hit", False),
                   native_execution_count=native.get("native_execution_count", 0))
    return summary


def payload_runner():
    spec = importlib.util.spec_from_file_location("vaso_build_payload_owner", PAYLOAD_RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def execute(a):
    """Admit once, then retain exactly the explicitly selected lifecycle owner."""
    request_id = uuid.uuid4().hex
    command = payload(a, request_id if not a.plan_only else "plan-only")
    plan, workspace = admit(a, command)
    if a.plan_only:
        print(json.dumps(plan, indent=2))
        return 0
    output = a.output
    output.mkdir(parents=True, exist_ok=False)
    request = {
        "schema": "vaso.foundry-build-request.v1", "target": a.target, "phase": a.phase,
        "request_id": request_id, "started_at": utc_now(), "command": command,
        "owner": "external" if a.supervisor else "native", "insula": str(a.insula),
        "spec": str(a.spec), "workspace": str(workspace), "plan_sha256": plan["plan_sha256"],
        "bundle_digest": plan["main"].get("bundle_digest"),
        "state": str(a.state) if a.state else None, "run_id": a.run_id,
        "supervisor": str(a.supervisor) if a.supervisor else None,
    }
    atomic_json(output / "request.json", request)
    atomic_json(output / "plan.json", plan)
    atomic_json(output / "summary.json", _base(request))
    if a.supervisor is None:
        argv = owner_command(a, command, plan["plan_sha256"])
        # Insula becomes the process owner. Status later combines its terminal
        # receipt with the exact request-indexed native artifact receipt.
        try:
            os.execv(argv[0], argv)
            raise RuntimeError("Insula exec unexpectedly returned")
        except (OSError, RuntimeError) as exc:
            failed = _base(request)
            failed.update(state="failed", exit_code=2, error=str(exc))
            atomic_json(output / "summary.json", failed)
            raise
    module = payload_runner()
    args = argparse.Namespace(
        insula=a.insula, supervisor=a.supervisor, spec=a.spec, output=output / "lifecycle",
        timeout=a.max_seconds or 14400, admission_timeout=a.max_seconds or 14400,
        expected_plan_sha256=plan["plan_sha256"], gpu=False, lease_cli=None, lease_estate=None,
    )
    with module.SignalRelay() as signals:
        rc = module.run_request(args, command, signals)
        summary = status(output, a.target)
        if signals.number is not None:
            rc = 128 + signals.number
            summary.update(state="failed", exit_code=rc, error="build request was cancelled",
                           cancelled_signal=signals.number)
        elif rc and summary["state"] != "failed":
            summary.update(state="failed", exit_code=rc, error="payload owner returned nonzero")
        atomic_json(output / "summary.json", summary)
    print(json.dumps(summary, indent=2))
    return int(summary["exit_code"] or (0 if summary["state"] in ("built", "prepared") else 1))
