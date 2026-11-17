"""Bounded declared graph tests; never evaluate repository-rule builds."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class B200TargetTest(unittest.TestCase):
    def test_preserves_every_non_sdk_direct_source_prefix(self):
        legacy = (ROOT / 'native/pytorch/BUILD.bazel').read_text()
        legacy = legacy.split('pytorch_action_prefix(', 1)[1].split('release_wheel_action(', 1)[0]
        variant = (ROOT / 'native/pytorch/b200/BUILD.bazel').read_text().split('pytorch_variant_action(', 1)[1]
        pattern = r'(\w+_prefix_file) = "([^"]+)"'
        old, new = dict(re.findall(pattern, legacy)), dict(re.findall(pattern, variant))
        self.assertEqual(len(old), 36)
        self.assertEqual(old.keys(), new.keys())
        sdk = {'cuda','cudnn','nccl','cusparselt','cudss','nvshmem'}
        for name, label in old.items():
            if name.removesuffix('_prefix_file') in sdk:
                self.assertTrue(new[name].startswith('@b200_cuspy_site//:'))
            else:
                self.assertEqual(new[name], label)
        self.assertIn('@python_313_native//:prefix_path.txt', variant)

    def test_matching_llvm_source_is_existing_lock_pin(self):
        source = json.loads((ROOT.parent / 'insula/rootfs/cuda_ecosystem.lock.json').read_text())['components']['llvm']['source']
        module = (ROOT / 'cuspy.MODULE.bazel').read_text()
        self.assertIn(source['sha256'], module)
        self.assertIn(source['url'], module)
        self.assertIn(source['strip_prefix'] + '/clang/bindings/python', module)

    def test_explicit_b200_arch_and_codegen(self):
        target = (ROOT / 'native/pytorch/b200/BUILD.bazel').read_text()
        self.assertIn('cuda_arch_list = "10.0"', target)
        self.assertIn('python_version = "3.13.13"', target)
        self.assertIn('enable_cuspy_codegen = True', target)
        self.assertIn('@llvm23_cuspy_bindings//:package', target)
        self.assertNotIn('libclang18', target)


if __name__ == '__main__':
    unittest.main()
