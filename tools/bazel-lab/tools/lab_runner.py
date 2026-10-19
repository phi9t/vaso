#!/usr/bin/env python3
"""Run the bazel-lab mechanics experiments against a copied workspace."""

from __future__ import annotations

import argparse
import dataclasses
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


FAST_EXPERIMENTS = ("E2", "E3", "E4", "E6")
ALL_EXPERIMENTS = ("E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", "E9", "E10", "E11")
ALLOWED_N1_ENV = {"LAB_ALLOWED", "LAB_CC", "LC_ALL", "PATH"}


@dataclasses.dataclass(frozen=True)
class BuildResult:
    output: str
    stdout: str
    stderr: str
    compact_log: Path


class LabFailure(AssertionError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")


def tail(text: str, lines: int = 40) -> str:
    return "\n".join(text.splitlines()[-lines:])


class Lab:
    def __init__(self, args: argparse.Namespace) -> None:
        if os.environ.get("VASO_IN_INSULA") != "1":
            raise LabFailure("Bazel experiments must run under insula; use tools/bazel-lab/run_experiments.sh")
        self.source = Path(__file__).resolve().parents[1]
        self.repo_root = self.source.parents[1]
        self.estate = Path(os.environ["VASO_ESTATE_ROOT"]).resolve()
        self.agent = args.agent or os.environ.get("VASO_AGENT", "fc-l1")
        self.bazel = Path(
            args.bazel
            or os.environ.get("BAZEL_BIN")
            or "/opt/vaso/bin/bazel-real"
        )
        self.root = self.estate / "agents" / self.agent / "bazel-lab"
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + f"-{os.getpid()}"
        self.workspace = self.root / "workspace"
        self.logs = self.root / "logs" / self.run_id
        self.disk_cache = self.root / "disk-cache" / self.run_id
        self.output_bases = self.root / "output-bases" / self.run_id
        self.clean = args.clean

    def prepare(self) -> None:
        if not self.bazel.is_file():
            raise LabFailure(f"missing Bazel binary: {self.bazel}")
        self.root.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)
        self.disk_cache.mkdir(parents=True, exist_ok=True)
        self.output_bases.mkdir(parents=True, exist_ok=True)
        if self.clean and self.workspace.exists():
            shutil.rmtree(self.workspace)
        if self.workspace.exists():
            shutil.rmtree(self.workspace)
        def ignore(_dir: str, names: list[str]) -> set[str]:
            ignored = {"__pycache__", ".pytest_cache", "MODULE.bazel.lock"}
            ignored.update(name for name in names if name.startswith("bazel-"))
            return ignored

        shutil.copytree(self.source, self.workspace, ignore=ignore)

    def ob(self, name: str) -> Path:
        path = self.output_bases / safe_name(name)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def run(
        self,
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        result = subprocess.run(
            [str(arg) for arg in argv],
            cwd=cwd or self.workspace,
            env=merged_env,
            text=True,
            capture_output=True,
            check=False,
        )
        if check and result.returncode != 0:
            rendered = " ".join(str(arg) for arg in argv)
            raise LabFailure(
                f"command failed rc={result.returncode}: {rendered}\n"
                f"stdout:\n{tail(result.stdout)}\n"
                f"stderr:\n{tail(result.stderr)}"
            )
        return result

    def bazel_cmd(self, ob: Path, command: str, args: list[str]) -> list[str]:
        return [
            str(self.bazel),
            f"--output_base={ob}",
            command,
            "--lockfile_mode=off",
            "--registry=file:///nonexistent",
            "--noshow_progress",
            "--color=no",
            "--curses=no",
            *args,
        ]

    def bazel_run(
        self,
        ob: Path,
        command: str,
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        check: bool = True,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return self.run(self.bazel_cmd(ob, command, args), cwd=cwd, env=env, check=check)

    def aquery(self, target: str, ob: Path, extra: list[str] | None = None) -> dict:
        query = f'mnemonic("LabNative", {target})'
        result = self.bazel_run(
            ob,
            "aquery",
            [
                "--output=jsonproto",
                "--include_commandline",
                *(extra or []),
                query,
            ],
        )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise LabFailure(f"aquery did not return JSON for {target}: {exc}\n{tail(result.stdout)}") from exc

    def action_for(self, target: str, ob: Path, extra: list[str] | None = None) -> dict:
        data = self.aquery(target, ob, extra)
        actions = data.get("actions", [])
        labels: dict[str, str] = {}
        for item in data.get("targets", []) + data.get("configuredTargets", []):
            if "id" in item and "label" in item:
                labels[str(item["id"])] = item["label"]
        matching = []
        for action in actions:
            label = labels.get(str(action.get("targetId")), "")
            if label == target or label.endswith(target):
                matching.append(action)
        if not matching and len(actions) == 1:
            matching = actions
        if len(matching) != 1:
            raise LabFailure(f"expected one LabNative action for {target}, got {len(matching)}")
        return matching[0]

    def action_key(self, target: str, ob_name: str, extra: list[str] | None = None) -> str:
        action = self.action_for(target, self.ob(ob_name), extra)
        key = action.get("actionKey")
        if not key:
            raise LabFailure(f"aquery action for {target} had no actionKey")
        return str(key)

    def action_env(self, target: str, ob_name: str, extra: list[str] | None = None) -> tuple[bool, set[str]]:
        action = self.action_for(target, self.ob(ob_name), extra)
        default_env = bool(action.get("useDefaultShellEnvironment"))
        names = set()
        for entry in action.get("environmentVariables", []):
            name = entry.get("key") or entry.get("name")
            if name:
                names.add(str(name))
        return default_env, names

    def build(
        self,
        target: str,
        ob_name: str,
        extra: list[str] | None = None,
        *,
        env: dict[str, str] | None = None,
    ) -> BuildResult:
        compact_log = self.logs / f"{safe_name(ob_name)}-{safe_name(target)}.compact.log"
        result = self.bazel_run(
            self.ob(ob_name),
            "build",
            [
                "--disk_cache=" + str(self.disk_cache),
                "--execution_log_compact_file=" + str(compact_log),
                *(extra or []),
                target,
            ],
            env=env,
        )
        output = self.output_file(target, ob_name, extra)
        if not compact_log.exists() or compact_log.stat().st_size == 0:
            raise LabFailure(f"missing compact execution log for {target}: {compact_log}")
        return BuildResult(
            output=output.read_text(encoding="utf-8"),
            stdout=result.stdout,
            stderr=result.stderr,
            compact_log=compact_log,
        )

    def output_file(self, target: str, ob_name: str, extra: list[str] | None = None) -> Path:
        result = self.bazel_run(
            self.ob(ob_name),
            "cquery",
            [
                "--output=files",
                *(extra or []),
                target,
            ],
        )
        candidates = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if not candidates:
            raise LabFailure(f"no output path for {target}")
        execution_root = Path(
            self.bazel_run(self.ob(ob_name), "info", ["execution_root"]).stdout.strip().splitlines()[-1]
        )
        for raw in candidates:
            path = Path(raw)
            if path.is_file():
                return path
            for base in (execution_root, self.workspace):
                candidate = base / raw
                if candidate.is_file():
                    return candidate
        raise LabFailure(f"no readable output for {target}: {candidates}")

    def repo_stamp(self, ob_name: str, env_value: str) -> str:
        extra = [f"--repo_env=LAB_REPO_ENV={env_value}"]
        self.bazel_run(self.ob(ob_name), "build", [*extra, "@generated_dep//:stamp.txt"])
        path = self.output_file("@generated_dep//:stamp.txt", ob_name, extra)
        return path.read_text(encoding="utf-8")

    def append(self, rel: str, text: str) -> None:
        path = self.workspace / rel
        path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")

    def write(self, rel: str, text: str) -> None:
        (self.workspace / rel).write_text(text, encoding="utf-8")

    def generated_plans(self) -> dict[str, str]:
        out = self.root / "generated-plans"
        if out.exists():
            shutil.rmtree(out)
        self.run(["/usr/bin/python3", str(self.workspace / "tools" / "plan_generator.py"), "--out", str(out)])
        return {
            path.name: path.read_text(encoding="utf-8")
            for path in sorted(out.glob("*.plan.json"))
        }

    def key_snapshot(self, check: bool = False) -> subprocess.CompletedProcess[str]:
        args = [
            "/usr/bin/python3",
            str(self.workspace / "tools" / "key_snapshot.py"),
            "--root",
            str(self.workspace),
        ]
        if check:
            args.extend(["--check", str(self.workspace / "plans" / "keys.json")])
        return self.run(args, check=check)


def assert_equal(left: object, right: object, message: str) -> None:
    if left != right:
        raise LabFailure(message)


def assert_not_equal(left: object, right: object, message: str) -> None:
    if left == right:
        raise LabFailure(message)


def assert_contains(text: str, needle: str, message: str) -> None:
    if needle not in text:
        raise LabFailure(f"{message}; expected {needle!r} in:\n{text}")


def directory_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return total
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total


def cache_content_file_count(path: Path) -> int:
    return sum(1 for item in path.rglob("*") if item.is_file() and item.name != "lock")


def test_e1(lab: Lab) -> str:
    bad_before = lab.action_key("//pkg:e1_bad_driver_input", "e1")
    fixed_before = lab.action_key("//pkg:e1_fixed_committed_plan", "e1")
    bad_output_before = lab.build("//pkg:e1_bad_driver_input", "e1").output
    fixed_output_before = lab.build("//pkg:e1_fixed_committed_plan", "e1").output

    lab.append("drivers/bad_driver.sh", "\n# E1 comment mutation\n")
    bad_after_driver = lab.action_key("//pkg:e1_bad_driver_input", "e1")
    fixed_after_driver = lab.action_key("//pkg:e1_fixed_committed_plan", "e1")
    bad_output_after_driver = lab.build("//pkg:e1_bad_driver_input", "e1").output
    fixed_output_after_driver = lab.build("//pkg:e1_fixed_committed_plan", "e1").output

    lab.append("planners/bad_planner.py", "\n# E1 planner comment mutation\n")
    bad_after_planner = lab.action_key("//pkg:e1_bad_driver_input", "e1")
    fixed_after_planner = lab.action_key("//pkg:e1_fixed_committed_plan", "e1")
    bad_output_after_planner = lab.build("//pkg:e1_bad_driver_input", "e1").output
    fixed_output_after_planner = lab.build("//pkg:e1_fixed_committed_plan", "e1").output

    assert_equal(bad_before, bad_after_driver, "E1 bad driver content edit changed the aquery key")
    assert_equal(bad_before, bad_after_planner, "E1 bad planner content edit changed the aquery key")
    assert_equal(fixed_before, fixed_after_driver, "E1 fixed plan key changed after driver edit")
    assert_equal(fixed_before, fixed_after_planner, "E1 fixed plan key changed after planner edit")
    assert_not_equal(bad_output_before, bad_output_after_driver, "E1 bad driver input did not re-execute")
    assert_not_equal(bad_output_after_driver, bad_output_after_planner, "E1 bad planner input did not re-execute")
    assert_equal(fixed_output_before, fixed_output_after_driver, "E1 fixed action re-executed after driver edit")
    assert_equal(fixed_output_before, fixed_output_after_planner, "E1 fixed action re-executed after planner edit")
    return "declared driver/planner inputs re-executed with stable aquery key; committed-plan action did not"


def test_e2(lab: Lab) -> str:
    bad_one = lab.action_key("//pkg:e2_bad_default_env", "e2", ["--action_env=FOO=one"])
    bad_two = lab.action_key("//pkg:e2_bad_default_env", "e2", ["--action_env=FOO=two"])
    fixed_one = lab.action_key("//pkg:e2_fixed_explicit_env", "e2", ["--action_env=FOO=one"])
    fixed_two = lab.action_key("//pkg:e2_fixed_explicit_env", "e2", ["--action_env=FOO=two"])

    assert_not_equal(bad_one, bad_two, "E2 default shell env did not key action_env FOO")
    assert_equal(fixed_one, fixed_two, "E2 explicit env keyed unrelated action_env FOO")
    return "default shell env keyed FOO; explicit env ignored FOO"


def test_e3(lab: Lab) -> str:
    bad_one = lab.action_key("//pkg:e3_bad_seed_setting", "e3", ["--//settings:seed=one"])
    bad_two = lab.action_key("//pkg:e3_bad_seed_setting", "e3", ["--//settings:seed=two"])
    fixed_one = lab.action_key("//pkg:e3_fixed_plan_seed", "e3", ["--//settings:seed=one"])
    fixed_two = lab.action_key("//pkg:e3_fixed_plan_seed", "e3", ["--//settings:seed=two"])

    assert_not_equal(bad_one, bad_two, "E3 seed build setting did not change the key")
    assert_equal(fixed_one, fixed_two, "E3 plan-derived seed changed with unused build setting")
    return "argv seed setting changed keys; plan-derived seed stayed stable"


def test_e4(lab: Lab) -> str:
    bad_before = lab.action_key("//pkg:e4_bad_path_dep", "e4")
    fixed_before = lab.action_key("//pkg:e4_fixed_declared_dep", "e4")
    bad_first = lab.build("//pkg:e4_bad_path_dep", "e4").output
    fixed_first = lab.build("//pkg:e4_fixed_declared_dep", "e4").output
    assert_contains(bad_first, "dep-v1", "E4 bad output did not read initial path dependency")
    assert_contains(fixed_first, "dep-v1", "E4 fixed output did not read initial dependency")

    lab.write("pkg/mutable_dep.txt", "dep-v2\n")
    bad_after = lab.action_key("//pkg:e4_bad_path_dep", "e4")
    fixed_after = lab.action_key("//pkg:e4_fixed_declared_dep", "e4")
    bad_second = lab.build("//pkg:e4_bad_path_dep", "e4").output
    fixed_second = lab.build("//pkg:e4_fixed_declared_dep", "e4").output

    assert_equal(bad_before, bad_after, "E4 path-string dependency changed the key")
    assert_equal(bad_first, bad_second, "E4 path-string dependency did not produce stale cached output")
    assert_equal(fixed_before, fixed_after, "E4 declared dependency changed the aquery action definition key")
    assert_contains(fixed_second, "dep-v2", "E4 declared dependency did not rebuild with new content")
    return "path-string dep went stale; declared dep kept aquery key but rebuilt from input digest"


def test_e5(lab: Lab) -> str:
    first = lab.repo_stamp("e5-one", "one")
    time.sleep(0.02)
    lab.append("repo_rules/generated_repo.bzl", "\n# E5 comment mutation\n")
    after_bzl = lab.repo_stamp("e5-one", "one")
    time.sleep(0.02)
    after_env = lab.repo_stamp("e5-one", "two")
    time.sleep(0.02)
    new_ob = lab.repo_stamp("e5-two", "two")

    assert_not_equal(first, after_bzl, "E5 repository rule did not rerun after .bzl edit")
    assert_contains(after_env, "env=two", "E5 repository rule did not see repo env change")
    assert_not_equal(after_bzl, after_env, "E5 repository rule did not rerun after environ change")
    assert_not_equal(after_env, new_ob, "E5 repository rule output was reused from disk cache across output bases")
    return "repository rule reran on .bzl edit/env/new output base despite shared disk cache"


def test_e6(lab: Lab) -> str:
    bounded_gc = [
        "--experimental_disk_cache_gc_max_size=128M",
        "--experimental_disk_cache_gc_max_age=1h",
        "--experimental_disk_cache_gc_idle_delay=0s",
    ]
    stable_key_one = lab.action_key("//pkg:e6_good_cache_stable", "e6-stable-a")
    stable_key_two = lab.action_key("//pkg:e6_good_cache_stable", "e6-stable-b")
    stable_key_gc = lab.action_key("//pkg:e6_good_cache_stable", "e6-stable-gc-key", bounded_gc)
    stable_one = lab.build("//pkg:e6_good_cache_stable", "e6-stable-a", bounded_gc).output
    stable_two = lab.build("//pkg:e6_good_cache_stable", "e6-stable-b", bounded_gc).output

    bad_key_one = lab.action_key(
        "//pkg:e6_bad_prefix_map_arg",
        "e6-bad-a",
        ["--//settings:prefix_root=" + str(lab.ob("e6-bad-a"))],
    )
    bad_key_two = lab.action_key(
        "//pkg:e6_bad_prefix_map_arg",
        "e6-bad-b",
        ["--//settings:prefix_root=" + str(lab.ob("e6-bad-b"))],
    )
    bad_one = lab.build(
        "//pkg:e6_bad_prefix_map_arg",
        "e6-bad-a",
        ["--//settings:prefix_root=" + str(lab.ob("e6-bad-a"))],
    ).output
    bad_two = lab.build(
        "//pkg:e6_bad_prefix_map_arg",
        "e6-bad-b",
        ["--//settings:prefix_root=" + str(lab.ob("e6-bad-b"))],
    ).output

    fixed_key_one = lab.action_key("//pkg:e6_fixed_runtime_prefix_map", "e6-fixed-a")
    fixed_key_two = lab.action_key("//pkg:e6_fixed_runtime_prefix_map", "e6-fixed-b")
    fixed_one = lab.build("//pkg:e6_fixed_runtime_prefix_map", "e6-fixed-a").output
    fixed_two = lab.build("//pkg:e6_fixed_runtime_prefix_map", "e6-fixed-b").output

    assert_equal(stable_key_one, stable_key_two, "E6 stable action key changed across output bases")
    assert_equal(stable_key_one, stable_key_gc, "E6 disk-cache GC flags changed the action key")
    assert_equal(stable_one, stable_two, "E6 size-bounded disk cache missed a reusable entry")
    if directory_size(lab.disk_cache) > 128 * 1024 * 1024:
        raise LabFailure("E6 size-bounded disk cache exceeded its 128M limit")
    assert_not_equal(bad_key_one, bad_key_two, "E6 output-base prefix arg did not change the key")
    assert_not_equal(bad_one, bad_two, "E6 output-base prefix arg did not force re-execution")
    assert_equal(fixed_key_one, fixed_key_two, "E6 runtime prefix map key changed across output bases")
    assert_equal(fixed_one, fixed_two, "E6 runtime prefix map missed the shared disk cache")

    expiring_gc = [
        "--experimental_disk_cache_gc_max_size=128M",
        "--experimental_disk_cache_gc_max_age=1s",
        "--experimental_disk_cache_gc_idle_delay=0s",
    ]
    old_entry = lab.build("//pkg:e6_good_cache_stable", "e6-gc-old", bounded_gc).output
    old_mtime = time.time() - 3600
    for item in lab.disk_cache.rglob("*"):
        if item.is_file() and item.name != "lock":
            os.utime(item, (old_mtime, old_mtime))
    for attempt in range(6):
        lab.bazel_run(
            lab.ob("e6-gc-trigger"),
            "info",
            ["--disk_cache=" + str(lab.disk_cache), *expiring_gc],
        )
        if cache_content_file_count(lab.disk_cache) == 0:
            break
        time.sleep(0.5)
    if cache_content_file_count(lab.disk_cache) != 0:
        raise LabFailure("E6 age-GC did not remove the aged disk-cache entries")
    new_entry = lab.build("//pkg:e6_good_cache_stable", "e6-gc-after", bounded_gc).output
    if new_entry == old_entry:
        raise LabFailure("E6 age-GC'd disk cache entry was still reused instead of becoming a clean miss")

    return "bounded GC preserved fresh hits; aged GC entries became clean misses"


def test_e7(lab: Lab) -> str:
    bad_old_key = lab.action_key("//pkg:e7_bad_persistent_workdir", "e7", ["--//settings:flag=OLD"])
    bad_new_key = lab.action_key("//pkg:e7_bad_persistent_workdir", "e7", ["--//settings:flag=NEW"])
    bad_old = lab.build("//pkg:e7_bad_persistent_workdir", "e7", ["--//settings:flag=OLD"]).output
    bad_new = lab.build("//pkg:e7_bad_persistent_workdir", "e7", ["--//settings:flag=NEW"]).output
    fixed_old = lab.build("//pkg:e7_fixed_fresh_workdir", "e7", ["--//settings:flag=OLD"]).output
    fixed_new = lab.build("//pkg:e7_fixed_fresh_workdir", "e7", ["--//settings:flag=NEW"]).output

    assert_not_equal(bad_old_key, bad_new_key, "E7 flag change did not change the bad action key")
    assert_equal(bad_old, "OLD\n", "E7 bad first build did not compile OLD")
    assert_equal(bad_new, "OLD\n", "E7 persistent work dir did not reuse stale OLD object")
    assert_equal(fixed_old, "OLD\n", "E7 fixed first build did not compile OLD")
    assert_equal(fixed_new, "NEW\n", "E7 fresh work dir did not rebuild NEW")
    return "persistent work dir reused OLD object; fresh action work dir rebuilt NEW"


def test_e8(lab: Lab) -> str:
    bad_key_one = lab.action_key("//pkg:e8_bad_jobs_arg", "e8", ["--//settings:jobs=1"])
    bad_key_two = lab.action_key("//pkg:e8_bad_jobs_arg", "e8", ["--//settings:jobs=9"])
    bad_one = lab.build("//pkg:e8_bad_jobs_arg", "e8", ["--//settings:jobs=1"]).output
    bad_two = lab.build("//pkg:e8_bad_jobs_arg", "e8", ["--//settings:jobs=9"]).output

    fixed_key_one = lab.action_key("//pkg:e8_fixed_unkeyed_jobs", "e8")
    fixed_one = lab.build("//pkg:e8_fixed_unkeyed_jobs", "e8").output
    lab.write("pkg/runtime_jobs.txt", "9\n")
    fixed_key_two = lab.action_key("//pkg:e8_fixed_unkeyed_jobs", "e8")
    fixed_two = lab.build("//pkg:e8_fixed_unkeyed_jobs", "e8").output

    assert_not_equal(bad_key_one, bad_key_two, "E8 jobs argv did not change the key")
    assert_equal(bad_one, bad_two, "E8 jobs argv changed deterministic output")
    assert_equal(fixed_key_one, fixed_key_two, "E8 unkeyed job source changed the key")
    assert_equal(fixed_one, fixed_two, "E8 unkeyed job source changed deterministic output")
    return "jobs argv changed key with same output; unkeyed jobs left key/output stable"


def test_e9(lab: Lab) -> str:
    generated = lab.generated_plans()
    committed = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted((lab.workspace / "plans").glob("*.plan.json"))
    }
    if generated != committed:
        diff = "\n".join(
            difflib.unified_diff(
                committed.get("native.plan.json", "").splitlines(),
                generated.get("native.plan.json", "").splitlines(),
                fromfile="committed",
                tofile="generated",
            )
        )
        raise LabFailure("E9 plan drift test failed\n" + diff)
    tampered = dict(committed)
    tampered["native.plan.json"] = tampered["native.plan.json"].replace("native-plan-seed", "tampered-seed")
    assert_not_equal(generated, tampered, "E9 drift test did not reject a tampered plan")

    snapshot = lab.key_snapshot(check=False).stdout
    expected = (lab.workspace / "plans" / "keys.json").read_text(encoding="utf-8")
    assert_equal(snapshot, expected, "E9 key snapshot drifted")
    tampered_snapshot = expected.replace("native-pkg", "native-pkg-tampered", 1)
    assert_not_equal(snapshot, tampered_snapshot, "E9 key snapshot did not reject tampering")

    good_default_env, good_env = lab.action_env("//pkg:e9_full_n1", "e9")
    _bad_default_env, bad_env = lab.action_env("//pkg:e9_bad_default_env", "e9", ["--action_env=FOO=bad"])
    if good_default_env:
        raise LabFailure("E9 N1 target uses default shell env")
    disallowed = sorted(good_env - ALLOWED_N1_ENV)
    if disallowed:
        raise LabFailure(f"E9 N1 target has env outside allowlist: {disallowed}")
    if "FOO" not in bad_env:
        raise LabFailure("E9 bad env target did not expose action_env FOO")
    return "plan drift, key snapshot and env allowlist accepted N1 and rejected tampering/bad env"


def write_e10_root(root: Path, *, with_override: bool, local_dep: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    module_overrides = local_dep.parents[1] / "module_overrides"
    module = [
        'module(name = "e10_root", version = "0.0.0")',
        'bazel_dep(name = "local_dep", version = "0.0.1")',
    ]
    for name in (
        "apple_support",
        "buildozer",
        "platforms",
        "protobuf",
        "rules_cc",
        "rules_java",
        "rules_license",
        "rules_python",
        "rules_shell",
        "zlib",
    ):
        module.append(f'local_path_override(module_name = "{name}", path = "{module_overrides / name}")')
    if with_override:
        module.append(f'local_path_override(module_name = "local_dep", path = "{local_dep}")')
    (root / "MODULE.bazel").write_text("\n".join(module) + "\n", encoding="utf-8")
    (root / "BUILD.bazel").write_text('exports_files(["root.txt"])\n', encoding="utf-8")
    (root / "root.txt").write_text("root\n", encoding="utf-8")


def test_e10(lab: Lab) -> str:
    local_dep = lab.workspace / "e10" / "local_dep"
    good = lab.root / "e10-good"
    bad = lab.root / "e10-bad"
    if good.exists():
        shutil.rmtree(good)
    if bad.exists():
        shutil.rmtree(bad)
    write_e10_root(good, with_override=True, local_dep=local_dep)
    write_e10_root(bad, with_override=False, local_dep=local_dep)

    good_result = lab.bazel_run(
        lab.ob("e10-good"),
        "build",
        ["@local_dep//:hello.txt"],
        cwd=good,
        check=False,
    )
    if good_result.returncode != 0:
        raise LabFailure("E10 local override build failed\n" + tail(good_result.stderr))

    bad_result = lab.bazel_run(
        lab.ob("e10-bad"),
        "build",
        ["@local_dep//:hello.txt"],
        cwd=bad,
        check=False,
    )
    if bad_result.returncode == 0:
        raise LabFailure("E10 build without local_path_override unexpectedly succeeded")
    return "nested Bazel succeeded offline with local override and failed without it"


def test_e11(lab: Lab) -> str:
    target = "//pkg:e11_repo_manifest_input"
    one_args = ["--repo_env=LAB_CONTENT=one"]
    two_args = ["--repo_env=LAB_CONTENT=two"]
    key_one = lab.action_key(target, "e11-one-key", one_args)
    key_two = lab.action_key(target, "e11-two-key", two_args)
    first = lab.build(target, "e11-one-a", one_args).output
    second = lab.build(target, "e11-one-b", one_args).output
    changed = lab.build(target, "e11-two", two_args).output

    assert_equal(key_one, key_two, "E11 actionKey changed when only repository input content changed")
    assert_equal(first, second, "E11 stable repository content missed the shared disk cache")
    assert_not_equal(changed, first, "E11 changed repository input content did not rerun the action")
    assert_contains(changed, "declared_dep=content=two", "E11 output did not observe changed repository content")
    return "repository input content changed downstream execution despite a stable actionKey"


TESTS = {
    "E1": test_e1,
    "E2": test_e2,
    "E3": test_e3,
    "E4": test_e4,
    "E5": test_e5,
    "E6": test_e6,
    "E7": test_e7,
    "E8": test_e8,
    "E9": test_e9,
    "E10": test_e10,
    "E11": test_e11,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", default=None)
    parser.add_argument("--bazel", default=None)
    parser.add_argument("--clean", action="store_true", default=True)
    parser.add_argument(
        "--subset",
        choices=("all", "fast"),
        default="all",
        help="fast runs E2, E3, E4 and E6 for standing verify",
    )
    parser.add_argument("experiments", nargs="*")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selected = list(args.experiments)
    unknown = sorted(set(selected) - set(ALL_EXPERIMENTS))
    if unknown:
        print(f"unknown experiment(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    if not selected:
        selected = list(FAST_EXPERIMENTS if args.subset == "fast" else ALL_EXPERIMENTS)

    lab = Lab(args)
    print(f"bazel-lab start {utc_now()} subset={args.subset} experiments={','.join(selected)}")
    print(f"bazel={lab.bazel}")
    print(f"state={lab.root}")
    lab.prepare()

    for name in selected:
        try:
            summary = TESTS[name](lab)
        except Exception as exc:
            print(f"{name} FAILED {exc}", file=sys.stderr)
            return 1
        print(f"{name} PASS {summary}")

    print(f"bazel-lab passed {utc_now()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
