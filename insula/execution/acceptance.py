#!/usr/bin/env python3
"""Exercise the real insula launcher and lease CLI in disposable owned state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FEATURES = ("readiness", "mounts-and-argv", "exit-status", "lease-recovery", "cleanup")


class AcceptanceFailure(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceFailure(message)


class AcceptanceRun:
    def __init__(self, rootfs: Path, evidence: Path):
        self.rootfs = rootfs.resolve()
        self.evidence = evidence.resolve()
        # Never reuse or remove a caller's directory, even after a failed run.
        self.evidence.mkdir(parents=True, mode=0o700, exist_ok=False)
        self.report: dict = {
            "schema_version": 1,
            "verdict": "failed",
            "rootfs": {
                "path": str(self.rootfs),
                "mode": "host-stand-in" if self.rootfs == Path("/") else "supplied-rootfs",
            },
            "features": {feature: False for feature in FEATURES},
            "commands": [],
        }
        self.save()

    def save(self) -> None:
        (self.evidence / "result.json").write_text(json.dumps(self.report, indent=2) + "\n")

    def command(self, name: str, argv: list[str], *, expected: int = 0) -> str:
        record = {"name": name, "argv": argv, "expected_exit": expected}
        self.report["commands"].append(record)
        self.save()
        process = subprocess.Popen(
            argv, cwd=self.state, env=self.env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
            record["timed_out"] = True
        (self.evidence / f"{name}.stdout").write_text(stdout)
        (self.evidence / f"{name}.stderr").write_text(stderr)
        record["exit_code"] = process.returncode
        self.save()
        require(not record.get("timed_out"), f"{name} timed out; see retained logs")
        require(process.returncode == expected, f"{name}: expected exit {expected}, got {process.returncode}; see {name}.stderr")
        return stdout

    def prepare(self) -> None:
        require(self.rootfs.is_dir(), f"rootfs directory is missing: {self.rootfs}")
        require((self.rootfs / "bin/sh").is_file(), f"rootfs has no /bin/sh: {self.rootfs}")
        bwrap = shutil.which("bwrap", path="/usr/bin:/bin")
        require(bwrap is not None, "real bwrap is required in /usr/bin or /bin")
        self.bwrap = str(Path(bwrap).resolve())
        self.report["bwrap"] = {"path": self.bwrap, "sha256": hashlib.sha256(Path(self.bwrap).read_bytes()).hexdigest()}
        self.report["source_sha256"] = {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in (
                "insula/execution/acceptance.py", "insula/execution/insula.sh",
                "insula/execution/lease.py", "insula/estate/overlay_root.py", "insula/execution/vaso/doctor.py",
            )
        }
        manifest = self.rootfs.parent / "rootfs-bundle.json"
        if self.rootfs != Path("/") and manifest.is_file():
            self.report["rootfs"]["manifest_sha256"] = hashlib.sha256(manifest.read_bytes()).hexdigest()
        estate = self.state / "estate"
        for relative in (
            "vaso/cache/bazel/repository-cache", "vaso/tmp", "vaso/state", "home/kvothe", "sources", "agents/leases",
        ):
            (estate / relative).mkdir(parents=True, exist_ok=True)
        (self.state / "input").mkdir()
        (self.state / "input/input.txt").write_text("original\n")
        # No ambient estate, inherited lease, BASH_ENV or fake-bwrap override.
        self.env = {
            "PATH": "/usr/bin:/bin", "HOME": str(estate / "home/kvothe"), "LC_ALL": "C",
            "PYTHONDONTWRITEBYTECODE": "1", "VASO_ESTATE_ROOT": str(estate), "VASO_AGENT": "acceptance",
        }
        plan = self.command("root-plan", [
            sys.executable, str(ROOT / "insula/estate/overlay_root.py"),
            "--base", str(self.rootfs), "--mountpoint", "/workspace/acceptance", "--mountpoint", "/vaso/state",
        ])
        plan_path = self.evidence / "root-plan.argv"
        plan_path.write_text(plan)
        launcher = self.evidence / "launcher.sh"
        launcher.write_text('''#!/usr/bin/env bash
set -euo pipefail
repo=$1; state=$2; real_bwrap=$3; plan=$4
shift 4
source "$repo/insula/execution/insula.sh"
mapfile -t ROOT_PLAN < "$plan"
INSULA_BIND_ARGS=(--ro-bind "$state/input" /workspace/acceptance --bind "$state/estate/vaso/state" /vaso/state)
INSULA_ENV_ARGS=(--setenv PATH /usr/bin:/bin --setenv LC_ALL C)
INSULA_BWRAP_BIN=$real_bwrap
INSULA_ESTATE_MIN_FREE_GIB=0
INSULA_REQUIRE_DRIVER=0
insula_preflight >&2 || exit $?
# Readiness was checked above; suppress only the duplicate internal check.
INSULA_SKIP_PREFLIGHT=1 insula "$@"
''')
        self.launch = ["/bin/bash", str(launcher), str(ROOT), str(self.state), self.bwrap, str(plan_path)]
        rendered = self.command("rendered-argv", [*self.launch, "--print-argv", "--", "/bin/sh", "-c", "printf 'ready\\n'"])
        self.report["bwrap"]["readiness_argv"] = json.loads(rendered)
        ready = self.command("readiness", [*self.launch, "--", "/bin/sh", "-c", "printf 'ready\\n'"])
        require(ready == "ready\n", "readiness command did not execute in the rootfs")
        self.report["features"]["readiness"] = True

    def mounts(self) -> None:
        literal = "space and $(touch /vaso/state/injected)"
        output = self.command("mounts", [*self.launch, "--", "/bin/sh", "-c", '''
set -eu
IFS= read -r value < /workspace/acceptance/input.txt
test "$value" = original
if (printf tampered > /workspace/acceptance/input.txt) 2>/dev/null; then exit 91; fi
printf 'persisted\n' > /vaso/state/result.txt
printf '%s' "$1" > /vaso/state/argv.txt
printf 'mounts-ok\n'
''', "acceptance", literal])
        require(output == "mounts-ok\n", "mount assertions did not finish")
        writable = self.state / "estate/vaso/state"
        require((writable / "result.txt").read_text() == "persisted\n", "writable bind did not persist")
        require((self.state / "input/input.txt").read_text() == "original\n", "read-only input changed")
        require((writable / "argv.txt").read_text() == literal, "argv was interpreted or changed")
        require(not (writable / "injected").exists(), "argv was interpreted by a shell")
        self.report["features"]["mounts-and-argv"] = True

    def leases(self) -> None:
        lease = [sys.executable, str(ROOT / "insula/execution/lease.py")]
        resource = "insula:acceptance"
        holder = subprocess.Popen(["/bin/sleep", "60"], env=self.env)
        self.report["lease_holder_pid"] = holder.pid
        acquire = [*lease, "acquire", "--resource", resource, "--holder", "acceptance", "--ttl", "60", "--timeout", "0"]
        try:
            self.command("lease-acquire", [*acquire, "--pid", str(holder.pid)])
            self.command("lease-contention", [*acquire, "--pid", str(os.getpid())], expected=75)
        finally:
            holder.terminate()
            holder.wait(timeout=5)
        replacement = json.loads(self.command("lease-recover", [*acquire, "--pid", str(os.getpid())]))
        self.command("lease-release", [*lease, "release", "--id", replacement["id"]])
        listed = json.loads(self.command("leases-after", [*lease, "list"]))
        require(listed["leases"] == [], "active leases remain after release")
        self.report["features"]["lease-recovery"] = True

    def run(self) -> int:
        temporary = tempfile.TemporaryDirectory(prefix="state-", dir=self.evidence)
        self.state = Path(temporary.name)
        self.report["state_dir"] = str(self.state)
        self.save()
        try:
            self.prepare()
            self.mounts()
            self.command("exit-status", [*self.launch, "--", "/bin/sh", "-c", "exit 23"], expected=23)
            self.report["features"]["exit-status"] = True
            self.leases()
        except (AcceptanceFailure, OSError, ValueError, subprocess.SubprocessError) as exc:
            self.report["error"] = str(exc)
        finally:
            temporary.cleanup()
            removed = not self.state.exists()
            self.report["cleanup"] = {"state_removed": removed}
            self.report["features"]["cleanup"] = removed
            self.report["verdict"] = "passed" if all(self.report["features"].values()) else "failed"
            self.save()
        print(f"insula acceptance {self.report['verdict']}: {self.evidence / 'result.json'}")
        return 0 if self.report["verdict"] == "passed" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rootfs", type=Path, required=True, help="existing rootfs; / explicitly selects a host stand-in")
    parser.add_argument("--evidence-dir", type=Path, required=True, help="new directory; evidence survives state cleanup")
    args = parser.parse_args(argv)
    try:
        return AcceptanceRun(args.rootfs, args.evidence_dir).run()
    except OSError as exc:
        print(f"cannot create acceptance run: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
