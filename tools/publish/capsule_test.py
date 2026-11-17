import gzip, hashlib, importlib.util, io, json, tarfile
from pathlib import Path
import unittest, tempfile
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("capsule", Path(__file__).with_name("capsule.py"))
c = importlib.util.module_from_spec(spec); spec.loader.exec_module(c)

def fixture(tmp_path):
    base=tmp_path/"base"; base.mkdir(); (base/"bin").mkdir(); (base/"bin/sh").write_text("fixture"); (base/"bin/sh").chmod(0o755)
    tools=tmp_path/"tools"; tools.mkdir(); (tools/"cc").write_text("compiler")
    return {"schema_version":1,"layers":[{"name":"base","source":str(base),"path":"rootfs"},
      {"name":"toolchain","source":str(tools),"path":"bundles/toolchain","guest":"/vaso/toolchains/cu133"}]}

def read_blob(layout, descriptor):
    data=(layout/"blobs/sha256"/descriptor["digest"].split(":")[1]).read_bytes()
    assert len(data)==descriptor["size"]
    assert hashlib.sha256(data).hexdigest()==descriptor["digest"].split(":")[1]
    return data

def inspect(layout):
    index=json.loads((layout/"index.json").read_text())
    return json.loads(read_blob(layout,index["manifests"][0]))

def test_standard_oci_transport_and_native_digests(tmp_path):
    s=fixture(tmp_path); out=tmp_path/"image"; c.pack(s,out)
    m=inspect(out); cfg=json.loads(read_blob(out,m["config"]))
    assert "Cmd" not in cfg["config"] and "Entrypoint" not in cfg["config"]
    assert cfg["architecture"]=="amd64" and len(m["layers"])==3
    for entry,diff in zip(m["layers"],cfg["rootfs"]["diff_ids"]):
        raw=gzip.decompress(read_blob(out,entry))
        assert "sha256:"+hashlib.sha256(raw).hexdigest()==diff
    raw=gzip.decompress(read_blob(out,m["layers"][-1]))
    with tarfile.open(fileobj=io.BytesIO(raw)) as t:
        descriptor=json.load(t.extractfile("capsule/descriptor.json"))
    assert descriptor["layers"][1]["guest"]=="/vaso/toolchains/cu133"
    assert "source" not in str(descriptor)

def test_semantic_layer_reuse(tmp_path):
    s=fixture(tmp_path); a=tmp_path/"a"; b=tmp_path/"b"; c.pack(s,a)
    (Path(s["layers"][1]["source"])/"cc").write_text("changed")
    c.pack(s,b); x=inspect(a)["layers"]; y=inspect(b)["layers"]
    assert x[0]["digest"]==y[0]["digest"]
    assert x[1]["digest"]!=y[1]["digest"]
    assert x[2]["digest"]==y[2]["digest"]

def test_basic_secret_exclusions(tmp_path):
    s=fixture(tmp_path); root=Path(s["layers"][0]["source"])
    (root/".ssh").mkdir(); (root/".ssh/key").write_text("secret")
    (root/"__pycache__").mkdir(); (root/"__pycache__/host-path.pyc").write_text("private build path")
    (root/"etc").mkdir(); (root/"etc/shadow").write_text("secret")
    c.pack(s,tmp_path/"image"); m=inspect(tmp_path/"image")
    raw=gzip.decompress(read_blob(tmp_path/"image",m["layers"][0]))
    with tarfile.open(fileobj=io.BytesIO(raw)) as t:
        names=t.getnames()
    assert not any(".ssh" in n or "__pycache__" in n or n.endswith("/shadow") for n in names)

def test_bad_paths(tmp_path):
    s=fixture(tmp_path)
    for path in ["../escape","/absolute","bundles/a/../../x","bundles//x"]:
        s["layers"][1]["path"]=path
        with unittest.TestCase().assertRaises(ValueError): c.pack(s,tmp_path/"bad")

def test_protected_guest_paths(tmp_path):
    s=fixture(tmp_path)
    for guest in ["/etc/passwd","/run/nvidia-driver","/vaso/../etc","/workspace/vaso"]:
        s["layers"][1]["guest"]=guest
        with unittest.TestCase().assertRaises(ValueError): c.validate(s,True)

def test_unknown_commands_and_host_paths_rejected(tmp_path):
    s=fixture(tmp_path); s["command"]="sh -c anything"
    with unittest.TestCase().assertRaises(ValueError): c.validate(s,True)

def test_no_existing_layout_replacement(tmp_path):
    s=fixture(tmp_path); dest=tmp_path/"keep"; dest.mkdir(); (dest/"precious").write_text("yes")
    with unittest.TestCase().assertRaises(ValueError): c.pack(s,dest)
    assert (dest/"precious").read_text()=="yes"

