"""Behavioral tests for scripts/agents/next-package.py."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "agents" / "next-package.py"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_next(repo: Path, tracker: Path, graph: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), "--tracker", str(tracker), "--graph", str(graph), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def sh(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def init_repo(repo: Path) -> None:
    repo.mkdir()
    sh(repo, "init", "-q", "-b", "target")
    sh(repo, "config", "user.email", "t@example.com")
    sh(repo, "config", "user.name", "t")
    write(repo / "base.txt", "base\n")
    sh(repo, "add", "base.txt")
    sh(repo, "commit", "-q", "-m", "base")


def issue(tracker: Path, number: int, slug: str, status: str, blocked_by: str, body: str) -> None:
    write(
        tracker / "issues" / f"{number:02d}-{slug}.md",
        f"# {slug.replace('-', ' ').title()}\n\n"
        f"Status: {status}\n"
        "Type: task\n"
        f"Blocked by: {blocked_by}\n\n"
        f"{body}\n",
    )


def small_graph(path: Path) -> None:
    nodes = [
        {"package": "py-six", "version": "1.17.0", "topo_index": 100, "deps": []},
        {
            "package": "py-protobuf",
            "version": "3.13.0",
            "topo_index": 101,
            "deps": [{"name": "py-six", "deptypes": ["build", "run"]}],
        },
        {
            "package": "py-sympy",
            "version": "1.14.0",
            "topo_index": 102,
            "deps": [{"name": "py-six", "deptypes": ["run"]}],
        },
        {
            "package": "py-hatchling",
            "version": "1.29.0",
            "topo_index": 105,
            "deps": [{"name": "py-sympy", "deptypes": ["build"]}],
        },
    ]
    write(path, json.dumps({"schema_version": 1, "nodes": nodes}, indent=2))


def tracker_with_ticket_08_and_09(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    init_repo(repo)
    tracker = repo / ".scratch" / "effort"
    issue(tracker, 7, "native-python-313-provider", "resolved", "-", "Done.\n")
    issue(
        tracker,
        8,
        "reseat-python-bound-natives",
        "claimed",
        "07",
        """
## Comments

- 2026-09-29T10:50Z (lead): **4 must be re-seated.** The graph gives them
  `python@3.13.13`, but the rules hard-code old layouts:
  py-six, py-protobuf (see ticket 09), py-sympy and py-hatchling.
""",
    )
    issue(
        tracker,
        9,
        "python-protobuf-compat-island",
        "ready-for-agent",
        "07",
        """
