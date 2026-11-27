"""Fail-closed checks for new-wheel GPU qualification; no CUDA access required."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest

import check_gpu_wheels as check


class GPUQualificationTest(unittest.TestCase):
    def test_private_imports_reject_every_old_native_module(self):
        venv = Path("/workspace/qualification/cpu/venv")
        names = ("torch", "torch._C", "triton", "triton._C.libtriton")
        expected = {name: str(venv / "lib/python3.12/site-packages" / (name + ".so")) for name in names}
        modules = [SimpleNamespace(__name__=name, __file__=expected[name]) for name in names]
        self.assertEqual(check.private_imports(modules, venv, expected), expected)
        for index, name in enumerate(names):
            changed = list(modules)
            changed[index] = SimpleNamespace(__name__=name, __file__="/opt/vaso/pascal/python/" + name + ".so")
            with self.subTest(module=name), self.assertRaisesRegex(RuntimeError, "old capsule wheel"):
                check.private_imports(changed, venv, expected)

    def test_environment_and_plan_require_all_existing_suites(self):
        output = Path("/workspace/qualification/gpu")
        venv = Path("/workspace/qualification/cpu/venv")
        site = str(venv / "lib/python3.12/site-packages")
        env = check.environment(output, venv)
        self.assertEqual(env["PYTHONPATH"].split(":")[0], site)
        self.assertEqual(env["HF_HUB_OFFLINE"], "1")
        specs = check.suite_specs(output, venv, Path("/workspace/checkpoints/tiny-inkling2-mini"))
        self.assertEqual({s["name"] for s in specs}, {"compiler", "operators", "model", "transport",
                                                    "packed_dense", "archive_compute_0", "archive_compute_1"})
        self.assertTrue(all(Path(s["script"]).is_relative_to(check.QUALIFICATION) for s in specs))
        self.assertIn(site, specs[0]["args"])
        self.assertNotIn("--control", specs[0]["args"])
        self.assertNotIn("--case", specs[4]["args"])

    def test_compiler_rejects_control_or_missing_gpu(self):
        result = {"passed": True, "control_only": False,
                  "results": [{"gpu": gpu, "passed": True} for gpu in (0, 1) for _ in range(8)]}
        check.validate_coverage("compiler", result)
        bad = deepcopy(result)
        bad["control_only"] = True
        with self.assertRaises(ValueError):
            check.validate_coverage("compiler", bad)
        bad = deepcopy(result)
        bad["results"] = bad["results"][:8]
        with self.assertRaises(ValueError):
            check.validate_coverage("compiler", bad)

    def test_transport_requires_both_roles_and_release(self):
        result = {"passed": True, "device_allocations_released": True,
                  "roles": [{"compute": "cuda:" + str(i), "archive": "cuda:" + str(1 - i),
                             "byte_checks": 124} for i in (0, 1)]}
        check.validate_coverage("transport", result)
        result["device_allocations_released"] = False
        with self.assertRaises(ValueError):
            check.validate_coverage("transport", result)

    def test_packed_requires_full_dense_case_coverage(self):
        result = {"passed": True, "cpu_references": [{}] * 8, "eviction_runs": [{}] * 4,
                  "runs": [{"gpu": gpu, "mode": mode, "repeat": repeat, "case": case, "use_cache": cached}
                           for gpu in (0, 1) for mode in ("dense_fp32", "packed_fp32") for repeat in (0, 1)
                           for case in ("plain", "chat", "window_boundary", "batch_two") for cached in (False, True)]}
        check.validate_coverage("packed_dense", result)
        result["runs"] = [r for r in result["runs"] if r["case"] != "window_boundary"]
        with self.assertRaises(ValueError):
            check.validate_coverage("packed_dense", result)

    def test_archive_requires_both_sources_and_zero_residency(self):
        result = {"passed": True, "compute_device": "cuda:0", "archive_device": "cuda:1",
                  "closed": {"resident_bytes": 0, "archive": {"resident_bytes": 0}},
                  "runs": [{"archive_mode": mode, "use_cache": cached, "repeat": repeat}
                           for mode in ("full", "mixed") for cached in (False, True) for repeat in (0, 1)]}
        check.validate_coverage("archive_compute_0", result)
        result["closed"]["archive"]["resident_bytes"] = 16
        with self.assertRaises(ValueError):
            check.validate_coverage("archive_compute_0", result)


if __name__ == "__main__":
    unittest.main()
