#!/usr/bin/env python3
"""Qualify a completed source-built wheel in the exact build Insula.

This is a runtime acceptance action, not a native build input. It uses the
native build's one-writer lock, verifies its wheel and immutable input IDs,
installs to a unique attempt, and runs the GPU tests with normal Insula
admission. Failed/interrupted qualifications remain failed and retain logs.
"""
from __future__ import annotations

import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import zipfile

from source_build import SourceBuild, digest, utc, write_json

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


def checked_wheel(build: SourceBuild, state: dict) -> Path:
    """Never accept an old install or merely configured/native-in-progress build."""
    if state.get('state') != 'built' or state.get('wheel_built') is not True:
        raise ValueError('QUALIFICATION_BLOCKED: native wheel is not complete')
    if state.get('exit_code') != 0:
        raise ValueError('QUALIFICATION_BLOCKED: build did not exit successfully')
    expected = build.inputs()
    if state.get('inputs') != expected:
        raise ValueError('QUALIFICATION_BLOCKED: build input identities differ')
    wheel = Path(state.get('wheel', '')).resolve()
    if not wheel.is_relative_to((build.work / 'wheelhouse').resolve()) or not wheel.is_file():
        raise ValueError('QUALIFICATION_BLOCKED: wheel is outside the owned wheelhouse')
    if digest(wheel) != state.get('wheel_sha256'):
        raise ValueError('QUALIFICATION_BLOCKED: wheel SHA256 mismatch')
    with zipfile.ZipFile(wheel) as z:
        names = z.namelist()
        if 'torch/_C.cpython-312-x86_64-linux-gnu.so' not in names:
            raise ValueError('QUALIFICATION_BLOCKED: native CPython extension absent')
        meta = [n for n in names if n.endswith('.dist-info/METADATA')]
        if len(meta) != 1:
            raise ValueError('QUALIFICATION_BLOCKED: invalid distribution metadata')
        text = z.read(meta[0]).decode('utf-8')
        versions = [x[9:] for x in text.splitlines() if x.startswith('Version: ')]
        if versions != [build.profile['torch']['version']]:
            raise ValueError('QUALIFICATION_BLOCKED: distribution version mismatch')
    return wheel


def run_owned(argv: list[str], log: Path, timeout: int) -> int:
    """Bound and clean the entire command process group, including torch ranks."""
    with log.open('w') as out:
        child = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        try:
            return child.wait(timeout=timeout)
        except BaseException:
            # A shell/parent may exit on SIGTERM while a descendant ignores it.
            # Terminate the owned group, not merely the leader process.
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            finally:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
            raise


def qualify(build: SourceBuild, timeout: int = 180) -> dict:
    if timeout < 30 or timeout > 300:
        raise ValueError('Qualification timeout must be between 30 and 300 seconds')
    if not build.work.is_dir():
        raise ValueError('QUALIFICATION_BLOCKED: native build has not started')
    with (build.work / 'owner.lock').open('a+') as writer:
        try:
            fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('QUALIFICATION_BLOCKED: source build is still owned/running') from exc
        state = json.loads(build.status_path.read_text())
        wheel = checked_wheel(build, state)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%f')
        attempt = build.work / 'qualification' / stamp
        attempt.mkdir(parents=True, exist_ok=False)
        guest = build.guest + '/qualification/' + stamp
        result = {
            'schema': 'vaso.source-wheel-qualification.v1',
            'started_at': utc(), 'state': 'running', 'passed': False,
            'wheel': str(wheel), 'wheel_sha256': state['wheel_sha256'],
            'expected_version': build.profile['torch']['version'],
            'profile_sha256': digest(build.profile_path),
            'runtime_test_sha256': digest(HERE / 'qualification_runtime.py'),
            'parallel_tests_sha256': digest(HERE / 'parallel_components.py'),
            'attempt': str(attempt),
        }
        result_path = attempt / 'result.json'
        write_json(result_path, result)
        original = json.loads((build.work / 'request.json').read_text())
        # Runtime acceptance can run from this separately versioned checkout;
        # the native source driver's bytes/profile/rootfs must remain identical.
        request = dict(original)
        request.update(repo=str(REPO), workdir=guest,
                       gpu_lease_estate=str(Path(build.site.get('gpu_lease_estate', str(build.estate))).resolve()))
        request['environment'] = dict(original['environment'])
        request['argv'] = [
            '/usr/local/bin/python3', '-m', 'pip', 'install', '--no-index',
            '--no-deps', '--target', guest + '/installed',
            build.guest + '/wheelhouse/' + wheel.name,
        ]
        try:
            install_request = attempt / 'install-request.json'
            write_json(install_request, request)
            rc = run_owned(['bash', str(HERE / 'enter_insula.sh'), str(install_request)],
                           attempt / 'install.log', timeout)
            if rc:
                raise RuntimeError(f'Wheel installation failed with exit {rc}; see {attempt / "install.log"}')
            request['environment'].update(
                PYTHONPATH=guest + '/installed:/opt/vaso/python-deps',
                LD_LIBRARY_PATH='/run/nvidia-driver/lib:/opt/vaso/cuda/lib64:/usr/local/lib',
                OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', GLOO_SOCKET_IFNAME='lo',
                PYTHONNOUSERSITE='1',
            )
            request['argv'] = [
                '/usr/local/bin/python3',
                '/workspace/vaso/foundry/native/pytorch215/qualification_runtime.py',
                '--out', guest + '/runtime', '--expected', build.profile['torch']['version'],
                '--torch-root', guest + '/installed/torch',
            ]
            gpu_request = attempt / 'gpu-request.json'
            write_json(gpu_request, request)
            rc = run_owned(['bash', str(HERE / 'gpu_qualification.sh'), str(gpu_request)],
                           attempt / 'gpu.log', timeout)
            if rc:
                raise RuntimeError(f'GPU qualification failed with exit {rc}; see {attempt / "gpu.log"}')
            summary = json.loads((attempt / 'runtime/summary.json').read_text())
            if (summary.get('passed') is not True or summary.get('new_wheel_qualified') is not True
                    or summary.get('torch_version') != build.profile['torch']['version']):
                raise ValueError('Runtime report did not establish qualification of this wheel')
            result.update(state='passed', passed=True, finished_at=utc(), runtime=summary)
            write_json(result_path, result)
            # A separate receipt never rewrites native build identity or hides
            # failed attempts. Source status distinguishes build vs qualification.
            write_json(build.work / 'qualification.json', result)
            return result
        except BaseException as exc:
            result.update(state='failed', passed=False, finished_at=utc(),
                          error=f'{type(exc).__name__}: {exc}')
            write_json(result_path, result)
            raise


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--site', required=True, type=Path)
    p.add_argument('--profile', type=Path, default=HERE / 'profiles/cu126-sm61-r4.json')
    p.add_argument('--timeout', type=int, default=180)
    args = p.parse_args()
    build = SourceBuild(args.site, args.profile)
    result = qualify(build, timeout=args.timeout)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    def terminated(signum: int, frame: object) -> None:
        raise InterruptedError(f'Qualification interrupted by signal {signum}')
    signal.signal(signal.SIGINT, terminated)
    signal.signal(signal.SIGTERM, terminated)
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print('QUALIFICATION_ERROR: ' + str(exc), file=sys.stderr)
        raise SystemExit(2)
