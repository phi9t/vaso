#!/usr/bin/env python3
"""Pinned source-wheel execution using Vaso Insula; no network during builds.

Site paths stay in an external JSON file, while compiler flags and source identity
are owned by the checked-in profile. Builds are resumable only for identical
profile/rootfs identities. Each attempt has a log and an atomically written status.
"""
from __future__ import annotations
import argparse, datetime, fcntl, hashlib, json, os, signal, subprocess, sys, tarfile, tempfile, time, zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]

def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(4 << 20), b''):
            h.update(b)
    return h.hexdigest()

def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as f:
        json.dump(value, f, indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno()); tmp = Path(f.name)
    os.replace(tmp, path)

def utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def read_profile(path: Path) -> dict:
    p = json.loads(path.read_text())
    if p.get('schema') != 'vaso.torch-source-profile.v1':
        raise ValueError('Unsupported source profile schema')
    name = p.get('name', '')
    if not name or any(x not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for x in name):
        raise ValueError('Unsafe profile name')
    if len(p['torch']['archive_sha256']) != 64:
        raise ValueError('Source archive must have SHA256 identity')
    arch = p['toolchain']['cuda_arch_list']
    cuda = p['toolchain']['cuda_major_minor']
    if arch == '6.1' and not cuda.startswith('12.'):
        raise ValueError('SM61 requires a CUDA12 compiler, not CUDA13')
    if p['toolchain']['profiler'] != 'kineto':
        raise ValueError('This pinned profile is qualified only for Kineto; Cuspy requires separate admission')
    return p

def apply_source_edits(source: Path, edits: list[dict]) -> None:
    """Apply exactly pinned, fail-closed native source edits to a fresh extraction."""
    for edit in edits:
        rel = Path(edit['path'])
        if rel.is_absolute() or '..' in rel.parts:
            raise ValueError('Unsafe native source edit path')
        target = source / rel
        if not target.is_file() or target.read_bytes() != edit['before_text'].encode('utf-8'):
            raise ValueError('Source edit preimage mismatch: ' + str(rel))
        text = target.read_text()
        if text.count(edit['old']) != 1:
            raise ValueError('Source edit must match exactly once: ' + str(rel))
        result = text.replace(edit['old'], edit['new'], 1).encode()
        if result != edit['after_text'].encode('utf-8'):
            raise ValueError('Source edit postimage mismatch: ' + str(rel))
        target.write_bytes(result)