Build exact `py-protobuf@4.21.12`. This island owns the protobuf package
handoff from ticket 08.
""",
    )
    graph = repo / "graph.json"
    small_graph(graph)
    sh(
        repo,
        "add",
        ".scratch/effort/issues/07-native-python-313-provider.md",
        ".scratch/effort/issues/08-reseat-python-bound-natives.md",
        ".scratch/effort/issues/09-python-protobuf-compat-island.md",
        "graph.json",
    )
    sh(repo, "commit", "-q", "-m", "tracker fixtures")
    return repo, tracker, graph


def test_active_ticket_08_moves_handed_off_package_to_ticket_09(tmp_path: Path) -> None:
    repo, tracker, graph = tracker_with_ticket_08_and_09(tmp_path)

    result = run_next(repo, tracker, graph)

    assert result.returncode == 0, result.stderr
    assert "MOVED py-protobuf -> ticket 09" in result.stdout
    assert "VERSION_MISMATCH py-protobuf graph=3.13.0 ticket=09 target=4.21.12" in result.stdout
    assert "NEXT ticket 08 package py-six" in result.stdout
    assert "ticket 09" in result.stdout
    assert "REMAINING py-six(100), py-sympy(102 blocked_by=py-six)" in result.stdout
    assert "py-protobuf(101" not in result.stdout


def test_done_markers_remove_finished_packages_and_dependencies_gate_the_pick(tmp_path: Path) -> None:
    repo, tracker, graph = tracker_with_ticket_08_and_09(tmp_path)
    with (tracker / "issues" / "08-reseat-python-bound-natives.md").open("a", encoding="utf-8") as fh:
        fh.write(
            "\n- 2026-09-29T11:00Z (TRAE): package-scoped re-seat is `py-six`.\n"
        )

    result = run_next(repo, tracker, graph, "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["next"] == {"ticket": 8, "package": "py-sympy"}
    assert [item["package"] for item in payload["remaining"]] == ["py-sympy", "py-hatchling"]
    assert payload["remaining"][1]["blocked_by"] == ["py-sympy"]
    assert payload["moved"] == [{"package": "py-protobuf", "to_ticket": 9}]
    assert payload["version_mismatches"] == [
        {
            "package": "py-protobuf",
            "ticket": 9,
            "graph_version": "3.13.0",
            "target_version": "4.21.12",
        }
    ]


def test_reseat_commits_count_as_finished_packages(tmp_path: Path) -> None:
    repo, tracker, graph = tracker_with_ticket_08_and_09(tmp_path)
    write(repo / "native" / "py_six.txt", "done\n")
    sh(repo, "add", "native/py_six.txt")
    sh(repo, "commit", "-q", "-m", "Re-seat py-six on Python 3.13")

    result = run_next(repo, tracker, graph)

    assert result.returncode == 0, result.stderr
    assert "NEXT ticket 08 package py-sympy" in result.stdout
    assert "REMAINING py-sympy(102), py-hatchling(105 blocked_by=py-sympy)" in result.stdout
    assert "py-protobuf(101" not in result.stdout


def test_moved_package_appears_under_owner_ticket_when_source_worklist_is_done(
    tmp_path: Path,
) -> None:
    repo, tracker, graph = tracker_with_ticket_08_and_09(tmp_path)
    with (tracker / "issues" / "08-reseat-python-bound-natives.md").open("a", encoding="utf-8") as fh:
        fh.write(
            "\n- 2026-09-29T11:00Z (TRAE): package-scoped re-seat is `py-six`.\n"
            "- 2026-09-29T11:30Z (TRAE): package-scoped re-seat is `py-sympy`.\n"
            "- 2026-09-29T12:00Z (TRAE): package-scoped re-seat is `py-hatchling`.\n"
        )

    result = run_next(repo, tracker, graph)

    assert result.returncode == 0, result.stderr
    assert "MOVED py-protobuf -> ticket 09" in result.stdout
    assert "VERSION_MISMATCH py-protobuf graph=3.13.0 ticket=09 target=4.21.12" in result.stdout
    assert "NEXT ticket 09 package py-protobuf" in result.stdout
    assert "REMAINING py-protobuf(101)" in result.stdout


def test_later_commit_needs_ordering_reason_for_computed_package(tmp_path: Path) -> None:
    repo, tracker, graph = tracker_with_ticket_08_and_09(tmp_path)
    write(repo / "notes.md", "started later ticket\n")
    sh(repo, "add", "notes.md")
    sh(repo, "commit", "-q", "-m", "Ticket 09: start py-protobuf@4.21.12 island")

    result = run_next(repo, tracker, graph)

    assert result.returncode == 0, result.stderr
    assert 'ORDERING_SKIP py-six — needs an "ORDERING:" reason in the tracker' in result.stdout
    assert "NEXT ticket 08 package py-six" in result.stdout


def test_matching_ordering_reason_allows_later_work(tmp_path: Path) -> None:
    repo, tracker, graph = tracker_with_ticket_08_and_09(tmp_path)
    with (tracker / "issues" / "08-reseat-python-bound-natives.md").open("a", encoding="utf-8") as fh:
        fh.write("\nORDERING: skipped py-six because ticket 09 needed a compatibility probe.\n")
    write(repo / "notes.md", "started later ticket\n")
    sh(repo, "add", ".scratch/effort/issues/08-reseat-python-bound-natives.md", "notes.md")
    sh(repo, "commit", "-q", "-m", "Ticket 09: start py-protobuf@4.21.12 island")

    result = run_next(repo, tracker, graph)

    assert result.returncode == 0, result.stderr
    assert "ORDERING_SKIP" not in result.stdout
    assert "NEXT ticket 08 package py-six" in result.stdout


def test_unresolved_blockers_keep_unclaimed_tickets_out_of_the_ready_set(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    tracker = repo / ".scratch" / "effort"
    issue(tracker, 1, "blocked", "ready-for-agent", "02", "Blocked work.\n")
    issue(tracker, 2, "blocker", "claimed", "-", "Still in progress.\n")
    graph = repo / "graph.json"
    small_graph(graph)
    sh(
        repo,
        "add",
        ".scratch/effort/issues/01-blocked.md",
        ".scratch/effort/issues/02-blocker.md",
        "graph.json",
    )
    sh(repo, "commit", "-q", "-m", "tracker fixtures")

    result = run_next(repo, tracker, graph)

    assert result.returncode == 0, result.stderr
    assert "NEXT none" in result.stdout
    assert "CLAIMED_SKIPPED ticket 02" in result.stdout
