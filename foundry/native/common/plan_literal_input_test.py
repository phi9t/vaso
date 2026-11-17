"""Small metadata integrity checks do not require another digest pass."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import plan_executor as ex
from plan_schema import schema_v1, _validate_schema

class LiteralInputTest(unittest.TestCase):
    def test_exact_bytes_without_hash(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'manifest.json'; p.write_bytes(b'{"v":1}\n')
            with patch.object(ex,'sha256_file',side_effect=AssertionError('tiny-file hash')):
                ex.verify_file_identity(p, {'expected_text':'{"v":1}\n'}, 'manifest')
    def test_extra_input_literal_binding(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'input';p.write_bytes(b'abc')
            with patch.object(ex,'sha256_file',side_effect=AssertionError('tiny-file hash')):
                ex.verify_extra_inputs({'inputs':[{'path':'metadata','expected_text':'abc'}]}, [f'metadata={p}'])
    def test_patch_literal_binding(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'edit.patch';p.write_text('patch contents')
            obj={'path':'edit.patch','expected_text':'patch contents'}
            with patch.object(ex,'sha256_file',side_effect=AssertionError('tiny-file hash')), patch.object(ex.subprocess,'run') as run:
                run.return_value.returncode=0
                ex.apply_source_patches({'patches':[obj]},Path(d),{'edit.patch':p})
                run.assert_called_once()
                p.write_text('mutated')
                with self.assertRaises(SystemExit):
                    ex.apply_source_patches({'patches':[obj]},Path(d),{'edit.patch':p})
                run.assert_called_once()
    def test_nested_manifest_literal_binding(self):
        with tempfile.TemporaryDirectory() as d:
            text='{"schema_version":1,"entries":[]}'
            p=Path(d)/'manifest.json';p.write_text(text)
            obj={'label':'test','expected_text':text}
            plan={'nested_bazel':{'archives':[], 'distdir_manifest':obj,'repository_cache_manifest':obj}}
            with patch.object(ex,'sha256_file',side_effect=AssertionError('tiny-file hash')):
                ex.verify_nested_cache_manifests(plan,[f'distdir={p}',f'repository_cache={p}'])
                p.write_text('{}')
                with self.assertRaises(SystemExit):
                    ex.verify_nested_cache_manifests(plan,[f'distdir={p}',f'repository_cache={p}'])
    def test_mutation_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'manifest.json'; p.write_text('wrong')
            with self.assertRaises(SystemExit):
                ex.verify_file_identity(p, {'expected_text':'right'}, 'manifest')
    def test_missing_or_ambiguous_identity_rejected(self):
        for obj in ({}, {'sha256':'a'*64}, {'expected_text':'x','sha256':'a'*64}, {'expected_text':None}):
            with self.subTest(obj=obj), self.assertRaises(SystemExit):
                ex.validate_file_identity(obj,'test')
    def test_schema_rejects_ambiguous_identity(self):
        schema=schema_v1()
        spec=schema['$defs']['input']
        for value in ({'path':'x'}, {'path':'x','sha256':'a','expected_text':'b'}):
            self.assertTrue(_validate_schema(value,spec,schema,'input'))
        self.assertFalse(_validate_schema({'path':'x','expected_text':'b'},spec,schema,'input'))
    def test_empty_text_supported(self):
        ex.validate_file_identity({'expected_text':''}, 'test')
    def test_schema_requires_exactly_one_identity(self):
        s=schema_v1()
        for obj in (s['$defs']['input'],s['properties']['toolchain']['properties']['rootfs_identity']):
            self.assertIn('expected_text',obj['properties'])
            self.assertNotIn('sha256',obj['properties'])

if __name__=='__main__': unittest.main()
