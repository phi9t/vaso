#!/usr/bin/env python3
"""Materialize a complete pinned developer-header tree, including nested files.

This helper performs input preparation, not a native build. The caller owns the
new/unsealed rootfs. No file is replaced on a digest collision. Existing sealed
rootfs manifests are rejected; historical rootfs and running builds stay fixed.
"""
from __future__ import annotations
import argparse, hashlib, json, os, shutil, tempfile
from pathlib import Path


def digest(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for data in iter(lambda:f.read(1<<20),b''):h.update(data)
    return h.hexdigest()


def source_manifest(source: Path) -> dict[str,str]:
    source=source.resolve()
    if not source.is_dir():raise ValueError('Missing provider include directory')
    files={}
    for entry in sorted(source.rglob('*')):
        if entry.is_symlink() and not entry.resolve().is_relative_to(source):raise ValueError('Header symlink escapes provider')
        if not entry.is_file() or entry.suffix not in ('.h','.hpp','.cuh','.inl'):continue
        files[str(entry.relative_to(source))]=digest(entry)
    if not files:raise ValueError('Empty developer header tree')
    return files


def materialize(source: Path, rootfs: Path, destination: str, expected: dict[str,str]) -> dict:
    source=source.resolve();rootfs=rootfs.resolve();relative=Path(destination)
    if relative.is_absolute() or '..' in relative.parts:raise ValueError('Unsafe developer-header destination')
    if (rootfs/'opt/vaso/build-manifest.json').exists():raise ValueError('Refusing to modify a sealed rootfs')
    actual=source_manifest(source)
    if actual!=expected:raise ValueError('Provider include closure does not match pinned manifest')
    target=rootfs/relative
    if target.exists() and not target.resolve().is_relative_to(rootfs):raise ValueError('Header destination escapes rootfs')
    # Preflight *all* collisions before creating any new output files.
    pending=[]
    for name,sha in actual.items():
        path=target/name
        if not path.resolve().is_relative_to(rootfs):raise ValueError('Header output escapes rootfs')
        if path.exists():
            if not path.is_file() or digest(path)!=sha:raise ValueError('Developer-header collision: '+name)
        else:pending.append(name)
    for name in pending:
        path=target/name;path.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as f:
            temp=Path(f.name)
        try:
            shutil.copyfile(source/name,temp)
            if digest(temp)!=actual[name]:raise ValueError('Header source changed while preparing: '+name)
            temp.chmod(0o644);os.replace(temp,path)
        finally:temp.unlink(missing_ok=True)
    for name,sha in actual.items():
        if digest(target/name)!=sha:raise ValueError('Materialized header mismatch: '+name)
    return {'schema':'vaso.provider-headers.v1','destination':destination,'file_count':len(actual),
            'new_files':len(pending),'files':actual}


if __name__=='__main__':
    a=argparse.ArgumentParser(description=__doc__);a.add_argument('--source',type=Path,required=True)
    a.add_argument('--rootfs',type=Path,required=True);a.add_argument('--destination',default='opt/vaso/cuda/include')
    a.add_argument('--manifest',type=Path,required=True);a.add_argument('--receipt',type=Path,required=True)
    x=a.parse_args();expected=json.loads(x.manifest.read_text())
    result=materialize(x.source,x.rootfs,x.destination,expected)
    x.receipt.write_text(json.dumps(result,indent=2)+'\n');print('DEVELOPER_HEADERS_MATERIALIZED',result['file_count'])
