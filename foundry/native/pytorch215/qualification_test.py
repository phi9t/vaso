from __future__ import annotations
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
import signal
import time
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import qualification as q


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.work = self.root / 'build'
        (self.work / 'wheelhouse').mkdir(parents=True)
        self.identity = {'schema':'example.inputs','sha':'a'*64}
        self.version = '2.15.0+vaso.cu126.sm61'
        self.build = types.SimpleNamespace(
            work=self.work, profile={'torch':{'version':self.version}},
            inputs=lambda: copy.deepcopy(self.identity), status_path=self.work/'status.json')
        self.wheel = self.work/'wheelhouse/torch-2.15.0-cp312-cp312-linux_x86_64.whl'
        self.write_wheel()
        self.state = {'state':'built','wheel_built':True,'exit_code':0,
                      'wheel':str(self.wheel),'wheel_sha256':q.digest(self.wheel),
                      'inputs':copy.deepcopy(self.identity)}

    def tearDown(self):
        self.temp.cleanup()

    def write_wheel(self, version=None, extension=True):
        with zipfile.ZipFile(self.wheel, 'w') as z:
            if extension: z.writestr('torch/_C.cpython-312-x86_64-linux-gnu.so', b'fake test extension')
            z.writestr('torch-2.15.0.dist-info/METADATA', 'Name: torch\nVersion: '+(version or self.version)+'\n')

    def test_valid_wheel_identity(self):
        self.assertEqual(q.checked_wheel(self.build,self.state), self.wheel)

    def test_running_build_never_qualifies(self):
        self.state.update(state='running', wheel_built=False)
        with self.assertRaisesRegex(ValueError,'not complete'): q.checked_wheel(self.build,self.state)

    def test_nonzero_native_exit_rejected(self):
        self.state['exit_code']=1
        with self.assertRaisesRegex(ValueError,'exit successfully'): q.checked_wheel(self.build,self.state)

    def test_modified_input_identity_rejected(self):
        self.state['inputs']['sha']='b'*64
        with self.assertRaisesRegex(ValueError,'identities differ'): q.checked_wheel(self.build,self.state)

    def test_wheel_must_be_inside_owned_directory(self):
        outside=self.root/'other.whl';outside.write_bytes(self.wheel.read_bytes())
        self.state['wheel']=str(outside)
        with self.assertRaisesRegex(ValueError,'owned wheelhouse'): q.checked_wheel(self.build,self.state)

    def test_symlink_cannot_escape_wheelhouse(self):
        outside=self.root/'other.whl';outside.write_bytes(self.wheel.read_bytes())
        self.wheel.unlink();self.wheel.symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'owned wheelhouse'): q.checked_wheel(self.build,self.state)

    def test_changed_wheel_bytes_rejected(self):
        with self.wheel.open('ab') as f:f.write(b'modified')
        with self.assertRaisesRegex(ValueError,'SHA256 mismatch'): q.checked_wheel(self.build,self.state)

    def test_wrong_distribution_version_rejected(self):
        self.write_wheel('2.14.1+cu126');self.state['wheel_sha256']=q.digest(self.wheel)
        with self.assertRaisesRegex(ValueError,'version mismatch'): q.checked_wheel(self.build,self.state)

    def test_missing_native_extension_rejected(self):
        self.write_wheel(extension=False);self.state['wheel_sha256']=q.digest(self.wheel)
        with self.assertRaisesRegex(ValueError,'extension absent'): q.checked_wheel(self.build,self.state)

    def test_active_build_lock_is_respected(self):
        with (self.work/'owner.lock').open('a+') as f:
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError,'owned/running'): q.qualify(self.build)
        self.assertFalse((self.work/'qualification').exists())

    def test_command_exit_is_preserved(self):
        code=q.run_owned([sys.executable,'-c','raise SystemExit(19)'],self.root/'log',3)
        self.assertEqual(code,19)

    def test_command_timeout_is_not_success(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            q.run_owned([sys.executable,'-c','import time; time.sleep(20)'],self.root/'log',0.1)

    def test_timeout_kills_descendant_that_ignores_term(self):
        marker = self.root / 'child.json'
        child_code = (
            'import json,os,signal,time,pathlib; '
            'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
            f'pathlib.Path({str(marker)!r}).write_text(json.dumps({{"pid":os.getpid()}})); '
            'time.sleep(30)'
        )
        parent_code = (
            'import subprocess,sys,time; '
            f'subprocess.Popen([sys.executable,"-c",{child_code!r}]); '
            'time.sleep(30)'
        )
        pid = None
        try:
            with self.assertRaises(subprocess.TimeoutExpired):
                q.run_owned([sys.executable, '-c', parent_code], self.root/'group.log', 1.5)
            self.assertTrue(marker.is_file(), 'child did not reach ready state')
            pid = json.loads(marker.read_text())['pid']
            stat = Path(f'/proc/{pid}/stat')
            end = time.monotonic() + 2
            while stat.exists() and time.monotonic() < end:
                if stat.read_text().rsplit(')', 1)[1].strip().split()[0] == 'Z':
                    break
                time.sleep(0.02)
            self.assertTrue(not stat.exists() or stat.read_text().rsplit(')', 1)[1].strip().split()[0] == 'Z',
                            'SIGTERM-ignoring owned descendant survived timeout')
        finally:
            if pid:
                try: os.kill(pid, signal.SIGKILL)
                except ProcessLookupError: pass

    def test_invalid_timeout_cannot_launch(self):
        for timeout in [0, 10, 301]:
            with self.assertRaisesRegex(ValueError,'between 30 and 300'):q.qualify(self.build,timeout)


if __name__=='__main__':unittest.main(verbosity=2)
