"""Canonical build target routing and fail-closed Insula plan admission."""
from __future__ import annotations
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_contract as b


class BuildContractTest(unittest.TestCase):
    def parse(self, *argv):
        return b.parser().parse_args(argv)

    def test_catalog_and_matrix(self):
        self.assertEqual(set(b.TARGETS), {
            "cu129/torch", "cu129/jax", "cu130/torch", "cu130/jax",
            "cu126-sm61/torch", "cu126-sm61/triton",
        })
        for name, target in b.TARGETS.items():
            if target.backend != "bazel":
                continue
            for phase, flag in (("prepare", "--prepare-only"), ("status", "--status"), ("build", None)):
                with self.subTest(name=name, phase=phase):
                    cmd = b.dispatch(self.parse("--target", name, "--phase", phase, "--estate-root", "/estate"))
                    self.assertEqual(Path(cmd[2]).name, "build_variants.py")
                    self.assertEqual(cmd[cmd.index("--line")+1], target.cuda_line)
                    self.assertEqual(cmd[cmd.index("--profile")+1], name.split("/")[-1])
                    if flag:
                        self.assertIn(flag, cmd)
                    else:
                        self.assertNotIn("--status", cmd)
                        self.assertNotIn("--prepare-only", cmd)

    def test_torch_route_and_resume(self):
        a = self.parse("--target", "cu126-sm61/torch", "--site", "/site.json", "--resume", "--jobs", "2")
        cmd = b.dispatch(a)
        self.assertEqual(Path(cmd[2]).name, "source_build.py")
        self.assertTrue(cmd[cmd.index("--profile")+1].endswith("cu126-sm61-r4.json"))
        self.assertIn("--resume", cmd)
        self.assertEqual(cmd[-1], "build")

    def test_torch_mismatched_profile_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/"profile.json"
            p.write_text(json.dumps({"toolchain":{"cuda_major_minor":"13.0","cuda_arch_list":"10.0","python_abi":"cp313"}}))
            with self.assertRaisesRegex(ValueError, "contradicts"):
                b.dispatch(self.parse("--target","cu126-sm61/torch","--site","/site.json","--source-profile",str(p)))

    def test_unrelated_flags_fail_closed(self):
        for argv in (
            ("--target","cu129/torch","--estate-root","/estate","--site","/site"),
            ("--target","cu130/jax","--estate-root","/estate","--phase","status","--fetch"),
            ("--target","cu126-sm61/torch","--site","/site","--phase","prepare","--resume"),
            ("--target","cu126-sm61/triton","--insula","/insula","--spec","/spec","--jobs","2"),
            ("--target","cu126-sm61/triton","--insula","/insula","--spec","/spec","--supervisor","/owner","--state","/state","--output","/out"),
            ("--target","cu126-sm61/triton","--phase","status","--output","/out","--run-id","run"),
            ("--target","cu126-sm61/triton","--insula","/insula","--spec","/spec","--supervisor","/owner"),
        ):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                b.dispatch(self.parse(*argv))

    def test_capsule_prepare_plan_and_native_run(self):
        base = ("--target","cu126-sm61/triton","--phase","prepare","--insula","/insula","--spec","/spec")
        plan = b.dispatch(self.parse(*base,"--plan-only"))
        self.assertEqual(plan[:2], ["/insula","plan"])
        self.assertEqual(plan[-2:], ["/opt/vaso/pascal/bin/build-triton","--prepare-only"])
        cmd = b.dispatch(self.parse(*base,"--state","/state","--run-id","fresh"))
        self.assertEqual(cmd[:2], ["/insula","run"])
        self.assertEqual(cmd[-2:], plan[-2:])
        self.assertIn("--timeout-seconds", cmd)

    def test_capsule_external_owner_explicit(self):
        cmd = b.dispatch(self.parse("--target","cu126-sm61/triton","--insula","/insula","--spec","/spec","--supervisor","/owner","--output","/out","--max-seconds","60"))
        self.assertEqual(Path(cmd[2]).name,"run_payload.py")
        self.assertEqual(cmd[cmd.index("--timeout")+1],"60")
        self.assertNotIn("--gpu",cmd)
        self.assertEqual(cmd[-1],"/opt/vaso/pascal/bin/build-triton")

    def test_external_status_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"status.json"
            p.write_text('{"state":"passed"}\n')
            out=io.StringIO()
            with contextlib.redirect_stdout(out), patch.object(b.os,"execv",side_effect=AssertionError("executed")):
                self.assertEqual(b.main(["--target","cu126-sm61/triton","--phase","status","--output",tmp]),0)
            self.assertEqual(json.loads(out.getvalue()),{"state":"passed"})

    def test_capsule_plan_admission(self):
        a = self.parse("--target","cu126-sm61/triton","--phase","prepare","--insula","/insula","--spec","/spec","--plan-only")
        cmd=b.dispatch(a)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"rootfs"
            prefix=root/"opt/vaso/pascal"
            (prefix/"bin").mkdir(parents=True)
            (prefix/"bin/build-triton").touch()
            p=prefix/"profile.json"
            profile={"cuda":"12.6","gpu_arch":"sm_61","python_abi":"cp312","runtime":"standalone-insula"}
            p.write_text(json.dumps(profile))
            plan={"kind":"single","main":{"argv":cmd[cmd.index("--")+1:],"rootfs":str(root),"env":{},"devices":[],"extra_mounts":[]},"plan_sha256":"a"*64}
            with patch.object(b.subprocess,"check_output",return_value=json.dumps(plan)) as run:
                _,digest=b.capsule_plan(a,cmd)
                self.assertEqual(digest,"a"*64)
                self.assertEqual(run.call_args.args[0],cmd)
            for field,value in (("devices",["/dev/nvidia0"]),("extra_mounts",[{}]),("env",{"PYTHONPATH":"/workspace"}),("argv",["/bin/sh"])):
                with self.subTest(field=field):
                    bad={**plan,"main":{**plan["main"],field:value}}
                    with patch.object(b.subprocess,"check_output",return_value=json.dumps(bad)),self.assertRaises(RuntimeError):
                        b.capsule_plan(a,cmd)
            p.write_text(json.dumps({**profile,"cuda":"13.0"}))
            with patch.object(b.subprocess,"check_output",return_value=json.dumps(plan)),self.assertRaisesRegex(RuntimeError,"contradicts"):
                b.capsule_plan(a,cmd)

    def test_plan_digest_passed_to_external_owner(self):
        args=["--target","cu126-sm61/triton","--insula","/insula","--spec","/spec","--supervisor","/owner","--output","/out"]
        with patch.object(b,"capsule_plan",return_value=("{}", "a"*64)),patch.object(b.os,"execv") as execute:
            b.main(args)
        argv=execute.call_args.args[1]
        i=argv.index("--expected-plan-sha256")
        self.assertEqual(argv[i+1],"a"*64)
        self.assertLess(i,argv.index("--"))


if __name__ == "__main__":
    unittest.main()
