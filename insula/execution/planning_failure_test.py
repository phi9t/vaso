"""Launchers retain planner failures instead of consuming partial stdout."""

import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


@pytest.mark.parametrize("launcher", ["generic", "foundry"])
@pytest.mark.parametrize("failure", ["overlay", "namespace"])
def test_planning_error_stops_launcher_before_execution(tmp_path, launcher, failure):
    estate = tmp_path / "estate"
    rootfs = estate / "rootfs-lines/cu129/rootfs"
    _executable(rootfs / "bin/sh", "#!/bin/sh\nexit 0\n")
    _executable(rootfs / "bin/bash", "#!/bin/sh\nexit 0\n")
    (rootfs.parent / "rootfs-bundle.json").write_text("{}")
    marker = tmp_path / "executed"
    bin_dir = tmp_path / "bin"
    _executable(
        bin_dir / "bwrap",
        f"#!/bin/bash\ntouch {shlex.quote(str(marker))}\nexit 99\n",
    )
    _executable(
        bin_dir / "python3",
        "#!/bin/bash\n"
        'case "$1" in\n'
        f"  */overlay_root.py) [[ {shlex.quote(failure)} == overlay ]] && "
        '{ echo "--dir"; echo /partial; echo "fixture planning failure" >&2; exit 37; } ;;\n'
        f"  */estate.py) [[ {shlex.quote(failure)} == namespace && "
        '" $* " == *" --print-sandbox-dirs "* ]] && '
        '{ echo /partial; echo "fixture planning failure" >&2; exit 37; } ;;\n'
        "esac\n"
        f'exec {shlex.quote(sys.executable)} "$@"\n',
    )
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
        "VASO_ESTATE_ROOT": str(estate),
        "VASO_CUDA_LINE": "cu129",
        "VASO_REQUIRED_GIB": "0",
        "VASO_GPUS": "0",
        "VASO_LEASE_TIMEOUT": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if launcher == "generic":
        command = [
            "bash",
            str(ROOT / "insula/run.sh"),
            "--rootfs",
            str(rootfs),
            "--workspace",
            str(ROOT),
            "--print-argv",
            "--",
            "true",
        ]
    else:
        command = ["bash", str(ROOT / "foundry/run.sh"), "--insula-cmd", "true"]
    result = subprocess.run(
        command, env=env, capture_output=True, text=True, check=False, timeout=20
    )
    assert result.returncode == 37, result.stdout + result.stderr
    assert "fixture planning failure" in result.stderr
    assert not marker.exists()
    if launcher == "generic":
        assert result.stdout == ""
    else:
        reports = list((estate / "agents/trae/results").glob("*.json"))
        assert len(reports) == 1
        report = json.loads(reports[0].read_text())
        assert report["exit_code"] == 37
