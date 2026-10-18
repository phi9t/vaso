from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import scripts.agents.trace_digest as trace_digest


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_rollout_digest_streams_counts_and_notable_commands(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout-2026-09-29T00-00-00-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa.jsonl"
    write_jsonl(
        rollout,
        [
            {
                "timestamp": "2026-09-28T23:59:00Z",
                "type": "session_meta",
                "payload": {"session_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "cwd": "/repo", "timestamp": "2026-09-28T23:58:00Z"},
            },
            {
                "timestamp": "2026-09-29T00:01:00Z",
                "type": "event_msg",
                "payload": {"type": "task_started", "turn_id": "turn-1"},
            },
            {
                "timestamp": "2026-09-29T00:02:00Z",
                "type": "event_msg",
                "payload": {
                    "type": "exec_command_end",
                    "turn_id": "turn-1",
                    "cwd": "/repo",
                    "command": "bazel-real test //tools:all",
                    "exit_code": 1,
                    "status": "failed",
                    "duration": {"secs": 12, "nanos": 500000000},
                },
            },
            {
                "timestamp": "2026-09-29T00:03:00Z",
                "type": "event_msg",
                "payload": {"type": "patch_apply_end", "success": False, "status": "failed"},
            },
            {
                "timestamp": "2026-09-29T00:04:00Z",
                "type": "event_msg",
                "payload": {"type": "context_compacted"},
            },
            {
                "timestamp": "2026-09-29T00:05:00Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {"total_tokens": 123},
                        "total_token_usage": {"total_tokens": 1000},
                        "model_context_window": 2000,
                    },
                },
            },
            {
                "timestamp": "2026-09-29T00:06:00Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {"total_tokens": 456},
                        "total_token_usage": {"total_tokens": 1400},
                        "auto_compact_token_limit": 1800,
                    },
                },
            },
            {
                "timestamp": "2026-09-29T00:07:00Z",
                "type": "event_msg",
                "payload": {"type": "task_complete", "turn_id": "turn-1", "duration_ms": 3000},
            },
        ],
    )

    session, timeline = trace_digest.digest_rollout(
        rollout,
        trace_digest.parse_time("2026-09-29T00:00:00Z"),
        trace_digest.parse_time("2026-09-29T01:00:00Z"),
        "/repo",
    )

    assert session is not None
    assert session["id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert session["turns"] == 1
    assert session["exec_commands"]["count"] == 1
    assert session["exec_commands"]["failures_by_exit_code"] == {"1": 1}
    assert session["exec_commands"]["longest"][0]["duration_seconds"] == 12.5
    assert session["patch_applies"] == {"ok": 0, "failed": 1}
    assert session["context_compacted"]["timestamps"] == ["2026-09-29T00:04:00Z"]
    assert session["tokens"]["peak_context_tokens"] == 456
    assert session["tokens"]["total_token_delta_in_window"] == 400
    assert timeline[0]["category"] == "bazel"


def test_rollout_digest_filters_by_cwd_prefix(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout-2026-09-29T00-00-00-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb.jsonl"
    write_jsonl(
        rollout,
        [
            {
                "timestamp": "2026-09-29T00:00:00Z",
                "type": "session_meta",
                "payload": {"session_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "cwd": "/elsewhere"},
            },
            {
                "timestamp": "2026-09-29T00:01:00Z",
                "type": "event_msg",
                "payload": {
                    "type": "exec_command_end",
                    "cwd": "/elsewhere",
                    "command": "run.sh",
                    "exit_code": 0,
                    "duration": {"secs": 1},
                },
            },
        ],
    )

    session, timeline = trace_digest.digest_rollout(
        rollout,
        trace_digest.parse_time("2026-09-29T00:00:00Z"),
        trace_digest.parse_time("2026-09-29T01:00:00Z"),
        "/repo",
    )

    assert session is None
    assert timeline == []


def test_rollout_digest_filters_by_window(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout-2026-09-29T00-00-00-cccccccc-cccc-cccc-cccc-cccccccccccc.jsonl"
    write_jsonl(
        rollout,
        [
            {
                "timestamp": "2026-09-29T00:00:00Z",
                "type": "session_meta",
                "payload": {"session_id": "cccccccc-cccc-cccc-cccc-cccccccccccc", "cwd": "/repo"},
            },
            {
                "timestamp": "2026-09-29T00:01:00Z",
                "type": "event_msg",
                "payload": {"type": "exec_command_end", "cwd": "/repo", "command": "run.sh", "exit_code": 0, "duration": {"secs": 1}},
            },
            {
                "timestamp": "2026-09-29T00:03:00Z",
                "type": "event_msg",
                "payload": {"type": "exec_command_end", "cwd": "/repo", "command": "run.sh", "exit_code": 0, "duration": {"secs": 1}},
            },
        ],
    )

    session, _ = trace_digest.digest_rollout(
        rollout,
        trace_digest.parse_time("2026-09-29T00:02:00Z"),
        trace_digest.parse_time("2026-09-29T00:04:00Z"),
        "/repo",
    )

    assert session is not None
    assert session["exec_commands"]["count"] == 1
    assert session["window_start"] == "2026-09-29T00:03:00Z"


def test_redacts_secret_like_command_parts() -> None:
    command = (
        "curl -H 'Authorization: Bearer abc.def.ghi' "
        "TOKEN=super-secret token=also-secret "
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    )

    redacted = trace_digest.redacted_command(command, limit=400)

    assert "abc.def.ghi" not in redacted
    assert "super-secret" not in redacted
    assert "also-secret" not in redacted
    assert "AAAAAAAAAAAAAAAA" not in redacted
    assert "Authorization: Bearer <redacted>" in redacted
    assert "TOKEN=<redacted>" in redacted
    assert "token=<redacted>" in redacted


def test_worker_run_digest_uses_metadata_without_brief_body(tmp_path: Path) -> None:
    run_dir = tmp_path / "agents" / "worker-a" / "runs" / "20260929T000000Z"
    run_dir.mkdir(parents=True)
    (run_dir / "meta.json").write_text(
        json.dumps(
            {
                "agent": "worker-a",
                "branch": "branch-a",
                "base": "abc123",
                "worktree": "/repo/.worktrees/worker-a",
                "started_utc": "2026-09-29T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "brief.md").write_text("do not copy this brief body\n", encoding="utf-8")
    (run_dir / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
                json.dumps({"type": "turn.started"}),
                json.dumps({"type": "item.completed", "item": {"id": "1", "type": "agent_message", "text": "do not copy this message"}}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "id": "2",
                            "type": "command_execution",
                            "command": "scripts/agents/verified-commit.sh -m done",
                            "exit_code": 0,
                            "status": "completed",
                        },
                    }
                ),
                json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "exit_code").write_text("0\n", encoding="utf-8")
    end = datetime(2026, 9, 29, 0, 10, tzinfo=timezone.utc).timestamp()
    os.utime(run_dir / "exit_code", (end, end))

    worker, timeline = trace_digest.digest_worker_run(
        run_dir,
        trace_digest.parse_time("2026-09-29T00:00:00Z"),
        trace_digest.parse_time("2026-09-29T01:00:00Z"),
        "/repo",
    )

    assert worker is not None
    assert worker["id"] == "worker-a/20260929T000000Z"
    assert worker["outcome"] == "success"
    assert worker["brief_present"] is True
    assert worker["turns"] == 1
    assert worker["exec_commands"]["count"] == 1
    assert worker["patch_applies"] == {"ok": 0, "failed": 0}
    assert worker["context_compacted"] == {"count": 0, "timestamps": []}
    assert worker["subagent_spawns"] == []
    assert worker["tokens"] == {"input_tokens": 10, "output_tokens": 5}
    serialized = json.dumps(worker) + json.dumps(timeline)
    assert "do not copy" not in serialized
    assert timeline[0]["category"] == "verified-commit"
