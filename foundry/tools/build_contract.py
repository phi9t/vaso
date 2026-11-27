#!/usr/bin/env python3
"""Common Foundry target selection. Backends retain native recipe ownership.

Build selection does not alter package action keys, sources, compiler flags,
or the identity of an existing build estate.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from build_selection import CUDA_LINES, PACKAGES

FOUNDRY = Path(__file__).resolve().parents[1]
PASCAL = FOUNDRY / "native/triton/pascal/capsule"
TORCH = FOUNDRY / "native/pytorch215"
PYTHON = [sys.executable, "-B"]


@dataclass(frozen=True)
class Target:
    backend: str
    cuda_line: str
    architecture: str
    python_abi: str
    packages: tuple[str, ...]


TARGETS = {
    f"{line}/{profile}": Target("bazel", line, "profile-defined", "cp313", packages)
    for line in CUDA_LINES for profile, packages in PACKAGES.items()
}
TARGETS.update({
    "cu126-sm61/torch": Target("torch-source", "cu126", "sm_61", "cp312", ("pytorch",)),
    "cu126-sm61/triton": Target("pascal-capsule", "cu126", "sm_61", "cp312", ("triton",)),
})
COMMON = {"target", "phase", "describe", "list_targets", "plan_only"}
OPTIONS = {
    "bazel": {"estate_root", "seed_estate", "fetch"},
    "torch-source": {"site", "source_profile", "jobs", "resume", "max_seconds"},
    "pascal-capsule": {"insula", "spec", "state", "run_id", "supervisor", "output", "max_seconds"},
}


def parser():
    p = argparse.ArgumentParser(description=__doc__, prog="foundry/build.sh", allow_abbrev=False)
    p.add_argument("--list-targets", action="store_true")
    p.add_argument("--target", choices=TARGETS)
    p.add_argument("--phase", choices=("prepare", "build", "status"), default="build")
    p.add_argument("--describe", action="store_true", help="print dispatch, not an execution or admission result")
    p.add_argument("--plan-only", action="store_true", help="resolve Insula admission without running (capsule only)")
    for option in ("estate-root", "seed-estate", "site", "source-profile", "insula", "spec", "state", "supervisor", "output"):
        p.add_argument("--" + option, type=Path)
    p.add_argument("--fetch", action="store_true")
    p.add_argument("--jobs", type=int)
    p.add_argument("--resume", action="store_true")
    p.add_argument("--max-seconds", type=int)
    p.add_argument("--run-id")
    return p


def absolute(path, label):
    if path is None or not path.is_absolute():
        raise ValueError(f"{label} must be an explicit absolute path")
    return str(path)


def require(value, label):
    if value is None:
        raise ValueError(f"{label} is required")
    return str(value)


def options(argv, a, names):
    for name in names:
        value = getattr(a, name)
        flag = "--" + name.replace("_", "-")
        if value is True:
            argv.append(flag)
        elif value is not None and value is not False:
            argv.extend([flag, str(value)])
    return argv


def validate(a):
    target = TARGETS[a.target]
    used = {k for k, v in vars(a).items() if v is not None and v is not False}
    invalid = used - COMMON - OPTIONS[target.backend]
    if invalid:
        raise ValueError("Unsupported options for target: " + ", ".join("--" + n.replace("_", "-") for n in sorted(invalid)))
    if a.plan_only and (target.backend != "pascal-capsule" or a.phase == "status" or a.describe):
        raise ValueError("--plan-only requires a capsule prepare/build phase and excludes --describe")
    for name in ("jobs", "max_seconds"):
        value = getattr(a, name)
        if value is not None and value <= 0:
            raise ValueError("--" + name.replace("_", "-") + " must be positive")
    if target.backend == "bazel":
        if a.phase == "status" and (a.seed_estate or a.fetch):
            raise ValueError("status does not acquire build inputs")
    elif target.backend == "torch-source":
        if a.phase != "build" and (a.resume or a.jobs is not None or a.max_seconds is not None):
            raise ValueError("jobs, resume and deadline apply only to Torch builds")
    else:
        if a.phase == "status" and (a.spec or a.supervisor or a.max_seconds is not None):
            raise ValueError("status accepts native state/run-id or an external output directory")
        if a.plan_only and (a.output or a.supervisor or a.run_id):
            raise ValueError("plan-only does not allocate run identity or select an external owner")
    return target


def dispatch(a):
    """Resolve a canonical target to an existing backend without executing."""
    target = validate(a)
    if target.backend == "bazel":
        root = a.estate_root or (Path(os.environ["VASO_ESTATE_ROOT"]) if os.environ.get("VASO_ESTATE_ROOT") else None)
        argv = PYTHON + [str(FOUNDRY / "tools/build_variants.py"), "--estate-root",
                         require(root, "--estate-root or VASO_ESTATE_ROOT"),
                         "--line", target.cuda_line, "--profile", a.target.split("/")[-1]]
        if a.phase == "prepare":
            argv.append("--prepare-only")
        elif a.phase == "status":
            argv.append("--status")
        return options(argv, a, ("seed_estate", "fetch"))
    if target.backend == "torch-source":
        profile = a.source_profile or TORCH / "profiles/cu126-sm61-r4.json"
        t = json.loads(profile.read_text())["toolchain"]
        if (t["cuda_major_minor"], t["cuda_arch_list"], t["python_abi"]) != ("12.6", "6.1", "cp312"):
            raise ValueError("Torch source profile contradicts the selected target")
        argv = PYTHON + [str(TORCH / "source_build.py"), "--site", require(a.site, "--site"),
                         "--profile", str(profile)]
        return options(argv, a, ("jobs", "resume", "max_seconds")) + [a.phase]

    if a.phase == "status" and a.output is not None:
        if a.insula or a.state or a.run_id:
            raise ValueError("external status and native status are mutually exclusive")
        absolute(a.output, "--output")
        return None
    runtime = absolute(a.insula, "--insula")
    if a.phase == "status":
        return [runtime, "status", "--state", absolute(a.state, "--state"), require(a.run_id, "--run-id")]
    spec = absolute(a.spec, "--spec")
    command = ["/opt/vaso/pascal/bin/build-triton"]
    if a.phase == "prepare":
        command.append("--prepare-only")
    if a.supervisor is not None or a.output is not None:
        if a.state or a.run_id or a.plan_only:
            raise ValueError("external owner and native state/run-id are mutually exclusive")
        return PYTHON + [str(PASCAL / "run_payload.py"), "--insula", runtime,
                         "--supervisor", absolute(a.supervisor, "--supervisor"),
                         "--spec", spec, "--output", absolute(a.output, "--output"),
                         "--timeout", str(a.max_seconds or 7200), "--", *command]
    plan = [runtime, "plan", "-f", spec]
    if a.state:
        plan += ["--state", absolute(a.state, "--state")]
    plan += ["--timeout-seconds", str(a.max_seconds or 7200)]
    if a.plan_only:
        return plan + ["--", *command]
    return [runtime, "run", "--state", absolute(a.state, "--state"),
            "--run-id", require(a.run_id, "--run-id"), "-f", spec,
            "--timeout-seconds", str(a.max_seconds or 7200), "--", *command]


def capsule_plan(a, argv):
    """Check the real Insula plan and packaged profile before execution."""
    command = argv[argv.index("--") + 1:]
    plan_argv = [str(a.insula), "plan", "-f", str(a.spec)]
    if a.state:
        plan_argv += ["--state", str(a.state)]
    if a.supervisor is None:
        plan_argv += ["--timeout-seconds", str(a.max_seconds or 7200)]
    raw = subprocess.check_output(plan_argv + ["--", *command], text=True, timeout=90)
    plan = json.loads(raw)
    main = plan["main"]
    if (plan.get("kind") != "single" or main["argv"] != command
            or main["devices"] or main["extra_mounts"]):
        raise RuntimeError("Pascal build requires an exact CPU-only capsule plan without external dependency mounts")
    # The packaged builder owns its environment. The only caller-tunable
    # variable is the bounded parallelism setting, not dependency resolution.
    env = main["env"]
    if set(env) - {"MAX_JOBS"}:
        raise RuntimeError("Pascal build spec may set only MAX_JOBS")
    if "MAX_JOBS" in env and (not re.fullmatch(r"[1-9][0-9]*", env["MAX_JOBS"])
                             or int(env["MAX_JOBS"]) > (os.cpu_count() or 1)):
        raise RuntimeError("MAX_JOBS must fit available host CPUs")
    if not re.fullmatch(r"[a-f0-9]{64}", plan["plan_sha256"]):
        raise RuntimeError("Invalid Insula plan digest")
    root = Path(main["rootfs"])
    profile = json.loads((root / "opt/vaso/pascal/profile.json").read_text())
    if (profile["cuda"], profile["gpu_arch"], profile["python_abi"], profile["runtime"]) != ("12.6", "sm_61", "cp312", "standalone-insula"):
        raise RuntimeError("Selected capsule contradicts the requested build target")
    if not (root / "opt/vaso/pascal/bin/build-triton").is_file():
        raise RuntimeError("Selected capsule has no packaged Triton builder")
    return raw, plan["plan_sha256"]


def main(argv=None):
    p = parser()
    a = p.parse_args(argv)
    if a.list_targets:
        if a.target or a.describe or a.plan_only or a.phase != "build" or any(
            getattr(a, k) is not None and getattr(a, k) is not False for k in set(vars(a)) - COMMON
        ):
            p.error("--list-targets is a standalone operation")
        print(json.dumps({name: asdict(target) for name, target in TARGETS.items()}, indent=2))
        return 0
    if not a.target:
        p.error("--target is required")
    try:
        command = dispatch(a)
        if a.describe:
            print(json.dumps({"target": a.target, "phase": a.phase, "backend": TARGETS[a.target].backend,
                              "argv": command, "status_file": str(a.output / "status.json") if command is None else None,
                              "executed": False}, indent=2))
            return 0
        if command is None:
            print((a.output / "status.json").read_text(), end="")
            return 0
        if TARGETS[a.target].backend == "pascal-capsule" and a.phase != "status":
            raw, digest = capsule_plan(a, command)
            if a.plan_only:
                print(raw, end="")
                return 0
            boundary = command.index("--")
            command[boundary:boundary] = ["--expected-plan-sha256", digest]
        # No independent process owner: retain the selected backend's lifecycle.
        os.execv(command[0], command)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Foundry build contract: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
