"""Common Foundry target selection, execution admission, and result interface."""
from __future__ import annotations
import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import capsule_build
from build_report import latest_target_report, target_result
from build_selection import CUDA_LINES, PACKAGES

FOUNDRY = Path(__file__).resolve().parents[1]
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
    "cu126-sm61/torch": Target("bazel-capsule", "cu126", "sm_61", "cp312", ("pytorch",)),
    "cu126-sm61/triton": Target("bazel-capsule", "cu126", "sm_61", "cp312", ("triton",)),
})
COMMON = {"target", "phase", "describe", "list_targets", "plan_only"}
OPTIONS = {
    "bazel": {"estate_root", "seed_estate", "fetch"},
    "bazel-capsule": {"insula", "spec", "state", "run_id", "supervisor", "output", "jobs", "max_seconds"},
}


def parser():
    p = argparse.ArgumentParser(description=__doc__, prog="foundry/build.sh", allow_abbrev=False)
    p.add_argument("--list-targets", action="store_true")
    p.add_argument("--target", choices=TARGETS)
    p.add_argument("--phase", choices=("prepare", "build", "status"), default="build")
    p.add_argument("--describe", action="store_true", help="print dispatch without executing or admitting")
    p.add_argument("--plan-only", action="store_true", help="resolve capsule admission without allocating a run")
    for option in ("estate-root", "seed-estate", "insula", "spec", "state", "supervisor", "output"):
        p.add_argument("--" + option, type=Path)
    for option in ("site", "source-profile"):
        p.add_argument("--" + option, type=Path, help=argparse.SUPPRESS)
    p.add_argument("--resume", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--fetch", action="store_true")
    p.add_argument("--jobs", type=int)
    p.add_argument("--max-seconds", type=int)
    p.add_argument("--run-id")
    return p


def validate(a):
    target = TARGETS[a.target]
    if a.site or a.source_profile or a.resume:
        raise ValueError("canonical Pascal builds now use a packaged Bazel capsule; use --insula/--spec/--output. "
                         "Historical source builds remain available through foundry/build.sh --source")
    used = {k for k, v in vars(a).items() if v is not None and v is not False}
    invalid = used - COMMON - OPTIONS[target.backend]
    if invalid:
        raise ValueError("Unsupported options for target: " + ", ".join("--" + n.replace("_", "-") for n in sorted(invalid)))
    for name in ("jobs", "max_seconds"):
        value = getattr(a, name)
        if value is not None and value <= 0:
            raise ValueError("--" + name.replace("_", "-") + " must be positive")
    if a.jobs is not None and a.jobs > capsule_build.cpu_count():
        raise ValueError("--jobs exceeds admitted host CPUs")
    if a.plan_only and (target.backend != "bazel-capsule" or a.phase == "status" or a.describe):
        raise ValueError("--plan-only requires a capsule prepare/build phase and excludes --describe")
    if target.backend == "bazel":
        if a.phase == "status" and (a.seed_estate or a.fetch):
            raise ValueError("status does not acquire build inputs")
        if not a.estate_root and not os.environ.get("VASO_ESTATE_ROOT"):
            raise ValueError("--estate-root or VASO_ESTATE_ROOT is required")
        return target
    if a.phase == "status":
        capsule_build.absolute(a.output, "--output")
        if any((a.insula, a.spec, a.state, a.run_id, a.supervisor, a.jobs, a.max_seconds)):
            raise ValueError("status selects the recorded owner and request using only --target and --output")
        return target
    capsule_build.absolute(a.insula, "--insula")
    capsule_build.absolute(a.spec, "--spec")
    if a.plan_only:
        if a.output or a.supervisor or a.run_id:
            raise ValueError("plan-only does not allocate an attempt or select an external owner")
        if a.state:
            capsule_build.absolute(a.state, "--state")
        return target
    capsule_build.absolute(a.output, "--output")
    if a.supervisor:
        capsule_build.absolute(a.supervisor, "--supervisor")
        if a.state or a.run_id:
            raise ValueError("external supervisor and native state/run-id are mutually exclusive")
    else:
        capsule_build.absolute(a.state, "--state")
        if not a.run_id:
            raise ValueError("--run-id is required for native Insula supervision")
    return target


def estate(a):
    return (a.estate_root or Path(os.environ["VASO_ESTATE_ROOT"])).expanduser().resolve()


def dispatch(a):
    target = validate(a)
    if a.phase == "status":
        return None
    if target.backend == "bazel":
        argv = PYTHON + [str(FOUNDRY / "tools/build_variants.py"), "--estate-root", str(estate(a)),
                         "--line", target.cuda_line, "--profile", a.target.split("/")[-1]]
        if a.phase == "prepare":
            argv.append("--prepare-only")
        if a.seed_estate:
            argv += ["--seed-estate", str(a.seed_estate)]
        if a.fetch:
            argv.append("--fetch")
        return argv
    command = capsule_build.payload(a, "plan-only" if a.plan_only else "generated-per-attempt")
    if a.plan_only:
        return [str(a.insula), "plan", "-f", str(a.spec), "--", *command]
    return capsule_build.owner_command(a, command)


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
                              "argv": command, "output": str(a.output) if a.output else None,
                              "executed": False}, indent=2))
            return 0
        if a.phase == "status":
            if TARGETS[a.target].backend == "bazel-capsule":
                result = capsule_build.status(a.output, a.target)
            else:
                line, profile = a.target.split("/")
                loaded = latest_target_report(estate(a) / "agents/build-variants", line, profile)
                if loaded is None:
                    raise ValueError("no build attempt recorded for target " + a.target)
                result = target_result(loaded[0], a.target)
            print(json.dumps(result, indent=2))
            return 0
        if TARGETS[a.target].backend == "bazel-capsule":
            return capsule_build.execute(a)
        os.execv(command[0], command)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Foundry build contract: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
