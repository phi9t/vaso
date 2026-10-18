"""Behavioral tests for scripts/agents/relay.py against real temporary git repos."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

RELAY_PATH = Path(__file__).resolve().parents[1] / "scripts" / "agents" / "relay.py"
_spec = importlib.util.spec_from_file_location("relay", RELAY_PATH)
assert _spec is not None and _spec.loader is not None
relay = importlib.util.module_from_spec(_spec)
sys.modules["relay"] = relay
_spec.loader.exec_module(relay)

TRACKER = ".scratch/effort"


def sh(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def repos(tmp_path: Path) -> dict[str, Path]:
    """A follower checkout on `target` and a lead worktree on `landing`."""
    follower = tmp_path / "follower"
    follower.mkdir()
    sh(follower, "init", "-q", "-b", "target")
    sh(follower, "config", "user.email", "t@example.com")
    sh(follower, "config", "user.name", "t")
    write(follower / "a.txt", "base\n")
    write(follower / "keep.txt", "base\n")
    sh(follower, "add", ".")
    sh(follower, "commit", "-q", "-m", "base")
    lead = tmp_path / "lead"
    sh(follower, "worktree", "add", "-q", "-b", "landing", str(lead), "target")
    return {"follower": follower, "lead": lead, "tmp": tmp_path}


def lead_commit(lead: Path, files: dict[str, str], message: str = "lead") -> None:
    for rel, text in files.items():
        write(lead / rel, text)
    sh(lead, "add", "-A")
    sh(lead, "commit", "-q", "-m", message)


def superseded_setup(repos: dict[str, Path]) -> Path:
    """Follower has an in-flight edit to a.txt plus an untracked tracker copy;
    the lead lands a hardened a.txt and commits the same tracker."""
    follower, lead = repos["follower"], repos["lead"]
    write(follower / "a.txt", "in-flight draft\n")
    write(follower / TRACKER / "spec.md", "spec v1\n")
    lead_commit(lead, {"a.txt": "hardened\n", f"{TRACKER}/spec.md": "spec v1\n"})
    manifest = repos["tmp"] / "manifest.json"
    assert relay.main([
        "manifest", "--follower", str(follower), "--landing", "landing", "--tracker", TRACKER,
        "--superseded", "a.txt", "--out", str(manifest),
    ]) == 0
    return manifest


def land(repos: dict[str, Path], manifest: Path | None, *extra: str) -> int:
    argv = ["land", "--follower", str(repos["follower"]), "--tracker", TRACKER,
            "--premerge-dir", str(repos["tmp"] / "premerge")]
    if manifest is not None:
        argv += ["--manifest", str(manifest)]
    else:
        argv += ["--landing", "landing"]
    return relay.main([*argv, *extra])


def test_land_fast_forwards_and_discards_only_superseded_state(repos):
    manifest = superseded_setup(repos)
    assert land(repos, manifest, "--verify", "test -f a.txt") == 0
    follower = repos["follower"]
    assert sh(follower, "rev-parse", "HEAD") == sh(follower, "rev-parse", "landing")
    assert (follower / "a.txt").read_text() == "hardened\n"
    assert sh(follower, "status", "--porcelain") == ""
    assert not (repos["tmp"] / "premerge").exists()


def test_land_refuses_unexpected_dirty_file_and_changes_nothing(repos, capsys):
    manifest = superseded_setup(repos)
    write(repos["follower"] / "keep.txt", "follower's own unfinished work\n")
    head = sh(repos["follower"], "rev-parse", "HEAD")
    assert land(repos, manifest) == 2
    assert "keep.txt" in capsys.readouterr().err
    assert sh(repos["follower"], "rev-parse", "HEAD") == head
    assert (repos["follower"] / "a.txt").read_text() == "in-flight draft\n"
    assert (repos["follower"] / TRACKER / "spec.md").is_file()


def test_land_allows_dirty_tracked_file_outside_landing_diff(repos, capsys):
    lead_commit(repos["lead"], {"lead.txt": "lead version\n"})
    write(repos["follower"] / "keep.txt", "follower's own unfinished work\n")
    assert land(repos, None, "--allow-dirty-non-overlap", "--verify", "test -f lead.txt") == 0
    out = capsys.readouterr().out
    assert "DIRTY_NON_OVERLAP kept=1" in out
    follower = repos["follower"]
    assert sh(follower, "rev-parse", "HEAD") == sh(follower, "rev-parse", "landing")
    assert (follower / "keep.txt").read_text() == "follower's own unfinished work\n"
    assert relay.dirty_entries(follower) == [(" M", "keep.txt")]


def test_land_refuses_dirty_tracked_file_in_landing_diff(repos, capsys):
    lead_commit(repos["lead"], {"keep.txt": "lead version\n"})
    write(repos["follower"] / "keep.txt", "follower's own unfinished work\n")
    head = sh(repos["follower"], "rev-parse", "HEAD")
    assert land(repos, None, "--allow-dirty-non-overlap") == 2
    assert "keep.txt" in capsys.readouterr().err
    follower = repos["follower"]
    assert sh(follower, "rev-parse", "HEAD") == head
    assert (follower / "keep.txt").read_text() == "follower's own unfinished work\n"


def test_land_allow_dirty_non_overlap_keeps_manifest_tracker_behavior(repos, capsys):
    manifest = superseded_setup(repos)
    write(repos["follower"] / "keep.txt", "follower's own unfinished work\n")
    assert land(repos, manifest, "--allow-dirty-non-overlap") == 0
    out = capsys.readouterr().out
    follower = repos["follower"]
    assert "DIRTY_NON_OVERLAP kept=1" in out
    assert (follower / "a.txt").read_text() == "hardened\n"
    assert (follower / "keep.txt").read_text() == "follower's own unfinished work\n"
    assert relay.dirty_entries(follower) == [(" M", "keep.txt")]
    assert not (repos["tmp"] / "premerge").exists()


def test_land_allow_dirty_non_overlap_dry_run_has_same_preconditions(repos, capsys):
    lead_commit(repos["lead"], {"lead.txt": "lead version\n"})
    write(repos["follower"] / "keep.txt", "follower's own unfinished work\n")
    head = sh(repos["follower"], "rev-parse", "HEAD")
    assert land(repos, None, "--allow-dirty-non-overlap", "--dry-run") == 0
    out = capsys.readouterr().out
    assert "DIRTY_NON_OVERLAP kept=1" in out
    assert "DRY_RUN ok" in out
    assert sh(repos["follower"], "rev-parse", "HEAD") == head
    assert (repos["follower"] / "lead.txt").exists() is False
    assert (repos["follower"] / "keep.txt").read_text() == "follower's own unfinished work\n"

    write(repos["follower"] / "keep.txt", "same dirty work, still should block overlap\n")
    lead_commit(repos["lead"], {"keep.txt": "lead version\n"}, "lead overlaps keep")
    assert land(repos, None, "--allow-dirty-non-overlap", "--dry-run") == 2
    assert "keep.txt" in capsys.readouterr().err
    assert sh(repos["follower"], "rev-parse", "HEAD") == head
    assert (repos["follower"] / "keep.txt").read_text() == "same dirty work, still should block overlap\n"


def test_land_refuses_when_superseded_content_changed_after_manifest(repos, capsys):
    manifest = superseded_setup(repos)
    write(repos["follower"] / "a.txt", "follower kept editing\n")
    assert land(repos, manifest) == 2
    assert "content changed since the manifest" in capsys.readouterr().err
    assert (repos["follower"] / "a.txt").read_text() == "follower kept editing\n"


def test_land_refuses_non_fast_forward(repos, capsys):
    lead_commit(repos["lead"], {"lead.txt": "x\n"})
    write(repos["follower"] / "mine.txt", "y\n")
    sh(repos["follower"], "add", "mine.txt")
    sh(repos["follower"], "commit", "-q", "-m", "follower moved on")
    assert land(repos, None) == 2
    assert "ask the lead to rebase" in capsys.readouterr().err


def test_land_reports_nothing_to_land_when_follower_already_contains_landing(repos, capsys):
    # The follower committed on the target and the landing branch has no lead
    # commits of its own: there is nothing to land, so this is not a refusal.
    write(repos["follower"] / "mine.txt", "y\n")
    sh(repos["follower"], "add", "mine.txt")
    sh(repos["follower"], "commit", "-q", "-m", "follower moved on")
    head = sh(repos["follower"], "rev-parse", "HEAD")
    assert land(repos, None) == 0
    assert "NOTHING_TO_LAND" in capsys.readouterr().out
    assert sh(repos["follower"], "rev-parse", "HEAD") == head


def test_land_keeps_premerge_copy_when_follower_edited_tracker(repos, capsys):
    manifest = superseded_setup(repos)
    write(repos["follower"] / TRACKER / "spec.md", "spec v1\n- follower comment\n")
    assert land(repos, manifest) == 0
    out = capsys.readouterr().out
    assert "TRACKER_DIFFERS" in out
    assert (repos["tmp"] / "premerge" / "spec.md").read_text().endswith("follower comment\n")


def test_land_dry_run_changes_nothing(repos, capsys):
    manifest = superseded_setup(repos)
    head = sh(repos["follower"], "rev-parse", "HEAD")
    assert land(repos, manifest, "--dry-run") == 0
    assert "DRY_RUN ok" in capsys.readouterr().out
    assert sh(repos["follower"], "rev-parse", "HEAD") == head
    assert (repos["follower"] / "a.txt").read_text() == "in-flight draft\n"


def test_land_reports_verify_failure(repos):
    lead_commit(repos["lead"], {"lead.txt": "x\n"})
    assert land(repos, None, "--verify", "false") == 4


def test_manifest_refuses_superseded_path_that_is_not_dirty(repos, capsys):
    assert relay.main([
        "manifest", "--follower", str(repos["follower"]), "--landing", "landing", "--tracker", TRACKER,
        "--superseded", "a.txt", "--out", str(repos["tmp"] / "m.json"),
    ]) == 2
    assert "not dirty" in capsys.readouterr().err


def sync(repos: dict[str, Path]) -> int:
    return relay.main([
        "sync-tracker", "--lead-worktree", str(repos["lead"]), "--follower", str(repos["follower"]),
        "--tracker", TRACKER, "--state", str(repos["tmp"] / "sync-state.json"),
    ])


def test_sync_tracker_guards_follower_edits_and_mid_landing(repos, capsys):
    write(repos["lead"] / TRACKER / "spec.md", "v1\n")
    assert sync(repos) == 0
    assert (repos["follower"] / TRACKER / "spec.md").read_text() == "v1\n"

    write(repos["lead"] / TRACKER / "spec.md", "v2\n")
    assert sync(repos) == 0
    assert (repos["follower"] / TRACKER / "spec.md").read_text() == "v2\n"

    write(repos["follower"] / TRACKER / "spec.md", "v2\n- follower ack\n")
    assert sync(repos) == 2
    assert "edited by someone else" in capsys.readouterr().err
    assert (repos["follower"] / TRACKER / "spec.md").read_text().endswith("follower ack\n")

    write(repos["follower"] / TRACKER / "spec.md", "v2\n")
    import shutil

    shutil.rmtree(repos["follower"] / TRACKER)
    assert sync(repos) == 2
    assert "mid-landing" in capsys.readouterr().err
    assert not (repos["follower"] / TRACKER).exists()


def test_sync_tracker_is_noop_once_tracked(repos, capsys):
    write(repos["follower"] / TRACKER / "spec.md", "v1\n")
    sh(repos["follower"], "add", ".")
    sh(repos["follower"], "commit", "-q", "-m", "tracker landed")
    write(repos["lead"] / TRACKER / "spec.md", "lead edit\n")
    assert sync(repos) == 0
    assert "TRACKED" in capsys.readouterr().out
    assert (repos["follower"] / TRACKER / "spec.md").read_text() == "v1\n"


def rebase(repos: dict[str, Path], *extra: str) -> int:
    return relay.main(["rebase", "--lead-worktree", str(repos["lead"]), "--target", "target", *extra])


def rebase_verified(repos: dict[str, Path], estate: Path, verify: str = "true") -> int:
    return rebase(repos, "--verify", verify, "--agent", "lead", "--io-root", str(estate))


def autoland_step(repos: dict[str, Path], estate: Path, *extra: str) -> int:
    return relay.main([
        "autoland-step", "--lead-worktree", str(repos["lead"]), "--follower", str(repos["follower"]),
        "--target", "target", "--landing", "landing", "--tracker", TRACKER,
        "--premerge-dir", str(repos["tmp"] / "premerge"), "--agent", "lead", "--io-root", str(estate),
        *extra,
    ])


def test_rebase_replays_lead_commits_and_verifies(repos, capsys):
    lead_commit(repos["lead"], {"lead.txt": "x\n"})
    write(repos["follower"] / "f.txt", "y\n")
    sh(repos["follower"], "add", "f.txt")
    sh(repos["follower"], "commit", "-q", "-m", "follower commit")
    assert rebase(repos, "--verify", "test -f f.txt && test -f lead.txt") == 0
    out = capsys.readouterr().out
    assert "REBASED" in out and "VERIFY_PASSED" in out
    assert relay.is_ancestor(repos["lead"], "target", "HEAD")


def test_rebase_stops_on_conflict_without_resolving(repos, capsys):
    lead_commit(repos["lead"], {"a.txt": "lead version\n"})
    write(repos["follower"] / "a.txt", "follower version\n")
    sh(repos["follower"], "commit", "-q", "-am", "follower edits a")
    assert rebase(repos) == 3
    out = capsys.readouterr().out
    assert "REBASE_CONFLICT" in out and "a.txt" in out
    assert "<<<<<<<" in (repos["lead"] / "a.txt").read_text()


def test_rebase_refuses_dirty_lead_worktree(repos, capsys):
    write(repos["lead"] / "wip.txt", "uncommitted\n")
    assert rebase(repos) == 2
    assert "uncommitted" in capsys.readouterr().err


def test_watch_state_diff_reports_each_kind_of_change():
    old = relay.WatchState(tip="aaa", busy="busy", tracker="t1", ack_lines=0, deleted=())
    assert relay.diff_watch_states(old, old) == []
    new = relay.WatchState(tip="bbb", busy="idle", tracker="t2", ack_lines=2, deleted=("tools/check.py",))
    assert relay.diff_watch_states(old, new, "Capture x") == [
        "TARGET_MOVED aaa -> bbb: Capture x",
        "FOLLOWER busy -> idle",
        "TRACKER_CHANGED in follower checkout",
        "ACK_CHANGED lines=2",
        "FOLLOWER_DELETED_TRACKED tools/check.py",
    ]
    restored = relay.WatchState(tip="bbb", busy="idle", tracker="t2", ack_lines=2, deleted=())
    assert relay.diff_watch_states(new, restored) == ["FOLLOWER_RESTORED_TRACKED tools/check.py"]


def test_watch_reads_ack_lines_and_follower_exit(repos, tmp_path):
    handoff = tmp_path / "handoff.md"
    handoff.write_text("# H\n\n## Acknowledgement\n\n- acked\n", encoding="utf-8")
    assert relay._ack_lines(handoff, "## Acknowledgement") == 1
    assert relay._busy_state("true", None) == "busy"
    assert relay._busy_state("false", None) == "idle"
    assert relay._busy_state(None, 2**22 + 12345) == "gone"


def test_land_leaves_unrelated_untracked_files_alone(repos):
    manifest = superseded_setup(repos)
    write(repos["follower"] / "scratch_graph.json", "{}\n")
    assert land(repos, manifest) == 0
    assert (repos["follower"] / "scratch_graph.json").read_text() == "{}\n"
    assert sh(repos["follower"], "status", "--porcelain") == "?? scratch_graph.json"


def test_land_refuses_untracked_file_the_landing_branch_would_create(repos, capsys):
    lead_commit(repos["lead"], {"new_tool.py": "lead version\n"})
    write(repos["follower"] / "new_tool.py", "follower's untracked copy\n")
    assert land(repos, None) == 2
    assert "new_tool.py" in capsys.readouterr().err
    assert (repos["follower"] / "new_tool.py").read_text() == "follower's untracked copy\n"


def test_tracked_deletions_are_detected(repos):
    assert relay.tracked_deletions(repos["follower"]) == ()
    (repos["follower"] / "keep.txt").unlink()
    write(repos["follower"] / "scratch.txt", "untracked\n")
    assert relay.tracked_deletions(repos["follower"]) == ("keep.txt",)


def test_rebase_moves_landing_ref_only_after_verify_passes(repos, capsys):
    lead_commit(repos["lead"], {"lead.txt": "x\n"})
    before = sh(repos["lead"], "rev-parse", "landing")
    write(repos["follower"] / "f.txt", "y\n")
    sh(repos["follower"], "add", "f.txt")
    sh(repos["follower"], "commit", "-q", "-m", "follower commit")
    assert rebase(repos, "--verify", "false") == 4
    out = capsys.readouterr().out
    assert "VERIFY_FAILED" in out and "landing branch unchanged" in out
    assert sh(repos["lead"], "rev-parse", "landing") == before
    assert sh(repos["lead"], "rev-parse", "--abbrev-ref", "HEAD") == "landing"
    assert sh(repos["lead"], "status", "--porcelain") == ""

    assert rebase(repos, "--verify", "test -f f.txt") == 0
    assert relay.is_ancestor(repos["lead"], "target", "landing")
    assert sh(repos["lead"], "rev-parse", "--abbrev-ref", "HEAD") == "landing"


def test_rebase_conflict_never_moves_landing_ref(repos, capsys):
    lead_commit(repos["lead"], {"a.txt": "lead version\n"})
    before = sh(repos["lead"], "rev-parse", "landing")
    write(repos["follower"] / "a.txt", "follower version\n")
    sh(repos["follower"], "commit", "-q", "-am", "follower edits a")
    assert rebase(repos) == 3
    assert sh(repos["lead"], "rev-parse", "landing") == before


def test_agent_io_root_refuses_tmpfs_and_pins_env(repos, monkeypatch, tmp_path):
    monkeypatch.setattr(relay, "fs_type", lambda path: "tmpfs")
    with pytest.raises(relay.RelayError, match="tmpfs"):
        relay.agent_io_env(tmp_path / "estate", "trae")
    monkeypatch.setattr(relay, "fs_type", lambda path: "ext4")
    env = relay.agent_io_env(tmp_path / "estate", "trae")
    root = tmp_path / "estate" / "agents" / "trae"
    assert env["TMPDIR"] == str(root / "tmp") and (root / "tmp").is_dir()
    assert env["VASO_BAZEL_OB"] == str(root / "bazel-ob")
    assert env["VASO_AGENT_IO_ROOT"] == str(root)


def test_verify_runs_with_pinned_tmpdir(repos, monkeypatch, tmp_path):
    monkeypatch.setattr(relay, "fs_type", lambda path: "ext4")
    lead_commit(repos["lead"], {"lead.txt": "x\n"})
    io_root = tmp_path / "estate"
    probe = tmp_path / "probe.txt"
    assert relay.main([
        "land", "--follower", str(repos["follower"]), "--landing", "landing",
        "--agent", "trae", "--io-root", str(io_root),
        "--verify", f'echo "$TMPDIR|$VASO_BAZEL_OB" > {probe}',
    ]) == 0
    assert probe.read_text().strip() == f"{io_root}/agents/trae/tmp|{io_root}/agents/trae/bazel-ob"


def test_io_health_reports_thresholds_and_new_tmp_entries(tmp_path):
    fake_tmp = tmp_path / "tmp"
    fake_tmp.mkdir()
    old = relay.io_health([fake_tmp], inode_used=lambda p: 0.5, space_used=lambda p: 0.1)
    (fake_tmp / "vaso-new-ob").mkdir()
    (fake_tmp / "unrelated").mkdir()
    new = relay.io_health([fake_tmp], inode_used=lambda p: 0.93, space_used=lambda p: 0.1)
    events = relay.diff_io_health(old, new)
    assert f"IO_INODES_HIGH {fake_tmp} 93%" in events
    assert f"IO_NEW_TMP_ENTRY {fake_tmp}/vaso-new-ob" in events
    assert not any("unrelated" in e for e in events)


def test_verify_that_writes_into_the_source_tree_fails(repos, monkeypatch, capsys):
    lead_commit(repos["lead"], {"lead.txt": "x\n"})
    before = sh(repos["lead"], "rev-parse", "landing")
    write(repos["follower"] / "f.txt", "y\n")
    sh(repos["follower"], "add", "f.txt")
    sh(repos["follower"], "commit", "-q", "-m", "follower commit")
    # e.g. a Bazel run whose --output_base expanded to "" builds into the tree
    assert rebase(repos, "--verify", "mkdir -p execroot && touch execroot/x") == 5
    out = capsys.readouterr().out
    assert "VERIFY_DIRTIED_TREE" in out and "execroot/x" in out
    assert sh(repos["lead"], "rev-parse", "landing") == before


def test_land_verify_that_dirties_tree_fails(repos, capsys):
    lead_commit(repos["lead"], {"lead.txt": "x\n"})
    assert land(repos, None, "--verify", "touch stray-output.txt") == 5
    assert "VERIFY_DIRTIED_TREE" in capsys.readouterr().out


def test_watch_flags_a_rewritten_target(repos):
    follower = repos["follower"]
    lead_commit(follower, {"x.txt": "1\n"}, "reseat")
    old_tip = sh(follower, "rev-parse", "--short", "target")
    sh(follower, "commit", "-q", "--amend", "-m", "reseat (trailer fixed)")
    new_tip = sh(follower, "rev-parse", "--short", "target")
    assert relay.target_rewritten(follower, old_tip, new_tip)
    lead_commit(follower, {"y.txt": "2\n"}, "next")
    assert not relay.target_rewritten(follower, new_tip, sh(follower, "rev-parse", "--short", "target"))


def test_autoland_step_lands_verified_clean_branch(repos, tmp_path, capsys):
    estate = tmp_path / "estate"
    lead_commit(repos["lead"], {"lead.txt": "lead version\n"})
    assert rebase_verified(repos, estate, "test -f lead.txt") == 0
    landing_sha = sh(repos["lead"], "rev-parse", "landing")
    assert json.loads((estate / "agents" / "lead" / "relay-verify-results.json").read_text()) == {
        landing_sha: "PASSED",
    }
    assert autoland_step(repos, estate) == 0
    out = capsys.readouterr().out
    assert "AUTOLAND_LANDED" in out
    assert sh(repos["follower"], "rev-parse", "HEAD") == sh(repos["follower"], "rev-parse", "landing")
    assert (repos["follower"] / "lead.txt").read_text() == "lead version\n"


def test_autoland_step_refuses_unverified_landing_branch(repos, tmp_path, capsys):
    lead_commit(repos["lead"], {"lead.txt": "lead version\n"})
    head = sh(repos["follower"], "rev-parse", "HEAD")
    assert autoland_step(repos, tmp_path / "estate") == 0
    assert "UNVERIFIED" in capsys.readouterr().out
    assert sh(repos["follower"], "rev-parse", "HEAD") == head
    assert not (repos["follower"] / "lead.txt").exists()


def test_autoland_step_refuses_non_fast_forward_landing_branch(repos, tmp_path, capsys):
    estate = tmp_path / "estate"
    lead_commit(repos["lead"], {"lead.txt": "lead version\n"})
    assert rebase_verified(repos, estate, "test -f lead.txt") == 0
    write(repos["follower"] / "mine.txt", "follower version\n")
    sh(repos["follower"], "add", "mine.txt")
    sh(repos["follower"], "commit", "-q", "-m", "follower moved")
    head = sh(repos["follower"], "rev-parse", "HEAD")

    assert autoland_step(repos, estate) == 0
    assert "NON_FF" in capsys.readouterr().out
    assert sh(repos["follower"], "rev-parse", "HEAD") == head
    assert not (repos["follower"] / "lead.txt").exists()


def test_autoland_step_refuses_overlapping_dirty_tracked_file(repos, tmp_path, capsys):
    estate = tmp_path / "estate"
    lead_commit(repos["lead"], {"keep.txt": "lead version\n"})
    assert rebase_verified(repos, estate, "test -f keep.txt") == 0
    write(repos["follower"] / "keep.txt", "follower draft\n")
    head = sh(repos["follower"], "rev-parse", "HEAD")

    assert autoland_step(repos, estate) == 0
    out = capsys.readouterr().out
    assert "DIRTY_OVERLAP" in out and "keep.txt" in out
    assert sh(repos["follower"], "rev-parse", "HEAD") == head
    assert (repos["follower"] / "keep.txt").read_text() == "follower draft\n"


def test_autoland_step_keeps_non_overlapping_dirty_tracked_file(repos, tmp_path, capsys):
    estate = tmp_path / "estate"
    lead_commit(repos["lead"], {"lead.txt": "lead version\n"})
    assert rebase_verified(repos, estate, "test -f lead.txt") == 0
    write(repos["follower"] / "keep.txt", "follower draft\n")

    assert autoland_step(repos, estate) == 0
    out = capsys.readouterr().out
    assert "DIRTY_NON_OVERLAP kept=1" in out and "AUTOLAND_LANDED" in out
    assert sh(repos["follower"], "rev-parse", "HEAD") == sh(repos["follower"], "rev-parse", "landing")
    assert (repos["follower"] / "keep.txt").read_text() == "follower draft\n"
    assert relay.dirty_entries(repos["follower"]) == [(" M", "keep.txt")]


def test_claim_refuses_other_agent_until_lease_expires(tmp_path, monkeypatch, capsys):
    estate = tmp_path / "estate"
    now = {"t": 1000.0}
    monkeypatch.setattr(relay.time, "time", lambda: now["t"])

    assert relay.main(["claim", "C4a", "--agent", "worker-a", "--lease-minutes", "1",
                       "--estate-root", str(estate)]) == 0
    assert relay.main(["claimed", "C4a", "--estate-root", str(estate)]) == 0

    assert relay.main(["claim", "C4a", "--agent", "worker-b", "--estate-root", str(estate)]) == 2
    assert "claimed by worker-a" in capsys.readouterr().err

    now["t"] = 1061.0
    assert relay.main(["claim", "C4a", "--agent", "worker-b", "--estate-root", str(estate)]) == 0
    claim = json.loads((estate / "agents" / "claims" / "C4a.json").read_text())
    assert claim["agent"] == "worker-b"


def test_heartbeat_claims_list_and_release(tmp_path, monkeypatch, capsys):
    estate = tmp_path / "estate"
    now = {"t": 2000.0}
    monkeypatch.setattr(relay.time, "time", lambda: now["t"])

    assert relay.main(["claim", "F7c", "--agent", "worker-a", "--lease-minutes", "1",
                       "--estate-root", str(estate)]) == 0
    first = json.loads((estate / "agents" / "claims" / "F7c.json").read_text())

    now["t"] = 2030.0
    assert relay.main(["heartbeat", "F7c", "--agent", "worker-a", "--lease-minutes", "2",
                       "--estate-root", str(estate)]) == 0
    renewed = json.loads((estate / "agents" / "claims" / "F7c.json").read_text())
    assert renewed["expires_at"] > first["expires_at"]

    assert relay.main(["claims", "--estate-root", str(estate)]) == 0
    assert "F7c worker-a" in capsys.readouterr().out

    assert relay.main(["release", "F7c", "--agent", "worker-b", "--estate-root", str(estate)]) == 2
    assert "owned by worker-a" in capsys.readouterr().err
    assert relay.main(["release", "F7c", "--agent", "worker-a", "--estate-root", str(estate)]) == 0
    assert relay.main(["claimed", "F7c", "--estate-root", str(estate)]) == 1


def issue_path(tmp_path: Path) -> Path:
    issue = tmp_path / ".scratch" / "effort" / "issues" / "01-demo.md"
    write(issue, "# Demo\n\nStatus: open\n\nHuman-written context stays.\n\n## Comments\n\n- keep this note\n")
    return issue


def ledger_entries(issue: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (issue.parents[1] / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_ticket_status_and_note_append_ledger_and_render_marked_block(tmp_path, monkeypatch):
    issue = issue_path(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    date = bin_dir / "date"
    date.write_text("#!/usr/bin/env bash\nprintf '2026-10-02T12:34:56Z\\n'\n", encoding="utf-8")
    date.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("VASO_AGENT", "worker-a")

    assert relay.main(["ticket", "set", str(issue), "--status", "ready-for-review"]) == 0
    assert relay.main(["ticket", "note", str(issue), "--evidence", "standing verify passed"]) == 0

    entries = ledger_entries(issue)
    assert entries == [
        {
            "agent": "worker-a",
            "status": "ready-for-review",
            "ticket": "issues/01-demo.md",
            "type": "status",
            "written_utc": "2026-10-02T12:34:56Z",
        },
        {
            "agent": "worker-a",
            "evidence": "standing verify passed",
            "ticket": "issues/01-demo.md",
            "type": "evidence",
            "written_utc": "2026-10-02T12:34:56Z",
        },
    ]
    text = issue.read_text(encoding="utf-8")
    assert "Status: ready-for-review\n" in text
    assert "Human-written context stays." in text
    assert "## Comments\n\n- keep this note\n" in text
    assert "<!-- relay-ticket-ledger:start -->" in text
    assert "<!-- relay-ticket-ledger:end -->" in text
    assert "- 2026-10-02T12:34:56Z [worker-a] standing verify passed" in text


def test_ticket_render_replaces_only_marker_bounded_evidence(tmp_path):
    issue = tmp_path / ".scratch" / "effort" / "issues" / "02-demo.md"
    write(
        issue,
        "# Demo\n\nStatus: open\n\nBefore markers.\n\n"
        "<!-- relay-ticket-ledger:start -->\n"
        "old generated text\n"
        "<!-- relay-ticket-ledger:end -->\n\n"
        "After markers.\n",
    )

    assert relay.main(["ticket", "note", str(issue), "--evidence", "new proof"]) == 0

    text = issue.read_text(encoding="utf-8")
    assert "Before markers." in text
    assert "After markers." in text
    assert "old generated text" not in text
    assert text.count("<!-- relay-ticket-ledger:start -->") == 1
    assert text.count("## Evidence (ledger)") == 1
    assert "new proof" in text


def test_ticket_rejects_invalid_status_without_writing_ledger(tmp_path, capsys):
    issue = issue_path(tmp_path)

    assert relay.main(["ticket", "set", str(issue), "--status", "ready for review"]) == 2

    assert "invalid status" in capsys.readouterr().err
    assert "Status: open\n" in issue.read_text(encoding="utf-8")
    assert not (issue.parents[1] / "ledger.jsonl").exists()


def test_ticket_concurrent_notes_are_append_only_and_render_all_evidence(tmp_path):
    issue = issue_path(tmp_path)
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                str(RELAY_PATH),
                "ticket",
                "note",
                str(issue),
                "--evidence",
                f"proof {index}",
            ],
            env={**os.environ, "VASO_AGENT": f"worker-{index % 3}"},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(20)
    ]

    for proc in procs:
        out, err = proc.communicate(timeout=10)
        assert proc.returncode == 0, out + err

    entries = ledger_entries(issue)
    assert len(entries) == 20
    assert sorted(entry["evidence"] for entry in entries) == sorted(f"proof {index}" for index in range(20))
    assert all(entry["written_utc"].endswith("Z") for entry in entries)
    text = issue.read_text(encoding="utf-8")
    assert text.count("<!-- relay-ticket-ledger:start -->") == 1
    for index in range(20):
        assert f"proof {index}" in text
