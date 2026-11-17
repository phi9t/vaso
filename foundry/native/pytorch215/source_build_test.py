from __future__ import annotations
import copy, hashlib, importlib.util, json, tempfile, unittest
from unittest.mock import patch
from pathlib import Path
spec=importlib.util.spec_from_file_location('source_build',Path(__file__).with_name('source_build.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class SourceBuildTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.profile=json.loads((Path(__file__).parent/'profiles/cu126-sm61.json').read_text())
  self.pp=self.root/'profile.json';self.pp.write_text(json.dumps(self.profile))
 def tearDown(self):self.tmp.cleanup()
 def test_profile_sm61_cuda126(self):
  p=m.read_profile(self.pp);self.assertEqual(p['toolchain']['cuda_arch_list'],'6.1');self.assertEqual(p['environment']['USE_SYSTEM_NCCL'],'1')
 def test_reject_cuda13_for_sm61(self):
  self.profile['toolchain']['cuda_major_minor']='13.3';self.pp.write_text(json.dumps(self.profile))
  with self.assertRaisesRegex(ValueError,'CUDA12'):m.read_profile(self.pp)
 def test_reject_path_name(self):
  self.profile['name']='../../outside';self.pp.write_text(json.dumps(self.profile))
  with self.assertRaisesRegex(ValueError,'Unsafe'):m.read_profile(self.pp)
 def test_reject_implicit_cuspy(self):
  self.profile['toolchain']['profiler']='cuspy';self.pp.write_text(json.dumps(self.profile))
  with self.assertRaisesRegex(ValueError,'Cuspy'):m.read_profile(self.pp)
 def test_atomic_json_replaces(self):
  f=self.root/'x/result.json';m.write_json(f,{'a':1});m.write_json(f,{'a':2});self.assertEqual(json.loads(f.read_text()),{'a':2});self.assertEqual(list(f.parent.iterdir()),[f])
 def make_build(self):
  site={'estate_root':str(self.root/'estate'),'rootfs':str(self.root/'rootfs'),'source_archive':str(self.root/'source.tar.zst')}
  f=self.root/'site.json';f.write_text(json.dumps(site));return m.SourceBuild(f,self.pp)
 def test_status_not_started(self):self.assertEqual(self.make_build().status()['state'],'not_started')
 def test_missing_inputs_fail_before_process(self):
  b=self.make_build()
  with self.assertRaisesRegex(FileNotFoundError,'Required build input'):b.inputs()
 def test_no_gpu_required_to_compile(self):
  b=self.make_build();b.archive.write_bytes(b'source')
  for n in ['opt/vaso/build-manifest.json','opt/vaso/cuda/bin/nvcc','opt/vaso/cmake/bin/cmake','opt/vaso/python-deps/bin/ninja','usr/bin/g++']:
   p=b.rootfs/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{}')
  with patch.object(m,'digest',side_effect=AssertionError('metadata hashing forbidden')):
   self.assertIn('rootfs_manifest_text',b.inputs())
 def test_previous_identity_mismatch_rejected(self):
  b=self.make_build();b.archive.write_bytes(b'source')
  for n in ['opt/vaso/build-manifest.json','opt/vaso/cuda/bin/nvcc','opt/vaso/cmake/bin/cmake','opt/vaso/python-deps/bin/ninja','usr/bin/g++']:
   p=b.rootfs/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{}')
  b.work.mkdir(parents=True);(b.work/'inputs.json').write_text('{}')
  with self.assertRaisesRegex(RuntimeError,'identity changed'):b.prepare()
 def test_declared_edit_is_literal_checked(self):
  p=self.root/'file.cpp';p.write_text('before\n')
  e={'path':'file.cpp','old':'before','new':'after','before_text':'before\n','after_text':'after\n'}
  m.apply_source_edits(self.root,[e]);self.assertEqual(p.read_text(),'after\n')
  with self.assertRaisesRegex(ValueError,'preimage'):m.apply_source_edits(self.root,[e])
 def test_edit_may_not_escape_source(self):
  with self.assertRaisesRegex(ValueError,'Unsafe'):m.apply_source_edits(self.root,[{'path':'../x'}])
 def test_bad_postimage_preserves_input(self):
  p=self.root/'file.cpp';p.write_text('before')
  e={'path':'file.cpp','old':'before','new':'after','before_text':'before','after_text':'wrong'}
  with self.assertRaisesRegex(ValueError,'postimage'):m.apply_source_edits(self.root,[e])
  self.assertEqual(p.read_text(),'before')
 def test_required_header_checked_before_compile(self):
  self.profile['required_toolchain_files']=['opt/vaso/cuda/include/nvml.h']
  self.pp.write_text(json.dumps(self.profile));b=self.make_build();b.archive.write_bytes(b'source')
  for n in ['opt/vaso/build-manifest.json','opt/vaso/cuda/bin/nvcc','opt/vaso/cmake/bin/cmake','opt/vaso/python-deps/bin/ninja','usr/bin/g++']:
   p=b.rootfs/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{}')
  with self.assertRaisesRegex(FileNotFoundError,'nvml.h'):b.inputs()
  f=b.rootfs/'opt/vaso/cuda/include/nvml.h';f.parent.mkdir(parents=True,exist_ok=True);f.write_text('/* pinned nvml */')
  with patch.object(m,'digest',side_effect=AssertionError('metadata hashing forbidden')):
   self.assertIn('rootfs_manifest_text',b.inputs())
if __name__=='__main__':unittest.main(verbosity=2)
