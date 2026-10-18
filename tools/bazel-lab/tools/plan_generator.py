#!/usr/bin/env python3
"""Generate the committed bazel-lab plan files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


PLANS = {
    "dep.plan.json": {
        "schema": 1,
        "name": "dep-prefix",
        "source": {"files": ["pkg/dep.c"], "patches": []},
        "toolchain": {"cc": "toolchain:host_gcc", "identity": "host-gcc-wrapper"},
        "configure": {"argv": ["make", "configure"], "env": {"LC_ALL": "C"}},
        "build": {"argv": ["make", "all"], "env": {"CFLAGS": "-O2"}},
        "install": {"argv": ["make", "install"]},
        "determinism": {
            "seed": "dep-plan-seed",
            "source_date_epoch": "2026-10-06T00:00:00Z",
            "prefix_maps": ["{work}=.", "{out}=."],
        },
    },
    "native.plan.json": {
        "schema": 1,
        "name": "native-pkg",
        "source": {"files": ["pkg/pkg.c"], "patches": []},
        "toolchain": {"cc": "toolchain:host_gcc", "identity": "host-gcc-wrapper"},
        "deps": ["//pkg:dep_prefix"],
        "configure": {"argv": ["make", "configure"], "env": {"LC_ALL": "C"}},
        "build": {"argv": ["make", "all"], "env": {"CFLAGS": "-O2"}},
        "install": {"argv": ["make", "install"]},
        "determinism": {
            "seed": "native-plan-seed",
            "source_date_epoch": "2026-10-06T00:00:00Z",
            "prefix_maps": ["{work}=.", "{out}=."],
        },
    },
}


def write_plan(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for name, payload in sorted(PLANS.items()):
        write_plan(args.out / name, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
