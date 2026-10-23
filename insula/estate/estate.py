"""Host backing paths and disk placement for insula execution.

VasoEstate describes paths relative to an explicitly chosen root and creates
empty directories. discover_estate_root checks placement and free space; neither
operation installs a rootfs, acquires inputs, or builds packages.

The current layout is defined by ESTATE_DIRS and LINE_ESTATE_DIRS below:
rootfs-lines/<line>/ holds prepared bases; vaso/ holds persistent state and
caches; sources/ holds acquired inputs; agents/ holds execution evidence and
leases. Foundry's adapter chooses which paths to project into each process.
Compatibility directories do not select or supply ambient host toolchains.

See docs/estate.md for the complete layout, projection, and restore contract.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

# Relative dirs that make up the estate. Bazel cache/output live here so they
# persist on the host and are projected into the insula.
CUDA_LINES: tuple[str, ...] = ("cu129", "cu130")

ESTATE_DIRS: tuple[str, ...] = (
    "rootfs-lines",
    "sources",
    "home/kvothe",
    "home/kvothe/.cache",
    "home/kvothe/.config",
    "home/kvothe/.local",
    "vaso",
    "vaso/state",
    "vaso/state/toolchains",
    "vaso/cache",
    "vaso/cache/bazel/repository-cache",
    "vaso/cache/spack/source",
    "vaso/cache/spack/user",
    "vaso/cache/uv",
    "vaso/cache/cargo",
    "vaso/cache/go",
    "vaso/runs",
    "vaso/traces",
    "vaso/tmp",
    "vaso/tools/bin",
    "agents",
    "agents/leases",
    "opt-vaso",
    "opt-vaso/bin",
    "opt-vaso/spack",
    "opt-vaso/view",
    "workspace",
)

LINE_ESTATE_DIRS: tuple[str, ...] = (
    "cache/bazel/output-base",
    "cache/bazel/disk-cache",
    "state/native",
    "state/stamps",
    "state/spack",
)

# The canonical sandbox namespace presented inside every vaso insula. Each entry
# is a stable absolute path from docs/rootfs-container-infra-design.md. Overlay
# root mountpoints and estate binds are derived from this.
CANONICAL_SANDBOX_DIRS: tuple[str, ...] = (
    "/home/kvothe",
    "/workspace",
    "/vaso",
    "/vaso/state",
    "/vaso/cache",
    "/vaso/runs",
    "/vaso/traces",
    "/vaso/tmp",
    "/vaso/tools/bin",
    "/opt/vaso",
    "/run/vaso",
)

# Sandbox mount contract: host estate subdir -> (sandbox path, mode).
# `vaso` is bound whole at /vaso (a single writable surface for this synthetic
# experiment); the finer-grained per-surface binds in the design doc are a
# follow-up. tools/bin and /opt/vaso are re-bound read-only.
SANDBOX_MOUNTS: tuple[tuple[str, str, str], ...] = (
    ("home/kvothe", "/home/kvothe", "rw"),
    ("workspace", "/workspace", "rw"),
    ("vaso", "/vaso", "rw"),
    ("vaso/tools/bin", "/vaso/tools/bin", "ro"),
    ("opt-vaso", "/opt/vaso", "ro"),
)

FSTYPE_BLOCKLIST = {"tmpfs", "devtmpfs", "overlay", "squashfs", "ramfs"}


@dataclass(frozen=True)
class VasoEstate:
    """The host-side directory tree backing a vaso insula, seated at `root`."""

    root: Path

    def _validate_cuda_line(self, line: str) -> str:
        if line not in CUDA_LINES:
            raise ValueError(
                f"unknown CUDA line {line!r}; expected one of: {', '.join(CUDA_LINES)}"
            )
        return line

    @property
    def rootfs(self) -> Path:
        return self.root / "rootfs"

    @property
    def rootfs_manifest(self) -> Path:
        return self.root / "rootfs-bundle.json"

    def host_dir(self, rel: str) -> Path:
        return self.root / rel

    def cuda_rootfs(self, line: str) -> Path:
        return self.root / "rootfs-lines" / self._validate_cuda_line(line) / "rootfs"

    def cuda_rootfs_manifest(self, line: str) -> Path:
        return (
            self.root
            / "rootfs-lines"
            / self._validate_cuda_line(line)
            / "rootfs-bundle.json"
        )

    def line_host(self, line: str) -> Path:
        return self.root / "vaso" / "lines" / self._validate_cuda_line(line)

    def line_bazel_output_base(self, line: str) -> Path:
        return self.line_host(line) / "cache/bazel/output-base"

    def line_bazel_disk_cache(self, line: str) -> Path:
        return self.line_host(line) / "cache/bazel/disk-cache"

    def line_native_state(self, line: str) -> Path:
        return self.line_host(line) / "state/native"

    def line_native_stamps(self, line: str) -> Path:
        return self.line_host(line) / "state/stamps"

    def bazel_output_base(self) -> Path:
        return self.root / "vaso/cache/bazel/output-base"

    def bazel_repository_cache(self) -> Path:
        return self.root / "vaso/cache/bazel/repository-cache"

    def bazel_disk_cache(self) -> Path:
        return self.root / "vaso/cache/bazel/disk-cache"

    def sandbox_mounts(self) -> list[tuple[Path, str, str]]:
        """(host_path, sandbox_path, mode) for each declared writable/ro bind."""
        return [(self.root / rel, sb, mode) for rel, sb, mode in SANDBOX_MOUNTS]

    def canonical_sandbox_dirs(self) -> list[str]:
        """The stable sandbox namespace presented inside the insula."""
        return list(CANONICAL_SANDBOX_DIRS)

    def materialize(self) -> "VasoEstate":
        """Create every estate directory (idempotent)."""
        for rel in ESTATE_DIRS:
            (self.root / rel).mkdir(parents=True, exist_ok=True)
        for line in CUDA_LINES:
            for rel in LINE_ESTATE_DIRS:
                (self.line_host(line) / rel).mkdir(parents=True, exist_ok=True)
        return self

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "root": str(self.root),
            "dirs": list(ESTATE_DIRS),
            "cuda_lines": list(CUDA_LINES),
            "cuda_rootfs": {line: str(self.cuda_rootfs(line)) for line in CUDA_LINES},
            "cuda_rootfs_manifests": {
                line: str(self.cuda_rootfs_manifest(line)) for line in CUDA_LINES
            },
            "line_dirs": {
                line: [str(self.line_host(line) / rel) for rel in LINE_ESTATE_DIRS]
                for line in CUDA_LINES
            },
            "canonical_sandbox_dirs": list(CANONICAL_SANDBOX_DIRS),
            "sandbox_mounts": [
                {"host": str(self.root / rel), "sandbox": sb, "mode": mode}
                for rel, sb, mode in SANDBOX_MOUNTS
            ],
        }


REPORT_SUBTREES: tuple[tuple[str, str], ...] = (
    ("cache", "vaso/cache"),
    ("tmp", "vaso/tmp"),
    ("runs", "vaso/runs"),
    ("agents", "agents"),
)


def tree_usage(path: Path) -> tuple[int, int]:
    """Return logical bytes and inode count for a subtree."""
    if not path.exists():
        return 0, 0
    bytes_total = 0
    inodes = 0
    for current, _dirs, files in os.walk(path):
        current_path = Path(current)
        try:
            bytes_total += current_path.stat().st_size
            inodes += 1
        except OSError:
            pass
        for name in files:
            child = current_path / name
            try:
                bytes_total += child.stat().st_size
                inodes += 1
            except OSError:
                continue
    return bytes_total, inodes


def format_report(estate: VasoEstate) -> str:
    lines = ["name bytes inodes path"]
    for name, rel in REPORT_SUBTREES:
        path = estate.root / rel
        bytes_total, inodes = tree_usage(path)
        lines.append(f"{name} {bytes_total} {inodes} {path}")
    return "\n".join(lines) + "\n"


def seat_fstype(path: Path) -> str:
    """Filesystem type that actually backs `path` (its nearest existing ancestor).

    Inspect the nearest existing ancestor because the estate may not exist yet.
    Use its deepest matching mount so nested RAM filesystems are rejected too.
    """
    target = path
    while not target.exists() and target != target.parent:
        target = target.parent
    target = target.resolve()
    best, kind = "", ""
    try:
        lines = Path("/proc/mounts").read_text().splitlines()
    except OSError:
        return kind
    for line in lines:
        fields = line.split()
        if len(fields) < 3:
            continue
        # Decode once: a literal backslash followed by "040" must stay literal.
        mount = re.sub(
            r"\\([0-7]{3})", lambda escaped: chr(int(escaped[1], 8)), fields[1]
        )
        inside = str(target) == mount or str(target).startswith(mount.rstrip("/") + "/")
        if inside and len(mount) >= len(best):
            best, kind = mount, fields[2]
    return kind


def discover_estate_root(
    required_bytes: int,
    *,
    explicit_root: str | None = None,
    prefer: list[str] | None = None,
) -> VasoEstate:
    """Validate an explicit disk-backed estate and its available headroom.

    The argument takes precedence over VASO_ESTATE_ROOT. A missing root fails
    closed; no mount discovery or automatic placement occurs. ``prefer`` is
    accepted for compatibility with old callers and has no effect.
    """
    if required_bytes < 0:
        raise ValueError("required estate headroom must be nonnegative")
    explicit = explicit_root or os.environ.get("VASO_ESTATE_ROOT")
    if explicit:
        root = Path(explicit).expanduser().resolve()
        kind = seat_fstype(root)
        if not kind:
            raise SystemExit(
                f"cannot determine filesystem type for VASO_ESTATE_ROOT {root}; "
                "ensure /proc/mounts is readable"
            )
        if kind in FSTYPE_BLOCKLIST:
            raise SystemExit(
                f"VASO_ESTATE_ROOT {root} is on {kind}; the estate must live on a disk volume"
            )
        root.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(root).free
        if free < required_bytes:
            raise SystemExit(
                f"VASO_ESTATE_ROOT {root} has {free} bytes free, need {required_bytes}"
            )
        return VasoEstate(root=root)

    raise SystemExit("set VASO_ESTATE_ROOT or pass --root")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Validate and materialize an explicitly selected insula estate"
    )
    parser.add_argument(
        "--required-gib",
        type=float,
        default=40.0,
        help="required free space in GiB (rootfs bundle + caches)",
    )
    parser.add_argument("--root", help="estate root (defaults to VASO_ESTATE_ROOT)")
    parser.add_argument(
        "--prefer",
        action="append",
        default=[],
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--materialize", action="store_true", help="create the directories"
    )
    parser.add_argument(
        "--json", action="store_true", help="print the estate model as JSON"
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="print byte and inode usage for cache, tmp, runs, and agents subtrees",
    )
    parser.add_argument(
        "--print-sandbox-dirs",
        action="store_true",
        help="print the canonical sandbox dirs (one per line) and exit",
    )
    args = parser.parse_args(argv)

    if args.print_sandbox_dirs:
        for directory in CANONICAL_SANDBOX_DIRS:
            print(directory)
        return 0

    required_bytes = args.required_gib * (1024**3)
    if not math.isfinite(required_bytes) or required_bytes < 0:
        parser.error("--required-gib must give finite, nonnegative headroom")
    estate = discover_estate_root(
        int(required_bytes), explicit_root=args.root, prefer=args.prefer
    )
    if args.materialize:
        estate.materialize()
    if args.report:
        print(format_report(estate), end="")
        return 0
    if args.json:
        print(json.dumps(estate.to_json(), indent=2, sort_keys=True))
    else:
        print(estate.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
