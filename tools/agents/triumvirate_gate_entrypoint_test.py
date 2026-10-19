from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
HOST_GATE = REPO_ROOT / "tools" / "agents" / "triumvirate-gate.sh"
SHA = "abcdef1234567890abcdef1234567890abcdef12"


def write_executable(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def fake_gate_repo(tmp_path: Path) -> Path:
    root = tmp_path / "fake-repo"
    gate = root / "tools" / "agents" / "triumvirate-gate.sh"
    gate.parent.mkdir(parents=True)
    shutil.copy2(HOST_GATE, gate)
    write_executable(
        root / "foundry" / "tools" / "native_determinism_args.sh",
        """#!/usr/bin/env bash
native_determinism_resolve_release_git_sha() {
  printf '%s\\n' "$2"
}
native_determinism_source_date_epoch() {
  printf '%s\\n' "1600000000"
}
""",
    )
    write_executable(
        root / "foundry" / "run.sh",
        """#!/usr/bin/env bash
set -euo pipefail
python3 - "$FAKE_RUN_ARGV" "$@" <<'PY'
import json
import sys
from pathlib import Path

Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:], sort_keys=True) + "\\n", encoding="utf-8")
PY
acceptance_out=""
out_dir=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --acceptance-out) acceptance_out="$2"; shift 2 ;;
    --out-dir) out_dir="$2"; shift 2 ;;
    *) shift ;;
  esac
done
host_path() {
  case "$1" in
    /vaso/agents) printf '%s\\n' "$FAKE_ESTATE_ROOT/agents" ;;
    /vaso/agents/*) printf '%s/%s\\n' "$FAKE_ESTATE_ROOT/agents" "${1#/vaso/agents/}" ;;
    /vaso/*) printf '%s/%s\\n' "$FAKE_ESTATE_ROOT/vaso" "${1#/vaso/}" ;;
    *) printf '%s\\n' "$1" ;;
  esac
}
if [ -n "$acceptance_out" ]; then
  host_out="$(host_path "$acceptance_out")"
  mkdir -p "$(dirname "$host_out")"
  python3 - "$host_out" "$acceptance_out" <<'PY'
import json
import sys
from pathlib import Path

Path(sys.argv[1]).write_text(
    json.dumps({"verdict": "passed", "acceptance_out": sys.argv[2]}, sort_keys=True) + "\\n",
    encoding="utf-8",
)
PY
fi
if [ -n "$out_dir" ]; then
  host_out_dir="$(host_path "$out_dir")"
  mkdir -p "$host_out_dir"
  printf 'proof\\n' > "$host_out_dir/proof.txt"
fi
exit "${FAKE_RUN_RC:-0}"
""",
    )
    return root


def arg_value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def test_host_gate_stages_and_copies_unbound_host_outputs(tmp_path: Path) -> None:
    fake_root = fake_gate_repo(tmp_path)
    estate = tmp_path / "estate"
    requested_acceptance = tmp_path / "caller-results" / "release-torch-cu129.acceptance.json"
    requested_out_dir = tmp_path / "caller-results" / "proof-dir"
    run_argv = tmp_path / "run-argv.json"
    env = {
        **os.environ,
        "VASO_ESTATE_ROOT": str(estate),
        "VASO_AGENT": "gate-entrypoint-test",
        "FAKE_ESTATE_ROOT": str(estate),
        "FAKE_RUN_ARGV": str(run_argv),
    }

    proc = subprocess.run(
        [
            "bash",
            str(fake_root / "tools" / "agents" / "triumvirate-gate.sh"),
            "--release",
            "--profile",
            "torch",
            "--line",
            "cu129",
            "--commit",
            SHA,
            "--acceptance-out",
            str(requested_acceptance),
            "--out-dir",
            str(requested_out_dir),
        ],
        cwd=fake_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    acceptance = json.loads(requested_acceptance.read_text(encoding="utf-8"))
    assert acceptance["verdict"] == "passed"
    assert acceptance["acceptance_out"].startswith("/vaso/agents/gate-entrypoint-test/tmp/")
    assert (requested_out_dir / "proof.txt").read_text(encoding="utf-8") == "proof\n"
    argv = json.loads(run_argv.read_text(encoding="utf-8"))
    assert arg_value(argv, "--acceptance-out").startswith("/vaso/agents/gate-entrypoint-test/tmp/")
    assert arg_value(argv, "--acceptance-out") != str(requested_acceptance)
    assert arg_value(argv, "--out-dir").startswith("/vaso/agents/gate-entrypoint-test/tmp/")
    assert arg_value(argv, "--out-dir") != str(requested_out_dir)


def test_host_gate_preserves_gate_exit_code_after_copy_back(tmp_path: Path) -> None:
    fake_root = fake_gate_repo(tmp_path)
    estate = tmp_path / "estate"
    requested_acceptance = tmp_path / "caller-results" / "release-torch-cu129.acceptance.json"
    env = {
        **os.environ,
        "VASO_ESTATE_ROOT": str(estate),
        "VASO_AGENT": "gate-entrypoint-test",
        "FAKE_ESTATE_ROOT": str(estate),
        "FAKE_RUN_ARGV": str(tmp_path / "run-argv.json"),
        "FAKE_RUN_RC": "42",
    }

    proc = subprocess.run(
        [
            "bash",
            str(fake_root / "tools" / "agents" / "triumvirate-gate.sh"),
            "--release",
            "--profile",
            "torch",
            "--line",
            "cu129",
            "--commit",
            SHA,
            "--acceptance-out",
            str(requested_acceptance),
        ],
        cwd=fake_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 42, proc.stdout + proc.stderr
    assert json.loads(requested_acceptance.read_text(encoding="utf-8"))["verdict"] == "passed"
