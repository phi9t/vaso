#!/usr/bin/env python3
"""Run one finite, digest-admitted Insula workload and retain its lifecycle evidence."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--insula", type=Path, required=True)
    p.add_argument("--supervisor", type=Path, required=True)
    p.add_argument("--spec", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--timeout", type=int, default=7200)
    p.add_argument("--gpu", action="store_true")
    p.add_argument("--lease-cli", type=Path)
    p.add_argument("--lease-estate", type=Path)
    p.add_argument("command", nargs=argparse.REMAINDER)
    a = p.parse_args()
    command = a.command[1:] if a.command[:1] == ["--"] else a.command
    if not command:
        p.error("a capsule command is required")
    if a.gpu and (not a.lease_cli or not a.lease_estate):
        p.error("GPU execution requires the shared lease authority")
    for path in (a.insula, a.supervisor, a.spec):
        path.resolve(strict=True)
    a.output.mkdir(parents=True, exist_ok=False)
    status = {"state": "planning", "owner_pid": os.getpid(), "command": command,
              "runtime": "standalone-insula-payload", "dependency_mounts": []}
    grant = None
    started = closed = False
    def save():
        status["updated_utc"] = datetime.now(timezone.utc).isoformat()
        tmp = a.output / "status.json.tmp"
        tmp.write_text(json.dumps(status, indent=2) + "\n")
        tmp.replace(a.output / "status.json")
    def gpu_idle():
        text = subprocess.check_output(
            ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader"],
            text=True, timeout=10)
        return not text.strip()
    def lease(*args):
        env = {**os.environ, "VASO_ESTATE_ROOT": str(a.lease_estate)}
        return json.loads(subprocess.check_output(
            [sys.executable, str(a.lease_cli), *args], text=True, env=env, timeout=30))
    save()
    try:
        planned = subprocess.run([str(a.insula), "plan", "-f", str(a.spec), "--", *command],
                                 text=True, capture_output=True, timeout=90)
        (a.output / "plan.json").write_text(planned.stdout)
        (a.output / "plan.stderr").write_text(planned.stderr)
        planned.check_returncode()
        plan = json.loads(planned.stdout)
        main_plan = plan["main"]
        if main_plan["argv"] != command or main_plan["extra_mounts"]:
            raise RuntimeError("Expected exact command and no external dependency mounts")
        if bool(main_plan["devices"]) != a.gpu:
            raise RuntimeError("Plan device admission does not match workload")
        status["plan_sha256"] = plan["plan_sha256"]
        if a.gpu:
            status["state"] = "waiting-for-gpus"; save()
            registry = a.lease_estate / "agents/leases/gpu.json"
            while True:
                reg = json.loads(registry.read_text())
                active = [r for r in reg["leases"] if r["expiry_time"] > time.time()
                          and (r["host"] != socket.gethostname() or Path("/proc", str(r["pid"])).exists())]
                if not active and gpu_idle():
                    break
                time.sleep(5)
            grant = lease("acquire", "--resource", "gpu", "--holder", a.output.name,
                          "--pid", str(os.getpid()), "--amount", "2", "--capacity", "2",
                          "--ttl", str(a.timeout + 120), "--timeout", "3")
            status["lease_id"] = grant["id"]
            if not gpu_idle():
                raise RuntimeError("GPU became occupied after resource admission")
        status["state"] = "running"; save()
        payload = [str(a.insula), "payload", "--expected-plan-sha256", plan["plan_sha256"],
                   "--process-record", str(a.output / "process.json"), "-f", str(a.spec),
                   "--", *command]
        started = True
        rc = subprocess.call([sys.executable, str(a.supervisor), "--output",
                              str(a.output / "owner"), "--timeout", str(a.timeout),
                              "--cleanup-timeout", "30", "--owner-id", a.output.name,
                              "--", *payload], stdin=subprocess.DEVNULL)
        receipt = json.loads((a.output / "owner/receipt.json").read_text())
        closed = receipt["cleanup"]["complete"] is True
        status.update(exit_code=rc, cleanup_complete=closed,
                      duration_seconds=receipt.get("duration_seconds"))
        if rc or not closed:
            raise RuntimeError(f"Payload exit={rc}, cleanup_complete={closed}")
        status["state"] = "passed"
        return 0
    except BaseException as exc:
        status.update(state="failed", error=repr(exc))
        raise
    finally:
        if grant:
            if (not started or closed) and gpu_idle():
                lease("release", "--resource", "gpu", "--id", grant["id"])
                status["lease_released"] = True
            else:
                status["lease_released"] = False
        save()


if __name__ == "__main__":
    sys.exit(main())
