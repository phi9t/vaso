"""Translate paths from the insula namespace back to host paths.

This mirrors the bind order in foundry/run.sh:
specific over-binds such as /vaso/agents and /vaso/rootfs-lines are resolved
before the general writable /vaso bind.
"""

from __future__ import annotations

import os
import posixpath
from pathlib import Path


def _match_mount(path: str, mount: str) -> str | None:
    if path == mount:
        return ""
    prefix = f"{mount}/"
    if path.startswith(prefix):
        return path[len(prefix) :]
    return None


def _join_host(host_root: Path, rel: str) -> Path:
    if not rel:
        return host_root
    return host_root / Path(rel)


def insula_to_host_path(
    path: os.PathLike[str] | str,
    estate_root: os.PathLike[str] | str,
    *,
    line: str | None = None,
    repo_root: os.PathLike[str] | str | None = None,
) -> Path:
    """Return the host path for a path produced inside the standard vaso insula."""

    raw = os.fspath(path)
    if not raw.startswith("/"):
        return Path(raw)

    normalized = posixpath.normpath(raw)
    estate = Path(estate_root)
    repo = Path(repo_root) if repo_root is not None else None

    # An absolute path already in this estate is in the caller's namespace.
    # This matters for owned nested estates under /vaso/agents/... too.
    if Path(normalized).is_relative_to(estate):
        return Path(normalized)

    if normalized == "/run/vaso/rootfs-bundle.json" and line:
        return estate / "rootfs-lines" / line / "rootfs-bundle.json"

    mounts: list[tuple[str, Path]] = []
    if repo is not None:
        mounts.extend(
            [
                ("/workspace/experiment", repo / "foundry"),
                ("/workspace/third_party", repo / "third_party"),
                ("/workspace/repo", repo),
            ]
        )

    mounts.extend(
        [
        ("/vaso/agents", estate / "agents"),
        ("/vaso/rootfs-lines", estate / "rootfs-lines"),
        ("/vaso/sources", estate / "sources"),
        ("/vaso", estate / "vaso"),
        ("/opt/vaso", estate / "rootfs-lines" / line / "rootfs" / "opt/vaso" if line else estate / "opt-vaso"),
        ("/workspace", estate / "workspace"),
        ("/home/kvothe", estate / "home" / "kvothe"),
        ]
    )

    for mount, host_root in mounts:
        rel = _match_mount(normalized, mount)
        if rel is not None:
            return _join_host(host_root, rel)
    return Path(raw)
