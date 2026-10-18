#!/usr/bin/env python3
"""Run the committed JAX plan with the lease owner's unkeyed CPU budget."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path


def read_jobs(path: Path) -> str:
    value = path.read_text(encoding="utf-8").strip()
    if not value.isdecimal() or not 1 <= int(value) <= 96:
        raise ValueError(f"{path}: JAX runtime jobs must be an integer from 1 to 96")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executor", type=Path, required=True)
    parser.add_argument("--runtime-jobs-file", type=Path, required=True)
    parser.add_argument("executor_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    jobs = read_jobs(args.runtime_jobs_file)
    sys.path.insert(0, str(args.executor.resolve().parent))
    spec = importlib.util.spec_from_file_location("vaso_jax_plan_executor", args.executor)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load plan executor: {args.executor}")
    executor = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = executor
    spec.loader.exec_module(executor)
    executor.job_count = lambda: jobs
    executor_args = args.executor_args
    if executor_args[:1] == ["--"]:
        executor_args = executor_args[1:]
    return int(executor.main(executor_args))


if __name__ == "__main__":
    raise SystemExit(main())
