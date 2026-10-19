from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BUNDLE_SH = ROOT / "insula" / "rootfs" / "bundle.sh"
INSULA_SH = ROOT / "insula" / "execution" / "insula.sh"


def write_fixture_estate(tmp_path: Path, line: str = "cu129") -> tuple[Path, Path]:
    estate = tmp_path / "estate"
    line_dir = estate / "rootfs-lines" / line
    rootfs = line_dir / "rootfs"
    (rootfs / "etc").mkdir(parents=True)
    (rootfs / "usr" / "bin").mkdir(parents=True)
    (rootfs / "etc" / "issue").write_text("vaso fixture\n", encoding="utf-8")
    tool = rootfs / "usr" / "bin" / "tool"
    tool.write_text("#!/bin/sh\necho fixture\n", encoding="utf-8")
    tool.chmod(0o755)
    rootfs_manifest = {
        "schema_version": 1,
        "line": line,
        "build_time": "2026-01-02T03:04:05Z",
        "packages": [{"name": "base-files", "version": "1"}],
    }
    (line_dir / "rootfs-bundle.json").write_text(
        json.dumps(rootfs_manifest, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lock = tmp_path / "cuda_ecosystem.lock.json"
    lock.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "SOURCE_DATE_EPOCH": 1760000000,
                "lines": {line: {"base_image": "fixture"}},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return estate, lock


def run_bundle(
    tmp_path: Path,
    estate: Path,
    lock: Path,
    out_dir: Path,
    line: str = "cu129",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            str(BUNDLE_SH),
            "--line",
            line,
            "--estate-root",
            str(estate),
            "--lock",
            str(lock),
            "--out-dir",
            str(out_dir),
        ],
        cwd=ROOT,
        env={**os.environ, "TMPDIR": str(tmp_path / "tmp")},
        capture_output=True,
        text=True,
        check=False,
    )


def test_rootfs_bundle_rebuilds_byte_identically_and_writes_content_addressed_manifest(
    tmp_path: Path,
) -> None:
    estate, lock = write_fixture_estate(tmp_path)
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    first = run_bundle(tmp_path, estate, lock, first_dir)
    second = run_bundle(tmp_path, estate, lock, second_dir)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    first_bundle = next(first_dir.glob("rootfs-cu129-*.tar.zst"))
    second_bundle = next(second_dir.glob("rootfs-cu129-*.tar.zst"))
    assert first_bundle.name == second_bundle.name
    assert first_bundle.read_bytes() == second_bundle.read_bytes()

    digest = hashlib.sha256(first_bundle.read_bytes()).hexdigest()
    assert first_bundle.name == f"rootfs-cu129-{digest}.tar.zst"
    sidecar = json.loads((first_dir / "rootfs-cu129.json").read_text(encoding="utf-8"))
    assert sidecar["line"] == "cu129"
    assert sidecar["digest"] == digest
    assert sidecar["size"] == first_bundle.stat().st_size
    assert sidecar["lock_sha256"] == hashlib.sha256(lock.read_bytes()).hexdigest()
    assert sidecar["rootfs_bundle"]["packages"] == [
        {"name": "base-files", "version": "1"}
    ]


def test_rootfs_unbundle_refuses_tampered_archive_before_extracting(tmp_path: Path) -> None:
    estate, lock = write_fixture_estate(tmp_path)
    out_dir = tmp_path / "out"
    result = run_bundle(tmp_path, estate, lock, out_dir)
    assert result.returncode == 0, result.stderr
    bundle = next(out_dir.glob("rootfs-cu129-*.tar.zst"))
    data = bytearray(bundle.read_bytes())
    data[len(data) // 2] ^= 0x01
    bundle.write_bytes(bytes(data))
    dest = tmp_path / "extract"

    tampered = subprocess.run(
        [
            "bash",
            str(BUNDLE_SH),
            "unbundle",
            "--bundle",
            str(bundle),
            "--dest",
            str(dest),
        ],
        cwd=ROOT,
        env={**os.environ, "TMPDIR": str(tmp_path / "tmp")},
        capture_output=True,
        text=True,
        check=False,
    )

    assert tampered.returncode != 0
    assert "digest mismatch" in tampered.stderr
    assert not (dest / "rootfs" / "etc" / "issue").exists()


def test_insula_rootfs_from_bundle_verifies_and_extracts_bundle(tmp_path: Path) -> None:
    estate, lock = write_fixture_estate(tmp_path)
    out_dir = tmp_path / "out"
    result = run_bundle(tmp_path, estate, lock, out_dir)
    assert result.returncode == 0, result.stderr
    bundle = next(out_dir.glob("rootfs-cu129-*.tar.zst"))
    dest_parent = tmp_path / "unpacked"
    harness = f"""
set -euo pipefail
source {str(INSULA_SH)!r}
insula_rootfs_from_bundle rootfs_path {str(bundle)!r} {str(dest_parent)!r}
printf '%s\\n' "$rootfs_path"
"""

    helper = subprocess.run(
        ["bash", "-c", harness],
        cwd=ROOT,
        env={**os.environ, "TMPDIR": str(tmp_path / "tmp")},
        capture_output=True,
        text=True,
        check=False,
    )

    assert helper.returncode == 0, helper.stderr
    extracted_rootfs = Path(helper.stdout.strip())
    assert extracted_rootfs == dest_parent / "cu129" / "rootfs"
    assert (extracted_rootfs / "etc" / "issue").read_text(encoding="utf-8") == "vaso fixture\n"
