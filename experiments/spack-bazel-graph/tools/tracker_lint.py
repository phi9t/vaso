#!/usr/bin/env python3
"""Lint the local Markdown tracker for status and real-run evidence rules."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


ALLOWED_STATUSES = frozenset(
    {
        "active",
        "approved",
        "approved by the human",
        "backlog",
        "blocked",
        "claimed",
        "completed",
        "decided",
        "done",
        "frozen",
        "in-progress",
        "needs-info",
        "needs-review",
        "needs-triage",
        "open",
        "paused",
        "proposal",
        "ready-for-agent",
        "ready-for-agent-after-build",
        "ready-for-human",
        "ready-for-review",
        "ready-for-trae-proof",
        "report only",
        "resolved",
        "resolved-by-human",
        "resolved-with-follow-ups",
        "specced",
        "superseded",
        "wontfix",
    }
)
ALLOWED_STATUS_PATTERNS = (re.compile(r"superseded-by-\d+\Z"),)
CLOSED_ACTION_STATUSES = frozenset({"done", "ready-for-review", "resolved"})
HEADER_RE = re.compile(r"^(?P<name>[A-Za-z][A-Za-z -]*):\s*(?P<value>.+?)\s*$", re.M)
ACTION_TARGET_RE = re.compile(r"//[A-Za-z0-9_./+-]+:[A-Za-z0-9_.+-]*_action[A-Za-z0-9_.+-]*")
REAL_RUN_RE = re.compile(r"^\s*(?:[-*]\s*)?real-run:\s*(?P<evidence>.+?)\s*$", re.M | re.I)
DRY_RUN_RE = re.compile(r"(?i)(?:\bdry[-_ ]run\b|--nobuild|\bexecute\s*=\s*false\b)")
LOG_RE = re.compile(r"(?i)(?:\blog(?:[-_ ]?path)?\s*=|(?:^|\s)/[^\s`]+\.log\b)")
PREFIX_RE = re.compile(r"(?i)(?:\bprefixes\s*=\s*real\b|\breal[- ]prefix(?:es)?\b)")


@dataclass(frozen=True)
class AllowlistEntry:
    path: str
    code: str


@dataclass(frozen=True)
class LintError:
    path: str
    line: int
    code: str
    message: str

    @property
    def allowlist_entry(self) -> AllowlistEntry:
        return AllowlistEntry(self.path, self.code)


def source_root_from(anchor: Path) -> Path:
    resolved = anchor.resolve()
    if resolved.parent.name == "tools":
        return resolved.parents[1]
    return resolved.parent


def _tracker_display_path(path: Path, tracker_root: Path) -> str:
    try:
        rel = path.resolve().relative_to(tracker_root.resolve())
    except ValueError:
        return path.as_posix()
    if tracker_root.name == ".scratch":
        return f".scratch/{rel.as_posix()}"
    return rel.as_posix()


def _iter_tracker_files(tracker_root: Path) -> list[Path]:
    if not tracker_root.exists():
        return []
    files = set(tracker_root.glob("**/spec.md"))
    files.update(tracker_root.glob("**/issues/*.md"))
    return sorted(path for path in files if path.is_file())


def _line_for_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _headers(text: str) -> dict[str, tuple[int, str]]:
    result: dict[str, tuple[int, str]] = {}
    for match in HEADER_RE.finditer(text):
        name = match.group("name").strip().lower()
        if name not in result:
            result[name] = (_line_for_offset(text, match.start()), match.group("value").strip())
    return result


def normalize_status(value: str) -> str:
    value = value.strip().lower()
    for marker in (" (", ",", ";"):
        if marker in value:
            value = value.split(marker, 1)[0].strip()
    return value.rstrip(".")


def _valid_status(value: str) -> bool:
    status = normalize_status(value)
    return status in ALLOWED_STATUSES or any(pattern.fullmatch(status) for pattern in ALLOWED_STATUS_PATTERNS)


def _real_run_line_errors(evidence: str) -> list[str]:
    errors: list[str] = []
    if DRY_RUN_RE.search(evidence):
        errors.append("dry-run or --nobuild evidence is not a real run")
    if "execute=True" not in evidence:
        errors.append("missing execute=True")
    if not LOG_RE.search(evidence):
        errors.append("missing log path")
    if not PREFIX_RE.search(evidence):
        errors.append("missing real prefixes")
    return errors


def _is_issue(path: Path) -> bool:
    return path.parent.name == "issues"


def _touches_build_action(headers: dict[str, tuple[int, str]], text: str) -> bool:
    type_header = headers.get("type")
    if type_header is not None and type_header[1].strip().lower() == "build-action":
        return True
    return ACTION_TARGET_RE.search(text) is not None


def lint_file(path: Path, tracker_root: Path) -> list[LintError]:
    text = path.read_text(encoding="utf-8")
    display_path = _tracker_display_path(path, tracker_root)
    headers = _headers(text)
    errors: list[LintError] = []

    status_header = headers.get("status")
    normalized_status = ""
    if status_header is not None:
        line, value = status_header
        normalized_status = normalize_status(value)
        if not _valid_status(value):
            errors.append(
                LintError(
                    display_path,
                    line,
                    "invalid-status",
                    f"Status value {value!r} is not in the defined tracker set",
                )
            )

    if _is_issue(path) and normalized_status in CLOSED_ACTION_STATUSES and _touches_build_action(headers, text):
        real_run_matches = list(REAL_RUN_RE.finditer(text))
        valid_real_run = False
        invalid_messages: list[str] = []
        for match in real_run_matches:
            line_errors = _real_run_line_errors(match.group("evidence"))
            if line_errors:
                invalid_messages.append(
                    f"line {_line_for_offset(text, match.start())}: {', '.join(line_errors)}"
                )
            else:
                valid_real_run = True

        if not valid_real_run:
            if invalid_messages:
                errors.append(
                    LintError(
                        display_path,
                        real_run_matches[0] and _line_for_offset(text, real_run_matches[0].start()),
                        "invalid-real-run",
                        "; ".join(invalid_messages),
                    )
                )
            else:
                line = status_header[0] if status_header is not None else 1
                errors.append(
                    LintError(
                        display_path,
                        line,
                        "missing-real-run",
                        "closed build-action ticket must include real-run: log=<path> execute=True prefixes=real",
                    )
                )

    return errors


def check_tracker(tracker_root: Path, allowlist: set[AllowlistEntry]) -> list[LintError]:
    active: list[LintError] = []
    for path in _iter_tracker_files(tracker_root):
        active.extend(lint_file(path, tracker_root))

    active_entries = {error.allowlist_entry for error in active}
    visible = [error for error in active if error.allowlist_entry not in allowlist]
    stale = sorted(allowlist - active_entries, key=lambda item: (item.path, item.code))
    for entry in stale:
        visible.append(
            LintError(
                entry.path,
                1,
                "stale-allowlist",
                f"{entry.path}\t{entry.code} no longer matches a tracker violation; remove it",
            )
        )
    return visible


def read_allowlist(path: Path | None) -> set[AllowlistEntry]:
    if path is None:
        return set()
    entries: set[AllowlistEntry] = set()
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" in line:
            rel, code = line.split("\t", 1)
        else:
            parts = line.split()
            if len(parts) != 2:
                raise SystemExit(f"{path}:{line_number}: allowlist entries are '<path><TAB><rule-code>'")
            rel, code = parts
        entries.add(AllowlistEntry(rel.strip(), code.strip()))
    return entries


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-anchor",
        type=Path,
        help="file inside the source checkout; relative --tracker-root paths are resolved from that checkout",
    )
    parser.add_argument("--tracker-root", type=Path, default=Path(".scratch"))
    parser.add_argument("--allowlist", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    tracker_root = args.tracker_root
    if not tracker_root.is_absolute() and args.source_anchor is not None:
        tracker_root = source_root_from(args.source_anchor) / tracker_root
    allowlist = read_allowlist(args.allowlist)
    errors = check_tracker(tracker_root, allowlist)
    for error in errors:
        print(f"{error.path}:{error.line}: {error.code}: {error.message}", file=sys.stderr)
    if errors:
        return 1
    print(f"tracker lint passed ({len(_iter_tracker_files(tracker_root))} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
