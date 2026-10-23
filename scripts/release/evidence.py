"""Canonical JSON and atomic persistence for release evidence.

New evidence is immutable; progress state is replaceable. Both use a complete,
flushed file on the destination filesystem before making its name visible.
These helpers do not select a schema or interpret a release verdict.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any


def canonical(doc: Any) -> bytes:
    return (json.dumps(doc, sort_keys=True, indent=2) + "\n").encode()


@contextmanager
def _staged_json(path: Path, data: bytes) -> Iterator[Path]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=path.parent, prefix=f".{path.name}."
    ) as directory:
        temporary = Path(directory) / "record.json"
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        yield temporary


def write_new(path: Path, doc: Any) -> str:
    """Publish a complete new record and return its hash; never replace a name."""
    data = canonical(doc)
    with _staged_json(path, data) as temporary:
        # Linking on the same filesystem creates the final name atomically and
        # fails if another writer already published it, including a symlink.
        os.link(temporary, path)
    return hashlib.sha256(data).hexdigest()


def replace_json(path: Path, doc: Any) -> None:
    """Atomically replace mutable progress state, preserving old bytes on failure."""
    with _staged_json(path, canonical(doc)) as temporary:
        os.replace(temporary, path)
