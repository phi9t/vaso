"""Discover release wheels from retained Bazel build events.

Parsing describes artifact locations and recorded metadata. The build workflow
checks materialization; release qualification owns content and runtime proof.
"""

from __future__ import annotations

from collections.abc import Iterator
import json
from pathlib import Path
from typing import Any


# release_wheel_action declares <target>_wheelhouse for the supported
# *_release_wheel and *_release_wheels targets.
_RELEASE_WHEELHOUSE_SUFFIXES = (
    "_release_wheel_wheelhouse",
    "_release_wheels_wheelhouse",
)


def _wheel_files(event: Any) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Validate named-file records and yield safe release-wheel paths."""
    if not isinstance(event, dict):
        raise ValueError("expected an event object")
    if "namedSetOfFiles" not in event:
        return
    named_set = event["namedSetOfFiles"]
    if not isinstance(named_set, dict):
        raise ValueError("expected a namedSetOfFiles object")
    files = named_set.get("files", [])
    if not isinstance(files, list):
        raise ValueError("expected a namedSetOfFiles.files list")
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValueError("expected a named file object with a string name")
        name = item["name"]
        if not name.endswith(".whl"):
            continue
        prefix = item.get("pathPrefix", [])
        if not isinstance(prefix, list) or not all(
            isinstance(part, str) for part in prefix
        ):
            raise ValueError("expected a release-wheel pathPrefix list of strings")
        relative = Path(*prefix) / name
        if relative.is_absolute() or ".." in relative.parts or "\x00" in str(relative):
            raise ValueError(f"unsafe Bazel artifact path: {relative}")
        if any(
            part.endswith(_RELEASE_WHEELHOUSE_SUFFIXES) for part in relative.parts[:-1]
        ):
            yield relative, item


def build_event_artifacts(events: Path, output_base: Path) -> list[dict[str, Any]]:
    """Describe release wheels, preserving Bazel metadata for missing files.

    Malformed records raise ValueError with their source file and line. Missing
    wheels remain in the inventory with exists=False so the workflow can report
    materialization failures separately from parsing failures. Repeated paths
    must agree on known metadata; omitted fields never erase recorded values.
    """
    artifacts: dict[str, dict[str, Any]] = {}
    declared_lengths: dict[str, int] = {}
    try:
        handle = events.open(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read Bazel build events {events}: {exc}") from exc
    with handle:
        for line_number, raw in enumerate(handle, start=1):
            try:
                event = json.loads(raw)
                for relative, item in _wheel_files(event):
                    host_path = output_base / "execroot/_main" / relative
                    key = str(relative)
                    previous = artifacts.get(key, {})
                    length = item.get("length")
                    if length is None:
                        length = declared_lengths.get(key)
                    if length is None:
                        try:
                            size = host_path.stat().st_size
                        except OSError:
                            size = 0
                    else:
                        if type(length) not in (str, int):
                            raise ValueError("invalid release-wheel length")
                        size = int(length)
                        if size < 0:
                            raise ValueError("negative release-wheel length")
                        if key in declared_lengths and size != declared_lengths[key]:
                            raise ValueError(
                                f"conflicting release-wheel length for {relative}"
                            )
                        declared_lengths[key] = size
                    digest = item.get("digest", "")
                    if not isinstance(digest, str):
                        raise ValueError("expected a release-wheel digest string")
                    known_digest = previous.get("digest", "")
                    if digest and known_digest and digest != known_digest:
                        raise ValueError(
                            f"conflicting release-wheel digest for {relative}"
                        )
                    artifacts[key] = {
                        "name": relative.name,
                        "path": str(host_path),
                        "size_bytes": size,
                        "digest": digest or known_digest,
                        "exists": host_path.is_file(),
                    }
            except ValueError as exc:
                raise ValueError(
                    f"invalid Bazel build event {events}:{line_number}: {exc}"
                ) from exc
    return sorted(artifacts.values(), key=lambda item: item["name"])
