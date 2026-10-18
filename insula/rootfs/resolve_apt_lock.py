#!/usr/bin/env python3
"""Resolve per-line Ubuntu apt package pins for cuda_ecosystem.lock.json."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any


DEFAULT_PACKAGES = (
    "ca-certificates",
    "curl",
    "git",
    "gfortran",
    "python3",
    "python3-venv",
    "cmake",
    "ninja-build",
    "pkg-config",
    "bzip2",
    "xz-utils",
    "patchelf",
    "file",
    "unzip",
    "zip",
)
SNAPSHOT_RE = re.compile(r"^20[0-9]{6}T[0-9]{6}Z$")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"{path}: expected a JSON object")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def parse_package_lines(text: str) -> dict[str, str]:
    packages: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if "=" not in line:
            raise SystemExit(f"invalid dpkg package line: {line!r}")
        name, version = line.split("=", 1)
        # dpkg status records bare names; drop the arch qualifier of Multi-Arch: same packages.
        name = name.split(":", 1)[0]
        if not name or not version:
            raise SystemExit(f"invalid dpkg package line: {line!r}")
        packages[name] = version
    if not packages:
        raise SystemExit("resolver produced no apt package versions")
    return dict(sorted(packages.items()))


def resolver_script(snapshot: str, packages: list[str]) -> str:
    package_args = " ".join(shlex.quote(package) for package in packages)
    # Stdout carries only the final dpkg-query listing; everything else goes to stderr.
    return f"""set -euo pipefail
exec 3>&1 1>&2
export DEBIAN_FRONTEND=noninteractive
rm -f /etc/apt/sources.list
printf '%s\\n' \\
  'Types: deb' \\
  'URIs: https://snapshot.ubuntu.com/ubuntu/{snapshot}' \\
  'Suites: noble noble-updates noble-security' \\
  'Components: main restricted universe multiverse' \\
  'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg' \\
  'Check-Valid-Until: no' \\
  > /etc/apt/sources.list.d/ubuntu.sources
apt-get update
purge_pkgs="$(dpkg-query -W -f='${{binary:Package}}\\n' 'libnccl*' 'libcudnn*' 2>/dev/null || true)"
if [ -n "$purge_pkgs" ]; then apt-mark unhold $purge_pkgs || true; apt-get purge -y --allow-change-held-packages $purge_pkgs; fi
llvm_pkgs="$(dpkg-query -W -f='${{binary:Package}}\\n' 'clang*' 'lld*' 'llvm*' 'libclang*' 'libllvm*' 'mlir*' 2>/dev/null || true)"
if [ -n "$llvm_pkgs" ]; then apt-mark unhold $llvm_pkgs || true; apt-get purge -y --allow-change-held-packages $llvm_pkgs; fi
apt-get install -y --no-install-recommends {package_args}
dpkg-query -W -f='${{binary:Package}}=${{Version}}\\n' >&3
"""


def resolve_packages(lock: dict[str, Any], line: str, snapshot: str, packages: list[str], docker: str) -> dict[str, str]:
    base = lock["lines"][line]["base_image"]["reference"]
    completed = subprocess.run(
        [
            docker,
            "run",
            "--rm",
            "--entrypoint",
            "bash",
            base,
            "-lc",
            resolver_script(snapshot, packages),
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise SystemExit(completed.stderr or f"docker resolver failed with exit code {completed.returncode}")
    return parse_package_lines(completed.stdout)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--lock", type=Path, required=True)
    p.add_argument("--line", choices=("cu129", "cu130"), required=True)
    p.add_argument("--snapshot", required=True)
    p.add_argument("--out", type=Path)
    p.add_argument("--docker", default="docker")
    p.add_argument("--package", action="append", dest="packages", default=[])
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not SNAPSHOT_RE.fullmatch(args.snapshot):
        raise SystemExit(f"invalid snapshot timestamp: {args.snapshot!r}")
    packages = args.packages or list(DEFAULT_PACKAGES)
    lock = load_json(args.lock)
    if args.line not in lock.get("lines", {}):
        raise SystemExit(f"line {args.line!r} not found in {args.lock}")
    resolved = resolve_packages(lock, args.line, args.snapshot, packages, args.docker)
    lock["lines"][args.line]["apt"] = {
        "snapshot": args.snapshot,
        "unresolved": False,
        "packages": resolved,
    }
    out = args.out or args.lock
    write_json(out, lock)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
