from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class RepoMount:
    name: str
    host: Path
    sandbox: str
    mode: Literal["ro", "rw"] = "ro"


@dataclass(frozen=True)
class VasoConfig:
    host_root: Path
    repo_root: Path
    state_root: Path
    repos: dict[str, RepoMount]


def resolve_repo_self(start: Path, host_root: Path) -> Path:
    resolved = start.resolve()
    root = host_root.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"repo://self resolved outside host_root: {resolved} not under {root}")
    return resolved


def default_config(repo_root: Path | None = None) -> VasoConfig:
    repo = (repo_root or Path.cwd()).resolve()
    host_root = repo.parent
    self_repo = resolve_repo_self(repo, host_root)
    state_root = self_repo / ".vaso"
    return VasoConfig(
        host_root=host_root,
        repo_root=self_repo,
        state_root=state_root,
        repos={
            "vaso": RepoMount(
                name="vaso",
                host=self_repo,
                sandbox="/workspace/vaso",
                mode="rw",
            )
        },
    )
