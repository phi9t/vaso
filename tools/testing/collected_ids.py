#!/usr/bin/env python3
"""List the pytest tests under some roots as location-independent ids.

Moving a test file beside its source changes its path and may change its name
from test_x.py to x_test.py. The ids written here drop both, so a baseline
recorded before a move still matches after it, and a lost or renamed test shows
up as a difference.

  python3 collected_ids.py --write BASELINE    record the ids (use a python that has pytest)
  collected_ids.py --check BASELINE    exit 1 and print the difference if they changed
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path, PurePosixPath


# A collection hook supplies structured data; terminal output is reserved for
# diagnostics and can vary with pytest verbosity, conftest files, or plugins.
_COLLECTION_SCRIPT = """
import json
from pathlib import Path
import sys
import pytest

class Recorder:
    def pytest_collection_finish(self, session):
        Path(sys.argv[1]).write_text(
            json.dumps([item.nodeid for item in session.items]), encoding="utf-8"
        )

raise SystemExit(pytest.main(
    ["--collect-only", "-q", "-p", "no:cacheprovider", *sys.argv[2:]],
    plugins=[Recorder()],
))
"""


def normalize(nodeid: str) -> str:
    path, _, rest = nodeid.partition("::")
    stem = PurePosixPath(path).stem
    if stem.startswith("test_"):
        stem = stem[len("test_") :]
    elif stem.endswith("_test"):
        stem = stem[: -len("_test")]
    return f"{stem}::{rest}"


def compare(before: list[str], after: list[str]) -> tuple[list[str], list[str]]:
    """The ids only in `before` (lost) and only in `after` (gained)."""
    old, new = Counter(before), Counter(after)
    return sorted((old - new).elements()), sorted((new - old).elements())


def collect(roots: list[str], *, cwd: str | None = None) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="vaso-collection-") as directory:
        output = Path(directory) / "nodeids.json"
        proc = subprocess.run(
            [sys.executable, "-c", _COLLECTION_SCRIPT, str(output), *roots],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if proc.returncode not in (0, 5):
            raise RuntimeError(
                f"pytest collection failed ({proc.returncode}):\n{proc.stdout}{proc.stderr}"
            )
        try:
            nodeids = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"pytest did not produce readable collection data: {exc}"
            ) from exc
        if not isinstance(nodeids, list) or not all(
            isinstance(item, str) for item in nodeids
        ):
            raise RuntimeError("pytest produced invalid collection data")
        return [normalize(nodeid) for nodeid in nodeids]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", metavar="BASELINE")
    mode.add_argument("--check", metavar="BASELINE")
    parser.add_argument(
        "--roots",
        nargs="+",
        default=[],
        help="override pytest's testpaths (default: use pyproject.toml)",
    )
    parser.add_argument("--cwd")
    args = parser.parse_args(argv)

    try:
        ids = collect(args.roots, cwd=args.cwd)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.write:
        with open(args.write, "w", encoding="utf-8") as handle:
            handle.write("\n".join(sorted(ids)) + "\n")
        print(f"recorded {len(ids)} test ids in {args.write}")
        return 0

    with open(args.check, encoding="utf-8") as handle:
        baseline = [line for line in handle.read().splitlines() if line]
    lost, gained = compare(baseline, ids)
    for item in lost:
        print(f"LOST   {item}")
    for item in gained:
        print(f"GAINED {item}")
    if lost or gained:
        print(
            f"collected test ids differ from {args.check}: {len(lost)} lost, {len(gained)} gained"
        )
        return 1
    print(f"collected test ids match {args.check} ({len(ids)} tests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
