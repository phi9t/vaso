import json
from datetime import UTC, datetime
from pathlib import Path

from vaso.bwrap import BwrapPlan
from vaso.config import default_config
from vaso.run_records import create_run_dir, new_run_id, write_log, write_plan, write_result


def test_run_id_is_filesystem_safe():
    run_id = new_run_id()
    assert "/" not in run_id
    assert ":" not in run_id
    assert run_id.endswith("Z")


def test_run_id_includes_subsecond_precision():
    run_id = new_run_id(datetime(2026, 8, 20, 0, 18, 13, 123456, tzinfo=UTC))
    assert run_id == "20260820T001813123456Z"


def test_write_plan_includes_digest(tmp_path):
    repo = tmp_path / "workspace" / "vaso"
    repo.mkdir(parents=True)
    config = default_config(repo)
    run_dir = create_run_dir(config, "20260820T000000Z-test")
    plan = BwrapPlan(
        run_id="20260820T000000Z-test",
        target_repo="vaso",
        cwd="/workspace/vaso",
        uid=1018,
        gid=1018,
        rootfs=Path(".vaso/rootfs"),
        mounts=[],
        environment={},
        command_argv=["/bin/true"],
    )
    path = write_plan(run_dir, plan)
    data = json.loads(path.read_text())
    assert data["run_id"] == "20260820T000000Z-test"
    assert len(data["bwrap_argv_sha256"]) == 64


def test_write_result_and_logs(tmp_path):
    repo = tmp_path / "workspace" / "vaso"
    repo.mkdir(parents=True)
    config = default_config(repo)
    run_dir = create_run_dir(config, "20260820T000000000000Z-test")

    stdout = write_log(run_dir, "stdout.log", "out\n")
    stderr = write_log(run_dir, "stderr.log", "err\n")
    result = write_result(run_dir, ok=True, exit_code=0, kind="validation", extra={"tier": "0"})

    assert stdout.read_text() == "out\n"
    assert stderr.read_text() == "err\n"
    data = json.loads(result.read_text())
    assert data == {"exit_code": 0, "kind": "validation", "ok": True, "tier": "0"}
