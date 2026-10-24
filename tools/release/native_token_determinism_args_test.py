import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "foundry/tools/native_determinism_args.sh"
RUN_SH = ROOT / "foundry/run.sh"
HOST_GATE = ROOT / "scripts/agents/triumvirate-gate.sh"
INSULA_GATE = ROOT / "foundry/scripts/agents/triumvirate-gate-insula.sh"
TOKEN_FLAG = re.compile(r"--//native/[A-Za-z0-9_./-]+:token=")
EXPECTED_TOKEN_EMITTERS = {
    Path("foundry/run.sh"): "BAZEL_RELEASE_ARGS",
    Path("foundry/scripts/agents/triumvirate-gate-insula.sh"): "out_args",
    Path("tools/release/determinism_check.sh"): "BAZEL_FLAGS",
}


def _tracked_source_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    paths: list[Path] = []
    for raw in result.stdout.splitlines():
        path = Path(raw)
        if path.suffix not in {".sh", ".py", ".bzl", ".bazel"} and path.name not in {
            "BUILD",
            "BUILD.bazel",
        }:
            continue
        if "tests" in path.parts or path.name.endswith("_test.py"):
            continue
        if (ROOT / path).is_file():
            paths.append(path)
    return paths


def test_native_token_emitters_use_shared_determinism_helper() -> None:
    helper_text = HELPER.read_text(encoding="utf-8")
    for flag in (
        "--//native/common:source_date_epoch=",
        "--//native/common:deterministic_seed=",
        "--//native/common:deterministic_prefix_map_roots=",
        "--//native/common:native_action_cuda_home=",
    ):
        assert flag in helper_text
    assert "native_determinism_fixed_output_base_mount()" in helper_text

    emitters = {
        path
        for path in _tracked_source_files()
        if TOKEN_FLAG.search((ROOT / path).read_text(encoding="utf-8"))
    }

    assert emitters == set(EXPECTED_TOKEN_EMITTERS)
    for path, array_name in EXPECTED_TOKEN_EMITTERS.items():
        text = (ROOT / path).read_text(encoding="utf-8")
        assert "native_determinism_args.sh" in text
        assert re.search(
            rf"append_native_determinism_bazel_args\s+\\?\s*{re.escape(array_name)}\b",
            text,
        ), path


def test_native_output_base_mount_is_shared_by_all_tokened_entrypoints() -> None:
    helper_text = HELPER.read_text(encoding="utf-8")
    assert 'printf "%s\\n" "/vaso-bazel-ob"' in helper_text

    for path in EXPECTED_TOKEN_EMITTERS:
        text = (ROOT / path).read_text(encoding="utf-8")
        assert "native_determinism_fixed_output_base_mount" in text, path
        assert "/vaso/lines/$line/cache/bazel/output-base" not in text, path
        assert "/vaso/lines/$VASO_CUDA_LINE/cache/bazel/output-base" not in text, path


def test_disk_cache_gc_flags_are_emitted_by_shared_helper() -> None:
    script = f"""
set -euo pipefail
source {str(HELPER)!r}
args=()
append_native_bazel_disk_cache_gc_args args
printf '%s\\n' "${{args[@]}}"
"""
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == [
        "--experimental_disk_cache_gc_max_size=80G",
        "--experimental_disk_cache_gc_max_age=72h",
    ]


def test_run_and_gate_bazel_argv_use_shared_disk_cache_gc_helper() -> None:
    helper_text = HELPER.read_text(encoding="utf-8")
    assert "--experimental_disk_cache_gc_max_size=" in helper_text
    assert "--experimental_disk_cache_gc_max_age=" in helper_text

    run_text = RUN_SH.read_text(encoding="utf-8")
    assert "append_native_bazel_disk_cache_gc_args bazel_disk_cache_gc_args" in run_text
    assert '"${bazel_disk_cache_gc_args[@]}" \\' in run_text
    assert "--experimental_disk_cache_gc_max_size=" not in run_text
    assert "--experimental_disk_cache_gc_max_age=" not in run_text

    gate_text = INSULA_GATE.read_text(encoding="utf-8")
    profile_args = gate_text.split("bazel_profile_command_args() {", 1)[1].split(
        "\n}", 1
    )[0]
    assert "append_native_bazel_disk_cache_gc_args out_args" in profile_args
    assert "--experimental_disk_cache_gc_max_size=" not in gate_text
    assert "--experimental_disk_cache_gc_max_age=" not in gate_text


def test_triumvirate_host_wrapper_forwards_release_epoch_from_helper() -> None:
    text = HOST_GATE.read_text(encoding="utf-8")

    assert 'native_determinism_resolve_release_git_sha "$ROOT" "$commit_input"' in text
    assert 'native_determinism_source_date_epoch "$ROOT" "$release_git_sha"' in text
    assert (
        'args+=("--commit" "$release_git_sha" "--source-date-epoch" "$release_source_date_epoch")'
        in text
    )
