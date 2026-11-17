#!/usr/bin/env python3
"""OCI transport for Insula capsules, not an executable container.

Only native OCI blob/diff IDs are computed. No per-member integrity inventory.
Inputs must be deliberately staged public-release content, not a live estate.
"""
from __future__ import annotations
import argparse, gzip, hashlib, io, json, os, re, shutil, subprocess, tarfile, tempfile
from pathlib import Path, PurePosixPath

MANIFEST = "application/vnd.oci.image.manifest.v1+json"
CONFIG = "application/vnd.oci.image.config.v1+json"
LAYER = "application/vnd.oci.image.layer.v1.tar+gzip"

def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

def relative(value):
    if not isinstance(value, str) or not value or any(ord(ch) < 32 for ch in value) or value.startswith("/"):
        raise ValueError("capsule paths must be relative")
    p = PurePosixPath(value)
    if ".." in p.parts or str(p) != value or value == ".":
        raise ValueError("noncanonical capsule path")
    return p

def guest_path(value):
    if not isinstance(value, str) or not value.startswith("/") or any(ord(ch) < 32 for ch in value) or "//" in value:
        raise ValueError("guest projection must be canonical and absolute")
    if str(PurePosixPath(value)) != value or ".." in PurePosixPath(value).parts:
        raise ValueError("unsafe guest projection")
    # System and credential mounts belong to Insula, never the downloaded descriptor.
    if value != "/sources" and not any(value.startswith(p) for p in ("/vaso/", "/sources/", "/opt/vaso/", "/workspace/")):
        raise ValueError("guest projection outside supported capsule namespaces")
    if value.startswith("/workspace/vaso/") or value == "/workspace/vaso":
        raise ValueError("capsule cannot replace executing Vaso checkout")
    return value

def validate(doc, sources=False):
    if doc.get("schema_version") != 1 or not isinstance(doc.get("layers"), list):
        raise ValueError("expected capsule schema_version 1 and layers")
    if len(doc["layers"]) > 256:
        raise ValueError("too many capsule layers")
    names, targets, guests = set(), [], []
    for item in doc["layers"]:
        if not isinstance(item, dict) or set(item) - ({"name", "path", "guest"} | ({"source"} if sources else set())):
            raise ValueError("unknown capsule layer fields")
        name = item.get("name", "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", name) or name in names:
            raise ValueError("invalid or duplicate layer name")
        names.add(name)
        path = str(relative(item.get("path")))
        if path != "rootfs" and not path.startswith("bundles/"):
            raise ValueError("layer must be rootfs or bundles/name")
        if any(path == p or path.startswith(p + "/") or p.startswith(path + "/") for p in targets):
            raise ValueError("overlapping capsule layer destinations")
        targets.append(path)
        if path == "rootfs":
            if "guest" in item:
                raise ValueError("base rootfs cannot have guest projection")
        else:
            g = guest_path(item.get("guest"))
            if any(g == p or g.startswith(p + "/") or p.startswith(g + "/") for p in guests):
                raise ValueError("overlapping guest projections")
            guests.append(g)
        if sources:
            source = Path(item.get("source", ""))
            if not source.is_absolute() or not source.is_dir() or source.is_symlink():
                raise ValueError("each source must be an explicit real directory")
    if targets.count("rootfs") != 1:
        raise ValueError("exactly one rootfs required")
    allowed = {"schema_version", "layers"} | ({"rootfs_manifest"} if sources else set())
    if set(doc) - allowed:
        raise ValueError("unknown descriptor fields")
    if sources and "rootfs_manifest" in doc:
        manifest = Path(doc["rootfs_manifest"])
        if not manifest.is_absolute() or not manifest.is_file() or manifest.is_symlink():
            raise ValueError("rootfs_manifest must be an explicit existing file")
    return doc

class DigestWriter:
    def __init__(self, sink):
        self.sink, self.digest, self.count = sink, hashlib.sha256(), 0
    def write(self, data):
        self.digest.update(data); self.count += len(data)
        self.sink.write(data)
        return len(data)
    def flush(self):
        self.sink.flush()

def blob(layout, data, media):
    digest = hashlib.sha256(data).hexdigest()  # native OCI descriptor digest
    (layout / "blobs/sha256" / digest).write_bytes(data)
    return {"mediaType": media, "digest": "sha256:" + digest, "size": len(data)}

