#!/usr/bin/env python3
"""Fork a failed source-build checkpoint after a verified additive header repair.

The native profile, source archive, compiler bytes, libraries, and guest paths
must remain identical. This is explicitly recorded recovery, never an implicit
cache hit. Existing source/build files are copied, not moved or hardlinked.
Ninja then resumes with its retained dependency database. Runtime qualification
is still mandatory. Changed or removed existing rootfs inputs are rejected.
"""
from __future__ import annotations
import argparse, fcntl, hashlib, importlib.util, json, os, shutil, subprocess
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('source_build',HERE/'source_build.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def additive_headers(old:dict,new:dict)->list[str]:
    before=old['file_hashes'];after=new['file_hashes']
    changed=[p for p,v in before.items() if after.get(p)!=v]
    if changed:raise ValueError('Existing rootfs inputs changed: '+repr(changed[:8]))
    added=sorted(set(after)-set(before))
    allowed=tuple(prefix+sub+'/' for prefix in ('opt/vaso/cuda/include/','opt/vaso/cuda/targets/x86_64-linux/include/')
                  for sub in ('nccl_device','bootstrap_device_host','device','device_host','device_host_transport','host','non_abi'))
    for p in added:
        if not p.startswith(allowed) or Path(p).suffix not in ('.h','.hpp','.cuh','.inl'):
            raise ValueError('Recovery permits only additive NCCL/NVSHMEM headers: '+p)
    if not added:raise ValueError('No additive repair detected')
    return added


def recover(old_site:Path,new_site:Path,profile:Path)->dict:
    src=m.SourceBuild(old_site,profile);dst=m.SourceBuild(new_site,profile)
    if src.guest!=dst.guest or src.estate==dst.estate:raise ValueError('Use a distinct estate with identical guest path')
    if (dst.work/'inputs.json').exists():raise ValueError('Destination already prepared; use the normal resume command')
    if not src.status_path.is_file():raise ValueError('Missing previous durable build status')
    state=json.loads(src.status_path.read_text())
    if state['state'] not in ('failed','interrupted'):raise ValueError('Only a stopped failed build can be recovered')
    old_inputs=json.loads((src.work/'inputs.json').read_text());new_inputs=dst.inputs()
    for key in ['profile_sha256','source_archive_sha256','source_driver_sha256','insula_entry_sha256']:
        if old_inputs.get(key)!=new_inputs.get(key):raise ValueError('Build identity differs: '+key)
    oldmf=src.rootfs/'opt/vaso/build-manifest.json';newmf=dst.rootfs/'opt/vaso/build-manifest.json'
    if m.digest(oldmf)!=old_inputs['rootfs_manifest_sha256']:raise ValueError('Old rootfs manifest changed')
    old=json.loads(oldmf.read_text());new=json.loads(newmf.read_text());added=additive_headers(old,new)
    if new.get('parent_rootfs_manifest_sha256')!=m.digest(oldmf):raise ValueError('Missing direct parent-rootfs proof')
    # Verify actual additions; prior contents were exhaustively hashed by rootfs sealing.
    for p in added:
        if m.digest(dst.rootfs/p)!=new['file_hashes'][p]['sha256']:raise ValueError('Added header digest mismatch: '+p)
    for f in src.profile['critical_source_files']:
        if not (src.source/f).is_file():raise ValueError('Previous source lost '+f)
    dst.work.parent.mkdir(parents=True,exist_ok=True)
    pending={'previous_work':str(src.work),'inputs':new_inputs}
    marker=dst.work/'recovery-pending.json'
    if dst.work.exists():
        if not marker.exists() or json.loads(marker.read_text())!=pending:raise ValueError('Unowned destination; refusing overwrite')
    else:
        dst.work.mkdir();m.write_json(marker,pending)
    with (src.work/'owner.lock').open('a+') as owner:
        fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        # Recoverable copy preserves mtimes and never mutates the stopped donor.
        dst.source.mkdir(exist_ok=True)
        subprocess.run(['rsync','-a','--no-owner','--no-group',str(src.source)+'/',str(dst.source)+'/'],check=True)
    if not (dst.source/'build/build.ninja').is_file():raise RuntimeError('Missing native Ninja graph')
    proof={'schema':'vaso.header-recovery.v1','previous_work':str(src.work),'new_work':str(dst.work),
           'guest_work':dst.guest,'previous_inputs':old_inputs,'new_inputs':new_inputs,
           'rootfs_existing_files_unchanged':len(old['file_hashes']),'new_headers':added,
           'copied_object_count':sum(1 for _ in (dst.source/'build').rglob('*.o')),
           'native_profile_unchanged':True,'source_and_compiler_unchanged':True,
           'previous_log':state['log'],'runtime_qualified':False}
    m.write_json(dst.work/'recovery.json',proof);m.write_json(dst.work/'inputs.json',new_inputs)
    (dst.work/'wheelhouse').mkdir();(dst.work/'logs').mkdir()
    return proof

if __name__=='__main__':
    a=argparse.ArgumentParser(description=__doc__);a.add_argument('--old-site',required=True,type=Path);a.add_argument('--new-site',required=True,type=Path);a.add_argument('--profile',type=Path,default=HERE/'profiles/cu126-sm61-r3.json')
    x=a.parse_args();r=recover(x.old_site,x.new_site,x.profile)
    print(json.dumps({k:v for k,v in r.items() if k!='new_headers'},indent=2));print('ADDED_HEADER_COUNT',len(r['new_headers']))
