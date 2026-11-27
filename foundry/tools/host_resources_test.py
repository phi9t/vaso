"""Visible cgroup fixture trees; no real host telemetry is stored in Git."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import host_resources as h

GIB = 1 << 30


class HostResourcesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="host-resources-")
        self.root = Path(self.tmp.name)
        self.mount = self.root / "cgroup"
        self.mount.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, directory, **values):
        directory.mkdir(parents=True, exist_ok=True)
        for name, value in values.items():
            (directory / name).write_text(str(value))

    def mount_line(self, point=None, root="/", version=2, controllers=""):
        point = str(point or self.mount).replace("\\", "\\134").replace(" ", "\\040")
        fs = "cgroup2" if version == 2 else "cgroup"
        return f"11 1 0:1 {root} {point} rw - {fs} cgroup rw{',' + controllers if controllers else ''}\n"

    def v2(self):
        defaults = {
            "cpu.max": "max 100000", "cpuset.cpus.effective": "0-7",
            "memory.max": "max", "memory.high": "max", "memory.current": str(GIB),
            "cpu.stat": "usage_usec 41\nnr_throttled 2\nthrottled_usec 7",
            "memory.stat": "anon 100\nfile 200", "memory.events": "high 2\noom 0\noom_kill 0",
        }
        for directory in [self.mount, self.mount/"parent", self.mount/"parent/job"]:
            self.write(directory, **defaults)
        self.write(self.mount/"parent", **{
            "cpu.max": "250000 100000", "memory.max": 12*GIB,
            "memory.high": 10*GIB, "memory.current": 9*GIB,
        })

    def observe(self, member="0::/parent/job\n", mounts=None, affinity=None, **kwargs):
        return h.inspect_visible(member, mounts or self.mount_line(),
                                 {0,1,2,3} if affinity is None else affinity,
                                 {"MemTotal":31*GIB,"MemAvailable":25*GIB}, **kwargs)

    def test_ancestor_quota_and_same_ancestor_memory_headroom(self):
        self.v2()
        result = self.observe()
        self.assertEqual(result["effective_visible_cpus"], 2.5)
        self.assertEqual(result["effective_visible_memory_limit_bytes"], 12*GIB)
        self.assertEqual(result["effective_visible_memory_headroom_bytes"], 3*GIB)
        self.assertEqual(result["minimum_visible_memory_high_advisory_headroom_bytes"], GIB)
        self.assertFalse(result["scope"]["complete"])
        self.assertEqual(result["admission"], "not-evaluated")

    def test_affinity_intersects_effective_cpusets_at_each_ancestor(self):
        self.v2()
        self.write(self.mount/"parent", **{"cpuset.cpus.effective":"0-3"})
        self.write(self.mount/"parent/job", **{"cpuset.cpus.effective":"2-5"})
        result = self.observe(affinity={1,3,5,7})
        self.assertEqual(result["affinity_cpuset_intersection_cpus"], 1)
        self.assertEqual(result["effective_visible_cpus"], 1)

    def test_high_is_advisory_and_does_not_replace_hard_limit(self):
        self.v2()
        self.write(self.mount/"parent", **{"memory.current":11*GIB})
        result = self.observe()
        self.assertEqual(result["effective_visible_memory_limit_bytes"], 12*GIB)
        self.assertEqual(result["effective_visible_memory_headroom_bytes"], GIB)
        self.assertEqual(result["minimum_visible_memory_high_advisory_headroom_bytes"], 0)

    def test_missing_current_does_not_claim_host_available_as_headroom(self):
        self.v2()
        (self.mount/"parent/memory.current").unlink()
        result = self.observe()
        self.assertIsNone(result["effective_visible_memory_headroom_bytes"])
        self.assertIn("memory-current-unknown-for-visible-limit", result["issues"])

    def test_counters_are_retained_without_paths_or_identity(self):
        self.v2()
        result = self.observe()
        record = result["hierarchies"][0]["ancestors"][0]
        self.assertEqual(record["cpu.stat"]["nr_throttled"], 2)
        self.assertEqual(record["memory.stat"], {"anon":100,"file":200})
        self.assertEqual(record["memory.events"]["oom_kill"], 0)
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertNotIn("/parent/job", json.dumps(result))

    def test_subtree_mount_stops_at_visible_boundary(self):
        self.v2()
        result = self.observe(mounts=self.mount_line(self.mount/"parent", root="/parent"))
        self.assertEqual(len(result["hierarchies"][0]["ancestors"]), 2)
        self.assertEqual(result["scope"]["hidden_ancestors"], "present")
        self.assertEqual(result["effective_visible_cpus"], 2.5)

    def test_unmappable_namespace_root_is_not_guessed(self):
        with patch.object(h, "read_ancestor") as unused:
            reader = unittest.mock.Mock(side_effect=AssertionError("must not read"))
            result = self.observe(member="0::/\n",
                                  mounts=self.mount_line(root="/different"), reader=reader)
            reader.assert_not_called()
        self.assertIn("unmappable-membership", result["issues"])
        self.assertEqual(result["limit_observation"]["cpu_quota"], "unknown")
        self.assertFalse(result["scope"]["complete"])


    def test_invalid_alternate_mount_preserves_valid_primary_observations(self):
        self.v2()
        alternate = self.mount_line(self.root/"alternate", root="/../../hidden")
        result = self.observe(mounts=self.mount_line()+alternate)
        self.assertEqual(result["effective_visible_cpus"], 2.5)
        self.assertEqual(result["effective_visible_memory_limit_bytes"], 12*GIB)
        self.assertEqual(result["hierarchies"][0]["mapping"], "mapped")
        self.assertEqual(result["hierarchies"][1]["mapping"], "unsafe-cgroup-path")
        self.assertEqual(result["scope"]["hidden_ancestors"], "unknown")
        self.assertFalse(result["scope"]["complete"])

    def test_parent_traversal_in_membership_or_mount_is_rejected(self):
        for member, root in [("0::/../hidden\n","/"), ("0::/job\n","/../hidden")]:
            reader = unittest.mock.Mock(side_effect=AssertionError("must not read"))
            result = self.observe(member=member, mounts=self.mount_line(root=root), reader=reader)
            reader.assert_not_called()
            self.assertIn("unsafe-cgroup-path", result["issues"])

    def test_symlink_directory_and_controller_file_cannot_escape_mount(self):
        outside = self.root/"outside"
        self.write(outside, **{"cpu.max":"1 100000"})
        (self.mount/"job").symlink_to(outside, target_is_directory=True)
        result = self.observe(member="0::/job\n")
        self.assertNotEqual(result["hierarchies"][0]["ancestors"][0]["directory_status"], "ok")
        (self.mount/"job").unlink()
        (self.mount/"job").mkdir()
        (self.mount/"job/cpu.max").symlink_to(outside/"cpu.max")
        result = self.observe(member="0::/job\n")
        file = result["hierarchies"][0]["ancestors"][0]["files"]["cpu.max"]
        self.assertEqual(file["status"], "symlink-or-nondirectory-rejected")
        self.assertNotIn("value", file)

    def test_denied_limits_are_explicit(self):
        self.v2()
        def denied(point, parts, names):
            record = h.read_ancestor(point, parts, names)
            if parts == ("parent",):
                record["files"]["cpu.max"] = {"status":"denied"}
            return record
        result = self.observe(reader=denied)
        self.assertIn("cpu.max:denied", result["issues"])
        self.assertIsNone(result["minimum_visible_cpu_quota_cores"])
        self.assertFalse(result["scope"]["complete"])

    def test_invalid_quota_and_unbounded_cpuset_input_remain_unknown(self):
        self.v2()
        self.write(self.mount/"parent", **{"cpu.max":"1 0"})
        result = self.observe()
        self.assertIn("cpu.max:invalid", result["issues"])
        self.assertIsNone(result["minimum_visible_cpu_quota_cores"])
        with self.assertRaises(ValueError):
            h.cpu_set("0-999999999")

    def test_mountinfo_escapes_are_decoded(self):
        point = self.root/"cgroup space"
        self.write(point/"job", **{"cpu.max":"150000 100000"})
        result = self.observe(member="0::/job\n", mounts=self.mount_line(point))
        self.assertEqual(result["effective_visible_cpus"], 1.5)

    def v1(self):
        text = ""
        for controller in ("cpu", "memory", "cpuset"):
            point = self.root/controller
            for part in ("", "parent", "parent/job"):
                path = point/part
                values = {
                    "cpu": {"cpu.cfs_quota_us":"-1","cpu.cfs_period_us":"100000","cpu.stat":"nr_throttled 3"},
                    "memory": {"memory.limit_in_bytes":str((1<<63)-4096),
                               "memory.usage_in_bytes":str(GIB), "memory.use_hierarchy":"1",
                               "memory.soft_limit_in_bytes":str((1<<63)-4096),
                               "memory.stat":"rss 42", "memory.failcnt":"2",
                               "memory.oom_control":"under_oom 0\noom_kill_disable 0"},
                    "cpuset": {"cpuset.cpus":"0-3"},
                }[controller]
                self.write(path, **values)
            text += self.mount_line(point, version=1, controllers=controller)
        self.write(self.root/"cpu/parent", **{"cpu.cfs_quota_us":"250000"})
        self.write(self.root/"memory/parent", **{
            "memory.limit_in_bytes":12*GIB, "memory.usage_in_bytes":9*GIB,
            "memory.soft_limit_in_bytes":10*GIB,
        })
        self.write(self.root/"cpuset/parent/job", **{"cpuset.cpus":"1-2"})
        members = "2:cpu:/parent/job\n3:memory:/parent/job\n4:cpuset:/parent/job\n"
        return members, text

    def test_v1_separate_controllers_quota_cpuset_and_memory(self):
        members, text = self.v1()
        result = self.observe(member=members, mounts=text)
        self.assertEqual(result["effective_visible_cpus"], 2)
        self.assertEqual(result["minimum_visible_cpu_quota_cores"], 2.5)
        self.assertEqual(result["effective_visible_memory_limit_bytes"], 12*GIB)
        self.assertEqual(result["effective_visible_memory_headroom_bytes"], 3*GIB)
        self.assertIsNone(result["minimum_visible_memory_high_bytes"])
        memory = next(r for r in result["hierarchies"] if "memory" in r["controllers"])
        self.assertEqual(memory["ancestors"][0]["files"]["memory.failcnt"]["value"], "2")


    def test_empty_configured_v1_cpuset_without_effective_file_is_unknown(self):
        members, text = self.v1()
        self.write(self.root/"cpuset/parent/job", **{"cpuset.cpus":""})
        result = self.observe(member=members, mounts=text)
        self.assertEqual(result["effective_visible_cpus"], 2.5)
        self.assertIn("empty-configured-v1-cpuset-unknown", result["issues"])
        cpuset = next(row for row in result["hierarchies"] if "cpuset" in row["controllers"])
        self.assertEqual(cpuset["ancestors"][0]["cpuset_source"], "empty-configured-v1-unknown")
        self.assertFalse(result["scope"]["complete"])
        self.assertEqual(result["admission"], "not-evaluated")

    def test_v1_disabled_hierarchical_accounting_does_not_apply_parent_cap(self):
        members, text = self.v1()
        self.write(self.root/"memory/parent", **{
            "memory.limit_in_bytes":2*GIB, "memory.use_hierarchy":"0",
        })
        self.write(self.root/"memory/parent/job", **{"memory.limit_in_bytes":8*GIB})
        result = self.observe(member=members, mounts=text)
        self.assertEqual(result["effective_visible_memory_limit_bytes"], 8*GIB)

    def fake_proc(self, same_namespace=True):
        self.v2()
        proc = self.root/"proc"
        self.write(proc/"self/ns", mnt="namespace")
        (proc/"42/ns").mkdir(parents=True)
        if same_namespace:
            os.link(proc/"self/ns/mnt", proc/"42/ns/mnt")
        else:
            (proc/"42/ns/mnt").write_text("different namespace")
        self.write(proc/"42", cgroup="0::/parent/job\n",
                   stat="42 (worker) " + " ".join(["S"]+["0"]*18+["100"]+["0"]*5))
        self.write(proc/"self", mountinfo=self.mount_line())
        self.write(proc, meminfo=f"MemTotal: {31*GIB//1024} kB\nMemAvailable: {25*GIB//1024} kB\n")
        return proc

    def test_owner_pid_in_same_mount_namespace_is_supported(self):
        result = h.probe(42, proc=self.fake_proc(), affinity_reader=lambda _: {0,1,2,3})
        self.assertEqual(result["effective_visible_cpus"], 2.5)
        self.assertNotIn("pid", result)

    def test_different_mount_namespace_is_rejected_before_cgroup_read(self):
        proc = self.fake_proc(same_namespace=False)
        with patch.object(h, "inspect_visible") as inspect:
            result = h.probe(42, proc=proc)
            inspect.assert_not_called()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["issues"], ["different-mount-namespace-rejected"])

    def test_pid_reuse_or_namespace_change_invalidates_observation(self):
        proc = self.fake_proc()
        identity = h.process_identity(proc, 42)
        with patch.object(h, "process_identity", side_effect=[identity, (*identity[:2], 101)]):
            result = h.probe(42, proc=proc, affinity_reader=lambda _: {0,1,2,3})
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("target-changed-during-observation", result["issues"])

    def test_runtime_rejection_still_emits_json(self):
        answer = {"schema":h.SCHEMA,"status":"unavailable","issues":["different-mount-namespace-rejected"]}
        stream = io.StringIO()
        with patch.object(h, "probe", return_value=answer), contextlib.redirect_stdout(stream):
            code = h.main(["--pid","42"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stream.getvalue()), answer)


if __name__ == "__main__":
    unittest.main()
