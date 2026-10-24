"""Translate standard insula projections using the owning estate's map."""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from path_map import insula_to_host_path


def test_insula_path_resolver_matches_release_gate_bind_map(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    repo = tmp_path / "repo"

    assert (
        insula_to_host_path("/vaso/agents/pb-nightly/a.json", estate)
        == estate / "agents" / "pb-nightly" / "a.json"
    )
    assert (
        insula_to_host_path("/vaso/rootfs-lines/cu129/rootfs-bundle.json", estate)
        == estate / "rootfs-lines" / "cu129" / "rootfs-bundle.json"
    )
    assert (
        insula_to_host_path("/run/vaso/rootfs-bundle.json", estate, line="cu129")
        == estate / "rootfs-lines" / "cu129" / "rootfs-bundle.json"
    )
    assert (
        insula_to_host_path("/vaso/sources/native/src.tar.gz", estate)
        == estate / "sources" / "native" / "src.tar.gz"
    )
    assert (
        insula_to_host_path("/vaso/run/release-cu129/torch.whl", estate)
        == estate / "vaso" / "run" / "release-cu129" / "torch.whl"
    )
    assert (
        insula_to_host_path("/opt/vaso/bin/bazel-real", estate)
        == estate / "opt-vaso" / "bin" / "bazel-real"
    )
    assert (
        insula_to_host_path("/opt/vaso/bin/bazel-real", estate, line="cu129")
        == estate / "rootfs-lines/cu129/rootfs/opt/vaso/bin/bazel-real"
    )
    nested_estate = Path("/vaso/agents/owned/estate")
    already_local = nested_estate / "ci/nightly/acceptance.json"
    assert insula_to_host_path(already_local, nested_estate) == already_local
    assert (
        insula_to_host_path("/vaso/lines/cu129/wheel.whl", nested_estate)
        == nested_estate / "vaso/lines/cu129/wheel.whl"
    )
    assert (
        insula_to_host_path("/workspace/build/input.txt", estate)
        == estate / "workspace" / "build" / "input.txt"
    )
    assert (
        insula_to_host_path(
            "/workspace/repo/tools/release/cut.py", estate, repo_root=repo
        )
        == repo / "tools" / "release" / "cut.py"
    )
    assert (
        insula_to_host_path("/workspace/experiment/run.sh", estate, repo_root=repo)
        == repo / "foundry" / "run.sh"
    )
    assert (
        insula_to_host_path(
            "/workspace/third_party/rust_pyo3_cargo/vendor-manifest.tsv",
            estate,
            repo_root=repo,
        )
        == repo / "third_party" / "rust_pyo3_cargo" / "vendor-manifest.tsv"
    )
    assert (
        insula_to_host_path("/home/kvothe/.cache/pip", estate)
        == estate / "home" / "kvothe" / ".cache" / "pip"
    )


@pytest.mark.parametrize("module", ["insula", "foundry"])
def test_module_projection_resolves_to_checkout(tmp_path, module):
    repo = tmp_path / "repo"
    result = insula_to_host_path(
        f"/workspace/{module}/README.md", tmp_path / "estate", repo_root=repo
    )
    assert result == repo / module / "README.md"


def test_mount_prefix_requires_a_complete_path_component(tmp_path):
    estate = tmp_path / "estate"
    assert (
        insula_to_host_path("/vaso/agents-old/log", estate)
        == estate / "vaso/agents-old/log"
    )
    assert insula_to_host_path("/vaso-old/log", estate) == Path("/vaso-old/log")
    assert insula_to_host_path("relative/log", estate) == Path("relative/log")
