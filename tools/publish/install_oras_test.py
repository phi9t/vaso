"""Exercise acquisition with a local archive and an interrupted transport."""

from __future__ import annotations

import hashlib
import io
import os
import re
import subprocess
import tarfile
from pathlib import Path

import pytest


INSTALLER = Path(__file__).with_name("install_oras.sh")


@pytest.fixture
def installer(tmp_path: Path) -> tuple[Path, dict[str, str], Path, bytes]:
    payload = b"#!/bin/sh\nprintf 'fixture oras\\n'\n"
    archive_data = io.BytesIO()
    with tarfile.open(fileobj=archive_data, mode="w:gz") as archive:
        info = tarfile.TarInfo("oras")
        info.size = len(payload)
        info.mode = 0o755
        archive.addfile(info, io.BytesIO(payload))
    archive_bytes = archive_data.getvalue()
    fixture_archive = tmp_path / "download.tar.gz"
    fixture_archive.write_bytes(archive_bytes)
    # Only substitute the content pin; run the installer's real shell workflow
    # with actual hashing, extraction, and installation of this local fixture.
    script, count = re.subn(
        r"(?m)^ORAS_SHA256=[0-9a-f]{64}$",
        "ORAS_SHA256=" + hashlib.sha256(archive_bytes).hexdigest(),
        INSTALLER.read_text(),
    )
    assert count == 1
    local_installer = tmp_path / "install.sh"
    local_installer.write_text(script)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "from pathlib import Path\n"
        "with Path(os.environ['FETCH_LOG']).open('a') as log: log.write('fetch\\n')\n"
        "mode = os.environ.get('FETCH_MODE', '')\n"
        "output = Path(sys.argv[sys.argv.index('-o') + 1])\n"
        "data = Path(os.environ['FETCH_ARCHIVE']).read_bytes()\n"
        "output.write_bytes(data if not mode else b'partial download')\n"
        "raise SystemExit(23 if mode == 'interrupted' else 0)\n"
    )
    curl.chmod(0o755)
    estate = tmp_path / "estate"
    env = {
        **os.environ,
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "VASO_ESTATE_ROOT": str(estate),
        "FETCH_ARCHIVE": str(fixture_archive),
        "FETCH_LOG": str(tmp_path / "fetch.log"),
    }
    return local_installer, env, estate, archive_bytes


def run_install(script: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script)],
        cwd=script.parent,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("mode", ["interrupted", "bad-checksum"])
@pytest.mark.parametrize("corrupt_cache", [False, True])
def test_failed_acquisition_can_retry_without_manual_cache_repair(
    installer: tuple[Path, dict[str, str], Path, bytes],
    mode: str,
    corrupt_cache: bool,
) -> None:
    script, env, estate, expected = installer
    cached = estate / "opt-vaso/src/oras_1.3.4_linux_amd64.tar.gz"
    if corrupt_cache:
        cached.parent.mkdir(parents=True)
        cached.write_bytes(b"older incomplete download")
    failed = run_install(script, {**env, "FETCH_MODE": mode})
    assert failed.returncode != 0
    if mode == "interrupted":
        assert failed.returncode == 23
    assert not (estate / "opt-vaso/bin/oras").exists()
    if corrupt_cache:
        assert cached.read_bytes() == b"older incomplete download"
    else:
        assert not cached.exists()
    assert not list((estate / "opt-vaso").rglob(".install-oras.*"))

    retried = run_install(script, env)
    assert retried.returncode == 0, retried.stderr
    assert "fixture oras" in retried.stdout
    assert cached.read_bytes() == expected
    assert Path(env["FETCH_LOG"]).read_text() == "fetch\nfetch\n"
    assert not list((estate / "opt-vaso").rglob(".install-oras.*"))


def test_verified_cached_archive_installs_without_fetching(
    installer: tuple[Path, dict[str, str], Path, bytes],
) -> None:
    script, env, estate, expected = installer
    cached = estate / "opt-vaso/src/oras_1.3.4_linux_amd64.tar.gz"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(expected)
    result = run_install(script, {**env, "FETCH_MODE": "interrupted"})
    assert result.returncode == 0, result.stderr
    assert "fixture oras" in result.stdout
    assert not Path(env["FETCH_LOG"]).exists()
