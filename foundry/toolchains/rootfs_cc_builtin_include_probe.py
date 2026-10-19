#!/usr/bin/env python3
"""Probe rootfs C++ compiler built-in include directories."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

COMPILERS = {
    "gcc_13_3": Path("/usr/bin/g++"),
    "clang_23": Path("/usr/lib/llvm-23/bin/clang++"),
}

ENV_INCLUDE_KEYS = (
    "CPATH",
    "C_INCLUDE_PATH",
    "CPLUS_INCLUDE_PATH",
    "OBJC_INCLUDE_PATH",
)


def _probe_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in ENV_INCLUDE_KEYS:
        env.pop(key, None)
    return env


def _parse_include_search_list(output: str) -> list[str]:
    includes: list[str] = []
    in_search_list = False
    for raw in output.splitlines():
        line = raw.strip()
        if line == "#include <...> search starts here:":
            in_search_list = True
            continue
        if in_search_list and line == "End of search list.":
            return includes
        if in_search_list:
            includes.append(line.removesuffix(" (framework directory)"))
    raise RuntimeError("compiler verbose output did not include a system include search list")


def probe_compiler(path: Path) -> list[str]:
    result = subprocess.run(
        [str(path), "-E", "-x", "c++", "-", "-v"],
        check=False,
        input="",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_probe_env(),
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{path} exited {result.returncode}:\n{result.stderr}")
    return _parse_include_search_list(result.stderr + "\n" + result.stdout)


def main() -> int:
    observed = {name: probe_compiler(path) for name, path in COMPILERS.items()}
    json.dump(observed, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