def privacy_filter(member):
    parts = PurePosixPath(member.name).parts
    if any(p in {".git", ".ssh", ".aws", ".gnupg", ".scratch", "__pycache__", ".pytest_cache"} for p in parts):
        return None
    root = "capsule/rootfs/"
    if member.name.startswith(root):
        rel = member.name[len(root):]
        if any(rel.startswith(p + "/") for p in ("home", "root", "run", "proc", "sys", "dev", "tmp", "var/log")):
            return None
        if rel in {"etc/shadow", "etc/gshadow", "etc/hostname", "etc/machine-id"}:
            return None
    if member.isdev() or member.isfifo():
        return None
    member.mode &= ~0o6000
    member.uid = member.gid = 0
    member.uname = member.gname = ""
    member.mtime = 0
    member.pax_headers = {}
    return member

def layer(layout, item=None, descriptor=None, rootfs_manifest=None):
    # One streaming pass over members; native compressed and uncompressed layer hashes.
    fd, tmp = tempfile.mkstemp(dir=layout)
    try:
        with os.fdopen(fd, "wb") as output:
            compressed = DigestWriter(output)
            with gzip.GzipFile(filename="", mode="wb", fileobj=compressed, compresslevel=1, mtime=0) as gz:
                raw = DigestWriter(gz)
                with tarfile.open(fileobj=raw, mode="w|", format=tarfile.PAX_FORMAT) as tar:
                    if item is not None:
                        tar.add(item["source"], arcname="capsule/" + item["path"], filter=privacy_filter)
                    elif descriptor is not None:
                        data = encoded(descriptor)
                        member = tarfile.TarInfo("capsule/descriptor.json")
                        member.size, member.mode = len(data), 0o644
                        tar.addfile(member, io.BytesIO(data))
                    if rootfs_manifest is not None:
                        tar.add(str(rootfs_manifest), arcname="capsule/rootfs-bundle.json", filter=privacy_filter)
            digest = compressed.digest.hexdigest()
        os.replace(tmp, layout / "blobs/sha256" / digest)
        return {"mediaType": LAYER, "digest": "sha256:" + digest, "size": compressed.count}, "sha256:" + raw.digest.hexdigest()
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

class TarLayer:
    """One native OCI tar stream. No installed-member hashes or inventories."""
    def __init__(self, layout):
        self.layout = layout
        fd, self.tmp = tempfile.mkstemp(dir=layout)
        self.output = os.fdopen(fd, "wb")
        self.compressed = DigestWriter(self.output)
        self.gz = gzip.GzipFile(filename="", mode="wb", fileobj=self.compressed, compresslevel=1, mtime=0)
        self.raw = DigestWriter(self.gz)
        self.tar = tarfile.open(fileobj=self.raw, mode="w|", format=tarfile.PAX_FORMAT)
        self.members = 0

    def finish(self):
        self.tar.close()
        self.gz.close()
        self.output.close()
        if self.compressed.count >= 10_000_000_000:
            os.unlink(self.tmp)
            raise ValueError("compressed layer exceeds GHCR limit; reduce the layer target or select a different artifact representation")
        digest = self.compressed.digest.hexdigest()
        os.replace(self.tmp, self.layout / "blobs/sha256" / digest)
        return ({"mediaType":LAYER,"digest":"sha256:"+digest,"size":self.compressed.count},
                "sha256:"+self.raw.digest.hexdigest())

    def abort(self):
        self.tar.close(); self.gz.close(); self.output.close()
        if os.path.exists(self.tmp): os.unlink(self.tmp)

