import json
from pathlib import Path
import unittest
from cuspy_admission import validate_evidence

class AdmissionTest(unittest.TestCase):
    def setUp(self):
        self.p = json.loads((Path(__file__).parent/'variants/torch215-cu133-sm90-cuspy.json').read_text())
        self.e = dict(torch_commit=self.p['torch']['commit'], python_abi='cp313', cuda_toolkit='13.3', cuda_arch_list='9.0', cupti_api=130300, cupti_build_runtime_match=True, native_cuspy=True, generated_cupti_stubs=True, cupti_python_import=True, numpy_import=True, nccl_header_closure=True, driver_runtime_compatible=True, cuspy_gpu_trace_passed=True, cuda_event_trace_passed=True)
    def test_complete_evidence(self):
        self.assertEqual(validate_evidence(self.p, self.e), [])
    def test_each_missing_field_fails_closed(self):
        for field in self.e:
            with self.subTest(field=field):
                e=self.e.copy(); e.pop(field)
                self.assertTrue(validate_evidence(self.p,e))
    def test_cuda13_without_recent_cupti_is_not_cuspy(self):
        self.e['cupti_api']=130000
        self.assertIn('CUPTI 13.3 or newer required', validate_evidence(self.p,self.e))
    def test_wheel_is_not_runtime_qualification(self):
        self.e['cuspy_gpu_trace_passed']=False
        self.assertTrue(validate_evidence(self.p,self.e))
    def test_profile_does_not_claim_build_wiring(self):
        self.assertEqual(self.p['status'],'planned-not-build-wired')
        self.assertFalse(self.p['runtime_qualified'])

if __name__ == '__main__': unittest.main()
