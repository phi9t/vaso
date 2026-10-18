import json

from vaso.cli import main
from vaso.validation import run_tier1_plan, tier1_bazel_commands


def test_tier1_bazel_commands_include_build_and_run():
    commands = tier1_bazel_commands("vaso-fixtures")
    joined = [" ".join(command) for command in commands]
    assert any("build //cpp:hello_cpp //python:hello_python" in item for item in joined)
    assert any("run //integration:all" in item for item in joined)


def test_tier1_plan_is_not_run_until_bwrap_executor_exists():
    result = run_tier1_plan("vaso-fixtures")
    assert result.tier == "1"
    assert not result.ok
    assert result.checks[0]["status"] == "not_run"


def test_cli_tier1_writes_run_record_files(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "workspace" / "vaso"
    repo.mkdir(parents=True)
    monkeypatch.chdir(repo)

    assert main(["validate", "--tier", "1", "--repo", "vaso-fixtures"]) == 1

    capsys.readouterr()
    run_dirs = list((repo / ".vaso" / "runs").iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    for rel in [
        "bwrap-plan.json",
        "stdout.log",
        "stderr.log",
        "result.json",
        "validation/tier1.json",
    ]:
        assert (run_dir / rel).is_file()
    result = json.loads((run_dir / "result.json").read_text())
    assert result["kind"] == "validation"
    assert result["tier"] == "1"
    assert result["ok"] is False