class SourceBuild:
    def __init__(self, site_path: Path, profile_path: Path):
        self.site_path = site_path.resolve()
        self.site = json.loads(self.site_path.read_text())
        self.profile_path = profile_path.resolve()
        self.profile = read_profile(self.profile_path)
        self.estate = Path(self.site['estate_root']).resolve()
        self.rootfs = Path(self.site['rootfs']).resolve()
        self.archive = Path(self.site['source_archive']).resolve()
        self.work = self.estate / 'vaso' / 'build' / self.profile['name']
        self.guest = '/vaso/build/' + self.profile['name']
        self.source = self.work / 'source'
        self.status_path = self.work / 'status.json'

    def inputs(self) -> dict:
        for p in [self.archive, self.rootfs / 'opt/vaso/build-manifest.json']:
            if not p.is_file(): raise FileNotFoundError(f'Required build input missing: {p}')
        root = self.rootfs / 'opt/vaso'
        for p in [root/'cuda/bin/nvcc', root/'cmake/bin/cmake', root/'python-deps/bin/ninja', self.rootfs/'usr/bin/g++']:
            if not p.is_file(): raise FileNotFoundError(f'Tool missing from selected frozen rootfs: {p}')
        for name in self.profile.get('required_toolchain_files', []):
            rel = Path(name)
            if rel.is_absolute() or '..' in rel.parts: raise ValueError('Unsafe required toolchain path')
            if not (self.rootfs/rel).is_file(): raise FileNotFoundError(f'Required header/library missing from rootfs: {name}')
        # NCCL's top-level nccl_device.h includes nested nccl_device/ headers.
        # Pin the entire provider tree before admission, not just the facade.
        for tree in self.profile.get('required_header_trees', []):
            header_root = Path(tree['root'])
            if header_root.is_absolute() or '..' in header_root.parts: raise ValueError('Unsafe provider header root')
            for name, expected in tree['files'].items():
                rel = Path(name)
                if rel.is_absolute() or '..' in rel.parts: raise ValueError('Unsafe provider header path')
                header = self.rootfs/header_root/rel
                if not header.is_file(): raise FileNotFoundError(f"Required {tree['provider']} header missing: {header_root/rel}")
                if not header.resolve().is_relative_to(self.rootfs): raise ValueError('Provider header escapes frozen rootfs')
                if digest(header) != expected: raise ValueError(f"Pinned {tree['provider']} header digest mismatch: {header_root/rel}")
        return {'schema':'vaso.source-build-inputs.v2', 'profile_text':self.profile_path.read_bytes().decode('utf-8'),
                'source_archive_sha256':self.profile['torch']['archive_sha256'],
                'rootfs_manifest_text':(root/'build-manifest.json').read_bytes().decode('utf-8'),
                'source_driver_text':Path(__file__).read_bytes().decode('utf-8'),
                'insula_entry_text':(HERE/'enter_insula.sh').read_bytes().decode('utf-8')}

    def prepare(self) -> dict:
        identity = self.inputs()
        owner = self.work / 'inputs.json'
        self.work.mkdir(parents=True, exist_ok=True)
        if owner.exists():
            if json.loads(owner.read_text()) != identity:
                raise RuntimeError('Profile/source/rootfs identity changed; use a new profile name, never overwrite a build')
            if not self.source.is_dir(): raise RuntimeError('Preparation incomplete: source directory missing')
        else:
            if self.source.exists(): raise RuntimeError('Unowned source directory exists; refusing overwrite')
            if digest(self.archive) != identity['source_archive_sha256']:
                raise ValueError('Source archive SHA256 mismatch')
            with tempfile.TemporaryDirectory(prefix='extract-', dir=self.work) as tmp:
                proc = subprocess.Popen(['zstd','-dc',str(self.archive)], stdout=subprocess.PIPE)
                try:
                    with tarfile.open(fileobj=proc.stdout, mode='r|') as tar:
                        tar.extractall(tmp, filter='data')
                finally:
                    proc.stdout.close()
                if proc.wait(): raise RuntimeError('zstd source extraction failed')
                src = Path(tmp) / self.profile['torch']['archive_prefix']
                for name in self.profile['critical_source_files']:
                    if not (src/name).is_file(): raise ValueError(f'Missing materialized source input: {name}')
                apply_source_edits(src, self.profile.get('source_edits', []))
                src.rename(self.source)
            write_json(owner, identity)
        for name in self.profile['critical_source_files']:
            if not (self.source/name).is_file(): raise ValueError(f'Build source lost critical file: {name}')
        (self.work/'wheelhouse').mkdir(exist_ok=True)
        (self.work/'logs').mkdir(exist_ok=True)
        return identity

    def launch(self, jobs: int, resume: bool, max_seconds: int) -> int:
        self.work.mkdir(parents=True, exist_ok=True)
        with (self.work/'owner.lock').open('a+') as lock:
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise RuntimeError('Another process owns this source build')
            existed = (self.work/'inputs.json').exists()
            if existed and not resume: raise ValueError('Build already prepared; use --resume for the same immutable inputs')
            identity = self.prepare()
            if not 1 <= jobs <= (os.cpu_count() or 1): raise ValueError('jobs exceeds available CPU count')
            attempt = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S')
            log = self.work/'logs'/f'build-{attempt}.log'
            env = dict(self.profile['environment'])
            env.update(MAX_JOBS=str(jobs), CMAKE_BUILD_PARALLEL_LEVEL=str(jobs),
                       TORCH_CUDA_ARCH_LIST=self.profile['toolchain']['cuda_arch_list'],
                       PYTORCH_BUILD_VERSION=self.profile['torch']['version'],
                       VASO_SOURCE_GIT_SHA=self.profile['torch']['upstream_commit'],
                       TMPDIR=self.guest+'/tmp')
            (self.work/'tmp').mkdir(exist_ok=True)
            cmd = ['/usr/local/bin/python3','-m','pip','wheel','-v','--no-deps','--no-build-isolation','--no-index','-w',self.guest+'/wheelhouse',self.guest+'/source']
            request = {'rootfs':str(self.rootfs), 'repo':str(REPO), 'estate':str(self.estate),
                       'workdir':self.guest+'/source','environment':env,'argv':cmd}
            request_path=self.work/'request.json';write_json(request_path,request)
            state={'state':'running','started_at':utc(),'attempt':attempt,'supervisor_pid':os.getpid(),'log':str(log),'inputs':identity,'wheel_built':False}
            write_json(self.status_path,state)
            process=None
            try:
                with log.open('w') as out:
                    process=subprocess.Popen(['bash',str(HERE/'enter_insula.sh'),str(request_path)], stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
                    state['pid']=process.pid;write_json(self.status_path,state)
                    try: rc=process.wait(timeout=max_seconds)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid,signal.SIGTERM)
                        try: process.wait(timeout=10)
                        except subprocess.TimeoutExpired: os.killpg(process.pid,signal.SIGKILL);process.wait()
                        rc=124
            except BaseException:
                if process and process.poll() is None:
                    os.killpg(process.pid,signal.SIGTERM)
                    try: process.wait(timeout=10)
                    except subprocess.TimeoutExpired: os.killpg(process.pid,signal.SIGKILL);process.wait()
                state.update(state='interrupted',finished_at=utc());write_json(self.status_path,state)
                raise
            state.update(state='built' if rc==0 else 'failed',exit_code=rc,finished_at=utc())
            wheels=sorted((self.work/'wheelhouse').glob('torch-*.whl'))
            if rc==0:
                if len(wheels)!=1: state.update(state='failed',exit_code=2,error='Expected exactly one wheel');rc=2
                else:
                    f=wheels[0]
                    with zipfile.ZipFile(f) as z:
                        if 'torch/_C.cpython-312-x86_64-linux-gnu.so' not in z.namelist():
                            state.update(state='failed',exit_code=2,error='Missing native CPython extension');rc=2
                    state.update(wheel_built=rc==0,wheel=str(f),wheel_sha256=digest(f),wheel_bytes=f.stat().st_size,runtime_qualified=False)
            write_json(self.status_path,state);print(json.dumps(state,indent=2));return rc

    def status(self) -> dict:
        if not self.status_path.exists(): return {'state':'not_started','work':str(self.work)}
        s=json.loads(self.status_path.read_text())
        if s['state']=='running':
            pid=s.get('supervisor_pid')
            try: os.kill(pid,0)
            except (ProcessLookupError,TypeError): s['state']='interrupted';s['warning']='Supervisor no longer exists'
        log=Path(s.get('log',''))
        if log.is_file():
            with log.open('rb') as f: f.seek(max(0,log.stat().st_size-6000));s['log_tail']=f.read().decode(errors='replace')
        return s

def main() -> int:
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument('--site',required=True,type=Path)
    a.add_argument('--profile',type=Path,default=HERE/'profiles/cu126-sm61-r4.json')
    a.add_argument('--jobs',type=int,default=3);a.add_argument('--resume',action='store_true')
    a.add_argument('--max-seconds',type=int,default=10800)
    a.add_argument('action',choices=['prepare','build','status'])
    x=a.parse_args();b=SourceBuild(x.site,x.profile)
    if x.action=='build':return b.launch(x.jobs,x.resume,x.max_seconds)
    print(json.dumps(b.prepare() if x.action=='prepare' else b.status(),indent=2));return 0
if __name__=='__main__':
    def terminated(signum, frame):
        raise InterruptedError(f'Source build interrupted by signal {signum}')
    signal.signal(signal.SIGTERM, terminated)
    signal.signal(signal.SIGINT, terminated)
    try: raise SystemExit(main())
    except (OSError,ValueError,RuntimeError) as e: print('SOURCE_BUILD_ERROR: '+str(e),file=sys.stderr);raise SystemExit(2)
