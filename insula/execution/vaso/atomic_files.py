"""Publish complete owned text records on their destination filesystem."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile


def atomic_text(path: Path, text: str) -> None:
    """Flush and replace a file, cleaning staging if publication fails.

    The caller owns parent-directory creation and serialization. A failed write
    or replacement preserves the prior destination; readers see complete files.
    """
    with tempfile.TemporaryDirectory(
        dir=path.parent, prefix=f".{path.name}."
    ) as directory:
        temporary = Path(directory) / "record"
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
