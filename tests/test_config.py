import pytest

from vaso.config import default_config, resolve_repo_self
from vaso.paths import ensure_host_layout


def test_default_config_uses_workspace_repo_and_vaso_state(tmp_path):
    repo = tmp_path / "workspace" / "vaso"
    repo.mkdir(parents=True)
    config = default_config(repo)
    assert config.host_root == tmp_path / "workspace"
    assert config.state_root == repo / ".vaso"
    assert config.repos["vaso"].sandbox == "/workspace/vaso"
    assert config.repos["vaso"].mode == "rw"


def test_repo_self_must_stay_under_host_root(tmp_path):
    host_root = tmp_path / "workspace"
    repo = host_root / "vaso"
    repo.mkdir(parents=True)
    assert resolve_repo_self(repo, host_root) == repo.resolve()
    with pytest.raises(ValueError, match="outside host_root"):
        resolve_repo_self(tmp_path / "elsewhere", host_root)


def test_ensure_host_layout_creates_backing_dirs(tmp_path):
    repo = tmp_path / "workspace" / "vaso"
    repo.mkdir(parents=True)
    config = default_config(repo)
    ensure_host_layout(config)
    for rel in ["rootfs", "home/kvothe", "state", "cache", "runs", "traces", "tmp"]:
        assert (config.state_root / rel).is_dir()