def component_layers(layout, item, max_layer_bytes):
    # Bound uncompressed tar bytes between members, safely below GHCR's 10GB
    # compressed-layer limit. Never slice a compressed stream into fake layers.
    current = TarLayer(layout)
    completed = []
    def visit(source, arcname):
        nonlocal current
        info = current.tar.gettarinfo(str(source), arcname=arcname)
        info = privacy_filter(info)
        if info is None:
            return
        if current.members and current.tar.offset + info.size + 1024 > max_layer_bytes:
            completed.append(current.finish())
            current = TarLayer(layout)
            info = privacy_filter(current.tar.gettarinfo(str(source), arcname=arcname))
        if info.isfile():
            with source.open("rb") as data:
                current.tar.addfile(info, data)
        else:
            current.tar.addfile(info)
        current.members += 1
        if info.isdir():
            for child in sorted(source.iterdir(), key=lambda x:x.name):
                visit(child, arcname+"/"+child.name)
    try:
        visit(Path(item["source"]), "capsule/"+item["path"])
        completed.append(current.finish())
        return completed
    except Exception:
        current.abort()
        raise

def pack(spec, dest, tag="capsule", max_layer_bytes=2_000_000_000):
    validate(spec, sources=True)
    if max_layer_bytes < 1024 or max_layer_bytes > 8_000_000_000:
        raise ValueError("layer byte target must be between 1024 and 8000000000")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag):
        raise ValueError("invalid OCI reference tag")
    if dest.exists(): raise ValueError("refusing to replace existing OCI layout")
    if any(dest.resolve().is_relative_to(Path(i["source"]).resolve()) for i in spec["layers"]):
        raise ValueError("OCI output cannot be inside a packaged source")
    dest.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".capsule-", dir=dest.parent))
    try:
        (stage / "blobs/sha256").mkdir(parents=True)
        desc = {"schema_version": 1, "layers": [{k:v for k,v in i.items() if k != "source"} for i in spec["layers"]]}
        layers, diffs = [], []
        for item in spec["layers"]:
            for part, (entry, diff) in enumerate(component_layers(stage, item, max_layer_bytes)):
                entry["annotations"] = {"io.vaso.capsule.layer": item["name"], "io.vaso.capsule.part":str(part)}
                layers.append(entry); diffs.append(diff)
        entry, diff = layer(stage, descriptor=desc, rootfs_manifest=spec.get("rootfs_manifest"))
        layers.append(entry); diffs.append(diff)
        config = blob(stage, encoded({"architecture":"amd64","os":"linux","config":{"Labels":{"io.vaso.capsule.schema":"1"}},"rootfs":{"type":"layers","diff_ids":diffs}}), CONFIG)
        manifest = blob(stage, encoded({"schemaVersion":2,"mediaType":MANIFEST,"config":config,"layers":layers}), MANIFEST)
        manifest["annotations"] = {"org.opencontainers.image.ref.name":tag}
        (stage / "index.json").write_bytes(encoded({"schemaVersion":2,"manifests":[manifest]}))
        (stage / "oci-layout").write_bytes(encoded({"imageLayoutVersion":"1.0.0"}))
        os.replace(stage, dest)
        return manifest["digest"]
    finally:
        if stage.exists(): shutil.rmtree(stage)

def add_rootfs_manifest(layout, source, tag="capsule"):
    """Append only base metadata; existing large OCI blobs are never repacked."""
    if not source.is_file() or source.is_symlink():
        raise ValueError("expected a regular rootfs manifest")
    index = json.loads((layout/"index.json").read_text())
    selected = [(i,m) for i,m in enumerate(index["manifests"])
                if m.get("annotations",{}).get("org.opencontainers.image.ref.name")==tag]
    if len(selected) != 1:
        raise ValueError("local image tag is missing or ambiguous")
    number, old = selected[0]
    def read_native(desc):
        digest = desc["digest"]
        if not re.fullmatch(r"sha256:[0-9a-f]{64}",digest):
            raise ValueError("unsupported OCI descriptor")
        return json.loads((layout/"blobs/sha256"/digest.split(":")[1]).read_text())
    manifest = read_native(old)
    config = read_native(manifest["config"])
    entry, diff = layer(layout, rootfs_manifest=source)
    if manifest["layers"] and manifest["layers"][-1]["digest"] == entry["digest"]:
        return old["digest"]
    entry["annotations"] = {"io.vaso.capsule.layer":"base-metadata"}
    manifest["layers"].append(entry)
    config["rootfs"]["diff_ids"].append(diff)
    manifest["config"] = blob(layout, encoded(config), CONFIG)
    updated = blob(layout, encoded(manifest), MANIFEST)
    updated["annotations"] = old.get("annotations",{})
    index["manifests"][number] = updated
    fd, temporary = tempfile.mkstemp(dir=layout, prefix=".index-")
    try:
        with os.fdopen(fd,"wb") as output: output.write(encoded(index))
        os.replace(temporary, layout/"index.json")
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    return updated["digest"]

