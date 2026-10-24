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
import time
from pathlib import Path

from vaso.atomic_files import atomic_text


ROOT = Path(__file__).resolve().parents[2]
FEATURES = ("readiness", "mounts-and-argv", "exit-status", "interruption-and-retry", "lease-recovery", "cleanup")


class AcceptanceFailure(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceFailure(message)


def source_hashes(repo: Path) -> dict[str, str]:
    """Identify execution code, including readiness's runtime package closure."""
    paths = [
        repo / relative
        for relative in (
            "insula/run.sh",
            "insula/execution/acceptance.py",
            "insula/execution/insula.sh",
            "insula/execution/doctor.sh",
            "insula/execution/lease.py",
            "insula/estate/overlay_root.py",
            "insula/estate/estate.py",
            "insula/rootfs/verify_cuda_ecosystem.py",
        )
    ]
    paths.extend(
        path
        for path in (repo / "insula/execution/vaso").rglob("*.py")
        if not (
            path.name.endswith("_test.py")
            or path.name.startswith("test_")
            or path.name == "conftest.py"
        )
    )
    return {
        path.relative_to(repo).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(paths)
    }


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
        atomic_text(self.evidence / "result.json", json.dumps(self.report, indent=2) + "\n")

    def command(
        self, name: str, argv: list[str], *, expected: int = 0,
        interrupt_when: Path | None = None,
    ) -> str:
        record = {"name": name, "argv": argv, "expected_exit": expected}
        self.report["commands"].append(record)
        self.save()
        process = subprocess.Popen(
            argv,
            cwd=self.state,
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        failure = None
        try:
            if interrupt_when is not None:
                deadline = time.monotonic() + 10
                while not interrupt_when.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                require(interrupt_when.exists(), f"{name}: child did not become ready for interruption")
                # Signal only the public launcher: bwrap must stop its owned child.
                process.send_signal(signal.SIGTERM)
                record["signal"] = "SIGTERM"
            stdout, stderr = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            record["timed_out"] = True
        except (Exception, KeyboardInterrupt) as exc:
            failure = exc
            record["error"] = str(exc) or type(exc).__name__
        finally:
            if process.poll() is None or failure is not None or record.get("timed_out"):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                stdout, stderr = process.communicate()
        (self.evidence / f"{name}.stdout").write_text(stdout)
        (self.evidence / f"{name}.stderr").write_text(stderr)
        record["exit_code"] = process.returncode
        self.save()
        if failure is not None:
            raise failure
        require(not record.get("timed_out"), f"{name} timed out; see retained logs")
        require(
            process.returncode == expected,
            f"{name}: expected exit {expected}, got {process.returncode}; see {name}.stderr",
        )
        return stdout

    def prepare(self) -> None:
        require(self.rootfs.is_dir(), f"rootfs directory is missing: {self.rootfs}")
        require((self.rootfs / "bin/sh").is_file(), f"rootfs has no /bin/sh: {self.rootfs}")
        bwrap = shutil.which("bwrap", path="/usr/bin:/bin")
        require(bwrap is not None, "real bwrap is required in /usr/bin or /bin")
        self.bwrap = str(Path(bwrap).resolve())
        self.report["bwrap"] = {"path": self.bwrap, "sha256": hashlib.sha256(Path(self.bwrap).read_bytes()).hexdigest()}
        self.report["source_sha256"] = source_hashes(ROOT)
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
        self.launch = [
            str(ROOT / "insula/run.sh"), "--rootfs", str(self.rootfs),
            "--workspace", str(self.state / "input"),
        ]
        rendered = self.command("rendered-argv", [*self.launch, "--print-argv", "--", "/bin/sh", "-c", "printf 'ready\\n'"])
        self.report["bwrap"]["readiness_argv"] = json.loads(rendered)
        ready = self.command("readiness", [*self.launch, "--", "/bin/sh", "-c", "printf 'ready\\n'"])
        require(ready == "ready\n", "readiness command did not execute in the rootfs")
        self.report["features"]["readiness"] = True

    def mounts(self) -> None:
        literal = "space and $(touch /vaso/state/injected)"
        output = self.command("mounts", [*self.launch, "--", "/bin/sh", "-c", '''
set -eu
IFS= read -r value < /workspace/vaso/input.txt
test "$value" = original
if (printf tampered > /workspace/vaso/input.txt) 2>/dev/null; then exit 91; fi
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

    def interruption(self) -> None:
        writable = self.state / "estate/vaso/state"
        self.command("interruption", [*self.launch, "--", "/bin/sh", "-c", """
set -eu
printf 'started\n' > /vaso/state/interrupted-started
while :; do printf tick >> /vaso/state/heartbeat; sleep 0.05; done
"""], expected=-signal.SIGTERM, interrupt_when=writable / "interrupted-started")
        heartbeat = writable / "heartbeat"
        before = heartbeat.read_bytes() if heartbeat.exists() else b""
        time.sleep(0.2)
        require((heartbeat.read_bytes() if heartbeat.exists() else b"") == before,
                "interrupted child continued writing after the launcher stopped")
        retry = self.command("retry", [*self.launch, "--", "/bin/sh", "-c", "printf 'retry-ok\\n'"])
        require(retry == "retry-ok\n", "retry did not execute after failure and interruption")
        self.report["features"]["interruption-and-retry"] = True

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
            self.interruption()
            self.leases()
        except (AcceptanceFailure, OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
            self.report["error"] = str(exc) or type(exc).__name__
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
    parser.add_argument(
        "--rootfs",
        type=Path,
        required=True,
        help="existing rootfs; / explicitly selects a host stand-in",
    )
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        required=True,
        help="new directory; evidence survives state cleanup",
    )
    args = parser.parse_args(argv)

    def interrupted(_signum, _frame):
        raise KeyboardInterrupt("acceptance interrupted by SIGTERM")

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        return AcceptanceRun(args.rootfs, args.evidence_dir).run()
    except OSError as exc:
        print(f"cannot create acceptance run: {exc}", file=sys.stderr)
        return 2
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    raise SystemExit(main())
