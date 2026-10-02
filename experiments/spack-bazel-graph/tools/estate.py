"""Vaso host-side directory structure: data model + placement discovery.

Two concerns, kept separate:

1. `VasoEstate` is the **data model** for the host-side directory tree that
   backs a vaso insula. It mirrors the sandbox contract in
   `docs/rootfs-container-infra-design.md`: a durable state/cache/runs/traces
   layout plus a rootfs bundle and a home backing store. It knows nothing about
   *where* on disk it lives.

2. `discover_estate_root` is the **placement tool**. Given the candidate mounts
   on this host and a required free-space budget, it picks a suitable base
   directory (a real ext4/xfs volume with enough headroom, writable, not tmpfs)
   and returns a `VasoEstate` seated there.

The estate layout (relative to a chosen `root`):

    <root>/
      rootfs-lines/<line>/    materialized/extracted bwrap rootfs bundles
      home/kvothe/            backing store for /home/kvothe
      state/                  durable vaso-owned state  (/vaso/state)
        spack/                spack locks/config state
      cache/                  explicit caches           (/vaso/cache)
        bazel/repository-cache
      lines/<cu129|cu130>/    per-CUDA-line state/cache partitions
        cache/bazel/output-base
        cache/bazel/disk-cache
        state/native
        state/stamps
        state/spack
        spack/                spack source/build caches
        uv/  cargo/  go/
      runs/                   run records               (/vaso/runs)
      traces/                 derived trace indexes      (/vaso/traces)
      tmp/                    scratch                    (/vaso/tmp)
      tools/bin/              host tool shims projected read-only into /vaso/tools/bin
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Relative dirs that make up the estate. Bazel cache/output live here so they
# persist on the host and are projected into the insula.
CUDA_LINES: tuple[str, ...] = ("cu129", "cu130")

ESTATE_DIRS: tuple[str, ...] = (
    "rootfs-lines",
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
            raise ValueError(f"unknown CUDA line {line!r}; expected one of: {', '.join(CUDA_LINES)}")
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
        return self.root / "rootfs-lines" / self._validate_cuda_line(line) / "rootfs-bundle.json"

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
                line: str(self.cuda_rootfs_manifest(line))
                for line in CUDA_LINES
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


@dataclass(frozen=True)
class MountCandidate:
    target: Path
    fstype: str
    avail_bytes: int
    seat: Path
    writable: bool

    @property
    def suitable(self) -> bool:
        return self.writable and self.fstype not in FSTYPE_BLOCKLIST


def _seat_for(mount_target: Path) -> Path:
    """Where under a mount to seat the estate.

    Shared cluster volumes are typically writable only under a per-user subtree
    (e.g. `<data-volume>/home/<user>`), not at the mount root. Prefer `$HOME` when it
    lives on this mount; otherwise fall back to a per-user dir under the mount.
    """
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or "vaso"
    home = Path(os.environ.get("HOME", "")).resolve() if os.environ.get("HOME") else None
    if home and _same_mount(home, mount_target):
        return home / ".vaso-estate"
    if mount_target == Path("/"):
        return Path("/var/tmp") / f"vaso-{user}" / "estate"
    return mount_target / user / "vaso-estate"


def seat_fstype(path: Path) -> str:
    """Filesystem type that actually backs `path` (its nearest existing ancestor).

    A mount's own fstype is not enough: the root mount's seat is under /var/tmp,
    which is frequently its own tmpfs (RAM) mount.
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
        mount = fields[1].replace("\\040", " ")
        inside = str(target) == mount or str(target).startswith(mount.rstrip("/") + "/")
        if inside and len(mount) >= len(best):
            best, kind = mount, fields[2]
    return kind


def _same_mount(path: Path, mount_target: Path) -> bool:
    try:
        return os.stat(path).st_dev == os.stat(mount_target).st_dev
    except OSError:
        return False


def _probe_writable(seat: Path) -> bool:
    """A seat is usable if we can create it and write a probe file."""
    try:
        seat.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    probe = seat / ".vaso-write-probe"
    try:
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False