def projections(bundle):
    outer = (bundle / "rootfs").resolve()
    capsule = (bundle / "rootfs/capsule").resolve()
    if not outer.is_relative_to(bundle.resolve()) or not capsule.is_relative_to(outer):
        raise ValueError("outer capsule path escapes unpacked bundle")
    descriptor = capsule / "descriptor.json"
    if descriptor.is_symlink() or descriptor.stat().st_size > 1024 * 1024:
        raise ValueError("invalid capsule descriptor file")
    doc = validate(json.loads(descriptor.read_text()))
    result = {"rootfs":str(capsule / "rootfs"), "bindings":[]}
    for item in doc["layers"]:
        source = (capsule / item["path"]).resolve()
        if not source.is_relative_to(capsule) or not source.is_dir():
            raise ValueError("capsule layer missing or escapes capsule")
        if "guest" in item:
            result["bindings"].append({"source":str(source),"guest":item["guest"]})
    return result

def unpack(layout, dest, tag="capsule", umoci="umoci"):
    if dest.exists(): raise ValueError("refusing to replace existing unpacked capsule")
    dest.parent.mkdir(parents=True, exist_ok=True)
    parent = Path(tempfile.mkdtemp(prefix=".capsule-unpack-", dir=dest.parent))
    try:
        stage = parent / "bundle"
        stage.mkdir()
        # raw unpack skips umoci's normal full per-member mtree inventory.
        subprocess.run([umoci,"raw","unpack","--rootless","--image",str(layout)+":"+tag,str(stage / "rootfs")],check=True)
        projections(stage)
        os.replace(stage,dest)
    finally:
        shutil.rmtree(parent)

def transfer(direction, layout, remote, tag="capsule", oras_bin=None):
    # Reuse the established GHCR auth/redaction/visibility helpers, but not the
    # legacy rootfs file inventories or split-stream artifact representation.
    import artifact_store as store
    match = re.fullmatch(r"ghcr\.io/phi9t/(vaso-capsule-[a-z0-9_.-]+)(?::([A-Za-z0-9_][A-Za-z0-9_.-]{0,127})|@(sha256:[a-f0-9]{64}))", remote)
    if not match:
        raise ValueError("expected scoped ghcr.io/phi9t/vaso-capsule-* reference")
    if direction == "pull" and not match.group(3):
        raise ValueError("pull requires an immutable manifest digest")
    if direction == "push" and not match.group(2):
        raise ValueError("push requires an explicit destination tag")
    expected = None
    if direction == "push":
        index=json.loads((layout/"index.json").read_text())
        expected=[m["digest"] for m in index["manifests"] if m.get("annotations",{}).get("org.opencontainers.image.ref.name")==tag]
        if len(expected) != 1:
            raise ValueError("local image tag is missing or ambiguous")
    args = argparse.Namespace(registry="ghcr.io/phi9t", oras_bin=oras_bin, dry_run=False)
    token = store.token_from_env_or_file()
    if not token:
        raise ValueError("GHCR token unavailable")
    if direction == "push":
        # Existing packages may already be public: fail BEFORE any blob upload.
        # A genuinely new GHCR package starts private. All other lookup errors
        # fail closed; the mandatory post-upload check remains.
        import urllib.error
        try:
            store.check_ghcr_private(args, match.group(1))
        except store.StoreError as exc:
            if not isinstance(exc.__context__, urllib.error.HTTPError) or exc.__context__.code != 404:
                raise
    with tempfile.TemporaryDirectory(prefix="vaso-capsule-auth-") as auth:
        env = {"DOCKER_CONFIG":auth}
        store.run_oras(args,["login","ghcr.io","-u","phi9t","--password-stdin"],stdin=token,secrets=[token],env_extra=env)
        local = str(layout)+":"+tag
        command = ["cp","--from-oci-layout",local,remote] if direction=="push" else ["cp","--to-oci-layout",remote,local]
        store.run_oras(args,command,secrets=[token],env_extra=env)
        actual = store.resolve_digest(args,remote,env)
        if direction=="push":
            store.check_ghcr_private(args,match.group(1))
            if expected != [actual]:
                raise ValueError("published manifest digest differs from selected local image")
        elif actual != match.group(3):
            raise ValueError("pulled manifest digest differs from requested image")
        return actual

