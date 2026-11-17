"""Small publication fixtures: no vendor rehash, no git commits or publication."""
from collections import Counter
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
PUBLISH = runpy.run_path(str(HERE / "publish_snapshot.py"))
Rule = PUBLISH["Rule"]
Error = PUBLISH["PublishError"]

class VendorPreservationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.vendor = self.root / PUBLISH["VENDOR_CHECKSUM_TREE"] / "crate"
        self.vendor.mkdir(parents=True)
        self.source = self.vendor / "README.md"
        self.source.write_text("fixture content\n")
        self.manifest = self.vendor / ".cargo-checksum.json"
        self.manifest.write_text('{"files":{"README.md":"preserved-upstream-receipt"}}\n')

    def test_unchanged_vendor_preserved_without_hashing(self):
        original = self.manifest.read_bytes()
        with patch("hashlib.sha256",side_effect=AssertionError("hash forbidden")):
            PUBLISH["apply_publication_overlay"](self.root, [])
            PUBLISH["scrub_filenames"](self.root, [], Counter())
            PUBLISH["scrub_contents"](self.root, [], Counter())
            self.assertEqual(self.manifest.read_bytes(), original)

    def test_content_scrub_blocks_before_write_and_scan_still_finds_term(self):
        rules = [Rule(1, "literal", "fixture", "public")]
        with self.assertRaisesRegex(Error, "checksum"):
            PUBLISH["scrub_contents"](self.root, rules, Counter())
        self.assertEqual(self.source.read_text(), "fixture content\n")
        self.assertTrue(PUBLISH["scan_remaining"](self.root, rules))

    def test_vendor_filename_or_ancestor_rename_blocks(self):
        for name in ("README", "third_party"):
            with self.subTest(name=name), self.assertRaises(Error):
                PUBLISH["scrub_filenames"](self.root, [Rule(1,"literal",name,"renamed")],Counter())

    def test_symlink_scrub_blocks(self):
        link = self.vendor / "link"
        link.symlink_to("fixture-target")
        with self.assertRaises(Error):
            PUBLISH["scrub_contents"](self.root,[Rule(1,"literal","fixture","public")],Counter())
        self.assertEqual(str(link.readlink()),"fixture-target")

    def test_overlay_blocks_before_write(self):
        self.source.write_text("private_token\nkeep\n")
        with self.assertRaises(Error):
            PUBLISH["prune_reference_lines"](self.source, {"private_token"}, self.root)
        self.assertEqual(self.source.read_text(),"private_token\nkeep\n")

    def test_exclusion_guard_rejects_vendor_and_ancestors(self):
        for name in (PUBLISH["VENDOR_CHECKSUM_TREE"],"third_party",
                     PUBLISH["VENDOR_CHECKSUM_TREE"]+"/crate/README.md"):
            with self.subTest(name=name), self.assertRaises(Error):
                PUBLISH["refuse_vendor_mutation"](name)
        PUBLISH["refuse_vendor_mutation"]("docs/README.md")

    def test_overlay_symlink_alias_cannot_write_vendor(self):
        self.source.write_text("private_token\n")
        docs=self.root/"docs";docs.mkdir()
        alias=docs/"README.md";alias.symlink_to(self.source)
        with self.assertRaises(Error):
            PUBLISH["apply_publication_overlay"](self.root,["private_token"])
        self.assertEqual(self.source.read_text(),"private_token\n")

    def test_tracker_allowlist_symlink_cannot_write_vendor(self):
        self.source.write_text(".scratch/private\nkeep\n")
        alias=self.root/"foundry/tools/tracker_lint_allowlist.txt"
        alias.parent.mkdir(parents=True);alias.symlink_to(self.source)
        with self.assertRaises(Error):
            PUBLISH["prune_tracker_allowlist"](self.root)
        self.assertEqual(self.source.read_text(),".scratch/private\nkeep\n")

    def test_overlay_symlink_cannot_write_outside_root(self):
        with tempfile.TemporaryDirectory() as other:
            outside=Path(other)/"README.md";outside.write_text("private_token\n")
            alias=self.root/"README.md";alias.symlink_to(outside)
            with self.assertRaises(Error):
                PUBLISH["apply_publication_overlay"](self.root,["private_token"])
            self.assertEqual(outside.read_text(),"private_token\n")

    def test_filename_traversal_rejected(self):
        path=self.root/"rename_me";path.write_text("keep")
        for target in ("..","../outside","sub/name","sub\\name"):
            with self.subTest(target=target), self.assertRaises(Error):
                PUBLISH["scrub_filenames"](self.root,[Rule(1,"literal","rename_me",target)],Counter())
            self.assertTrue(path.exists())

    def test_export_exclusion_blocks_actual_tar_member(self):
        import io
        import tarfile
        from types import SimpleNamespace
        stream=io.BytesIO()
        name=PUBLISH["VENDOR_CHECKSUM_TREE"]+"/crate/lib.rs"
        with tarfile.open(fileobj=stream,mode="w") as archive:
            member=tarfile.TarInfo(name);member.size=1
            archive.addfile(member,io.BytesIO(b"x"))
        stream.seek(0)
        process=SimpleNamespace(stdout=stream,stderr=io.BytesIO(),wait=lambda:0)
        with tempfile.TemporaryDirectory() as out, patch("subprocess.Popen",return_value=process):
            with self.assertRaisesRegex(Error,"checksum"):
                PUBLISH["export_source_tree"](self.root,"HEAD",Path(out),[],[name])
            self.assertFalse((Path(out)/name).exists())

    def test_explicit_skip_regeneration_does_not_read_manifest_or_run_command(self):
        with patch.object(Path,"read_text",side_effect=AssertionError("no manifest read")), \
             patch("subprocess.run",side_effect=AssertionError("no generator")):
            self.assertIsNone(PUBLISH["run_regeneration_manifest"](self.root,skip=True))
        args=PUBLISH["parse_args"](["--skip-regeneration","--branch","publish/fixture"])
        self.assertTrue(args.skip_regeneration)
        self.assertFalse(PUBLISH["parse_args"]([]).skip_regeneration)

    def test_public_blob_exception_only_removes_exact_content_token(self):
        self.source.write_text("known-host another-host\n")
        rel=self.source.relative_to(self.root).as_posix()
        allowed={rel:{"token":"known-host","blob":"a"*40}}
        scan=PUBLISH["scan_host_identity"]
        with patch.dict(scan.__globals__, {"regular_git_blob":lambda *args:"a"*40}), patch("subprocess.run", side_effect=lambda *a,**k: type("Result",(),{"stdout":self.source.read_bytes()})()):
            self.assertEqual(scan(self.root,{"known-host"},allowed),[])
            self.assertTrue(scan(self.root,{"known-host","another-host"},allowed))
            self.source.write_text("known-host " + "GPU-" + "01234567-0123-0123-0123-0123456789ab\n")
            self.assertTrue(scan(self.root,{"known-host"},allowed))

    def test_changed_or_unproven_blob_is_not_exempt(self):
        self.source.write_text("known-host\n")
        rel=self.source.relative_to(self.root).as_posix()
        scan=PUBLISH["scan_host_identity"]
        for current in ("b"*40,None):
            with patch.dict(scan.__globals__, {"regular_git_blob":lambda *args:current}):
                self.assertTrue(scan(self.root,{"known-host"},{rel:{"token":"known-host","blob":"a"*40}}))

    def test_public_allowlist_requires_source_and_public_base_identity(self):
        import json
        loader=PUBLISH["load_public_vendor_allowlist"]
        rel=self.source.relative_to(self.root).as_posix()
        with tempfile.TemporaryDirectory() as external:
            file=Path(external)/"reviewed.json"
            file.write_text(json.dumps([{"path":rel,"token":"known-host","blob":"a"*40}]))
            with patch.dict(loader.__globals__, {"regular_git_blob":lambda *args:"a"*40}):
                self.assertIn(rel,loader(file,self.root,"source","origin/ultron/mainline"))
            with patch.dict(loader.__globals__, {"regular_git_blob":lambda root,ref,path:"a"*40 if ref=="source" else "b"*40}):
                with self.assertRaisesRegex(Error,"identical"):
                    loader(file,self.root,"source","origin/ultron/mainline")
            file.write_text(json.dumps([{"path":"docs/README.md","token":"known-host","blob":"a"*40}]))
            with self.assertRaisesRegex(Error,"vendor"):
                loader(file,self.root,"source","origin/ultron/mainline")

    def test_exception_scans_staged_blob_not_unstaged_alias(self):
        from types import SimpleNamespace
        self.source.write_text("known-host private-host\n")
        rel=self.source.relative_to(self.root).as_posix()
        scan=PUBLISH["scan_host_identity"]
        with patch.dict(scan.__globals__,{"regular_git_blob":lambda *a:"a"*40}), \
             patch("subprocess.run",return_value=SimpleNamespace(stdout=b"known-host\n")):
            self.assertEqual(scan(self.root,{"known-host","private-host"},
                                 {rel:{"token":"known-host","blob":"a"*40}}),[])
        # Unstaged content is not claimed clean; only the inspected staged object is.
        with patch.dict(scan.__globals__,{"regular_git_blob":lambda *a:"a"*40}), \
             patch("subprocess.run",return_value=SimpleNamespace(stdout=b"known-host private-host\n")):
            self.assertTrue(scan(self.root,{"known-host","private-host"},
                                 {rel:{"token":"known-host","blob":"a"*40}}))

    def test_git_blob_reader_uses_metadata_and_rejects_symlink(self):
        from types import SimpleNamespace
        reader=PUBLISH["regular_git_blob"]
        rel=self.source.relative_to(self.root).as_posix()
        for mode,expected in (("100644","a"*40),("120000",None)):
            response=SimpleNamespace(stdout=mode+" "+("a"*40)+" 0\t"+rel+"\0")
            with patch.dict(reader.__globals__,{"git":lambda *args:response}), \
                 patch("hashlib.sha256",side_effect=AssertionError("no hashing")):
                self.assertEqual(reader(self.root,None,rel),expected)

    def test_commit_does_not_restage_after_final_scan(self):
        from types import SimpleNamespace
        commit=PUBLISH["commit_snapshot"]
        calls=[]
        def fake_git(root,*args):
            calls.append(args)
            return SimpleNamespace(stdout="subject" if args[0]=="log" else "commit-id")
        with patch.dict(commit.__globals__,{"git":fake_git}), patch("subprocess.run"):
            commit(self.root,"subject","Fixture","fixture@example.invalid")
        self.assertFalse(any("add" in args for args in calls))

    def test_helper_fails_without_reading_or_hashing_members(self):
        helper=runpy.run_path(str(HERE/"regenerate_vendor_checksums.py"))
        with patch.object(Path,"read_bytes",side_effect=AssertionError("no member reads")), \
             patch("hashlib.sha256",side_effect=AssertionError("hash forbidden")):
            with self.assertRaisesRegex(SystemExit,"disabled"): helper["main"]()
        commands=[s for s in (HERE/"publication-regenerate.txt").read_text().splitlines()
                  if s.strip() and not s.lstrip().startswith("#")]
        self.assertFalse(any("regenerate_vendor_checksums" in s for s in commands))

if __name__=="__main__":
    unittest.main()
