"""Sourceable bundle restoration commits its output only after success."""

from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("status", [0, 37])
@pytest.mark.parametrize("calling_context", ["plain", "conditional", "or-list"])
def test_bundle_restore_retains_status_and_output_ownership(
    tmp_path, status, calling_context
):
    checkout = tmp_path / "fixture checkout"
    helper = checkout / "insula/rootfs/bundle.sh"
    helper.parent.mkdir(parents=True)
    helper.write_text(
        '#!/bin/bash\nprintf "%s\\0" "$@" > "$ARGUMENTS"\n'
        'printf "%s\\n" "$RESTORED"\n'
        'if [[ "$RESTORE_STATUS" != 0 ]]; then echo "restore failed" >&2; fi\n'
        'exit "$RESTORE_STATUS"\n'
    )
    helper.chmod(0o755)
    bundle = tmp_path / ("rootfs-cu129-" + "a" * 64 + ".tar.zst")
    destination = tmp_path / "restore destination"
    rootfs = destination / "cu129/rootfs"
    invocation = 'insula_rootfs_from_bundle restored "$bundle" "$destination"'
    call = {
        "plain": invocation + "; status=$?",
        "conditional": "if " + invocation + "; then status=0; else status=$?; fi",
        "or-list": "status=0; " + invocation + " || status=$?",
    }[calling_context]
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1/insula/execution/insula.sh"; INSULA_REPO_ROOT="$2"; '
            'bundle="$3"; destination="$4"; restored=previous; '
            + call
            + '; printf "%s\\n%s\\n" "$status" "$restored"',
            "bash",
            str(ROOT),
            str(checkout),
            str(bundle),
            str(destination),
        ],
        env={
            "PATH": "/usr/bin:/bin",
            "ARGUMENTS": str(tmp_path / "arguments"),
            "RESTORED": str(rootfs),
            "RESTORE_STATUS": str(status),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        str(status),
        str(rootfs) if status == 0 else "previous",
    ]
    assert (tmp_path / "arguments").read_bytes().decode().split("\0")[:-1] == [
        "unbundle",
        "--bundle",
        str(bundle),
        "--dest",
        str(destination / "cu129"),
        "--line",
        "cu129",
        "--digest",
        "a" * 64,
    ]
    assert ("restore failed" in result.stderr) == (status != 0)


def test_missing_bundle_failure_survives_a_strict_conditional_caller(tmp_path):
    bundle = tmp_path / ("rootfs-cu129-" + "b" * 64 + ".tar.zst")
    destination = tmp_path / "restore"
    result = subprocess.run(
        [
            "bash",
            "-c",
            'set -euo pipefail; source "$1/insula/execution/insula.sh"; '
            "restored=previous; "
            'if insula_rootfs_from_bundle restored "$2" "$3"; '
            "then status=0; else status=$?; fi; "
            'printf "%s\\n%s\\n" "$status" "$restored"',
            "bash",
            str(ROOT),
            str(bundle),
            str(destination),
        ],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["1", "previous"]
    assert "missing bundle:" in result.stderr
    assert not destination.exists()
