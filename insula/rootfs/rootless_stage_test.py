import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rootless_stage as runner


class RootlessStageTest(unittest.TestCase):
    def setUp(self):
        self.lock = json.loads((HERE / "cuda_ecosystem.lock.json").read_text())

    def test_llvm_reuses_canonical_flags_without_cache_hash(self):
        with patch.object(runner.llvm_rootfs_cache, "cache_key", side_effect=AssertionError("no hash")):
            base, script = runner.stage_script(self.lock, "llvm", "cu130", 256)
        self.assertEqual(base, self.lock["components"]["llvm"]["build"]["base_image"]["reference"])
        self.assertIn("--parallel 64", script)
        self.assertIn("LLVM_ENABLE_PROJECTS", script)
        self.assertIn("AMDGPU", script)
        self.assertNotIn("rm -rf /build/src", script)
        subprocess.run(["bash", "-n"], input=script, text=True, check=True)

    def test_cuda_keeps_full_component_installer_and_resolved_snapshot(self):
        base, script = runner.stage_script(self.lock, "cuda", "cu130", 32)
        self.assertEqual(base, self.lock["lines"]["cu130"]["base_image"]["reference"])
        self.assertIn("snapshot.ubuntu.com", script)
        self.assertIn("/recipe/install_cuda_ecosystem.py", script)
        self.assertIn("/recipe/collect_cuda_versions.py", script)
        subprocess.run(["bash", "-n"], input=script, text=True, check=True)

    def test_docker_path_copies_shared_installers(self):
        script = (HERE / "build_rootfs.sh").read_text()
        for name in ("install_cuda_ecosystem.py", "collect_cuda_versions.py"):
            self.assertIn('cp "$ROOTFS_DIR/' + name + '" "$BUILD_CTX/' + name + '"', script)
            compile((HERE / name).read_text(), name, "exec")

    def test_namespace_is_local_root_with_shared_network_and_explicit_mounts(self):
        argv = runner.bwrap_argv(Path("/private/root"), Path("/private/work"), Path("/private/recipe"), "cu130", "bwrap", 16)
        self.assertIn("--unshare-user", argv)
        self.assertEqual(argv[argv.index("VASO_BUILD_JOBS") + 1], "16")
        self.assertEqual(argv[argv.index("--uid") + 1], "0")
        self.assertNotIn("--unshare-net", argv)
        self.assertNotIn("--dev-bind", argv)
        self.assertEqual(argv[-2:], ["/bin/bash", "/recipe/stage.sh"])

    def test_nccl_uses_explicit_budget(self):
        import ast
        source = (HERE / "install_cuda_ecosystem.py").read_text()
        tree = ast.parse(source)
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "install_nccl")
        selected = ast.get_source_segment(source, function)
        self.assertIn('os.environ.get("VASO_BUILD_JOBS"', selected)
        self.assertIn('f"-j{jobs}"', selected)
        self.assertIn('int(jobs) < 1', selected)

    def test_unresolved_lock_rejected(self):
        self.lock["lines"]["cu130"]["apt"]["unresolved"] = True
        with self.assertRaisesRegex(ValueError, "resolved APT"):
            runner.stage_script(self.lock, "cuda", "cu130", 32)

    def test_wrong_base_rejected_before_execute(self):
        with self.assertRaisesRegex(ValueError, "base reference"):
            runner.main(["--stage", "llvm", "--line", "cu130", "--rootfs", "/private/root",
                         "--work", "/private/work", "--base-reference", "incorrect"])


if __name__ == "__main__":
    unittest.main()
