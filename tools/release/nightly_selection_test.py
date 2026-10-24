"""Nightly selection works on supplied summaries without materializing an estate."""

import copy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from nightly_selection import select_nightly


def summary(finished: str) -> dict:
    return {
        "commit": "selected-source",
        "verdict": "passed",
        "finished_utc": finished,
        "stages": [
            {
                "kind": kind,
                "profile": "torch",
                "line": line,
                "verdict": "passed",
                "release": True,
            }
            for kind in ("release_gate", "determinism")
            for line in ("cu129", "cu130")
        ],
    }


def test_newest_complete_matching_summary_wins_without_mutating_inputs():
    older = summary("2026-01-01T00:00:00Z")
    chosen = summary("2026-01-02T00:00:00Z")
    incomplete = summary("2026-01-03T00:00:00Z")
    incomplete["stages"].pop()
    wrong_source = summary("2026-01-04T00:00:00Z")
    wrong_source["commit"] = "other-source"
    rejected = summary("2026-01-05T00:00:00Z")
    rejected["verdict"] = "failed"
    entries = [
        (Path("nightly/rejected/summary.json"), rejected),
        (Path("nightly/chosen/summary.json"), chosen),
        (Path("nightly/incomplete/summary.json"), incomplete),
        (Path("nightly/older/summary.json"), older),
        (Path("nightly/wrong-source/summary.json"), wrong_source),
    ]
    original = copy.deepcopy(entries)
    path, doc, release, determinism = select_nightly(
        iter(entries), "torch", "cu130", "selected-source"
    )
    assert path == Path("nightly/chosen/summary.json")
    assert doc is chosen
    assert release is chosen["stages"][1]
    assert determinism is chosen["stages"][3]
    assert entries == original