def _list_mounts() -> list[MountCandidate]:
    """Enumerate real filesystem mounts, each with a probed writable seat."""
    out = subprocess.run(
        ["findmnt", "-J", "-b", "-o", "TARGET,FSTYPE,AVAIL"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(out.stdout)
    candidates: list[MountCandidate] = []
    seen_seats: set[Path] = set()

    def walk(node: dict) -> None:
        target = node.get("target")
        fstype = node.get("fstype") or ""
        avail = node.get("avail")
        if target and avail is not None and fstype not in FSTYPE_BLOCKLIST:
            path = Path(target)
            seat = _seat_for(path)
            # Never probe (and so create) a seat on a RAM-backed filesystem:
            # the root mount's seat is under /var/tmp, often its own tmpfs.
            if seat_fstype(seat) in FSTYPE_BLOCKLIST:
                seen_seats.add(seat)
            if seat not in seen_seats:
                seen_seats.add(seat)
                candidates.append(
                    MountCandidate(
                        target=path,
                        fstype=fstype,
                        avail_bytes=int(avail),
                        seat=seat,
                        writable=_probe_writable(seat),
                    )
                )
        for child in node.get("children", []) or []:
            walk(child)

    for fs in data.get("filesystems", []):
        walk(fs)
    return candidates


def discover_estate_root(
    required_bytes: int,
    *,
    explicit_root: str | None = None,
    prefer: list[str] | None = None,
) -> VasoEstate:
    """Pick a disk volume with enough free space and seat the estate there.

    Selection order:
      1. `explicit_root` (env `VASO_ESTATE_ROOT` or arg) always wins if it has
         room and is writable.
      2. Any mount in `prefer` (path prefixes) that is suitable and has room.
      3. The suitable mount with the most available space, excluding tmpfs and
         near-full root, that clears `required_bytes` with headroom.
    """
    explicit = explicit_root or os.environ.get("VASO_ESTATE_ROOT")
    if explicit:
        root = Path(explicit).expanduser().resolve()
        kind = seat_fstype(root)
        if kind in FSTYPE_BLOCKLIST:
            raise SystemExit(f"VASO_ESTATE_ROOT {root} is on {kind}; the estate must live on a disk volume")
        root.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(root).free
        if free < required_bytes:
            raise SystemExit(
                f"VASO_ESTATE_ROOT {root} has {free} bytes free, need {required_bytes}"
            )
        return VasoEstate(root=root)

    candidates = [
        c
        for c in _list_mounts()
        if c.suitable and c.avail_bytes >= required_bytes and seat_fstype(c.seat) not in FSTYPE_BLOCKLIST
    ]
    if not candidates:
        raise SystemExit(
            f"no suitable volume with >= {required_bytes} bytes free "
            f"(need a writable, non-tmpfs mount)"
        )

    prefer = prefer or []

    # Sort: preferred prefixes first, non-root first, then most free space desc.
    candidates.sort(
        key=lambda c: (
            0 if any(str(c.target).startswith(p) for p in prefer) else 1,
            1 if c.target == Path("/") else 0,
            -c.avail_bytes,
        )
    )
    chosen = candidates[0]
    chosen.seat.mkdir(parents=True, exist_ok=True)
    return VasoEstate(root=chosen.seat.resolve())


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Discover and seat the vaso host estate")
    parser.add_argument(
        "--required-gib",
        type=float,
        default=40.0,
        help="required free space in GiB (rootfs bundle + caches)",
    )
    parser.add_argument("--root", help="explicit estate root (overrides discovery)")
    parser.add_argument(
        "--prefer",
        action="append",
        default=[],
        help="preferred mount prefixes, e.g. --prefer <data-volume> (repeatable)",
    )
    parser.add_argument("--materialize", action="store_true", help="create the directories")
    parser.add_argument("--json", action="store_true", help="print the estate model as JSON")
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

    required = int(args.required_gib * (1024**3))
    estate = discover_estate_root(required, explicit_root=args.root, prefer=args.prefer)
    if args.materialize:
        estate.materialize()
    if args.print_sandbox_dirs:
        for d in estate.canonical_sandbox_dirs():
            print(d)
        return 0
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
