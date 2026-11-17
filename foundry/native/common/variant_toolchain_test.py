import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import plan_executor as executor

class VariantToolchainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.contract = {"schema":1,"host_os":"linux","host_arch":"x86_64","cuda_version":"13.3",
                         "cuda_arch_list":"9.0","gpu_archs":["sm_90"],"python_abi":"cp313",
                         "python_version":"3.13.18","compiler_identity":"experimental:gcc@12.2.0",
                         "tools":{name:str(self.root/"bin"/name) for name in ("cc","cxx","nvcc","python")}}
        for name in ("cuda","python","nccl","cudnn"):
            marker=self.root/(name+".prefix"); marker.write_text(str(self.root))
            self.contract[name+"_prefix_file"]=str(marker)
        self.contract["library_prefix_files"] = {n:self.contract[n+"_prefix_file"] for n in ("nccl","cudnn")}
        site_marker=self.root/"site.prefix";site_marker.write_text(str(self.root/"site-packages"))
        self.contract["python_site_packages_file"]=str(site_marker)
        self.path=self.root/"contract.json"
        self.plan={"gpu_archs":["sm_90"],"toolchain":{"cc":"experimental:gcc@12.2.0",
                   "cxx":"experimental:gcc@12.2.0","python":"python_3_13_18_variant",
                   "cuda":"rootfs:cuda@13.3.0","env":{}}}
    def bind(self):
        self.path.write_text(json.dumps(self.contract))
        return executor.bind_variant_toolchain(self.plan,self.path,runtime_version=(3,13,18))
    def test_b200_is_explicit_and_consistent(self):
        self.contract["cuda_arch_list"] = "10.0"
        self.contract["gpu_archs"] = ["sm_100"]
        self.plan["gpu_archs"] = ["sm_100"]
        self.bind()
        self.contract["gpu_archs"] = ["sm_90"]
        with self.assertRaisesRegex(SystemExit, "consistently"):
            self.bind()

    def test_binds_real_declared_tools_without_hashes(self):
        with patch.object(executor,"sha256_file",side_effect=AssertionError("hash forbidden")):
            selected = self.bind()
        self.assertEqual(selected, {n:str(self.root) for n in ("cuda","python","nccl","cudnn")})
        self.assertEqual(self.plan["toolchain"]["env"]["PYTHONPATH"],str(self.root/"site-packages"))
        self.assertEqual(self.plan["toolchain"]["env"]["CC"],str(self.root/"bin/cc"))
        self.assertEqual(self.plan["toolchain"]["env"]["CUDACXX"],str(self.root/"bin/nvcc"))
    def test_relative_execroot_paths_resolve_before_phase_chdir(self):
        import os
        before = Path.cwd()
        try:
            os.chdir(self.root)
            self.contract["tools"]={n:"bin/"+n for n in ("cc","cxx","nvcc","python")}
            for n in ("cuda","python"):
                self.contract[n+"_prefix_file"]=n+".prefix"
            self.contract["library_prefix_files"]={n:n+".prefix" for n in ("nccl","cudnn")}
            selected=self.bind()
        finally:
            os.chdir(before)
        self.assertEqual(self.plan["toolchain"]["env"]["CC"],str(self.root/"bin/cc"))
        self.assertEqual(self.plan["toolchain"]["env"]["VASO_CUDNN_INCLUDE_DIR"],str(self.root/"include/x86_64-linux-gnu"))
        self.assertEqual(selected["nccl"],str(self.root))

    def codegen_fixture(self):
        paths = {"cupti_header":"cupti/include/cupti_activity.h",
                 "cupti_version_header":"cupti/include/cupti_version.h",
                 "libclang":"llvm/lib/libclang.so",
                 "clang_cindex":"llvm/python/clang/cindex.py",
                 "clang_init":"llvm/python/clang/__init__.py"}
        for value in paths.values():
            path = self.root / value
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture")
        self.contract["cuspy_codegen"] = {**{k:str(self.root/v) for k,v in paths.items()},
                                           "clang_source_identity":"fixture-common-source"}
        return paths

    def test_codegen_resolves_before_chdir_without_ambient_fallback(self):
        import os
        paths = self.codegen_fixture()
        self.contract["cuspy_codegen"].update(paths)
        before = Path.cwd()
        try:
            os.chdir(self.root)
            with patch.dict(os.environ, {"LIBCLANG_PATH":"/ambient/libclang.so",
                                         "CUPTI_INCLUDE_DIR":"/ambient/headers",
                                         "PYTHONPATH":"/ambient/python"}), \
                 patch.object(executor,"sha256_file",side_effect=AssertionError("hash forbidden")):
                self.bind()
            os.chdir(self.root / "llvm")
            env = self.plan["toolchain"]["env"]
            self.assertEqual(env["LIBCLANG_PATH"],str(self.root/"llvm/lib/libclang.so"))
            self.assertEqual(env["CUPTI_INCLUDE_DIR"],str(self.root/"cupti/include"))
            self.assertEqual(env["PYTHONPATH"],str(self.root/"llvm/python")+os.pathsep+str(self.root/"site-packages"))
            self.assertEqual(env["VASO_VARIANT_PYTHONPATH"],env["PYTHONPATH"])
        finally:
            os.chdir(before)

    def test_codegen_rejects_library_directory_or_missing_declared_input(self):
        self.codegen_fixture()
        self.contract["cuspy_codegen"]["libclang"] = str(self.root/"llvm/lib")
        with self.assertRaisesRegex(SystemExit,"must be a file"): self.bind()
        self.codegen_fixture()
        del self.contract["cuspy_codegen"]["libclang"]
        with self.assertRaisesRegex(SystemExit,"missing declared libclang"): self.bind()

    def test_codegen_rejects_unpaired_headers_bindings_and_missing_identity(self):
        for field,value in (("cupti_version_header","llvm/lib/libclang.so"),
                            ("clang_init","cupti/include/cupti_version.h"),
                            ("clang_source_identity","")):
            self.codegen_fixture()
            self.contract["cuspy_codegen"][field] = str(self.root/value) if value else ""
            with self.subTest(field=field), self.assertRaises(SystemExit):
                self.bind()

    def test_codegen_keeps_source_python31313_viable(self):
        self.codegen_fixture()
        self.contract["python_version"]="3.13.13"
        self.plan["toolchain"]["python"]="python_313_native"
        self.path.write_text(json.dumps(self.contract))
        executor.bind_variant_toolchain(self.plan,self.path,runtime_version=(3,13,13))
        self.assertIn("LIBCLANG_PATH",self.plan["toolchain"]["env"])

    def test_rejects_canonical_relabel(self):
        self.plan["toolchain"]["cc"]="toolchain:vaso-rootfs-gcc-13.3"
        with self.assertRaisesRegex(SystemExit,"identity"): self.bind()
    def test_rejects_wrong_gpu_arch(self):
        self.plan["gpu_archs"]=["sm_100"]
        with self.assertRaisesRegex(SystemExit,"SM90"): self.bind()
    def test_rejects_interpreter_patch_mismatch(self):
        self.contract["python_version"]="3.13.13"
        with self.assertRaisesRegex(SystemExit,"patch version"): self.bind()
    def test_rejects_ambient_nvcc(self):
        self.contract["tools"]["nvcc"]="/usr/local/cuda/bin/nvcc"
        with self.assertRaisesRegex(SystemExit,"outside"): self.bind()

if __name__=="__main__": unittest.main()
