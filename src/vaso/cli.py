from __future__ import annotations

import argparse
import json

from vaso.bwrap import validation_plan
from vaso.config import default_config
from vaso.doctor import run_doctor
from vaso.paths import ensure_host_layout
from vaso.run_records import create_run_dir, new_run_id, write_log, write_plan, write_result
from vaso.validation import run_tier0, run_tier1_plan, write_validation_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vaso")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor", help="check host and rootfs readiness")
    validate = subparsers.add_parser("validate", help="run validation tiers")
    validate.add_argument("--tier", choices=["0", "1"], required=True)
    validate.add_argument("--repo", default="vaso-fixtures")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    if args.command == "doctor":
        config = default_config()
        ensure_host_layout(config)
        checks = run_doctor(config)
        print(json.dumps({"checks": [check.to_json() for check in checks]}, indent=2, sort_keys=True))
        return 0 if all(check.ok for check in checks) else 1
    if args.command == "validate" and args.tier == "0":
        config = default_config()
        ensure_host_layout(config)
        run_id = new_run_id()
        run_dir = create_run_dir(config, run_id)
        write_plan(run_dir, validation_plan(config, run_id, "0"))
        result = run_tier0(config)
        write_validation_report(run_dir / "validation", result)
        write_log(run_dir, "stdout.log", json.dumps(result.to_json(), indent=2, sort_keys=True) + "\n")
        write_log(run_dir, "stderr.log", "")
        write_result(run_dir, ok=result.ok, exit_code=0 if result.ok else 1, kind="validation", extra={"tier": "0"})
        print(json.dumps(result.to_json(), indent=2, sort_keys=True))
        return 0 if result.ok else 1
    if args.command == "validate" and args.tier == "1":
        config = default_config()
        ensure_host_layout(config)
        run_id = new_run_id()
        run_dir = create_run_dir(config, run_id)
        write_plan(run_dir, validation_plan(config, run_id, "1"))
        result = run_tier1_plan(args.repo)
        write_validation_report(run_dir / "validation", result)
        write_log(run_dir, "stdout.log", json.dumps(result.to_json(), indent=2, sort_keys=True) + "\n")
        write_log(run_dir, "stderr.log", "")
        write_result(run_dir, ok=result.ok, exit_code=1, kind="validation", extra={"tier": "1"})
        print(json.dumps(result.to_json(), indent=2, sort_keys=True))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
