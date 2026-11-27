#!/usr/bin/env python3
"""Run one finite, digest-admitted Insula workload and retain its lifecycle evidence."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone


def planned_gpu_indices(devices):
    """Use exactly the physical NVIDIA devices admitted by the Insula plan."""
    indices = []
    for device in devices:
        match = re.fullmatch(r"/dev/nvidia(\d+)", device)
        if match:
            indices.append(int(match.group(1)))
    if len(indices) != len(set(indices)):
        raise ValueError("Plan contains duplicate physical GPU devices")
    return sorted(indices)


HANDLED_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


class Cancelled(RuntimeError):
    def __init__(self, number):
        self.number = number
        super().__init__(f"Cancelled by signal {number}")


def owner_handles_signals(pid):
    """Do not terminate the owner during Python startup, before its handlers exist."""
    try:
        fields = dict(line.split(":", 1) for line in Path(f"/proc/{pid}/status").read_text().splitlines()
                      if ":" in line)
        caught = int(fields["SigCgt"].strip(), 16)
    except (OSError, KeyError, ValueError):
        return False
    required = sum(1 << (number - 1) for number in HANDLED_SIGNALS)
    return caught & required == required


class SignalRelay:
    """Forward cancellation through finite owners without killing their supervisors.

    Use as a context manager. Children must install handlers for HANDLED_SIGNALS;
    the qualified payload owner and run_payload both meet this contract. Call
    check() after collecting a child receipt so cancellation cannot become success.
    """
    def __init__(self):
        self.number = None
        self.process = None
        self.forwarded_signal = None
        self._handlers = None

    def receive(self, number, _frame):
        if self.number is None:
            self.number = number

    def __enter__(self):
        self._handlers = {number: signal.signal(number, self.receive)
                          for number in HANDLED_SIGNALS}
        return self

    def __exit__(self, *_exc):
        for number, handler in self._handlers.items():
            signal.signal(number, handler)
        self._handlers = None

    def check(self):
        if self.number is not None:
            raise Cancelled(self.number)

    def run(self, argv, **popen_kwargs):
        if self._handlers is None:
            raise RuntimeError("SignalRelay.run requires its context manager")
        self.process = None
        self.forwarded_signal = None
        self.check()
        popen_kwargs.setdefault("stdin", subprocess.DEVNULL)
        popen_kwargs["start_new_session"] = True
        self.process = subprocess.Popen(argv, **popen_kwargs)
        # subprocess.call kills its child on KeyboardInterrupt. Keep the finite
        # owner alive instead: it alone terminates and reaps its descendants.
        while True:
            if self.number is not None and self.forwarded_signal is None and self.process.poll() is None:
                if owner_handles_signals(self.process.pid):
                    self.process.send_signal(self.number)
                    self.forwarded_signal = self.number
            try:
                return self.process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--insula", type=Path, required=True)
    p.add_argument("--supervisor", type=Path, required=True)
    p.add_argument("--spec", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--timeout", type=int, default=7200)
    p.add_argument("--admission-timeout", type=int, default=7200)
    p.add_argument("--expected-plan-sha256", help="Require the caller-reviewed plan identity")
    p.add_argument("--gpu", action="store_true")
    p.add_argument("--lease-cli", type=Path)
    p.add_argument("--lease-estate", type=Path)
    p.add_argument("command", nargs=argparse.REMAINDER)
    a = p.parse_args()
    command = a.command[1:] if a.command[:1] == ["--"] else a.command
    if not command:
        p.error("a capsule command is required")
    if a.expected_plan_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", a.expected_plan_sha256):
        p.error("expected plan SHA256 must be 64 lowercase hex characters")
    if a.timeout <= 0 or a.admission_timeout <= 0:
        p.error("timeouts must be positive")
    if a.gpu and (not a.lease_cli or not a.lease_estate):
        p.error("GPU execution requires the shared lease authority")
    for path in (a.insula, a.supervisor, a.spec):
        path.resolve(strict=True)
    with SignalRelay() as signals:
        return run_request(a, command, signals)


def run_request(a, command, signals):
    a.output.mkdir(parents=True, exist_ok=False)
    status = {"state": "planning", "owner_pid": os.getpid(), "command": command,
              "runtime": "standalone-insula-payload", "dependency_mounts": []}
    grant = None
    selected = []
    started = closed = False
    return_code = 1
    def save():
        status["updated_utc"] = datetime.now(timezone.utc).isoformat()
        tmp = a.output / "status.json.tmp"
        tmp.write_text(json.dumps(status, indent=2) + "\n")
        tmp.replace(a.output / "status.json")
    def gpu_idle():
        if not selected:
            return True
        text = subprocess.check_output(
            ["nvidia-smi", "--id=" + ",".join(map(str, selected)),
             "--query-compute-apps=pid", "--format=csv,noheader"],
            text=True, timeout=10)
        return not text.strip()
    def lease(*args):
        env = {**os.environ, "VASO_ESTATE_ROOT": str(a.lease_estate)}
        return json.loads(subprocess.check_output(
            [sys.executable, str(a.lease_cli), *args], text=True, env=env, timeout=30))
    try:
        save()
        planned = subprocess.run([str(a.insula), "plan", "-f", str(a.spec), "--", *command],
                                 text=True, capture_output=True, timeout=90)
        (a.output / "plan.json").write_text(planned.stdout)
        (a.output / "plan.stderr").write_text(planned.stderr)
        planned.check_returncode()
        signals.check()
        plan = json.loads(planned.stdout)
        expected = getattr(a, "expected_plan_sha256", None)
        if expected is not None and plan["plan_sha256"] != expected:
            raise RuntimeError("Resolved Insula plan differs from caller-reviewed plan")
        main_plan = plan["main"]
        if main_plan["argv"] != command or main_plan["extra_mounts"]:
            raise RuntimeError("Expected exact command and no external dependency mounts")
        selected = planned_gpu_indices(main_plan["devices"])
        if bool(selected) != a.gpu or (main_plan["devices"] and not selected):
            raise RuntimeError("Plan device admission does not match workload")
        status["plan_sha256"] = plan["plan_sha256"]
        if a.gpu:
            status.update(state="waiting-for-gpus", physical_gpu_indices=selected)
            save()
            deadline = time.monotonic() + a.admission_timeout
            while grant is None:
                signals.check()
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for physical GPUs {selected}")
                try:
                    idle = gpu_idle()
                except subprocess.TimeoutExpired:
                    # Busy driver queries can stall without indicating a failed
                    # workload. Keep waiting within the admission deadline.
                    status["admission_probe_timeouts"] = status.get("admission_probe_timeouts", 0) + 1
                    status["last_admission_probe_error"] = "nvidia-smi readiness query timed out"
                    save()
                    idle = False
                if idle:
                    signals.check()
                    try:
                        grant = lease("acquire", "--resource", "gpu", "--holder", a.output.name,
                                      "--pid", str(os.getpid()), "--amount", str(len(selected)),
                                      "--devices", ",".join(map(str, selected)),
                                      "--ttl", str(a.timeout + 120), "--timeout", "3")
                    except subprocess.CalledProcessError as error:
                        if error.returncode != 75:
                            raise
                    signals.check()
                    if grant is not None:
                        break
                time.sleep(5)
            status["lease_id"] = grant["id"]
            if sorted(grant.get("devices", [])) != selected:
                raise RuntimeError("Lease does not match the plan's physical GPU devices")
            if not gpu_idle():
                raise RuntimeError("Selected GPU became occupied after resource admission")
        signals.check()
        status["state"] = "running"
        save()
        payload = [str(a.insula), "payload", "--expected-plan-sha256", plan["plan_sha256"],
                   "--process-record", str(a.output / "process.json"), "-f", str(a.spec),
                   "--", *command]
        try:
            rc = signals.run(
                [sys.executable, str(a.supervisor), "--output", str(a.output / "owner"),
                 "--timeout", str(a.timeout), "--cleanup-timeout", "30",
                 "--owner-id", a.output.name, "--", *payload])
        finally:
            started = signals.process is not None
            if signals.forwarded_signal is not None:
                status["forwarded_signal"] = signals.forwarded_signal
        receipt = json.loads((a.output / "owner/receipt.json").read_text())
        closed = receipt["cleanup"]["complete"] is True
        return_code = 128 - rc if rc < 0 else rc
        status.update(payload_exit_code=return_code, cleanup_complete=closed,
                      duration_seconds=receipt.get("duration_seconds"))
        signals.check()
        if rc or not closed:
            return_code = return_code or 125
            raise RuntimeError(f"Payload exit={rc}, cleanup_complete={closed}")
        status["state"] = "passed"
    except BaseException as exc:
        return_code = 128 + exc.number if isinstance(exc, Cancelled) else (return_code or 1)
        status.update(state="failed", error=repr(exc))
        print(f"run_payload: {exc}", file=sys.stderr)
    finally:
        try:
            if grant:
                status["lease_released"] = False
                if started and not closed:
                    raise RuntimeError("Payload closure is unconfirmed; lease was not released")
                if not gpu_idle():
                    raise RuntimeError("Selected GPU is still occupied; lease was not released")
                released = lease("release", "--resource", "gpu", "--id", grant["id"])
                status["lease_released"] = released.get("released") is True
                if not status["lease_released"]:
                    raise RuntimeError("Shared authority did not confirm lease release")
        except BaseException as exc:
            return_code = return_code or 125
            status.update(state="failed", cleanup_error=repr(exc))
            status.setdefault("error", repr(exc))
            print(f"run_payload cleanup: {exc}", file=sys.stderr)
        finally:
            if signals.number is not None:
                return_code = 128 + signals.number
                status.update(state="failed", cancelled_signal=signals.number)
                status.setdefault("error", repr(Cancelled(signals.number)))
            status["exit_code"] = return_code
            save()
    return return_code


if __name__ == "__main__":
    sys.exit(main())
