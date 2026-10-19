#!/usr/bin/env python3
"""Pick the next tracker ticket/package for the follower.

Finished package rule: a package is done when the current git history has a
``Re-seat <package> ...``/``Reseat <package> ...`` subject, or when the package
ticket itself contains ``package-scoped re-seat is `<package>``` evidence. The
commit subject is the primary signal because it is target history; the tracker
marker keeps this useful while comments are ahead of local history.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

READY_STATUSES = {"open", "ready-for-agent"}
CLAIMED_STATUS = "claimed"
RESOLVED_STATUS = "resolved"

PACKAGE_RE = re.compile(r"\b[a-z][a-z0-9]*(?:-[a-z0-9]+)+\b")
PACKAGE_VERSION_RE = re.compile(
    r"(?<![A-Za-z0-9-])(?P<package>[a-z][a-z0-9]*(?:-[a-z0-9]+)*)@"
    r"(?P<version>[A-Za-z0-9][A-Za-z0-9_.-]*)"
)
RESEAT_SUBJECT_RE = re.compile(r"\b(?:re-seat|reseat)\s+([a-z][a-z0-9-]*)\b", re.IGNORECASE)
TICKET_RE = re.compile(r"\bticket\s*0*([0-9]+)\b", re.IGNORECASE)
MOVE_MARKER_RE = re.compile(
    r"(?:\bsee\s+|\bhandled\s+by\s+|\bowned\s+by\s+|\bhandoff(?:ed)?\s+to\s+|"
    r"\bmoved\s+to\s+|\bretarget(?:ed)?\s+to\s+|[-=]>\s*|\()\s*"
    r"ticket\s*0*([0-9]+)\b",
    re.IGNORECASE,
)
DONE_MARKER_RE = re.compile(
    r"package-scoped re-seat is\s+`?([a-z][a-z0-9-]*)`?",
    re.IGNORECASE,
)
TIMESTAMP_RE = re.compile(r"\b([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2})Z\b")


@dataclass(frozen=True)
class GraphNode:
    package: str
    topo_index: int
    version: str
    deps: tuple[str, ...]


@dataclass(frozen=True)
class Issue:
    number: int
    path: Path
    title: str
    status: str
    blockers: tuple[int, ...]
    text: str

    @property
    def is_resolved(self) -> bool:
        return self.status == RESOLVED_STATUS

    @property
    def is_claimed(self) -> bool:
        return self.status == CLAIMED_STATUS


@dataclass(frozen=True)
class WorklistItem:
    package: str
    topo_index: int
    version: str
    blocked_by: tuple[str, ...]


@dataclass(frozen=True)
class PackageMove:
    package: str
    to_ticket: int


@dataclass(frozen=True)
class VersionMismatch:
    package: str
    ticket: int
    graph_version: str
    target_version: str


@dataclass(frozen=True)
class TicketPlan:
    issue: Issue
    package: str | None
    remaining: tuple[WorklistItem, ...]
    reason: str


@dataclass(frozen=True)
class WorkEvent:
    when: dt.datetime
    ticket: int | None
    package: str | None
    source: str
    text: str


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Choose the next ready ticket/package from a local Markdown tracker."
    )
    parser.add_argument("--tracker", required=True, type=Path, help="Tracker directory containing issues/*.md")
    parser.add_argument("--graph", required=True, type=Path, help="Spack/Bazel build graph JSON")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return parser.parse_args(argv)


def normalize_status(raw: str) -> str:
    return raw.strip().lower()


def parse_blockers(raw: str) -> tuple[int, ...]:
    return tuple(int(match) for match in re.findall(r"\b[0-9]+\b", raw))


def parse_issues(tracker: Path) -> list[Issue]:
    issues_dir = tracker / "issues"
    issues: list[Issue] = []
    for path in sorted(issues_dir.glob("[0-9][0-9]-*.md")):
        number = int(path.name[:2])
        text = path.read_text(encoding="utf-8")
        title = path.stem[3:].replace("-", " ")
        status = ""
        blockers: tuple[int, ...] = ()
        for line in text.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
            elif line.lower().startswith("status:"):
                status = normalize_status(line.split(":", 1)[1])
            elif line.lower().startswith("blocked by:"):
                blockers = parse_blockers(line.split(":", 1)[1])
        issues.append(Issue(number, path, title, status, blockers, text))
    return issues


def load_graph(path: Path) -> dict[str, GraphNode]:
    data = json.loads(path.read_text(encoding="utf-8"))
    by_package: dict[str, GraphNode] = {}
    for raw in data.get("nodes", []):
        package = raw.get("package")
        if not package:
            continue
        deps = tuple(dep.get("name", "") for dep in raw.get("deps", []) if dep.get("name"))
        node = GraphNode(
            package=package,
            topo_index=int(raw.get("topo_index", 10**9)),
            version=str(raw.get("version", "")),
            deps=deps,
        )
        previous = by_package.get(package)
        if previous is None or node.topo_index < previous.topo_index:
            by_package[package] = node
    return by_package


def run_git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout


def finished_from_git_log() -> set[str]:
    out = run_git(["log", "--format=%s"])
    finished: set[str] = set()
    for subject in out.splitlines():
        match = RESEAT_SUBJECT_RE.search(subject)
        if match:
            finished.add(match.group(1).lower())
    return finished


def finished_from_ticket(issue: Issue) -> set[str]:
    return {match.group(1).lower() for match in DONE_MARKER_RE.finditer(issue.text)}


def collect_until_next_bullet(lines: list[str], start: int) -> str:
    block: list[str] = [lines[start]]
    for line in lines[start + 1 :]:
        if not line.strip():
            break
        if re.match(r"\s*-\s+\*\*", line):
            break
        if line.startswith("## "):
            break
        block.append(line)
    return "\n".join(block)


def graph_name_mentions_with_spans(
    text: str,
    graph: dict[str, GraphNode],
) -> Iterable[tuple[str, int, int]]:
    matches: list[tuple[int, int, str]] = []
    seen_spans: set[tuple[int, int, str]] = set()
    for match in PACKAGE_RE.finditer(text):
        package = match.group(0).lower()
        if package in graph:
            key = (match.start(), match.end(), package)
            seen_spans.add(key)
            matches.append((match.start(), match.end(), package))

    # Some ticket-08 packages are not hyphenated (for example meson, nvtx).
    for package in sorted(graph):
        if "-" in package or package in {"file", "libtool", "python", "zstd"}:
            continue
        for match in re.finditer(
            rf"(?<![A-Za-z0-9-]){re.escape(package)}(?![A-Za-z0-9-])",
            text,
            re.IGNORECASE,
        ):
            key = (match.start(), match.end(), package)
            if key not in seen_spans:
                matches.append((match.start(), match.end(), package))

    for start, end, package in sorted(matches):
        yield package, start, end


def moved_to_ticket(package_text: str, package_end: int) -> int | None:
    tail = package_text[package_end : package_end + 120]
    segment = re.split(r"[,;\n]", tail, maxsplit=1)[0]
    match = MOVE_MARKER_RE.search(segment)
    if not match:
        return None
    return int(match.group(1))


def extract_worklist(
    issue: Issue,
    graph: dict[str, GraphNode],
) -> tuple[tuple[str, ...], dict[str, int]]:
    lines = issue.text.splitlines()
    blocks = [
        collect_until_next_bullet(lines, index)
        for index, line in enumerate(lines)
        if re.search(r"must be re-seated", line, re.IGNORECASE)
    ]
    if not blocks:
        return (), {}

    block = blocks[-1]
    package_text = block.rsplit(":", 1)[-1] if ":" in block else block
    packages = set()
    moves: dict[str, int] = {}
    for package, _, end in graph_name_mentions_with_spans(package_text, graph):
        packages.add(package)
        ticket = moved_to_ticket(package_text, end)
        if ticket is not None:
            moves[package] = ticket
    return (
        tuple(sorted(packages, key=lambda package: (graph[package].topo_index, package))),
        moves,
    )


def package_target_versions(issue: Issue) -> dict[str, str]:
    targets: dict[str, str] = {}
    for match in PACKAGE_VERSION_RE.finditer(issue.text):
        package = match.group("package").lower()
        if package not in targets:
            targets[package] = match.group("version")
    return targets


def build_worklists(
    issues: list[Issue],
    graph: dict[str, GraphNode],
) -> tuple[dict[int, tuple[str, ...]], tuple[PackageMove, ...]]:
    raw_worklists: dict[int, tuple[str, ...]] = {}
    move_targets: dict[str, int] = {}
    for issue in issues:
        worklist, moves = extract_worklist(issue, graph)
        raw_worklists[issue.number] = worklist
        for package, to_ticket in moves.items():
            if to_ticket != issue.number:
                move_targets[package] = to_ticket

    worklists: dict[int, tuple[str, ...]] = {}
    for issue in issues:
        packages = [
            package
            for package in raw_worklists.get(issue.number, ())
            if move_targets.get(package, issue.number) == issue.number
        ]
        packages.extend(
            package
            for package, to_ticket in move_targets.items()
            if to_ticket == issue.number and package not in packages
        )
        worklists[issue.number] = tuple(
            sorted(packages, key=lambda package: (graph[package].topo_index, package))
        )

    moves = tuple(
        PackageMove(package, to_ticket)
        for package, to_ticket in sorted(
            move_targets.items(),
            key=lambda item: (item[1], graph[item[0]].topo_index, item[0]),
        )
    )
    return worklists, moves


def find_version_mismatches(
    issues: list[Issue],
    worklists: dict[int, tuple[str, ...]],
    graph: dict[str, GraphNode],
) -> tuple[VersionMismatch, ...]:
    mismatches: list[VersionMismatch] = []
    for issue in issues:
        target_versions = package_target_versions(issue)
        for package in worklists.get(issue.number, ()):
            target_version = target_versions.get(package)
            if target_version is None:
                continue
            graph_version = graph[package].version
            if graph_version and graph_version != target_version:
                mismatches.append(
                    VersionMismatch(package, issue.number, graph_version, target_version)
                )
    return tuple(
        sorted(
            mismatches,
            key=lambda item: (item.ticket, graph[item.package].topo_index, item.package),
        )
    )


def unresolved_blockers(issue: Issue, resolved: set[int]) -> tuple[int, ...]:
    return tuple(blocker for blocker in issue.blockers if blocker not in resolved)


def build_remaining(
    worklist: tuple[str, ...],
    graph: dict[str, GraphNode],
    finished: set[str],
) -> tuple[WorklistItem, ...]:
    workset = set(worklist)
    remaining: list[WorklistItem] = []
    for package in worklist:
        if package in finished:
            continue
        node = graph[package]
        blocked_by = tuple(dep for dep in node.deps if dep in workset and dep not in finished)
        remaining.append(WorklistItem(package, node.topo_index, node.version, blocked_by))
    return tuple(remaining)


def choose_package(remaining: tuple[WorklistItem, ...]) -> str | None:
    for item in remaining:
        if not item.blocked_by:
            return item.package
    return None


def plan_tickets(
    issues: list[Issue],
    graph: dict[str, GraphNode],
) -> tuple[
    TicketPlan | None,
    list[str],
    list[str],
    tuple[PackageMove, ...],
    tuple[VersionMismatch, ...],
]:
    resolved = {issue.number for issue in issues if issue.is_resolved}
    git_finished = finished_from_git_log()
    claimed_skipped: list[str] = []
    ready_skipped: list[str] = []
    candidates: list[TicketPlan] = []
    worklists, moves = build_worklists(issues, graph)
    version_mismatches = find_version_mismatches(issues, worklists, graph)

    for issue in sorted(issues, key=lambda item: item.number):
        if issue.is_resolved:
            continue

        worklist = worklists.get(issue.number, ())
        finished = git_finished | finished_from_ticket(issue)
        remaining = build_remaining(worklist, graph, finished) if worklist else ()
        package = choose_package(remaining) if remaining else None
        blocked = unresolved_blockers(issue, resolved)

        is_ready = issue.status in READY_STATUSES and not blocked
        is_active_worklist = issue.is_claimed and bool(remaining)
        if is_ready or is_active_worklist:
            if worklist:
                reason = (
                    "first unfinished package whose in-worklist dependencies are done; "
                    "finished by git re-seat subjects or ticket package-scoped markers"
                )
            else:
                reason = "ready ticket with all blockers resolved"
            candidates.append(TicketPlan(issue, package, remaining, reason))
        elif issue.is_claimed:
            if blocked and not worklist:
                reason = "blockers unresolved: " + ", ".join(f"{number:02d}" for number in blocked)
            elif worklist and not remaining:
                reason = "package worklist finished"
            elif not worklist:
                reason = "no package worklist"
            else:
                reason = "not ready"
            claimed_skipped.append(f"ticket {issue.number:02d} - {reason}")

    selected = candidates[0] if candidates else None
    if selected is not None:
        for candidate in candidates[1:]:
            ready_skipped.append(
                f"ticket {candidate.issue.number:02d} - later than selected ticket {selected.issue.number:02d}"
            )
        for issue in issues:
            if issue.is_claimed and issue.number != selected.issue.number:
                if all(not item.startswith(f"ticket {issue.number:02d} ") for item in claimed_skipped):
                    claimed_skipped.append(
                        f"ticket {issue.number:02d} - selected ticket {selected.issue.number:02d} is earlier"
                    )
    return selected, claimed_skipped, ready_skipped, moves, version_mismatches


def parse_subject_work(when: dt.datetime, subject: str) -> WorkEvent | None:
    ticket_match = TICKET_RE.search(subject)
    reseat_match = RESEAT_SUBJECT_RE.search(subject)
    package_match = PACKAGE_RE.search(subject)
    ticket = int(ticket_match.group(1)) if ticket_match else None
    package = None
    if reseat_match:
        package = reseat_match.group(1).lower()
    elif package_match:
        package = package_match.group(0).lower()
    if ticket is None and package is None:
        return None
    return WorkEvent(when, ticket, package, "commit", subject)


def parse_git_work_events() -> list[WorkEvent]:
    out = run_git(["log", "--max-count=200", "--format=%cI%x09%s"])
    events: list[WorkEvent] = []
    for line in out.splitlines():
        if "\t" not in line:
            continue
        raw_when, subject = line.split("\t", 1)
        try:
            when = dt.datetime.fromisoformat(raw_when.replace("Z", "+00:00"))
        except ValueError:
            continue
        event = parse_subject_work(when, subject)
        if event is not None:
            events.append(event)
    return events


def parse_tracker_work_events(issues: list[Issue]) -> list[WorkEvent]:
    events: list[WorkEvent] = []
    for issue in issues:
        lines = issue.text.splitlines()
        for index, line in enumerate(lines):
            timestamp = TIMESTAMP_RE.search(line)
            if not timestamp:
                continue
            chunk = "\n".join(lines[index : index + 3])
            if "TRAE" not in chunk and "package-scoped re-seat" not in chunk:
                continue
            raw_when = timestamp.group(1) + "+00:00"
            try:
                when = dt.datetime.fromisoformat(raw_when)
            except ValueError:
                continue
            done_match = DONE_MARKER_RE.search(chunk)
            package = done_match.group(1).lower() if done_match else None
            events.append(WorkEvent(when, issue.number, package, "tracker", line.strip()))
    return events


def newest_work_event(issues: list[Issue]) -> WorkEvent | None:
    events = parse_git_work_events() + parse_tracker_work_events(issues)
    if not events:
        return None
    return max(events, key=lambda event: event.when)


def package_topo(package: str | None, graph: dict[str, GraphNode]) -> int | None:
    if package is None:
        return None
    node = graph.get(package)
    return node.topo_index if node else None


def work_is_later(event: WorkEvent, selected: TicketPlan, graph: dict[str, GraphNode]) -> bool:
    if event.ticket is not None:
        if event.ticket > selected.issue.number:
            return True
        if event.ticket < selected.issue.number:
            return False

    selected_topo = package_topo(selected.package, graph)
    event_topo = package_topo(event.package, graph)
    if selected_topo is None or event_topo is None:
        return False
    return event_topo > selected_topo


def has_matching_ordering_line(issues: list[Issue], selected: TicketPlan) -> bool:
    package = selected.package.lower() if selected.package else None
    ticket_forms = {
        f"ticket {selected.issue.number}",
        f"ticket {selected.issue.number:02d}",
    }
    for issue in issues:
        for line in issue.text.splitlines():
            if "ORDERING:" not in line:
                continue
            lower = line.lower()
            if package and package in lower:
                return True
            if any(form in lower for form in ticket_forms):
                return True
    return False


def ordering_skip(
    selected: TicketPlan | None,
    issues: list[Issue],
    graph: dict[str, GraphNode],
) -> str | None:
    if selected is None:
        return None
    event = newest_work_event(issues)
    if event is None or not work_is_later(event, selected, graph):
        return None
    if has_matching_ordering_line(issues, selected):
        return None
    what = selected.package or f"ticket {selected.issue.number:02d}"
    return f'ORDERING_SKIP {what} — needs an "ORDERING:" reason in the tracker'


def remaining_text(remaining: tuple[WorklistItem, ...]) -> str:
    if not remaining:
        return "-"
    parts: list[str] = []
    for item in remaining:
        suffix = f" blocked_by={','.join(item.blocked_by)}" if item.blocked_by else ""
        parts.append(f"{item.package}({item.topo_index}{suffix})")
    return ", ".join(parts)


def print_text(
    selected: TicketPlan | None,
    claimed_skipped: list[str],
    ready_skipped: list[str],
    order_skip: str | None,
    moves: tuple[PackageMove, ...],
    version_mismatches: tuple[VersionMismatch, ...],
) -> None:
    for move in moves:
        print(f"MOVED {move.package} -> ticket {move.to_ticket:02d}")
    for mismatch in version_mismatches:
        print(
            "VERSION_MISMATCH "
            f"{mismatch.package} graph={mismatch.graph_version} "
            f"ticket={mismatch.ticket:02d} target={mismatch.target_version}"
        )
    if order_skip:
        print(order_skip)
    if selected is None:
        print("NEXT none - no ready tickets")
    elif selected.package:
        print(
            f"NEXT ticket {selected.issue.number:02d} package {selected.package} - {selected.reason}"
        )
        print(f"REMAINING {remaining_text(selected.remaining)}")
    else:
        print(f"NEXT ticket {selected.issue.number:02d} - {selected.reason}")
        print("REMAINING -")

    for line in ready_skipped:
        print(f"READY_SKIPPED {line}")
    for line in claimed_skipped:
        print(f"CLAIMED_SKIPPED {line}")


def json_item(item: WorklistItem) -> dict[str, object]:
    return {
        "package": item.package,
        "topo_index": item.topo_index,
        "version": item.version,
        "blocked_by": list(item.blocked_by),
    }


def print_json(
    selected: TicketPlan | None,
    claimed_skipped: list[str],
    ready_skipped: list[str],
    order_skip: str | None,
    moves: tuple[PackageMove, ...],
    version_mismatches: tuple[VersionMismatch, ...],
) -> None:
    payload: dict[str, object] = {
        "next": None,
        "reason": None,
        "remaining": [],
        "claimed_skipped": claimed_skipped,
        "ready_skipped": ready_skipped,
        "ordering_skip": order_skip,
        "moved": [
            {"package": move.package, "to_ticket": move.to_ticket}
            for move in moves
        ],
        "version_mismatches": [
            {
                "package": mismatch.package,
                "ticket": mismatch.ticket,
                "graph_version": mismatch.graph_version,
                "target_version": mismatch.target_version,
            }
            for mismatch in version_mismatches
        ],
    }
    if selected is not None:
        payload["next"] = {"ticket": selected.issue.number, "package": selected.package}
        payload["reason"] = selected.reason
        payload["remaining"] = [json_item(item) for item in selected.remaining]
    print(json.dumps(payload, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    graph = load_graph(args.graph)
    issues = parse_issues(args.tracker)
    selected, claimed_skipped, ready_skipped, moves, version_mismatches = plan_tickets(
        issues, graph
    )
    order_skip = ordering_skip(selected, issues, graph)
    if args.json:
        print_json(
            selected,
            claimed_skipped,
            ready_skipped,
            order_skip,
            moves,
            version_mismatches,
        )
    else:
        print_text(
            selected,
            claimed_skipped,
            ready_skipped,
            order_skip,
            moves,
            version_mismatches,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