RUN_SCRIPT = r"""
set -euo pipefail
repo=$1 rootfs=$2 workspace=$3 line=$4 print_argv=$5 count=$6
shift 6
source "$repo/insula/execution/insula.sh"
insula_prepare_context "$rootfs" "$workspace" "$line"
mountpoints=(--mountpoint /vaso --mountpoint /home/kvothe --mountpoint /workspace/vaso --mountpoint /run/vaso)
for ((i=0;i<count;i++)); do
  src=$1 guest=$2; shift 2
  mountpoints+=(--mountpoint "$guest")
  INSULA_BIND_ARGS+=(--ro-bind "$src" "$guest")
done
plan=$(python3 "$repo/insula/estate/overlay_root.py" --base "$rootfs" "${mountpoints[@]}")
mapfile -t ROOT_PLAN <<<"$plan"
if [[ "$print_argv" == 1 ]]; then
  insula --print-argv -- "$@"
else
  insula -- "$@"
fi
"""

def run_capsule(bundle, repo, estate, workspace, line, command, print_argv=False):
    if not command:
        raise ValueError("an explicit caller command is required")
    plan = projections(bundle)
    argv = ["bash", "-c", RUN_SCRIPT, "capsule", str(repo.resolve()), plan["rootfs"],
            str(workspace.resolve()), line, str(int(print_argv)), str(len(plan["bindings"]))]
    for binding in plan["bindings"]:
        argv.extend([binding["source"], binding["guest"]])
    argv.extend(command)
    env = dict(os.environ, VASO_ESTATE_ROOT=str(estate.resolve()))
    subprocess.run(argv, env=env, check=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="command",required=True)
    a=sub.add_parser("pack"); a.add_argument("--spec",type=Path,required=True); a.add_argument("--layout",type=Path,required=True); a.add_argument("--tag",default="capsule"); a.add_argument("--max-layer-bytes",type=int,default=2_000_000_000)
    a=sub.add_parser("unpack"); a.add_argument("--layout",type=Path,required=True); a.add_argument("--dest",type=Path,required=True); a.add_argument("--tag",default="capsule"); a.add_argument("--umoci",default="umoci")
    a=sub.add_parser("plan"); a.add_argument("--bundle",type=Path,required=True)
    a=sub.add_parser("run"); a.add_argument("--bundle",type=Path,required=True); a.add_argument("--repo",type=Path,required=True); a.add_argument("--estate",type=Path,required=True); a.add_argument("--workspace",type=Path,required=True); a.add_argument("--line",required=True); a.add_argument("--print-argv",action="store_true"); a.add_argument("argv",nargs=argparse.REMAINDER)
    for name in ("push", "pull"):
        a=sub.add_parser(name); a.add_argument("--layout",type=Path,required=True); a.add_argument("--remote",required=True); a.add_argument("--tag",default="capsule"); a.add_argument("--oras-bin")
    a=sub.add_parser("add-rootfs-manifest"); a.add_argument("--layout",type=Path,required=True); a.add_argument("--file",type=Path,required=True); a.add_argument("--tag",default="capsule")
    args=p.parse_args()
    if args.command=="add-rootfs-manifest": print(add_rootfs_manifest(args.layout,args.file,args.tag))
    elif args.command in ("push","pull"): print(transfer(args.command,args.layout,args.remote,args.tag,args.oras_bin))
    elif args.command=="pack": print(pack(json.loads(args.spec.read_text()),args.layout,args.tag,args.max_layer_bytes))
    elif args.command=="unpack": unpack(args.layout,args.dest,args.tag,args.umoci); print(args.dest)
    elif args.command=="run": run_capsule(args.bundle,args.repo,args.estate,args.workspace,args.line,args.argv[1:] if args.argv[:1]==["--"] else args.argv,args.print_argv)
    else: print(json.dumps(projections(args.bundle),indent=2))
if __name__=="__main__":
    main()
