"""CPU-only CLI regressions; the live Insula gate separately proves descendant closure."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


RUNNER = Path(__file__).with_name("run_payload.py")


class RunPayloadTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="run-payload-test-")
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.spec = self.root / "spec.toml"
        self.spec.write_text("test fixture\n")
        self.env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"]}
        self.insula = self.program("insula", r"""
import json,os,sys
argv=sys.argv[sys.argv.index('--')+1:]
devices=['/dev/nvidia1','/dev/nvidiactl'] if os.environ.get('TEST_GPU') else []
print(json.dumps({'plan_sha256':'a'*64,'main':{'argv':argv,'devices':devices,'extra_mounts':[]}}))
""")
        self.program("nvidia-smi", "pass\n")
        self.lease = self.program("lease", r"""
import json,os,sys
if sys.argv[1]=='acquire':
    assert sys.argv[sys.argv.index('--devices')+1]=='1'
    assert sys.argv[sys.argv.index('--amount')+1]=='1'
    print(json.dumps({'id':'fixture-lease','devices':[1]}))
elif os.environ['TEST_RELEASE']=='error':
    raise SystemExit(9)
else:
    print(json.dumps({'released':os.environ['TEST_RELEASE']=='success'}))
""")
        self.supervisor = self.program("supervisor", r"""
import json,os,signal,sys,time
from pathlib import Path
out=Path(sys.argv[sys.argv.index('--output')+1]);out.mkdir()
seen=[]
if os.environ.get('TEST_CANCEL'):
    (out/'startup').write_text('starting\n')
    time.sleep(0.4)
    for number in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):
        signal.signal(number,lambda number,frame:seen.append(number))
    (out/'ready').write_text('handlers ready\n')
    deadline=time.monotonic()+10
    while not seen and time.monotonic()<deadline:
        time.sleep(0.01)
    # A successful caller must wait past subprocess.call's 0.25s SIGINT grace.
    time.sleep(0.4)
try:
    os.waitpid(-1,os.WNOHANG)
    closed=False
except ChildProcessError:
    closed=True
rc=128+seen[0] if seen else 0
(out/'receipt.json').write_text(json.dumps({'cleanup':{'complete':closed,'echild_observed':closed},'exit_code':rc}))
raise SystemExit(rc)
""")

    def tearDown(self):
        self.temp.cleanup()

    def program(self, name, text):
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n" + text)
        path.chmod(0o755)
        return path

    def command(self, output, gpu=False):
        command = [sys.executable, str(RUNNER), "--insula", str(self.insula),
                   "--supervisor", str(self.supervisor), "--spec", str(self.spec),
                   "--output", str(output), "--timeout", "15"]
        if gpu:
            command += ["--gpu", "--lease-cli", str(self.lease), "--lease-estate", str(self.root)]
        return command + ["--", "/fixture/payload"]

    def test_caller_reviewed_plan_pin(self):
        for name, expected, success in (("matches", "a"*64, True), ("changed", "b"*64, False)):
            with self.subTest(name=name):
                output = self.root / name
                argv = self.command(output)
                boundary = argv.index("--")
                argv[boundary:boundary] = ["--expected-plan-sha256", expected]
                result = subprocess.run(argv, env=self.env, text=True, capture_output=True, timeout=15)
                status = json.loads((output / "status.json").read_text())
                if success:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(status["cleanup_complete"])
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("differs from caller-reviewed plan", status["error"])
                    self.assertFalse((output / "owner").exists())

    def test_admission_probe_timeout_retries_before_leasing(self):
        marker = self.root / "probe-started"
        self.program("nvidia-smi", """
import os,time
from pathlib import Path
marker=Path(os.environ['TEST_PROBE_MARKER'])
if not marker.exists():
    marker.write_text('first attempt')
    time.sleep(30)
""")
        output = self.root / "result"
        env = {**self.env, "TEST_GPU": "1", "TEST_RELEASE": "success",
               "TEST_PROBE_MARKER": str(marker)}
        result = subprocess.run(self.command(output, gpu=True), env=env, text=True,
                                capture_output=True, timeout=35)
        status = json.loads((output / "status.json").read_text())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(status["state"], "passed")
        self.assertEqual(status["admission_probe_timeouts"], 1)
        self.assertTrue(status["cleanup_complete"])
        self.assertTrue(status["lease_released"])

    def test_unconfirmed_release_fails_cli_and_records_closure(self):
        self.check_release_failure("false")

    def test_release_exception_preserves_final_status(self):
        self.check_release_failure("error")

    def check_release_failure(self, mode):
        output = self.root / "result"
        env = {**self.env, "TEST_GPU": "1", "TEST_RELEASE": mode}
        result = subprocess.run(self.command(output, gpu=True), env=env, text=True,
                                capture_output=True, timeout=15)
        status = json.loads((output / "status.json").read_text())
        self.assertEqual(result.returncode, 125, result.stderr)
        self.assertEqual(status["exit_code"], 125)
        self.assertEqual(status["payload_exit_code"], 0)
        self.assertEqual(status["state"], "failed")
        self.assertTrue(status["cleanup_complete"])
        self.assertFalse(status["lease_released"])
        self.assertIn("cleanup_error", status)

    def test_sigterm_waits_for_owner_receipt(self):
        self.check_cancellation(signal.SIGTERM)

    def test_sigint_waits_for_owner_receipt(self):
        self.check_cancellation(signal.SIGINT)

    def test_startup_signal_waits_for_owner_handlers(self):
        self.check_cancellation(signal.SIGTERM, startup=True)

    def check_cancellation(self, number, startup=False):
        output = self.root / "result"
        env = {**self.env, "TEST_CANCEL": "1"}
        process = subprocess.Popen(self.command(output), env=env, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        try:
            marker = output / "owner" / ("startup" if startup else "ready")
            deadline = time.monotonic() + 5
            while not marker.exists():
                if process.poll() is not None or time.monotonic() >= deadline:
                    self.fail("owner did not become ready")
                time.sleep(0.01)
            process.send_signal(number)
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 128 + number, (stdout, stderr))
            status = json.loads((output / "status.json").read_text())
            receipt = json.loads((output / "owner/receipt.json").read_text())
            self.assertEqual(status["forwarded_signal"], number)
            self.assertEqual(status["cancelled_signal"], number)
            self.assertTrue(status["cleanup_complete"])
            self.assertTrue(receipt["cleanup"]["echild_observed"])
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                process.communicate(timeout=15)


if __name__ == "__main__":
    unittest.main()
