"""Behavioral tests for scripts/release/cut.sh."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "cut.sh"
SHA = "abcdef1234567890abcdef1234567890abcdef12"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_release_wheel(path: Path, commit: str, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    dist_info = f"{path.name.split('-', 1)[0]}-1.0.dist-info"
    with zipfile.ZipFile(path, "w") as wheel:
        wheel.writestr("payload.txt", payload)
        wheel.writestr(f"{dist_info}/vaso_provenance.json", json.dumps({"git_sha": commit}) + "\n")


def insula_fixture_path(path: Path, estate: Path) -> str:
    for host_root, insula_root in (
        (estate / "agents", "/vaso/agents"),
        (estate / "rootfs-lines", "/vaso/rootfs-lines"),
        (estate / "sources", "/vaso/sources"),
        (estate / "vaso", "/vaso"),
    ):
        try:
            rel = path.relative_to(host_root)
        except ValueError:
            continue
        if str(rel) == ".":
            return insula_root
        return f"{insula_root}/{rel.as_posix()}"
    return str(path)


def make_green_nightly(
    estate: Path,
    *,
    run_id: str = "20261003T010203Z-green",
    commit: str = SHA,
    verdict: str = "passed",
    profile: str = "torch",
    line: str = "cu129",
    gate_verdict: str = "passed",
    gate_verdicts: dict[str, str] | None = None,
    determinism_verdict: str = "passed",
    determinism_verdicts: dict[str, str] | None = None,
    determinism_identical: bool = True,
    release_lines: tuple[str, ...] = ("cu129", "cu130"),
    determinism_lines: tuple[str, ...] = ("cu130", "cu129"),
    include_release_workloads: bool = True,
    wheel_commit: str = SHA,
    acceptance_insula_paths: bool = False,
    execroot_relative_wheels: bool = False,
    record_output_base: bool = True,
) -> dict[str, Path]:
    run_dir = estate / "ci" / "nightly" / run_id
    results = run_dir / "results"
    package = "pytorch" if profile == "torch" else "jaxlib"
    dist = "torch" if profile == "torch" else "jaxlib"
    stages = []
    info_by_line: dict[str, dict[str, Path]] = {}
    gate_verdicts = gate_verdicts or {}
    determinism_verdicts = determinism_verdicts or {}
    for release_line in release_lines:
        release_gate_verdict = gate_verdicts.get(release_line, gate_verdict)
        if acceptance_insula_paths:
            gate_run = estate / "vaso" / "run" / f"release-{profile}-{release_line}"
        else:
            gate_run = estate / "lines" / release_line / "work" / profile / "release-gate"
        wheels = gate_run / "wheels"
        output_base = run_dir / "output-bases" / f"release-{profile}-{release_line}"
        execroot = output_base / "execroot" / "_main"
        if execroot_relative_wheels:
            wheels = execroot / "bazel-out" / "k8-fastbuild" / "bin" / "native" / package / "wheelhouse"
        version = (
            f"2.14.0+vaso.{release_line}.g{wheel_commit[:8]}"
            if profile == "torch"
            else f"0.10.2+vaso.{release_line}.g{wheel_commit[:8]}"
        )
        wheel = wheels / f"{dist}-{version}-cp313-cp313-linux_x86_64.whl"
        write_release_wheel(wheel, wheel_commit, f"wheel payload {release_line}\n")
        wheel_sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
        sbom = gate_run / "sbom" / "cyclonedx.json"
        sbom.parent.mkdir(parents=True, exist_ok=True)
        sbom.write_text('{"bomFormat":"CycloneDX"}\n', encoding="utf-8")
        rootfs_manifest = estate / "rootfs-lines" / release_line / "rootfs-bundle.json"
        write_json(rootfs_manifest, {"schema_version": 1, "line": release_line, "sha256": "sha256:rootfs"})
        wheel_manifest = gate_run / "wheel_manifest.json"
        write_json(
            wheel_manifest,
            {
                "schema_version": 1,
                "wheels": [
                    {
                        "name": wheel.name,
                        "path": insula_fixture_path(wheel, estate) if acceptance_insula_paths else str(wheel),
                        "sha256": wheel_sha,
                    }
                ],
            },
        )
        artifact_path = (
            lambda path: insula_fixture_path(path, estate) if acceptance_insula_paths else str(path)
        )
        wheel_path = wheel.relative_to(execroot).as_posix() if execroot_relative_wheels else artifact_path(wheel)
        acceptance = results / f"release-{profile}-{release_line}.acceptance.json"
        release_doc = {
            "wheel_manifests": {
                package: {
                    "path": artifact_path(wheel_manifest),
                    "sha256": "sha256:manifest",
                    "wheels": [
                        {
                            "package": package,
                            "name": wheel.name,
                            "path": wheel_path,
                            "sha256": f"sha256:{wheel_sha}",
                        }
                    ],
                }
            },
            "wheels": [
                {
                    "package": package,
                    "name": wheel.name,
                    "path": wheel_path,
                    "sha256": f"sha256:{wheel_sha}",
                }
            ],
            "sbom": {
                "path": artifact_path(sbom),
                "sha256": f"sha256:{sha256_text(sbom.read_text(encoding='utf-8'))}",
            },
        }
        if include_release_workloads:
            release_doc["workload_summaries"] = {
                "release-workloads": {"verdict": "passed", "line": release_line}
            }
        write_json(
            acceptance,
            {
                "schema_version": 1,
                "profile": profile,
                "line": release_line,
                "commit": commit,
                "verdict": release_gate_verdict,
                "rootfs_manifest": "/run/vaso/rootfs-bundle.json" if acceptance_insula_paths else str(rootfs_manifest),
                "rootfs_manifest_digest": "sha256:rootfs",
                "lock_sha256": "sha256:lock",
                "workload_summary": {"native-prefix": {"verdict": "passed", "line": release_line}},
                "workload_summaries": {"workloads": {"native-prefix": {"verdict": "passed", "line": release_line}}},
                "release": release_doc,
            },
        )
        stages.append(
            {
                "kind": "release_gate",
                "name": f"release:{profile}:{release_line}",
                "profile": profile,
                "line": release_line,
                "release": True,
                "verdict": release_gate_verdict,
                "acceptance_path": str(acceptance),
                "acceptance_verdict": release_gate_verdict,
                **({"output_base": str(output_base)} if record_output_base else {}),
            }
        )
        info_by_line[release_line] = {
            "run_dir": run_dir,
            "wheel": wheel,
            "sbom": sbom,
            "acceptance": acceptance,
            "rootfs_manifest": rootfs_manifest,
            "version": Path(version),
        }

    for determinism_line in determinism_lines:
        line_determinism_verdict = determinism_verdicts.get(determinism_line, determinism_verdict)
        det_report = results / f"determinism-{profile}-{determinism_line}.json"
        write_json(
            det_report,
            {
                "schema_version": 1,
                "profile": profile,
                "line": determinism_line,
                "identical": determinism_identical,
                "differing_files": [],
            },
        )
        stages.append(
            {
                "kind": "determinism",
                "name": f"determinism:{profile}:{determinism_line}",
                "profile": profile,
                "line": determinism_line,
                "verdict": line_determinism_verdict,
                "identical": determinism_identical,
                "differing_files": [],
                "report_path": str(det_report),
            }
        )
    write_json(
        run_dir / "summary.json",
        {
            "schema_version": 1,
            "run_id": run_id,
            "commit": commit,
            "target": "HEAD",
            "finished_utc": "2026-10-03T01:02:03Z",
            "verdict": verdict,
            "stages": stages,
        },
    )
    return info_by_line[line]


def run_cut(estate: Path, *, profile: str = "torch", line: str = "cu129", sha: str = SHA) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "VASO_ESTATE_ROOT": str(estate)}
    return subprocess.run(
        ["bash", str(SCRIPT), "--profile", profile, "--line", line, "--sha", sha],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_release_cut_refuses_missing_red_or_wrong_sha_nightly(tmp_path: Path) -> None:
    estate = tmp_path / "estate"

    missing = run_cut(estate)
    assert missing.returncode != 0
    assert "no passed nightly" in missing.stderr

    make_green_nightly(estate, verdict="failed")
    red = run_cut(estate)
    assert red.returncode != 0
    assert "no passed nightly" in red.stderr

    make_green_nightly(estate, run_id="20261004T010203Z-other", commit="1234567890abcdef1234567890abcdef12345678")
    wrong_sha = run_cut(estate, sha="ffffffffffffffffffffffffffffffffffffffff")
    assert wrong_sha.returncode != 0
    assert "no passed nightly" in wrong_sha.stderr


def test_release_cut_refuses_existing_release_dir(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    info = make_green_nightly(estate)
    release_dir = estate / "releases" / "torch" / "cu129" / info["version"].name
    release_dir.mkdir(parents=True)

    result = run_cut(estate)

    assert result.returncode != 0
    assert "release dir already exists" in result.stderr


def test_release_cut_refuses_wheel_with_mismatched_commit_suffix(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    stale_commit = "deadbeef00000000000000000000000000000000"
    info = make_green_nightly(estate, wheel_commit=stale_commit)

    result = run_cut(estate)

    assert result.returncode != 0
    assert info["wheel"].name in result.stderr
    assert "does not match --sha" in result.stderr


def test_release_cut_requires_determinism_for_requested_line(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    make_green_nightly(estate, determinism_lines=("cu130",))

    result = run_cut(estate, line="cu129")

    assert result.returncode != 0
    assert "determinism torch/cu129" in result.stderr


def test_release_cut_requires_release_gate_for_both_lines(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    make_green_nightly(estate, line="cu130", release_lines=("cu130",))

    result = run_cut(estate, line="cu130")

    assert result.returncode != 0
    assert "release_gate torch/cu129 missing" in result.stderr


def test_release_cut_refuses_red_release_gate_on_other_line(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    make_green_nightly(estate, gate_verdicts={"cu129": "failed"})

    result = run_cut(estate, line="cu130")

    assert result.returncode != 0
    assert "release_gate torch/cu129 red" in result.stderr


def test_release_cut_requires_determinism_for_both_lines(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    make_green_nightly(estate, determinism_lines=("cu130",))

    result = run_cut(estate, line="cu130")

    assert result.returncode != 0
    assert "determinism torch/cu129 missing" in result.stderr


def test_release_cut_requires_release_workload_summaries(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    make_green_nightly(estate, include_release_workloads=False)

    result = run_cut(estate)

    assert result.returncode != 0
    assert "release workload_summaries" in result.stderr


def test_release_cut_writes_complete_release_dir_from_green_nightly(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    info = make_green_nightly(estate)

    result = run_cut(estate)

    assert result.returncode == 0, result.stdout + result.stderr
    release_dir = estate / "releases" / "torch" / "cu129" / info["version"].name
    assert release_dir.is_dir()
    assert (release_dir / info["wheel"].name).read_bytes() == info["wheel"].read_bytes()
    expected_wheel_sha = hashlib.sha256(info["wheel"].read_bytes()).hexdigest()
    assert (release_dir / "SHA256SUMS").read_text(encoding="utf-8") == f"{expected_wheel_sha}  {info['wheel'].name}\n"
    assert json.loads((release_dir / "sbom.json").read_text(encoding="utf-8"))["bomFormat"] == "CycloneDX"
    assert json.loads((release_dir / "acceptance.json").read_text(encoding="utf-8"))["verdict"] == "passed"
    assert json.loads((release_dir / "workload-results.json").read_text(encoding="utf-8")) == {
        "release-workloads": {"verdict": "passed", "line": "cu129"}
    }
    determinism = json.loads((release_dir / "determinism.json").read_text(encoding="utf-8"))
    assert determinism["identical"] is True
    assert determinism["line"] == "cu129"
    assert json.loads((release_dir / "rootfs-bundle.json").read_text(encoding="utf-8"))["line"] == "cu129"
    provenance = json.loads((release_dir / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["sha"] == SHA
    assert provenance["versions"]["torch"] == info["version"].name
    assert provenance["rootfs_bundle_digest"] == "sha256:rootfs"
    assert provenance["lock_digests"]["cuda_ecosystem"] == "sha256:lock"
    cut = json.loads((release_dir / "cut.json").read_text(encoding="utf-8"))
    assert cut["schema_version"] == 1
    assert cut["profile"] == "torch"
    assert cut["line"] == "cu129"
    assert cut["sha"] == SHA
    assert cut["version"] == info["version"].name
    assert cut["nightly_summary"].endswith("/summary.json")
    assert cut["artifacts"]["wheels"] == [info["wheel"].name]


def test_release_cut_resolves_insula_paths_from_green_nightly(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    info = make_green_nightly(estate, acceptance_insula_paths=True)

    result = run_cut(estate)

    assert result.returncode == 0, result.stdout + result.stderr
    release_dir = estate / "releases" / "torch" / "cu129" / info["version"].name
    assert (release_dir / info["wheel"].name).read_bytes() == info["wheel"].read_bytes()
    assert json.loads((release_dir / "sbom.json").read_text(encoding="utf-8"))["bomFormat"] == "CycloneDX"
    assert json.loads((release_dir / "rootfs-bundle.json").read_text(encoding="utf-8"))["line"] == "cu129"


def test_release_cut_resolves_execroot_relative_wheels_from_gate_output_base(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    info = make_green_nightly(estate, acceptance_insula_paths=True, execroot_relative_wheels=True)

    result = run_cut(estate)

    assert result.returncode == 0, result.stdout + result.stderr
    release_dir = estate / "releases" / "torch" / "cu129" / info["version"].name
    assert (release_dir / info["wheel"].name).read_bytes() == info["wheel"].read_bytes()


def test_release_cut_refuses_execroot_relative_wheel_without_gate_output_base(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    make_green_nightly(
        estate,
        acceptance_insula_paths=True,
        execroot_relative_wheels=True,
        record_output_base=False,
    )

    result = run_cut(estate)

    assert result.returncode != 0
    assert "output_base" in result.stderr
    assert not (estate / "releases").exists()
