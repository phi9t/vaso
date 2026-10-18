"""Publication snapshot tooling uses external scrub rules and synthetic fixtures."""
from __future__ import annotations

import json
import os
import runpy
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SANITIZE = ROOT / "tools" / "publish" / "sanitize.py"
PUBLISH = ROOT / "tools" / "publish" / "publish_snapshot.py"
SNAPSHOT = ROOT / "tools" / "publish" / "snapshot.sh"
TORCH_ARTIFACT_REGEN = ROOT / "tools" / "publish" / "regenerate_pending_torch_artifacts.py"

RULES = [[r"/home/example/estate", "$VASO_ESTATE_ROOT"], [r"(?i)\bsyntheticvendor\b", "<vendor>"]]


def git(repo: Path, *args: str, check: bool = True) -> str:
    res = subprocess.run(["git", "-C", str(repo), *args], check=check, capture_output=True, text=True)
    return res.stdout.strip()


def rules_file(tmp_path: Path, rules: object | None = None) -> Path:
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(RULES if rules is None else rules), encoding="utf-8")
    return path


def run_sanitize(tmp_path: Path, root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(SANITIZE), "--rules", str(rules_file(tmp_path)), str(root), *extra],
        capture_output=True,
        text=True,
    )


def init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "public", str(repo)], check=True)
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("public base\n", encoding="utf-8")
    (repo / "public-only.py").write_text("print('base only')\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "public base")
    return repo


def clear_worktree(repo: Path) -> None:
    for child in repo.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def add_private_commit(repo: Path) -> None:
    git(repo, "checkout", "-q", "--orphan", "private")
    clear_worktree(repo)
    (repo / ".scratch").mkdir()
    (repo / ".scratch" / "plan.md").write_text("tracker-only\n", encoding="utf-8")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text("{}\n", encoding="utf-8")
    (repo / ".trae").mkdir()
    (repo / ".trae" / "state.json").write_text("{}\n", encoding="utf-8")
    (repo / ".worktrees").mkdir()
    (repo / ".worktrees" / "scratch").write_text("generated\n", encoding="utf-8")
    (repo / "drop").mkdir()
    (repo / "drop" / "hidden.txt").write_text("exclude by rule\n", encoding="utf-8")
    allow = repo / "foundry" / "tools"
    allow.mkdir(parents=True)
    (allow / "tracker_lint_allowlist.txt").write_text(
        "# .scratch/<effort>/issues/<id>.md format\n"
        ".scratch/example/issues/01-old.md:1:missing-real-run\n"
        "kept/path.md:1:some-rule\n",
        encoding="utf-8",
    )
    (repo / "src").mkdir()
    (repo / "src" / "rename-me.txt").write_text("see /home/example/estate from SyntheticVendor\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "private source", "-m", "Co-authored-by: Synthetic Tool <tool@example.invalid>")


def write_regeneration_fixture(repo: Path, input_text: str) -> None:
    (repo / "data").mkdir()
    (repo / "data" / "input.txt").write_text(input_text, encoding="utf-8")
    (repo / "plans").mkdir()
    (repo / "tools").mkdir()
    (repo / "tools" / "regen.py").write_text(
        "from pathlib import Path\n"
        "import hashlib\n"
        "root = Path.cwd()\n"
        "digest = hashlib.sha256((root / 'data/input.txt').read_bytes()).hexdigest()\n"
        "(root / 'plans/pin.txt').write_text(digest + '\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    (repo / "tools" / "check_pin.py").write_text(
        "from pathlib import Path\n"
        "import hashlib\n"
        "root = Path.cwd()\n"
        "actual = (root / 'plans/pin.txt').read_text(encoding='utf-8').strip()\n"
        "expected = hashlib.sha256((root / 'data/input.txt').read_bytes()).hexdigest()\n"
        "raise SystemExit(0 if actual == expected else 1)\n",
        encoding="utf-8",
    )
    (repo / "tools" / "publish").mkdir(parents=True)
    (repo / "tools" / "publish" / "publication-regenerate.txt").write_text(
        "python3 tools/regen.py\n",
        encoding="utf-8",
    )
    subprocess.run(["python3", "tools/regen.py"], cwd=repo, check=True)


def run_publish(repo: Path, *args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(extra_env or {})
    env.setdefault("VASO_ESTATE_ROOT", str(repo.parent / "estate"))
    return subprocess.run(["python3", str(PUBLISH), *args], cwd=repo, env=env, capture_output=True, text=True)


def test_rewrites_in_rule_order_and_skips_git_and_binaries(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    (tree / ".git").mkdir(parents=True)
    (tree / "a.md").write_text("see /home/example/estate/x at SyntheticVendor\n", encoding="utf-8")
    (tree / ".git" / "config").write_text("SyntheticVendor\n", encoding="utf-8")
    (tree / "blob.bin").write_bytes(b"\xff\xfeSyntheticVendor")
    assert run_sanitize(tmp_path, tree).returncode == 0
    assert (tree / "a.md").read_text(encoding="utf-8") == "see $VASO_ESTATE_ROOT/x at <vendor>\n"
    assert (tree / ".git" / "config").read_text(encoding="utf-8") == "SyntheticVendor\n"
    assert (tree / "blob.bin").read_bytes() == b"\xff\xfeSyntheticVendor"


def test_check_reports_without_rewriting(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.md").write_text("SyntheticVendor\n", encoding="utf-8")
    res = run_sanitize(tmp_path, tree, "--check")
    assert res.returncode == 1 and "a.md" in res.stdout
    assert (tree / "a.md").read_text(encoding="utf-8") == "SyntheticVendor\n"
    (tree / "a.md").write_text("clean\n", encoding="utf-8")
    assert run_sanitize(tmp_path, tree, "--check").returncode == 0


def test_publish_snapshot_creates_one_clean_scrubbed_commit_without_history(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    add_private_commit(repo)
    rules = rules_file(
        tmp_path,
        [
            {"literal": "/home/example/estate", "replace": "$VASO_ESTATE_ROOT"},
            {"regex": "(?i)\\bsyntheticvendor\\b", "replace": "<vendor>"},
            {"literal": "rename-me", "replace": "renamed"},
            {"exclude": "drop/**"},
        ],
    )
    wt = tmp_path / "published"
    res = run_publish(
        repo,
        "--source",
        "private",
        "--base",
        "public",
        "--branch",
        "publish/synthetic",
        "--worktree",
        str(wt),
        "--rules",
        str(rules),
        "--verify-command",
        "true",
        "--message",
        "Publish synthetic snapshot",
    )
    assert res.returncode == 0, res.stderr
    assert git(repo, "rev-list", "--count", "public..publish/synthetic") == "1"
    assert git(repo, "rev-parse", "publish/synthetic^") == git(repo, "rev-parse", "public")
    assert git(repo, "log", "-1", "--format=%s", "publish/synthetic") == "Publish synthetic snapshot"
    body = git(repo, "log", "-1", "--format=%B", "publish/synthetic")
    assert "Co-authored-by:" not in body
    assert git(repo, "log", "-1", "--format=%an <%ae>", "publish/synthetic") == (
        "Snapshot Publisher <snapshot-publisher@example.invalid>"
    )
    files = set(git(repo, "ls-tree", "-r", "--name-only", "publish/synthetic").splitlines())
    assert "README.md" not in files
    assert "src/renamed.txt" in files
    assert not any(f.startswith(".scratch/") for f in files)
    assert not any(f.startswith(".claude/") for f in files)
    assert not any(f.startswith(".trae/") for f in files)
    assert not any(f.startswith(".worktrees/") for f in files)
    assert not any(f.startswith("drop/") for f in files)
    assert (wt / "src" / "renamed.txt").read_text(encoding="utf-8") == (
        "see $VASO_ESTATE_ROOT from <vendor>\n"
    )
    allowlist = wt / "foundry" / "tools" / "tracker_lint_allowlist.txt"
    assert allowlist.read_text(encoding="utf-8") == (
        "# .scratch/<effort>/issues/<id>.md format\n"
        "kept/path.md:1:some-rule\n"
    )
    assert "rule 0 hits: 1" in res.stdout
    assert "rule 1 hits: 1" in res.stdout
    assert "rule 2 hits: 1" in res.stdout
    assert "rescan: clean" in res.stdout
    assert "verify: passed" in res.stdout


def test_publish_snapshot_commit_ignores_caller_git_identity_hooks_and_config(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    add_private_commit(repo)
    hooks = tmp_path / "leaky-hooks"
    hooks.mkdir()
    hook = hooks / "commit-msg"
    hook.write_text('#!/bin/sh\nprintf "\\nCo-authored-by: Leaky Agent <leak@example.invalid>\\n" >> "$1"\n', encoding="utf-8")
    hook.chmod(0o755)
    res = run_publish(
        repo,
        "--source",
        "private",
        "--base",
        "public",
        "--branch",
        "publish/leaky-env",
        "--worktree",
        str(tmp_path / "published"),
        "--rules",
        str(rules_file(tmp_path, [{"literal": "/home/example/estate", "replace": "$VASO_ESTATE_ROOT"}])),
        "--verify-command",
        "true",
        "--message",
        "Publish synthetic snapshot",
        extra_env={
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": str(hooks),
            "GIT_CONFIG_KEY_1": "user.name",
            "GIT_CONFIG_VALUE_1": "Leaky Config",
            "GIT_AUTHOR_NAME": "Leaky Author",
            "GIT_AUTHOR_EMAIL": "leak-author@example.invalid",
            "GIT_COMMITTER_NAME": "Leaky Committer",
            "GIT_COMMITTER_EMAIL": "leak-committer@example.invalid",
        },
    )
    assert res.returncode == 0, res.stderr
    body = git(repo, "log", "-1", "--format=%B", "publish/leaky-env")
    assert body.strip() == "Publish synthetic snapshot"
    assert "Leaky" not in git(repo, "log", "-1", "--format=%an <%ae> %cn <%ce>", "publish/leaky-env")


def test_publish_snapshot_runs_committed_regeneration_after_scrub(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    git(repo, "checkout", "-q", "--orphan", "private")
    clear_worktree(repo)
    write_regeneration_fixture(repo, "source path: /home/example/estate\n")
    private_pin = (repo / "plans" / "pin.txt").read_text(encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "private source")
    wt = tmp_path / "published"
    res = run_publish(
        repo,
        "--source",
        "private",
        "--base",
        "public",
        "--branch",
        "publish/regenerated",
        "--worktree",
        str(wt),
        "--rules",
        str(rules_file(tmp_path, [{"literal": "/home/example/estate", "replace": "$VASO_ESTATE_ROOT"}])),
        "--verify-command",
        "python3 tools/check_pin.py",
        "--dry-run",
    )
    assert res.returncode == 0, res.stderr + res.stdout
    assert "regeneration commands: 1" in res.stdout
    assert (wt / "data" / "input.txt").read_text(encoding="utf-8") == "source path: $VASO_ESTATE_ROOT\n"
    assert (wt / "plans" / "pin.txt").read_text(encoding="utf-8") != private_pin
    assert git(repo, "show", "private:plans/pin.txt") == private_pin.strip()


def test_publish_snapshot_updates_dry_run_index_before_verify(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    git(repo, "checkout", "-q", "--orphan", "private")
    clear_worktree(repo)
    (repo / "src").mkdir()
    (repo / "src" / "kept.py").write_text("print('source only')\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "private source")
    wt = tmp_path / "published"
    verify = (
        "python3 - <<'PY'\n"
        "import subprocess\n"
        "files = subprocess.check_output(['git', 'ls-files'], text=True).splitlines()\n"
        "raise SystemExit(1 if 'public-only.py' in files else 0)\n"
        "PY"
    )
    res = run_publish(
        repo,
        "--source",
        "private",
        "--base",
        "public",
        "--branch",
        "publish/index-synced",
        "--worktree",
        str(wt),
        "--rules",
        str(rules_file(tmp_path, [])),
        "--verify-command",
        verify,
        "--dry-run",
    )
    assert res.returncode == 0, res.stderr + res.stdout


def test_publish_snapshot_applies_committed_exclusions_and_prunes_wiring(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    git(repo, "checkout", "-q", "--orphan", "private")
    clear_worktree(repo)
    (repo / "module" / "private_ext").mkdir(parents=True)
    (repo / "module" / "private_ext" / "payload.txt").write_text("excluded\n", encoding="utf-8")
    (repo / "tools").mkdir()
    (repo / "tools" / "private_ext_check.py").write_text("print('excluded')\n", encoding="utf-8")
    (repo / "tools" / "BUILD.bazel").write_text(
        "exports_files([\n"
        "    \"kept_tool.py\",\n"
        "    \"private_ext_check.py\",\n"
        "])\n"
        "\n"
        "py_test(\n"
        "    name = \"kept_test\",\n"
        "    srcs = [\"kept_tool.py\"],\n"
        ")\n"
        "\n"
        "py_test(\n"
        "    name = \"private_ext_check_test\",\n"
        "    srcs = [\"private_ext_check.py\"],\n"
        "    data = [\"//module/private_ext:all_sources\"],\n"
        ")\n",
        encoding="utf-8",
    )
    (repo / "tools" / "kept_tool.py").write_text("print('kept')\n", encoding="utf-8")
    (repo / "README.md").write_text(
        "Keep this line.\n"
        "See module/private_ext for private details.\n",
        encoding="utf-8",
    )
    (repo / "tools" / "publish").mkdir(parents=True)
    (repo / "tools" / "publish" / "publication-excludes.txt").write_text(
        "module/private_ext\n"
        "module/private_ext/**\n"
        "tools/private_ext*\n",
        encoding="utf-8",
    )
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "private source")
    wt = tmp_path / "published"
    res = run_publish(
        repo,
        "--source",
        "private",
        "--base",
        "public",
        "--branch",
        "publish/excluded",
        "--worktree",
        str(wt),
        "--rules",
        str(rules_file(tmp_path, [])),
        "--verify-command",
        "true",
        "--dry-run",
    )
    assert res.returncode == 0, res.stderr + res.stdout
    assert not (wt / "module" / "private_ext").exists()
    assert not (wt / "tools" / "private_ext_check.py").exists()
    build = (wt / "tools" / "BUILD.bazel").read_text(encoding="utf-8")
    assert "kept_test" in build
    assert "private_ext" not in build
    assert (wt / "README.md").read_text(encoding="utf-8") == "Keep this line.\n"


def test_scoped_scrub_rule_does_not_touch_files_outside_globs(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    git(repo, "checkout", "-q", "--orphan", "private")
    clear_worktree(repo)
    (repo / "metadata").mkdir()
    (repo / "metadata" / "pin.txt").write_text("TOKEN_IN_SCOPE\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "fixture.py").write_text("TOKEN_IN_SCOPE\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "private source")
    wt = tmp_path / "published"
    rules = rules_file(
        tmp_path,
        [
            {
                "literal": "TOKEN_IN_SCOPE",
                "replace": "PUBLIC_TOKEN",
                "include_globs": ["metadata/**"],
            }
        ],
    )
    res = run_publish(
        repo,
        "--source",
        "private",
        "--base",
        "public",
        "--branch",
        "publish/scoped",
        "--worktree",
        str(wt),
        "--rules",
        str(rules),
        "--verify-command",
        "true",
        "--dry-run",
    )
    assert res.returncode == 0, res.stderr + res.stdout
    assert (wt / "metadata" / "pin.txt").read_text(encoding="utf-8") == "PUBLIC_TOKEN\n"
    assert (wt / "src" / "fixture.py").read_text(encoding="utf-8") == "TOKEN_IN_SCOPE\n"
    assert "rule 0 hits: 1" in res.stdout


def test_regenerates_pending_torch_artifact_manifests_from_keys(tmp_path: Path) -> None:
    graph = tmp_path / "foundry"
    (graph / "plans").mkdir(parents=True)
    (graph / "plans" / "keys.json").write_text(
        json.dumps(
            {
                "packages": {
                    "torch/cu129/py-torch": {"merkle_key": "a" * 64},
                    "torch/cu130/py-torch": {"merkle_key": "b" * 64},
                }
            }
        ),
        encoding="utf-8",
    )
    for line in ("cu129", "cu130"):
        path = graph / "plans" / "torch" / line / "torch-artifact.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "archive": f"step-b-pending://torch/{line}/{'c' * 64}",
                    "plan_key": "c" * 64,
                    "sha256": "0" * 64,
                    "source_commit": "d" * 40,
                },
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    res = subprocess.run(
        ["python3", str(TORCH_ARTIFACT_REGEN), "--repo-root", str(graph)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    assert "refreshed: 2" in res.stdout
    assert json.loads((graph / "plans" / "torch" / "cu129" / "torch-artifact.json").read_text()) == {
        "archive": f"step-b-pending://torch/cu129/{'a' * 64}",
        "plan_key": "a" * 64,
        "sha256": "0" * 64,
        "source_commit": "d" * 40,
    }
    assert json.loads((graph / "plans" / "torch" / "cu130" / "torch-artifact.json").read_text()) == {
        "archive": f"step-b-pending://torch/cu130/{'b' * 64}",
        "plan_key": "b" * 64,
        "sha256": "0" * 64,
        "source_commit": "d" * 40,
    }


def test_regenerates_stale_real_torch_artifact_manifest_as_pending(tmp_path: Path) -> None:
    graph = tmp_path / "foundry"
    (graph / "plans").mkdir(parents=True)
    (graph / "plans" / "keys.json").write_text(
        json.dumps(
            {
                "packages": {
                    "torch/cu129/py-torch": {"merkle_key": "a" * 64},
                    "torch/cu130/py-torch": {"merkle_key": "b" * 64},
                }
            }
        ),
        encoding="utf-8",
    )
    manifests = {
        "cu129": {
            "archive": "vaso/artifacts/torch/cu129/torch-prefix-cu129-old.tar",
            "plan_key": "c" * 64,
            "sha256": "e" * 64,
            "source_commit": "d" * 40,
        },
        "cu130": {
            "archive": "vaso/artifacts/torch/cu130/torch-prefix-cu130-current.tar",
            "plan_key": "b" * 64,
            "sha256": "f" * 64,
            "source_commit": "d" * 40,
        },
    }
    for line, manifest in manifests.items():
        path = graph / "plans" / "torch" / line / "torch-artifact.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    res = subprocess.run(
        ["python3", str(TORCH_ARTIFACT_REGEN), "--repo-root", str(graph)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    assert "refreshed: 1" in res.stdout
    assert json.loads((graph / "plans" / "torch" / "cu129" / "torch-artifact.json").read_text()) == {
        "archive": f"step-b-pending://torch/cu129/{'a' * 64}",
        "plan_key": "a" * 64,
        "sha256": "0" * 64,
        "source_commit": "d" * 40,
    }
    assert json.loads((graph / "plans" / "torch" / "cu130" / "torch-artifact.json").read_text()) == manifests["cu130"]


def test_snapshot_wrapper_keeps_existing_interface(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    add_private_commit(repo)
    wt = tmp_path / "wrapped"
    res = subprocess.run(
        [
            "bash",
            str(SNAPSHOT),
            "--source",
            "private",
            "--branch",
            "publish/wrapped",
            "--worktree",
            str(wt),
            "--base",
            "public",
            "--rules",
            str(rules_file(tmp_path)),
            "--verify-command",
            "true",
            "-m",
            "Publish wrapped snapshot",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    assert git(repo, "rev-list", "--count", "public..publish/wrapped") == "1"
    assert git(repo, "log", "-1", "--format=%s", "publish/wrapped") == "Publish wrapped snapshot"


def test_rescan_fails_closed_without_printing_rule_terms(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    git(repo, "checkout", "-q", "--orphan", "private")
    clear_worktree(repo)
    (repo / "leak.txt").write_text("STILL_THERE\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "private source")
    rules = rules_file(tmp_path, [{"literal": "STILL_THERE", "replace": "STILL_THERE"}])
    res = run_publish(
        repo,
        "--source",
        "private",
        "--base",
        "public",
        "--branch",
        "publish/rescan",
        "--worktree",
        str(tmp_path / "rescan"),
        "--rules",
        str(rules),
        "--verify-command",
        "true",
        "--dry-run",
    )
    assert res.returncode == 1
    assert "rescan failed" in res.stderr
    assert "rule 0" in res.stderr
    assert "STILL_THERE" not in res.stdout
    assert "STILL_THERE" not in res.stderr
    assert git(repo, "show-ref", "--verify", "--quiet", "refs/heads/publish/rescan", check=False) == ""


def test_push_refuses_protected_or_non_publish_branches(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    for branch in ("master", "ultron/mainline", "release/synthetic"):
        res = run_publish(
            repo,
            "--source",
            "HEAD",
            "--base",
            "public",
            "--branch",
            branch,
            "--worktree",
            str(tmp_path / branch.replace("/", "_")),
            "--rules",
            str(rules_file(tmp_path)),
            "--verify-command",
            "true",
            "--push",
        )
        assert res.returncode == 2
        assert "push requires a publish/ branch" in res.stderr or "protected branch" in res.stderr


def test_verify_failure_blocks_snapshot_commit(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    add_private_commit(repo)
    res = run_publish(
        repo,
        "--source",
        "private",
        "--base",
        "public",
        "--branch",
        "publish/verify-red",
        "--worktree",
        str(tmp_path / "verify-red"),
        "--rules",
        str(rules_file(tmp_path)),
        "--verify-command",
        "false",
        "--message",
        "Publish should not commit",
    )
    assert res.returncode == 1
    assert "verify: failed" in res.stdout
    if git(repo, "show-ref", "--verify", "--quiet", "refs/heads/publish/verify-red", check=False) == "":
        assert git(repo, "rev-list", "--count", "public..publish/verify-red") == "0"


def test_rules_guard_reports_tracked_rules_file_or_terms_without_printing_terms(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    rules = rules_file(tmp_path, [{"literal": "SYNTHETIC_RULE_TOKEN", "replace": "<redacted>"}])
    (repo / "sanitize-rules.json").write_text("[]\n", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs" / "leak.txt").write_text("SYNTHETIC_RULE_TOKEN\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "tracked rule leak")
    res = run_publish(repo, "--rules", str(rules), "--check-rules-not-tracked")
    assert res.returncode == 1
    assert "tracked sanitize rules file" in res.stderr
    assert "rule 0" in res.stderr
    assert "SYNTHETIC_RULE_TOKEN" not in res.stdout
    assert "SYNTHETIC_RULE_TOKEN" not in res.stderr


def test_snapshot_refuses_missing_rules(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    res = run_publish(
        repo,
        "--source",
        "HEAD",
        "--base",
        "public",
        "--branch",
        "publish/missing-rules",
        "--worktree",
        str(tmp_path / "wt"),
        "--rules",
        str(tmp_path / "none.json"),
        "--verify-command",
        "true",
    )
    assert res.returncode == 2 and "rules file not found" in res.stderr


def test_rescan_refuses_host_identity_left_by_rules(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    git(repo, "checkout", "-q", "--orphan", "private")
    clear_worktree(repo)
    # Built at runtime so this file itself passes the snapshot rescan.
    volume_path = "/" + "/".join(["data07", "someone", "cache"])
    (repo / "volume.txt").write_text(f"cache at {volume_path}\n", encoding="utf-8")
    (repo / "gpu.txt").write_text("-".join(["GPU", "0a1b2c3d", "1111", "2222", "3333", "444455556666"]) + "\n", encoding="utf-8")
    (repo / "nic.txt").write_text("link/ether " + ":".join(["02", "42", "ac", "11", "00", "02"]) + "\n", encoding="utf-8")
    (repo / "nic-hyphen.txt").write_text("-".join(["02", "42", "ac", "11", "00", "02"]) + "\n", encoding="utf-8")
    (repo / "home.txt").write_text("/" + "/".join(["home", "machine-owner", "build"]) + "\n", encoding="utf-8")
    (repo / "ip.txt").write_text(".".join(["10", "23", "45", "67"]) + "\n", encoding="utf-8")
    (repo / "embedded.bin").write_bytes(b"\xff\x00" + volume_path.encode() + b"\x00")
    (repo / "log.synthhost-01.txt").write_text("ok\n", encoding="utf-8")
    (repo / "insula.txt").write_text("/vaso/build/torch-cu130/src and /usr/lib are fine\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "private source")
    res = run_publish(
        repo,
        "--source",
        "private",
        "--base",
        "public",
        "--branch",
        "publish/host",
        "--worktree",
        str(tmp_path / "host"),
        "--rules",
        str(rules_file(tmp_path)),
        "--host-token",
        "synthhost-01",
        "--host-token",
        "machine-owner",
        "--verify-command",
        "true",
        "--dry-run",
    )
    assert res.returncode == 1
    for finding in (
        "host-volume-path content volume.txt",
        "gpu-uuid content gpu.txt",
        "mac-address content nic.txt",
        "mac-address content nic-hyphen.txt",
        "host-token content home.txt",
        "private-ipv4 content ip.txt",
        "host-volume-path content embedded.bin",
        "host-token filename log.synthhost-01.txt",
    ):
        assert finding in res.stderr
    assert "insula.txt" not in res.stderr
    assert "synthhost-01" not in res.stderr.replace("log.synthhost-01.txt", "")
    assert "0a1b2c3d" not in res.stderr


def test_rescan_passes_insula_paths(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    git(repo, "checkout", "-q", "--orphan", "private")
    clear_worktree(repo)
    (repo / "insula.txt").write_text("/vaso/build/torch-cu130/src, /usr/lib, /home/kvothe, /home/build-account, /workspace/repo, /opt/cuda, cudnn@9.24.0.43, curand@" + ".".join(["10", "3", "9", "90"]) + "\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "private source")
    res = run_publish(
        repo,
        "--source",
        "private",
        "--base",
        "public",
        "--branch",
        "publish/insula",
        "--worktree",
        str(tmp_path / "insula"),
        "--rules",
        str(rules_file(tmp_path)),
        "--host-token",
        "synthhost-01",
        "--verify-command",
        "true",
        "--dry-run",
    )
    assert res.returncode == 0, res.stderr


def test_host_identity_discovery_includes_ipv6_and_ignores_loopback() -> None:
    namespace = runpy.run_path(str(PUBLISH))
    ipv4 = ".".join(["10", "23", "45", "67"])
    ipv6 = ":".join(["fd00", "23", "45", "67", "0", "0", "0", "1"])
    response = subprocess.CompletedProcess(["hostname", "-I"], 0, stdout=f"127.0.0.1 ::1 {ipv4} {ipv6} invalid\n")
    with patch("subprocess.run", return_value=response):
        assert namespace["host_ip_addresses"]() == {ipv4, ipv6}


def test_publication_overlay_removes_live_targets_requiring_private_tracker(tmp_path: Path) -> None:
    namespace = runpy.run_path(str(PUBLISH))
    build = tmp_path / "BUILD.bazel"
    build.write_text('sh_test(\n    name = "private_tracker",\n    args = ["--tracker-root", "../.scratch"],\n)\n\nsh_test(\n    name = "public_test",\n    srcs = ["test.sh"],\n)\n')
    assert namespace["apply_publication_overlay"](tmp_path, []) > 0
    assert "private_tracker" not in build.read_text()
    assert "public_test" in build.read_text()


def test_insula_identity_discovery_preserves_rootfs_accounts() -> None:
    namespace = runpy.run_path(str(PUBLISH))
    with patch.dict(os.environ, {"VASO_IN_INSULA": "1"}):
        assert namespace["host_tokens"](["explicit-host"]) == {"explicit-host"}


def test_post_verify_rescan_rejects_generated_host_identity(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    (repo / "verify.py").write_text(
        "from pathlib import Path\n"
        "Path('generated.txt').write_text('/' + '/'.join(['data07', 'machine', 'cache']))\n"
    )
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "verification fixture")
    res = run_publish(repo, "--source", "HEAD", "--base", "public",
                      "--branch", "publish/generated", "--worktree", str(tmp_path / "wt"),
                      "--rules", str(rules_file(tmp_path)), "--verify-command", "python3 verify.py")
    assert res.returncode == 1
    assert "post-verify rescan failed" in res.stderr
    assert "host-volume-path content generated.txt" in res.stderr
    assert git(tmp_path / "wt", "rev-parse", "HEAD") == git(repo, "rev-parse", "HEAD")


def test_publication_scans_tracked_binaries_but_ignores_runtime_caches(tmp_path: Path) -> None:
    namespace = runpy.run_path(str(PUBLISH))
    repo = init_repo(tmp_path)
    (repo / ".gitignore").write_text("*.pyc\n")
    (repo / "cache.pyc").write_bytes(b"\xff" + b"/" + b"/".join([b"data07", b"machine"]))
    git(repo, "add", ".")
    assert namespace["scan_host_identity"](repo, set()) == []
    git(repo, "add", "-f", "cache.pyc")
    assert namespace["scan_host_identity"](repo, set()) == ["host-volume-path content cache.pyc"]
    # Keep Unicode rules effective even when a payload also has binary bytes.
    (repo / "cache.pyc").write_bytes(b"\xff" + "café".encode())
    rules = namespace["load_rules"](rules_file(tmp_path, [{"literal": "café", "replace": "example"}]))
    assert namespace["scan_remaining"](repo, rules) == ["rule 0 content cache.pyc"]
