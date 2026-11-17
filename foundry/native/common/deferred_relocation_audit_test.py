from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import plan_executor as executor


class DeferredRelocationAuditTest(unittest.TestCase):
    def test_deferred_does_not_scan_prefix_or_wheel(self):
        with patch.object(executor, "scan_output_for_path_leaks", side_effect=AssertionError("no recursive scan")), \
             patch.object(executor, "scan_file_for_path_leaks", side_effect=AssertionError("no wheel scan")):
            executor.audit_output_relocation(Path("/not-read"), Path("/not-read.whl"), {"source"}, deferred=True)

    def test_default_still_scans_and_rejects_leaks(self):
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "fixture.whl"
            wheel.touch()
            with patch.object(executor, "scan_output_for_path_leaks", return_value=[]) as prefix_scan, \
                 patch.object(executor, "scan_file_for_path_leaks", return_value=["fixture leak"]) as wheel_scan:
                with self.assertRaises(SystemExit):
                    executor.audit_output_relocation(Path(directory), wheel, {"source"})
                prefix_scan.assert_called_once()
                wheel_scan.assert_called_once()

    def test_pkg_config_rewrite_is_retained_before_optional_audit(self):
        import inspect
        source = inspect.getsource(executor.execute_plan)
        self.assertIn("rewrite_pkg_config_files(prefix)", source)
        self.assertLess(source.index("rewrite_pkg_config_files(prefix)"),
                        source.index("audit_output_relocation(prefix"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pc = root / "lib/pkgconfig/example.pc"
            pc.parent.mkdir(parents=True)
            pc.write_text("prefix=/build/private\nlibdir=\u0024{prefix}/lib\n")
            executor.rewrite_pkg_config_files(root)
            self.assertIn("prefix=\u0024{pcfiledir}/../..", pc.read_text())


if __name__ == "__main__":
    unittest.main()
