import json
from pathlib import Path
import unittest
from unittest.mock import patch
import gen_plans

class VariantPlanTest(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[1]
        self.patch_path = "native/pytorch/variants/torch215-cuspy-no-header-hash.patch"
        self.approved_patch = {"path": self.patch_path, "expected_text": (self.repo_root / self.patch_path).read_bytes().decode("utf-8")}
        cudss_path = "native/pytorch/variants/torch215-cudss-context-include.patch"
        self.cudss_patch = {"path": cudss_path, "expected_text": (self.repo_root / cudss_path).read_text()}
        self.approved_patches = [self.approved_patch, self.cudss_patch]
        self.rootfs = {"components":{"cuda_toolkit":{"version":"13.3.0"}}, "live_checks":{"commands":[{"name":"fixture","argv":["true"],"expect_stdout":""}]}}
        self.rootfs_text = json.dumps(self.rootfs)
        self.source = {"name":"main", "archive":"{source_archive}", "sha256":"a"*64,
                       "strip_prefix":"pytorch-v2.15.0", "urls":["https://example.invalid/test-only.tar.zst"], "patches":list(self.approved_patches)}
        self.manifest_text = json.dumps({"source_commit": "31a77fc9c0a7c6fff15a1ff58c743f53b1a9d457",
                                        "archive":{"sha256":"a"*64}, "clean_recursive_source":True})
    def generate(self):
        return gen_plans.pytorch_h20_cuspy_plan(
            self.repo_root, source=self.source, source_manifest_path="native/pytorch/test-only.json",
            source_manifest_text=self.manifest_text, rootfs=self.rootfs, rootfs_identity_text=self.rootfs_text)
    def test_no_small_file_or_plan_hashing(self):
        with patch.object(gen_plans, "sha256_file", create=True, side_effect=AssertionError("file hashing forbidden")), \
             patch.object(gen_plans, "sha256_bytes", side_effect=AssertionError("text hashing forbidden")), \
             patch.object(gen_plans, "registered_toolchain", return_value=("gcc13",{})):
            plan = self.generate()
        self.assertEqual(plan["package"]["version"], "2.15.0")
        self.assertEqual(plan["line"], "cu133")
        self.assertEqual(plan["gpu_archs"], ["sm_90"])
        self.assertEqual(plan["inputs"][0]["expected_text"], self.manifest_text)
        self.assertEqual(plan["toolchain"]["rootfs_identity"]["expected_text"], self.rootfs_text)
        self.assertEqual(plan["determinism"]["seed"], "sha256:"+"a"*64)
        self.assertIn("9.0", plan["configure"]["argv"][2])
    def test_b200_preserves_full_graph_with_explicit_sm100(self):
        with patch.object(gen_plans, "registered_toolchain", return_value=("gcc13",{})), \
             patch.object(gen_plans, "sha256_bytes", side_effect=AssertionError("no hash")):
            h20 = self.generate()
            b200 = gen_plans.pytorch_b200_cuspy_plan(
                self.repo_root, source=self.source, source_manifest_path="native/pytorch/test-only.json",
                source_manifest_text=self.manifest_text, rootfs=self.rootfs, rootfs_identity_text=self.rootfs_text)
        self.assertEqual(b200["gpu_archs"], ["sm_100"])
        self.assertIn("10.0", b200["configure"]["argv"][2])
        self.assertEqual(b200["sources"], h20["sources"])
        self.assertEqual(b200["inputs"], h20["inputs"])
        self.assertEqual(b200["package"], h20["package"])
        self.assertEqual(b200["line"], "cu133")

    def test_approved_patch_is_preserved_for_executor(self):
        with patch.object(gen_plans, "registered_toolchain", return_value=("gcc13",{})):
            plan = self.generate()
        self.assertEqual(plan["sources"][0]["patches"], self.approved_patches)
        self.assertTrue(json.loads(self.manifest_text)["clean_recursive_source"])

    def test_reject_missing_unknown_extra_or_mutated_patch(self):
        for patches in ([], [self.approved_patch], [self.approved_patch, {**self.cudss_patch, "expected_text": "mutated"}], [{"path":"unknown.patch","expected_text":"x"}],
                        [self.approved_patch, self.approved_patch],
                        [{**self.approved_patch, "expected_text": self.approved_patch["expected_text"]+"x"}],
                        [{**self.approved_patch, "sha256":"forbidden"}]):
            with self.subTest(patches=patches):
                self.source["patches"] = patches
                with self.assertRaisesRegex(ValueError, "approved"):
                    self.generate()

    def test_explicit_canonical_driver_keeps_insula_contract(self):
        with patch.object(gen_plans, "registered_toolchain", side_effect=AssertionError("explicit selection")):
            plan = gen_plans.pytorch_h20_cuspy_plan(
                self.repo_root, source=self.source, source_manifest_path="native/pytorch/test-only.json",
                source_manifest_text=self.manifest_text, rootfs=self.rootfs, rootfs_identity_text=self.rootfs_text,
                compiler_identity="toolchain:gcc_13_3")
        env = plan["toolchain"]["env"]
        self.assertEqual(env["VASO_IN_INSULA"], "1")
        self.assertEqual(env["VASO_ROOTFS_BUNDLE_MANIFEST"], "/run/vaso/rootfs-bundle.json")
        self.assertEqual(env["CC"], "__requires_explicit_variant_toolchain__")

    def test_experimental_host_and_python_are_not_relabelled(self):
        with patch.object(gen_plans, "registered_toolchain", side_effect=AssertionError("must not select canonical GCC")):
            plan = gen_plans.pytorch_h20_cuspy_plan(
                self.repo_root, source=self.source, source_manifest_path="native/pytorch/test-only.json",
                source_manifest_text=self.manifest_text, rootfs=self.rootfs, rootfs_identity_text=self.rootfs_text,
                compiler_identity="experimental:gcc@12.2.0", python_version="3.13.18")
        self.assertEqual(plan["toolchain"]["cc"], "experimental:gcc@12.2.0")
        self.assertEqual(plan["toolchain"]["python"], "python_3_13_18_variant")

    def test_real_schema_and_phase_resolver_accept_debian_layout(self):
        import plan_executor
        import subprocess
        env={k:"" for k in ("CC","CXX","CFLAGS","CXXFLAGS","CUDAFLAGS","NVCC_FLAGS")}
        with patch.object(gen_plans, "registered_toolchain", return_value=("gcc13",env)):
            plan=self.generate()
        self.assertEqual(gen_plans.validate_plan(plan, gen_plans.schema_v1()), [])
        values={k:"/fixture/"+k for k in ("work","src","out","wheel","source_archive")}
        values.update(jobs="1",seed="fixture",source_date_epoch="0",prefix_map_flags="",cuda_prefix_map_flags="")
        deps={key:"/declared/"+key for key in plan["deps"]}
        for phase in ("configure","build","install"):
            argv=plan_executor.resolve_argv(plan[phase],values,deps,
                {"main":Path("/fixture/source.tar")},{"main":Path("/fixture/src")},None)
            self.assertNotIn("$"+"{VASO_",argv[2])
            checked=subprocess.run(["bash","-n"],input=argv[2],text=True,capture_output=True)
            self.assertEqual(checked.returncode,0,checked.stderr)
        self.assertIn("printenv VASO_CUDNN_INCLUDE_DIR",plan["configure"]["argv"][2])

    def test_h20_preserves_full_declared_dependency_graph(self):
        with patch.object(gen_plans, "registered_toolchain", return_value=("gcc13",{})):
            plan = self.generate()
        expected = gen_plans.PACKAGE_GENERATORS["py-torch"]["deps"]
        self.assertEqual(set(plan["deps"]), set(expected))
        self.assertEqual(len(plan["deps"]), 36)
        for key in ("openblas", "protobuf", "openmpi", "cusparselt", "cudss",
                    "nvshmem", "cpuinfo", "py-scikit-build-core", "zstd"):
            self.assertIn(key, plan["deps"])
        command = plan["configure"]["argv"][2]
        self.assertIn("export USE_CUSPARSELT=1", command)
        self.assertIn("export USE_CUDSS=1", command)
        self.assertIn("export USE_NVSHMEM=1", command)

    def test_reject_cuda130(self):
        self.rootfs["components"]["cuda_toolkit"]["version"]="13.0"
        self.rootfs_text=json.dumps(self.rootfs)
        with self.assertRaisesRegex(ValueError,"13.3"):
            self.generate()
    def test_reject_modified_source(self):
        d=json.loads(self.manifest_text); d["clean_recursive_source"]=False; self.manifest_text=json.dumps(d)
        with self.assertRaisesRegex(ValueError,"clean"):
            self.generate()
    def test_reject_archive_manifest_mismatch(self):
        self.source["sha256"]="b"*64
        with self.assertRaisesRegex(ValueError,"archive"):
            self.generate()
    def test_reject_identity_mismatch(self):
        self.rootfs_text="{}"
        with self.assertRaisesRegex(ValueError,"identity"):
            self.generate()
    def test_reject_unsafe_manifest_path(self):
        with self.assertRaisesRegex(ValueError,"relative"):
            gen_plans.pytorch_h20_cuspy_plan(Path("."),source=self.source,
                source_manifest_path="../bad",source_manifest_text=self.manifest_text,
                rootfs=self.rootfs,rootfs_identity_text=self.rootfs_text)

if __name__ == "__main__": unittest.main()
