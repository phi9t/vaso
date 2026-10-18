import json
import os
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "determinism_check.sh"


def write_stub_run_sh(path: Path, log_path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            python3 - "$VASO_BAZEL_OB" "$VASO_CUDA_LINE" {str(log_path)!r} "$@" <<'PY'
            import json
            import os
            import sys
            import zipfile
            from pathlib import Path

            ob = Path(sys.argv[1])
            line = sys.argv[2]
            log = Path(sys.argv[3])
            args = sys.argv[4:]
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({{
                    "args": args,
                    "cwd": os.getcwd(),
                    "line": line,
                    "output_base": str(ob),
                }}, sort_keys=True) + "\\n")

            target_to_output = {{
                "//native/pytorch:pytorch_release_wheel": (
                    "native/pytorch/pytorch_release_wheel_wheelhouse",
                    "torch-2.14.0-cp313-cp313-linux_x86_64.whl",
                ),
                "//native/torchvision:torchvision_release_wheel": (
                    "native/torchvision/torchvision_release_wheel_wheelhouse",
                    "torchvision-0.29.0-cp313-cp313-linux_x86_64.whl",
                ),
                "//native/torchaudio:torchaudio_release_wheel": (
                    "native/torchaudio/torchaudio_release_wheel_wheelhouse",
                    "torchaudio-2.11.0-cp313-cp313-linux_x86_64.whl",
                ),
                "//native/triton:triton_release_wheel": (
                    "native/triton/triton_release_wheel_wheelhouse",
                    "triton-3.8.0-cp313-cp313-linux_x86_64.whl",
                ),
                "//native/jaxlib:jaxlib_release_wheels": (
                    "native/jaxlib/jaxlib_release_wheels_wheelhouse",
                    "jaxlib-0.10.2-cp313-cp313-linux_x86_64.whl",
                ),
            }}
            root = ob / "execroot" / "_main" / "bazel-out" / "k8-fastbuild" / "bin"
            for target, (rel_dir, wheel_name) in target_to_output.items():
                if target not in args:
                    continue
                out_dir = root / rel_dir
                out_dir.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(out_dir / wheel_name, "w") as zf:
                    zf.writestr("pkg/__init__.py", b"stable\\n")
                    zf.writestr("pkg-1.0.dist-info/RECORD", b"different each build is normalized\\n")
            manifest = ob / "external" / "openssl_native" / "prefix_manifest.json"
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(json.dumps({{
                "entries": [
                    {{"mode": "0644", "path": "lib/libcrypto.a", "sha256": "stable", "size": 6, "type": "file"}}
                ],
                "schema_version": 1,
                "tree_sha256": "stable-tree",
            }}, sort_keys=True) + "\\n", encoding="utf-8")
            PY
            """
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_determinism_check_builds_torch_release_targets_twice_with_fresh_output_bases(tmp_path: Path) -> None:
    run_sh = tmp_path / "run.sh"
    log = tmp_path / "calls.jsonl"
    write_stub_run_sh(run_sh, log)
    out = tmp_path / "determinism.json"
    work_dir = tmp_path / "work"
    env = {
        **os.environ,
        "TMPDIR": str(tmp_path / "tmp"),
        "VASO_AGENT": "det-test",
        "VASO_ESTATE_ROOT": str(tmp_path / "estate"),
        "VASO_DETERMINISM_RUN_SH": str(run_sh),
        "VASO_NATIVE_PYTORCH_TOKEN": "build-native-pytorch",
        "VASO_NATIVE_TORCHVISION_TOKEN": "build-native-torchvision",
        "VASO_NATIVE_TORCHAUDIO_TOKEN": "build-native-torchaudio",
        "VASO_NATIVE_TRITON_TOKEN": "build-native-triton",
    }

    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "--profile",
            "torch",
            "--line",
            "cu130",
            "--work-dir",
            str(work_dir),
            "--out",
            str(out),
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    # run.sh resolves its helpers (tools/estate.py, ...) relative to its own directory.
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert calls and {call["cwd"] for call in calls} == {str(run_sh.parent)}
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["identical"] is True
    assert report["differing_files"] == []
    assert report["dep_determinism"] == {
        "first_output_base": str(work_dir / "output-base-first"),
        "identical": True,
        "mismatched_deps": [],
        "second_output_base": str(work_dir / "output-base-second"),
        "unmaterialized_manifests": [],
    }
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 2
    assert calls[0]["line"] == "cu130"
    assert calls[1]["line"] == "cu130"
    assert calls[0]["output_base"] != calls[1]["output_base"]
    git_sha = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    epoch = subprocess.run(
        ["git", "-C", str(ROOT), "show", "-s", "--format=%ct", git_sha],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    for call in calls:
        args = call["args"]
        assert args[:1] == ["--bazel-build"]
        assert "--remote_download_outputs=all" in args
        assert "//native/pytorch:pytorch_release_wheel" in args
        assert "//native/torchvision:torchvision_release_wheel" in args
        assert "//native/torchaudio:torchaudio_release_wheel" in args
        assert "//native/triton:triton_release_wheel" in args
        assert "--//native/common:release_line=cu130" in args
        assert f"--//native/common:release_git_sha={git_sha}" in args
        assert f"--//native/common:source_date_epoch={epoch}" in args
        assert f"--//native/common:deterministic_seed={git_sha}" in args
        assert (
            "--//native/common:deterministic_prefix_map_roots="
            "/vaso-bazel-ob/execroot/_main,/vaso/lines/cu130/work"
        ) in args
        assert "--//native/pytorch:token=build-native-pytorch" in args
        assert "--//native/torchvision:token=build-native-torchvision" in args
        assert "--//native/torchaudio:token=build-native-torchaudio" in args
        assert "--//native/triton:token=build-native-triton" in args


def test_determinism_check_builds_jax_release_wheelhouse(tmp_path: Path) -> None:
    run_sh = tmp_path / "run.sh"
    log = tmp_path / "calls.jsonl"
    write_stub_run_sh(run_sh, log)
    out = tmp_path / "determinism.json"
    env = {
        **os.environ,
        "TMPDIR": str(tmp_path / "tmp"),
        "VASO_AGENT": "det-test",
        "VASO_ESTATE_ROOT": str(tmp_path / "estate"),
        "VASO_DETERMINISM_RUN_SH": str(run_sh),
        "VASO_NATIVE_LLVM_TOKEN": "build-native-llvm",
    }

    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "--profile",
            "jax",
            "--line",
            "cu129",
            "--work-dir",
            str(tmp_path / "work"),
            "--out",
            str(out),
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["identical"] is True
    assert report["dep_determinism"] == {
        "first_output_base": str(tmp_path / "work" / "output-base-first"),
        "identical": True,
        "mismatched_deps": [],
        "second_output_base": str(tmp_path / "work" / "output-base-second"),
        "unmaterialized_manifests": [],
    }
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 2
    git_sha = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    epoch = subprocess.run(
        ["git", "-C", str(ROOT), "show", "-s", "--format=%ct", git_sha],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    for call in calls:
        assert call["line"] == "cu129"
        assert "//native/jaxlib:jaxlib_release_wheels" in call["args"]
        assert f"--//native/common:release_git_sha={git_sha}" in call["args"]
        assert f"--//native/common:source_date_epoch={epoch}" in call["args"]
        assert f"--//native/common:deterministic_seed={git_sha}" in call["args"]
        assert (
            "--//native/common:deterministic_prefix_map_roots="
            "/vaso-bazel-ob/execroot/_main,/vaso/lines/cu129/work"
        ) in call["args"]
        assert "--//native/jaxlib:token=build-native-llvm" in call["args"]
        assert not any(arg.startswith("//native/pytorch:") for arg in call["args"])