def test_failed_unpack_keeps_destination_absent(tmp_path):
    with patch.object(c.subprocess,"run",side_effect=RuntimeError("unpacker failed")):
        with unittest.TestCase().assertRaises(RuntimeError): c.unpack(tmp_path/"oci",tmp_path/"dest")
    assert not (tmp_path/"dest").exists()

def test_projection_escape_rejected(tmp_path):
    cap=tmp_path/"bundle/rootfs/capsule"; cap.mkdir(parents=True)
    (cap/"rootfs").mkdir(); (cap/"bundles").mkdir()
    (cap/"bundles/tools").symlink_to(tmp_path,target_is_directory=True)
    (cap/"descriptor.json").write_text(json.dumps({"schema_version":1,"layers":[
      {"name":"base","path":"rootfs"},{"name":"tools","path":"bundles/tools","guest":"/vaso/tools"}]}))
    with unittest.TestCase().assertRaises(ValueError): c.projections(tmp_path/"bundle")


def test_output_inside_source_rejected(tmp_path):
    s=fixture(tmp_path)
    with unittest.TestCase().assertRaises(ValueError):
        c.pack(s,Path(s["layers"][0]["source"])/"out")

def test_runner_shell_syntax(tmp_path):
    import subprocess
    subprocess.run(["bash","-n"],input=c.RUN_SCRIPT,text=True,check=True)

def test_control_paths_rejected(tmp_path):
    s=fixture(tmp_path); s["layers"][1]["guest"]="/vaso/a\nb"
    with unittest.TestCase().assertRaises(ValueError): c.validate(s,True)


def test_real_rootless_roundtrip(tmp_path):
    import os
    umoci=os.environ.get("CAPSULE_TEST_UMOCI")
    if not umoci: raise unittest.SkipTest("set CAPSULE_TEST_UMOCI for real OCI roundtrip")
    spec=fixture(tmp_path); c.pack(spec,tmp_path/"layout")
    c.unpack(tmp_path/"layout",tmp_path/"restored",umoci=umoci)
    plan=c.projections(tmp_path/"restored")
    assert (Path(plan["rootfs"])/"bin/sh").read_text()=="fixture"
    assert (Path(plan["bindings"][0]["source"])/"cc").read_text()=="compiler"
    assert not list((tmp_path/"restored").glob("*.mtree"))


def test_transfer_uses_native_image_copy_and_temporary_auth(tmp_path):
    import artifact_store as store
    spec=fixture(tmp_path); layout=tmp_path/"layout"; digest=c.pack(spec,layout)
    calls=[]
    def record(args, command, **kw):
        calls.append((command,kw))
    with patch.object(store,"token_from_env_or_file",return_value="test-token"), \
         patch.object(store,"run_oras",side_effect=record), \
         patch.object(store,"resolve_digest",return_value=digest), \
         patch.object(store,"check_ghcr_private") as private:
        assert c.transfer("push",layout,"ghcr.io/phi9t/vaso-capsule-test:v1")==digest
    assert calls[1][0][:2]==["cp","--from-oci-layout"]
    assert calls[0][1]["stdin"]=="test-token"
    assert not Path(calls[0][1]["env_extra"]["DOCKER_CONFIG"]).exists()
    assert private.call_count == 2

def test_pull_requires_digest_before_auth(tmp_path):
    with unittest.TestCase().assertRaises(ValueError):
        c.transfer("pull",tmp_path/"layout","ghcr.io/phi9t/vaso-capsule-test:latest")


def test_outer_capsule_escape_rejected(tmp_path):
    (tmp_path/"bundle/rootfs").mkdir(parents=True)
    (tmp_path/"bundle/rootfs/capsule").symlink_to(tmp_path,target_is_directory=True)
    with unittest.TestCase().assertRaises(ValueError): c.projections(tmp_path/"bundle")


def test_real_insula_projection_argv(tmp_path):
    import os, subprocess, sys
    umoci=os.environ.get("CAPSULE_TEST_UMOCI")
    if not umoci: raise unittest.SkipTest("set CAPSULE_TEST_UMOCI")
    spec=fixture(tmp_path); c.pack(spec,tmp_path/"oci")
    c.unpack(tmp_path/"oci",tmp_path/"restored",umoci=umoci)
    repo=Path(__file__).resolve().parents[2]
    result=subprocess.run([sys.executable,str(Path(__file__).with_name("capsule.py")),"run",
        "--bundle",str(tmp_path/"restored"),"--repo",str(repo),"--estate",str(tmp_path/"estate"),
        "--workspace",str(repo),"--line","cu130","--print-argv","--","/bin/sh","-c","true"],
        capture_output=True,text=True,check=True)
    argv=json.loads(result.stdout)
    guest=argv.index("/vaso/toolchains/cu133")
    assert guest > 0
    assert str(tmp_path/"restored") in " ".join(argv)
    assert argv[-3:]==["/bin/sh","-c","true"]


