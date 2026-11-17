from __future__ import annotations
import importlib.util,json,tempfile,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('provider_headers',Path(__file__).with_name('provider_headers.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class ProviderHeaderTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.source=self.root/'include';self.source.mkdir();(self.source/'nccl_device').mkdir()
  (self.source/'nccl_device.h').write_text('#include "nccl_device/coop.h"\n');(self.source/'nccl_device/coop.h').write_text('/* pinned */\n')
  self.dest=self.root/'rootfs';self.dest.mkdir();self.manifest=m.source_manifest(self.source)
 def tearDown(self):self.tmp.cleanup()
 def apply(self):return m.materialize(self.source,self.dest,'opt/vaso/cuda/include',self.manifest)
 def test_nested_tree_is_materialized(self):
  r=self.apply();self.assertEqual(r['file_count'],2);self.assertTrue((self.dest/'opt/vaso/cuda/include/nccl_device/coop.h').is_file())
 def test_repeat_is_idempotent(self):
  self.apply();self.assertEqual(self.apply()['new_files'],0)
 def test_mismatch_prevents_all_writes(self):
  (self.source/'nccl_device/coop.h').write_text('changed')
  with self.assertRaisesRegex(ValueError,'closure'):self.apply()
  self.assertEqual(list(self.dest.iterdir()),[])
 def test_collision_preserves_existing_file(self):
  f=self.dest/'opt/vaso/cuda/include/nccl_device/coop.h';f.parent.mkdir(parents=True);f.write_text('other provider')
  with self.assertRaisesRegex(ValueError,'collision'):self.apply()
  self.assertEqual(f.read_text(),'other provider');self.assertFalse((f.parent.parent/'nccl_device.h').exists())
 def test_sealed_rootfs_is_immutable(self):
  f=self.dest/'opt/vaso/build-manifest.json';f.parent.mkdir(parents=True);f.write_text('{}')
  with self.assertRaisesRegex(ValueError,'sealed rootfs'):self.apply()
 def test_symlink_source_cannot_escape(self):
  outside=self.root/'external.h';outside.write_text('external');(self.source/'escape.h').symlink_to(outside)
  with self.assertRaisesRegex(ValueError,'escapes provider'):m.source_manifest(self.source)
 def test_destination_parent_escape_rejected(self):
  with self.assertRaisesRegex(ValueError,'Unsafe'):m.materialize(self.source,self.dest,'../escape',self.manifest)
if __name__=='__main__':unittest.main(verbosity=2)
