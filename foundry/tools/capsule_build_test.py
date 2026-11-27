"""Admission and success require one matching native request and proven cleanup."""
import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path
import signal
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import capsule_build as b
from build_report import atomic_json

IDENTITY = "a"*32
DIGEST = "b"*64


class CapsuleBuildTest(unittest.TestCase):
    def fixtures(self, tmp, phase="build", short="triton", owner="external"):
        root = Path(tmp)
        output = root/"attempt"
        workspace = root/"workspace"
        workspace.mkdir(exist_ok=True)
        command = [b.BUILDER,"--target",short,"--phase",phase,"--request-id",IDENTITY]
        request = {"schema":"vaso.foundry-build-request.v1","target":"cu126-sm61/"+short,
                   "phase":phase,"request_id":IDENTITY,"started_at":"2026-01-01",
                   "owner":owner,"workspace":str(workspace),"command":command,"plan_sha256":DIGEST,
                   "insula":"/insula","spec":"/spec","state":"/state","run_id":"test"}
        atomic_json(output/"request.json",request)
        native = {"schema":b.NATIVE_SCHEMA,"target":short,"phase":phase,"request_id":IDENTITY,
                  "state":"prepared" if phase=="prepare" else "built","exit_code":0,
                  "backend":"bazel","python_abi":"cp312","cuda":"12.6","gpu_arch":"sm_61","artifacts":[],
                  "native_execution":phase=="build","cache_hit":False,"native_execution_count":1}
        if phase == "build":
            wheel = workspace/"native-build"/short/"artifacts/one/native.whl"
            wheel.parent.mkdir(parents=True)
            wheel.write_bytes(b"wheel")
            native["artifacts"] = [{"path":"/workspace/"+str(wheel.relative_to(workspace)),
                "name":wheel.name,"bytes":5,"sha256":hashlib.sha256(b"wheel").hexdigest(),"python_abi":"cp312"}]
        native_path=workspace/"native-build"/short/"requests"/(IDENTITY+".json")
        atomic_json(native_path,native)
        atomic_json(output/"lifecycle/status.json",{"state":"passed","exit_code":0,
                    "cleanup_complete":True,"plan_sha256":DIGEST,"command":command})
        atomic_json(output/"lifecycle/owner/receipt.json",{"exit_code":0,"cleanup":{"complete":True},
                    "lifecycle":{"reason":"completed"}})
        return output,request,native_path,native

    def test_success_requires_both_artifact_and_owner_receipts(self):
        for short in ("torch","triton"):
            with self.subTest(short=short), tempfile.TemporaryDirectory() as tmp:
                output,request,_,_=self.fixtures(tmp,short=short)
                result=b.status(output,request["target"])
                self.assertEqual(result["schema"],b.SCHEMA)
                self.assertEqual(result["state"],"built")
                self.assertTrue(result["lifecycle"]["cleanup_complete"])
                self.assertTrue(Path(result["artifacts"][0]["path"]).is_file())

    def test_status_is_read_only_and_target_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            output,request,_,_=self.fixtures(tmp)
            before=(output/"request.json").read_bytes()
            with patch.object(b.os,"execv",side_effect=AssertionError("executed")):
                b.status(output,request["target"])
                with self.assertRaisesRegex(ValueError,"selected target"):
                    b.status(output,"cu126-sm61/torch")
            self.assertEqual((output/"request.json").read_bytes(),before)
            self.assertFalse((output/"summary.json").exists())

    def test_stale_wrong_target_phase_and_request_receipts_cannot_pass(self):
        for field,value in (("request_id","c"*32),("target","torch"),("phase","prepare"),("state","running")):
            with self.subTest(field=field),tempfile.TemporaryDirectory() as tmp:
                output,request,path,native=self.fixtures(tmp)
                atomic_json(path,{**native,field:value})
                result=b.status(output,request["target"])
                self.assertEqual(result["state"],"failed")
                self.assertNotEqual(result["exit_code"],0)

    def test_mutable_latest_success_cannot_replace_missing_exact_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            output,request,path,native=self.fixtures(tmp)
            path.unlink()
            atomic_json(path.parent.parent/"status.json",native)
            result=b.status(output,request["target"])
            self.assertEqual(result["state"],"failed")

    def test_missing_truncated_or_escaping_artifact_cannot_pass(self):
        for mode in ("missing","truncated","escape"):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                output,request,path,native=self.fixtures(tmp)
                wheel=Path(request["workspace"])/Path(native["artifacts"][0]["path"]).relative_to("/workspace")
                if mode=="missing": wheel.unlink()
                elif mode=="truncated": wheel.write_bytes(b"x")
                else:
                    native["artifacts"][0]["path"]="/workspace/native-build/triton/artifacts/../../outside.whl"
                    atomic_json(path,native)
                self.assertEqual(b.status(output,request["target"])["state"],"failed")

    def test_prepare_never_claims_an_existing_wheel(self):
        with tempfile.TemporaryDirectory() as tmp:
            output,request,path,native=self.fixtures(tmp,phase="prepare")
            self.assertEqual(b.status(output,request["target"])["state"],"prepared")
            native["artifacts"]=[{"path":"/workspace/old.whl"}]
            atomic_json(path,native)
            self.assertEqual(b.status(output,request["target"])["state"],"failed")

    def test_cleanup_and_plan_mismatch_override_native_success(self):
        for change in ({"cleanup_complete":False},{"plan_sha256":"c"*64},{"exit_code":143}):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as tmp:
                output,request,_,_=self.fixtures(tmp)
                path=output/"lifecycle/status.json"
                atomic_json(path,{**json.loads(path.read_text()),**change})
                self.assertEqual(b.status(output,request["target"])["state"],"failed")

    def test_cancelled_or_unproven_owner_cannot_pass(self):
        for owner in ({"exit_code":0,"cleanup":{"complete":False},"lifecycle":{"reason":"completed"}},
                      {"exit_code":0,"cleanup":{"complete":True},"lifecycle":{"reason":"cancelled"}}):
            with self.subTest(owner=owner),tempfile.TemporaryDirectory() as tmp:
                output,request,_,_=self.fixtures(tmp)
                atomic_json(output/"lifecycle/owner/receipt.json",owner)
                self.assertEqual(b.status(output,request["target"])["state"],"failed")

    def test_native_owner_status_uses_exact_run_plan_and_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            output,request,_,_=self.fixtures(tmp,owner="native")
            owner={"run_id":"test","plan_sha256":DIGEST,"phase":"cleaned",
                   "cleanup":{"process_cleanup":{"root_populated":False,"removed":True}},
                   "exits":{"main":{"reason":"exited","exit_code":0}}}
            with patch.object(b.subprocess,"check_output",return_value=json.dumps(owner)) as run:
                self.assertEqual(b.status(output,request["target"])["state"],"built")
                self.assertEqual(run.call_args.args[0],["/insula","status","--state","/state","test"])
            with patch.object(b.subprocess,"check_output",return_value=json.dumps({**owner,"run_id":"wrong"})):
                with self.assertRaisesRegex(ValueError,"admitted run"):
                    b.status(output,request["target"])
            owner["cleanup"]["process_cleanup"]["removed"]=False
            with patch.object(b.subprocess,"check_output",return_value=json.dumps(owner)):
                self.assertEqual(b.status(output,request["target"])["state"],"failed")

    def test_native_planned_status_does_not_claim_payload_is_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            output,request,_,_=self.fixtures(tmp,owner="native")
            owner={"run_id":"test","plan_sha256":DIGEST,"phase":"planned","cleanup":None,"exits":None}
            with patch.object(b.subprocess,"check_output",return_value=json.dumps(owner)):
                result=b.status(output,request["target"])
            self.assertEqual(result["state"],"admitted")
            self.assertIsNone(result["exit_code"])
            self.assertEqual(result["artifacts"],[])

    def test_plan_admission_rejects_ambient_inputs_and_missing_builder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            prefix=root/"rootfs/opt/vaso/pascal"
            (prefix/"bin").mkdir(parents=True)
            (prefix/"bin/build-native").touch()
            atomic_json(prefix/"profile.json",{"cuda":"12.6","gpu_arch":"sm_61",
                "python_abi":"cp312","runtime":"standalone-insula"})
            workspace=root/"workspace"
            workspace.mkdir()
            a=argparse.Namespace(insula=Path("/insula"),spec=Path("/spec"),supervisor=Path("/owner"),
                                 state=None,max_seconds=30)
            command=[b.BUILDER,"--target","triton","--phase","prepare"]
            main={"argv":command,"rootfs":str(root/"rootfs"),"env":{},"devices":[],"extra_mounts":[],
                  "mounts":[{"target":"/workspace","source":str(workspace),"writable":True}]}
            plan={"kind":"single","main":main,"plan_sha256":DIGEST}
            with patch.object(b.subprocess,"check_output",return_value=json.dumps(plan)):
                self.assertEqual(b.admit(a,command)[1],workspace)
            for field,value in (("devices",["/dev/nvidia0"]),("extra_mounts",[{}]),
                                ("env",{"PYTHONPATH":"/ambient"}),("ephemeral",True),("argv",["/bin/sh"])):
                bad={**plan,"main":{**main,field:value}}
                with self.subTest(field=field),patch.object(b.subprocess,"check_output",return_value=json.dumps(bad)):
                    with self.assertRaises(RuntimeError): b.admit(a,command)
            (prefix/"bin/build-native").unlink()
            with patch.object(b.subprocess,"check_output",return_value=json.dumps(plan)):
                with self.assertRaisesRegex(RuntimeError,"builder"): b.admit(a,command)

    def test_native_exec_records_request_and_passes_reviewed_plan(self):
        class ExecReached(BaseException): pass
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            a=argparse.Namespace(target="cu126-sm61/torch",phase="build",jobs=1,plan_only=False,
                output=root/"out",insula=Path("/insula"),spec=Path("/spec"),state=Path("/state"),
                run_id="owned",supervisor=None,max_seconds=60)
            plan={"plan_sha256":DIGEST,"main":{"bundle_digest":"d"*64}}
            with patch.object(b,"admit",return_value=(plan,root/"workspace")),patch.object(b.os,"execv",side_effect=ExecReached) as run:
                with self.assertRaises(ExecReached): b.execute(a)
            request=json.loads((a.output/"request.json").read_text())
            argv=run.call_args.args[1]
            self.assertEqual(argv[argv.index("--expected-plan-sha256")+1],DIGEST)
            self.assertEqual(argv[argv.index("--request-id")+1],request["request_id"])
            with patch.object(b,"admit",return_value=(plan,root/"workspace")):
                with self.assertRaises(FileExistsError): b.execute(a)

    def test_cancellation_during_owner_return_cannot_become_common_success(self):
        runner=b.payload_runner()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            a=argparse.Namespace(target="cu126-sm61/triton",phase="prepare",jobs=1,plan_only=False,
                output=root/"out",insula=Path("/insula"),spec=Path("/spec"),state=None,run_id=None,
                supervisor=Path("/owner"),max_seconds=60)
            def run(args,command,signals):
                signals.receive(signal.SIGTERM,None)
                return 0
            module=SimpleNamespace(SignalRelay=runner.SignalRelay,run_request=run)
            plan={"plan_sha256":DIGEST,"main":{}}
            with patch.object(b,"admit",return_value=(plan,root/"workspace")),patch.object(b,"payload_runner",return_value=module),\
                 patch.object(b.uuid,"uuid4",return_value=SimpleNamespace(hex=IDENTITY)),\
                 patch.object(b,"status",return_value={"schema":b.SCHEMA,"target":a.target,"request_id":IDENTITY,
                                                       "state":"prepared","exit_code":0}),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(b.execute(a),143)
            result=json.loads((a.output/"summary.json").read_text())
            self.assertEqual(result["state"],"failed")
            self.assertEqual(b.status(a.output,a.target)["state"],"failed")

    def test_native_exec_failure_remains_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            a=argparse.Namespace(target="cu126-sm61/torch",phase="build",jobs=1,plan_only=False,
                output=root/"out",insula=Path("/insula"),spec=Path("/spec"),state=Path("/state"),
                run_id="owned",supervisor=None,max_seconds=60)
            with patch.object(b,"admit",return_value=({"plan_sha256":DIGEST,"main":{}},root)),\
                 patch.object(b.os,"execv",side_effect=OSError("cannot exec")):
                with self.assertRaises(OSError): b.execute(a)
            self.assertEqual(b.status(a.output,a.target)["state"],"failed")


if __name__ == "__main__":
    unittest.main()
