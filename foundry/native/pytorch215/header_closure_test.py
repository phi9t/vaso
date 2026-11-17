from __future__ import annotations
import copy, hashlib, importlib.util, json, tempfile, unittest
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('source_build',HERE/'source_build.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
spec2=importlib.util.spec_from_file_location('recover_headers',HERE/'recover_headers.py');r=importlib.util.module_from_spec(spec2);spec2.loader.exec_module(r)
class HeaderClosureTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.profile=json.loads((HERE/'profiles/cu126-sm61-r3.json').read_text())
  self.profile['required_toolchain_files']=[]
  self.profile['required_header_trees']=[{'provider':'nccl','root':'opt/vaso/cuda/include','files':{'nccl_device/coop.h':hashlib.sha256(b'header').hexdigest()}}]
  self.pp=self.root/'profile.json';self.pp.write_text(json.dumps(self.profile))
  site={'estate_root':str(self.root/'estate'),'rootfs':str(self.root/'rootfs'),'source_archive':str(self.root/'source.tar.zst')}
  self.site=self.root/'site.json';self.site.write_text(json.dumps(site));self.b=m.SourceBuild(self.site,self.pp);self.b.archive.write_bytes(b'archive')
  for n in ['opt/vaso/build-manifest.json','opt/vaso/cuda/bin/nvcc','opt/vaso/cmake/bin/cmake','opt/vaso/python-deps/bin/ninja','usr/bin/g++']:
   f=self.b.rootfs/n;f.parent.mkdir(parents=True,exist_ok=True);f.write_text('{}')
 def tearDown(self):self.tmp.cleanup()
 def header(self):
  f=self.b.rootfs/'opt/vaso/cuda/include/nccl_device/coop.h';f.parent.mkdir(parents=True,exist_ok=True);return f
 def test_reject_flat_incomplete_provider(self):
  with self.assertRaisesRegex(FileNotFoundError,'nccl.*coop.h'):self.b.inputs()
 def test_accept_exact_nested_header(self):
  self.header().write_bytes(b'header');self.assertIn('rootfs_manifest_text',self.b.inputs())
 def test_reject_corrupted_header(self):
  self.header().write_bytes(b'wrong')
  with self.assertRaisesRegex(ValueError,'digest mismatch'):self.b.inputs()
 def test_reject_external_header_symlink(self):
  outside=self.root/'outside.h';outside.write_bytes(b'header');self.header().symlink_to(outside)
  with self.assertRaisesRegex(ValueError,'escapes frozen rootfs'):self.b.inputs()
 def test_reject_parent_escape(self):
  self.b.profile['required_header_trees'][0]['files']={'../../../escape':'0'*64}
  with self.assertRaisesRegex(ValueError,'Unsafe provider header'):self.b.inputs()
 def test_r4_pins_complete_nccl_tree(self):
  p=m.read_profile(HERE/'profiles/cu126-sm61-r4.json');files=p['required_header_trees'][0]['files']
  self.assertEqual(len(files),44);self.assertIn('nccl_device/coop.h',files);self.assertIn('nccl_device/impl/core__types.h',files)
 def test_r4_pins_profiler_api_developer_headers(self):
  p=m.read_profile(HERE/'profiles/cu126-sm61-r4.json')
  needed={
   'opt/vaso/cuda/include/cudaProfiler.h',
   'opt/vaso/cuda/include/cuda_profiler_api.h',
   'opt/vaso/cuda/targets/x86_64-linux/include/cudaProfiler.h',
   'opt/vaso/cuda/targets/x86_64-linux/include/cuda_profiler_api.h',
  }
  self.assertTrue(needed.issubset(set(p['required_toolchain_files'])))
 def test_recovery_rejects_changed_compiler(self):
  old={'file_hashes':{'usr/bin/gcc':{'sha256':'a','bytes':1}}};new={'file_hashes':{'usr/bin/gcc':{'sha256':'b','bytes':1}}}
  with self.assertRaisesRegex(ValueError,'Existing rootfs inputs changed'):r.additive_headers(old,new)
 def test_recovery_rejects_removed_file(self):
  with self.assertRaisesRegex(ValueError,'Existing rootfs inputs changed'):r.additive_headers({'file_hashes':{'a':{'sha256':'1'}}},{'file_hashes':{}})
 def test_recovery_rejects_non_header_addition(self):
  with self.assertRaisesRegex(ValueError,'only additive'):r.additive_headers({'file_hashes':{}},{'file_hashes':{'usr/bin/tool':{}}})
 def test_recovery_accepts_additive_nccl_header(self):
  p='opt/vaso/cuda/include/nccl_device/coop.h';self.assertEqual(r.additive_headers({'file_hashes':{}},{'file_hashes':{p:{'sha256':'a','bytes':2}}}),[p])
if __name__=='__main__':unittest.main(verbosity=2)
