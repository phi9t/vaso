"""Vaso's standalone Insula client. Resource allocation remains caller-owned."""
import argparse
import json
import pathlib
import subprocess
import sys


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--insula", required=True)
    p.add_argument("--spec", required=True)
    p.add_argument("--state", required=True)
    p.add_argument("--run-id")
    p.add_argument("--plan-only", action="store_true")
    p.add_argument("action", choices=["build", "compiler", "operators", "activation", "model"])
    a = p.parse_args()
    runtime = pathlib.Path(a.insula).resolve(strict=True)
    spec = pathlib.Path(a.spec).resolve(strict=True)
    state = pathlib.Path(a.state)
    if not state.is_absolute():
        p.error("state must be an explicit absolute estate path")
    if not a.plan_only and not a.run_id:
        p.error("--run-id is required for execution")
    prefix = "/opt/vaso/pascal/bin/"
    command = [prefix + "build-triton"] if a.action == "build" else [prefix + "qualify", a.action]
    raw = subprocess.check_output([str(runtime), "plan", "--state", str(state), "-f", str(spec), "--", *command], text=True)
    plan = json.loads(raw)
    main = plan.get("main")
    if not isinstance(main, dict) or main["argv"] != command:
        raise RuntimeError("expected a single exact execution plan")
    if main["extra_mounts"]:
        raise RuntimeError("complete Pascal capsule must not depend on external package mounts")
    if a.action == "build" and main["devices"]:
        raise RuntimeError("native build must not reserve GPU devices")
    if a.action != "build" and not main["devices"]:
        raise RuntimeError("GPU qualification requires caller-admitted devices")
    if a.plan_only:
        print(raw, end="")
        return 0
    # Normal Insula run fails closed if the native host lacks delegated supervision.
    return subprocess.call([str(runtime), "run", "--state", str(state), "--run-id", a.run_id,
        "--expected-plan-sha256", plan["plan_sha256"], "-f", str(spec), "--", *command])


if __name__ == "__main__":
    sys.exit(main())
