import json

from vaso.cli import main
from vaso.config import default_config
from vaso.doctor import run_doctor
from vaso.paths import ensure_host_layout


def test_doctor_reports_unpopulated_rootfs(tmp_path):
    repo = tmp_path / "workspace" / "vaso"
    repo.mkdir(parents=True)
    config = default_config(repo)
    ensure_host_layout(config)
    checks = run_doctor(config)
    rootfs = [check for check in checks if check.name == "rootfs_has_shell"][0]
    assert not rootfs.ok
    assert "bin/sh" in rootfs.detail


def test_cli_doctor_outputs_json(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "workspace" / "vaso"
    repo.mkdir(parents=True)
    monkeypatch.chdir(repo)
    code = main(["doctor"])
    assert code == 1
    data = json.loads(capsys.readouterr().out)
    assert "checks" in data
