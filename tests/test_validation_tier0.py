import json

from vaso.cli import main
from vaso.config import default_config
from vaso.paths import ensure_host_layout
from vaso.validation import run_tier0, write_validation_report


def test_tier0_reports_writable_surfaces(tmp_path):
    repo = tmp_path / "workspace" / "vaso"
    repo.mkdir(parents=True)
    config = default_config(repo)
    ensure_host_layout(config)
    result = run_tier0(config)
    assert result.tier == "0"
    assert result.ok
    names = {check["name"] for check in result.checks}
    assert "writable:/vaso/cache" in names
    assert "writable:/vaso/runs" in names


def test_write_validation_report(tmp_path):
    repo = tmp_path / "workspace" / "vaso"
    repo.mkdir(parents=True)
    config = default_config(repo)
    ensure_host_layout(config)
    result = run_tier0(config)
    report = write_validation_report(config.state_root / "runs" / "run" / "validation", result)
    data = json.loads(report.read_text())
    assert data["tier"] == "0"
    assert data["ok"] is True


def test_tier0_records_unwritable_probe_failures(tmp_path):
    repo = tmp_path / "workspace" / "vaso"
    repo.mkdir(parents=True)
    config = default_config(repo)
    ensure_host_layout(config)
    blocked = config.state_root / "cache"
    blocked.rmdir()
    blocked.write_text("not a directory")

    result = run_tier0(config)

    assert not result.ok
    cache_check = [check for check in result.checks if check["name"] == "writable:/vaso/cache"][0]
    assert cache_check["ok"] is False
    assert "error" in cache_check


def test_cli_tier0_writes_run_record_files(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "workspace" / "vaso"
    repo.mkdir(parents=True)
    monkeypatch.chdir(repo)

    assert main(["validate", "--tier", "0"]) == 0

    capsys.readouterr()
    run_dirs = list((repo / ".vaso" / "runs").iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    for rel in [
        "bwrap-plan.json",
        "stdout.log",
        "stderr.log",
        "result.json",
        "validation/tier0.json",
    ]:
        assert (run_dir / rel).is_file()
    result = json.loads((run_dir / "result.json").read_text())
    assert result["kind"] == "validation"
    assert result["tier"] == "0"
