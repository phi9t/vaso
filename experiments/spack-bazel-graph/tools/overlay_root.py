#!/usr/bin/env python3
"""Plan a thin overlay for a bwrap root.

bwrap cannot create a new top-level mountpoint (like /vaso or /home/kvothe)
under a read-only root bind. Rather than symlink base dirs into an overlay
directory (which loops once the overlay *is* `/`), this tool emits a bwrap
**bind plan**: the base root's top-level entries are `--ro-bind`'d in from the
base, and the declared sandbox mountpoints are provided as `--dir`/`--tmpfs`
targets that later binds attach to.

It prints newline-separated bwrap args to stdout, so `run.sh` can splice them
into the argv. All base-system paths resolve to the real base root and no
self-referential symlink is introduced.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# bwrap manages these; never bind them from base.
SKIP_TOPLEVEL = {"proc", "dev", "tmp", "run", "sys"}


def plan_binds(base: Path, mountpoints: list[str]) -> list[str]:
    args: list[str] = []

    # Top-level names occupied by a mountpoint (e.g. /opt for /opt/vaso). These
    # get a tmpfs so we can keep base contents and punch new dirs.
    mp_tops = {Path(mp).parts[1] for mp in mountpoints if len(Path(mp).parts) > 1}

    for entry in sorted(base.iterdir()):
        name = entry.name
        if name in SKIP_TOPLEVEL or name in mp_tops:
            continue
        args += ["--ro-bind", str(entry), "/" + name]

    # A top that also holds a mountpoint: lay a tmpfs at "/top", ro-bind each
    # existing base child into it (except reserved mountpoint names).
    for top in sorted(mp_tops):
        base_top = base / top
        args += ["--tmpfs", "/" + top]
        if base_top.is_dir():
            reserved = {
                Path(mp).parts[2]
                for mp in mountpoints
                if len(Path(mp).parts) > 2 and Path(mp).parts[1] == top
            }
            for child in sorted(base_top.iterdir()):
                if child.name in reserved:
                    continue
                args += ["--ro-bind", str(child), f"/{top}/{child.name}"]

    # Declared mountpoints as dirs (targets for run.sh binds).
    for mp in mountpoints:
        args += ["--dir", mp]

    return args


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True, help="base root to overlay")
    parser.add_argument(
        "--mountpoint",
        action="append",
        default=[],
        dest="mountpoints",
        help="sandbox mountpoint to provide as a real dir (repeatable)",
    )
    args = parser.parse_args(argv)
    for token in plan_binds(args.base.resolve(), args.mountpoints):
        print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