def test_public_destination_rejected_before_upload(tmp_path):
    import artifact_store as store
    spec=fixture(tmp_path); layout=tmp_path/"oci"; c.pack(spec,layout)
    with patch.object(store,"token_from_env_or_file",return_value="test"), \
         patch.object(store,"check_ghcr_private",side_effect=store.StoreError("public")), \
         patch.object(store,"run_oras") as run:
        with unittest.TestCase().assertRaises(store.StoreError):
            c.transfer("push",layout,"ghcr.io/phi9t/vaso-capsule-test:v1")
    run.assert_not_called()

def test_sources_root_projection_allowed(tmp_path):
    spec=fixture(tmp_path); spec["layers"][1]["guest"]="/sources"
    c.validate(spec,True)


def test_bounded_parts_are_real_tar_layers(tmp_path):
    spec=fixture(tmp_path); tools=Path(spec["layers"][1]["source"])
    for i in range(8): (tools/str(i)).write_bytes(bytes(range(256))*12)
    c.pack(spec,tmp_path/"oci",max_layer_bytes=4096)
    manifest=inspect(tmp_path/"oci")
    assert len(manifest["layers"]) > 3
    config=json.loads(read_blob(tmp_path/"oci",manifest["config"]))
    for desc,diff in zip(manifest["layers"],config["rootfs"]["diff_ids"]):
        raw=gzip.decompress(read_blob(tmp_path/"oci",desc))
        assert "sha256:"+hashlib.sha256(raw).hexdigest()==diff
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            assert archive.getmembers()

def test_bounded_real_restore_preserves_links(tmp_path):
    import os
    umoci=os.environ.get("CAPSULE_TEST_UMOCI")
    if not umoci: raise unittest.SkipTest("set CAPSULE_TEST_UMOCI")
    spec=fixture(tmp_path); tools=Path(spec["layers"][1]["source"])
    payload=bytes(range(256))*12
    (tools/"large").write_bytes(payload)
    os.link(tools/"large",tools/"same")
    (tools/"alias").symlink_to("large")
    c.pack(spec,tmp_path/"oci",max_layer_bytes=2048)
    c.unpack(tmp_path/"oci",tmp_path/"restored",umoci=umoci)
    directory=Path(c.projections(tmp_path/"restored")["bindings"][0]["source"])
    assert (directory/"large").read_bytes()==payload
    assert (directory/"same").read_bytes()==payload
    assert (directory/"alias").read_bytes()==payload


def test_base_manifest_is_packed_without_local_path(tmp_path):
    spec=fixture(tmp_path); source=tmp_path/"base-manifest.json"; source.write_text('{"line":"cu130"}')
    spec["rootfs_manifest"]=str(source)
    c.pack(spec,tmp_path/"oci")
    m=inspect(tmp_path/"oci")
    raw=gzip.decompress(read_blob(tmp_path/"oci",m["layers"][-1]))
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        assert json.load(archive.extractfile("capsule/rootfs-bundle.json"))=={"line":"cu130"}
        desc=json.load(archive.extractfile("capsule/descriptor.json"))
    assert "rootfs_manifest" not in desc

def test_metadata_append_reuses_layers_and_restores(tmp_path):
    import os
    spec=fixture(tmp_path); layout=tmp_path/"oci"; c.pack(spec,layout)
    before=inspect(layout)["layers"]
    source=tmp_path/"manifest.json"; source.write_text('{"line":"cu130"}')
    digest=c.add_rootfs_manifest(layout,source)
    assert inspect(layout)["layers"][:-1]==before
    assert c.add_rootfs_manifest(layout,source)==digest
    umoci=os.environ.get("CAPSULE_TEST_UMOCI")
    if umoci:
        c.unpack(layout,tmp_path/"restored",umoci=umoci)
        assert json.loads((tmp_path/"restored/rootfs/capsule/rootfs-bundle.json").read_text())=={"line":"cu130"}


def test_manifest_source_symlink_rejected(tmp_path):
    spec=fixture(tmp_path); target=tmp_path/"manifest.json"; target.write_text("{}")
    source=tmp_path/"link.json"; source.symlink_to(target)
    spec["rootfs_manifest"]=str(source)
    with unittest.TestCase().assertRaises(ValueError): c.pack(spec,tmp_path/"oci")


class CapsuleTests(unittest.TestCase):
    pass

def case(fn):
    def run(self):
        with tempfile.TemporaryDirectory() as directory:
            fn(Path(directory))
    return run

for name, fn in list(globals().items()):
    if name.startswith("test_") and callable(fn):
        setattr(CapsuleTests, name, case(fn))

if __name__ == "__main__":
    unittest.main()
