#!/usr/bin/env python3
"""Tiny native-action executor used by bazel-lab."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def plan_seed(path: Path | None) -> str:
    if path is None:
        return "no-plan"
    payload = json.loads(path.read_text(encoding="utf-8"))
    seed = payload.get("determinism", {}).get("seed")
    return str(seed) if seed else digest(path)[:16]


def write_simple(args: argparse.Namespace) -> None:
    lines = [
        f"label={args.label}",
        "mode=simple",
        f"seed={args.seed or plan_seed(args.plan)}",
    ]
    if args.plan:
        lines.append(f"plan_sha256={digest(args.plan)}")
    if args.src:
        src_hashes = ",".join(digest(path) for path in args.src)
        lines.append(f"src_sha256={src_hashes}")
    if args.declared_dep:
        lines.append(f"declared_dep={read(args.declared_dep)}")
    if args.dep_path:
        lines.append(f"path_dep={read(Path(args.dep_path))}")
    if args.read_env:
        lines.append(f"env_{args.read_env}={os.environ.get(args.read_env, '')}")
    if args.prefix_root:
        lines.append(f"prefix_root={args.prefix_root}")
    if args.runtime_prefix_map:
        lines.append("runtime_prefix_map=.")
    if args.jobs:
        lines.append("jobs_arg=keyed")
    if args.job_file:
        _ = read(Path(args.job_file))
        lines.append("jobs_runtime=unkeyed")
    if args.observe_execution:
        lines.append(f"executed={time.time_ns()}")
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_make(args: argparse.Namespace) -> None:
    if not args.src:
        raise SystemExit("make mode needs --src")
    flag = args.flag or "unset"
    cc_driver = Path(args.cc_driver)
    if not cc_driver.is_absolute():
        cc_driver = Path.cwd() / cc_driver
    if args.persistent_work_dir:
        if args.persistent_work_dir.startswith("{outdir}/"):
            work = args.out.parent / args.persistent_work_dir.removeprefix("{outdir}/")
        else:
            work = Path(args.persistent_work_dir)
        work.mkdir(parents=True, exist_ok=True)
        main_c = work / "main.c"
        if not main_c.exists():
            shutil.copyfile(args.src[0], main_c)
    else:
        work = args.out.parent / f"{args.out.name}.work"
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
        shutil.copyfile(args.src[0], work / "main.c")

    makefile = work / "Makefile"
    if not makefile.exists():
        makefile.write_text(
            "app: main.o\n"
            "\t$(CC) main.o -o app\n"
            "main.o: main.c\n"
            "\t$(CC) $(CFLAGS) -c main.c -o main.o\n",
            encoding="utf-8",
        )
    subprocess.run(
        [
            "make",
            "-s",
            "-C",
            str(work),
            "app",
            f"CC={cc_driver}",
            f'CFLAGS=-DLAB_FLAG=\\"{flag}\\"',
        ],
        check=True,
        env={**os.environ, "PATH": "/usr/bin:/bin:" + os.environ.get("PATH", "")},
    )
    result = subprocess.run([str(work / "app")], check=True, text=True, capture_output=True)
    args.out.write_text(result.stdout, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cc-driver", required=True)
    parser.add_argument("--declared-dep", type=Path)
    parser.add_argument("--dep-path")
    parser.add_argument("--flag")
    parser.add_argument("--job-file")
    parser.add_argument("--jobs")
    parser.add_argument("--label", required=True)
    parser.add_argument("--mode", choices=("make", "simple"), required=True)
    parser.add_argument("--observe-execution", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--persistent-work-dir")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--prefix-root")
    parser.add_argument("--read-env")
    parser.add_argument("--runtime-prefix-map", action="store_true")
    parser.add_argument("--seed")
    parser.add_argument("--src", action="append", type=Path, default=[])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.mode == "make":
        write_make(args)
    else:
        write_simple(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
