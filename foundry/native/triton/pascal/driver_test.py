import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("pascal_driver", Path(__file__).with_name("driver.py"))
d = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(d)


class PlanTests(unittest.TestCase):
    def test_scoped_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "rootfs").mkdir()
            (p / "estate/vaso").mkdir(parents=True)
            config = {"rootfs": str(p / "rootfs"), "estate_root": str(p / "estate"),
                      "gpu_lease_estate": str(p / "estate"),
                      "inputs_dir": "inputs/triton", "work_dir": "build/triton",
                      "torch_install": "build/torch/installed", "vendor_tools": "inputs/vendor"}
            site = p / "site.json"
            site.write_text(json.dumps(config))
            s = d.Site(site)
            req = s.build_request(1)
            self.assertEqual(req["workdir"], "/vaso/build/triton/source")
            self.assertEqual(req["environment"]["TRITON_PTXAS_PATH"], "/opt/vaso/cuda/bin/ptxas")
            self.assertEqual(req["environment"]["TRITON_OFFLINE_BUILD"], "1")
            self.assertIn("--no-index", req["argv"])
            self.assertEqual(req["environment"]["CC"], "gcc")
            self.assertEqual(req["environment"]["C_INCLUDE_PATH"], "/opt/vaso/cuda/include")
            self.assertNotIn("TRITON_CODEGEN_BACKENDS=nvidia", req["environment"]["TRITON_APPEND_CMAKE_ARGS"])
            with self.assertRaises(ValueError):
                s.scoped("../outside")
            with self.assertRaises(ValueError):
                s.scoped("/tmp")
            config["work_dir"] = "inputs/triton/build"
            site.write_text(json.dumps(config))
            with self.assertRaises(ValueError):
                d.Site(site)


if __name__ == "__main__":
    unittest.main()
