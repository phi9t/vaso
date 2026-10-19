#!/usr/bin/env python3
"""Behavior tests for resolve_apt_lock.py."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOTFS_DIR = Path(__file__).resolve().parent
SCRIPT = ROOTFS_DIR / "resolve_apt_lock.py"
LOCK = ROOTFS_DIR / "cuda_ecosystem.lock.json"


def write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class ResolveAptLockTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMPDIR"))
        self.root = Path(self.tmp.name)
        self.lock = self.root / "lock.json"
        self.out = self.root / "resolved.json"
        self.lock.write_text(LOCK.read_text(encoding="utf-8"), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_resolver_writes_snapshot_and_exact_package_versions(self) -> None:
        docker = self.root / "bin/docker"
        write_executable(
            docker,
            """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' 'base-files=13ubuntu10.1' 'ca-certificates=20240203' 'python3=3.12.3-0ubuntu2'
""",
        )

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--lock",
                str(self.lock),
                "--line",
                "cu129",
                "--snapshot",
                "20261001T000000Z",
                "--out",
                str(self.out),
                "--docker",
                str(docker),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        resolved = json.loads(self.out.read_text(encoding="utf-8"))
        apt = resolved["lines"]["cu129"]["apt"]
        self.assertEqual(apt["snapshot"], "20261001T000000Z")
        self.assertFalse(apt["unresolved"])
        self.assertEqual(
            apt["packages"],
            {
                "base-files": "13ubuntu10.1",
                "ca-certificates": "20240203",
                "python3": "3.12.3-0ubuntu2",
            },
        )


    def test_resolver_parses_only_dpkg_lines_when_apt_is_chatty(self) -> None:
        fakebin = self.root / "fakebin"
        write_executable(fakebin / "apt-get", "#!/usr/bin/env bash\necho 'Get:1 https://example.invalid noble InRelease [1578 B]'\n")
        write_executable(fakebin / "apt-mark", "#!/usr/bin/env bash\necho 'unhold'\n")
        write_executable(
            fakebin / "dpkg-query",
            """#!/usr/bin/env bash
case "$*" in
  *Version*) printf '%s\\n' 'base-files=13ubuntu10.1' 'binutils-common:amd64=2.42-4ubuntu2.5' 'curl=8.5.0-2ubuntu10.6' ;;
  *) ;;
esac
""",
        )
        docker = self.root / "bin/docker"
        write_executable(
            docker,
            f"""#!/usr/bin/env bash
set -euo pipefail
script="${{@: -1}}"
# The resolver rewrites /etc/apt; run it against a scratch root instead.
script="$(printf '%s' "$script" | sed "s#/etc/apt#{self.root}/etc-apt#g")"
mkdir -p "{self.root}/etc-apt/sources.list.d"
PATH="{fakebin}:$PATH" bash -c "$script"
""",
        )
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--lock", str(self.lock), "--line", "cu130",
             "--snapshot", "20261001T000000Z", "--out", str(self.out), "--docker", str(docker)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        apt = json.loads(self.out.read_text(encoding="utf-8"))["lines"]["cu130"]["apt"]
        self.assertEqual(apt["packages"], {"base-files": "13ubuntu10.1", "binutils-common": "2.42-4ubuntu2.5", "curl": "8.5.0-2ubuntu10.6"})


if __name__ == "__main__":
    unittest.main()
