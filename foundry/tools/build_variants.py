#!/usr/bin/env python3
"""Serial, logged preparation and offline builds of the supported wheel variants.

The CLI selects a matrix; Runner sequences preflight, input preparation, and
builds. Pinned bytes belong to build_inputs; durable commands and run records
belong to build_session; build_report stores and selects reports;
build_progress renders them; build_artifacts discovers wheel outputs from
Bazel events.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import sys
import time
from urllib.parse import urlparse

from build_artifacts import (
    artifact_size_mismatches,
    build_event_artifacts,
    missing_wheel_targets,
)
from build_inputs import (
    Asset,
    InputStore,
    digest,
    rules_python_asset_from_mirror,
    selected_assets,
)
from build_progress import Console
from build_report import latest_report
from build_session import BuildSession, StageFailure, abandon_stale_run
from build_selection import CUDA_LINES, PACKAGES, TARGETS, TOKENS

ROOT = Path(__file__).resolve().parents[2]


class Runner:
    """Apply Foundry build policy using a durable session and verified input store."""

    def __init__(
        self,
        args: argparse.Namespace,
        repo_root: Path = ROOT,
        console: Console | None = None,
        lock_fd: int | None = None,
    ):
        self.args, self.root = args, repo_root
        self.estate = args.estate_root.expanduser().resolve()
        self.lines = list(CUDA_LINES) if args.line == "all" else [args.line]
        self.profiles = list(PACKAGES) if args.profile == "all" else [args.profile]
        self.run = BuildSession(
            repo_root,
            self.estate,
            self.lines,
            self.profiles,
            TARGETS,
            console=console if console is not None else Console(color=args.color),
            status_interval=args.status_interval,
            lock_fd=lock_fd,
        )
        self.inputs = InputStore(
            self.estate,
            seed_estate=args.seed_estate,
        )

    def execute(self):
        self.preflight()
        self.prepare_inputs()
        self.run.check_source()
        if self.args.prepare_only:
            self.run.report["status"] = "prepared"
            return
        self.run.set_phase("native builds")
        for line in self.lines:
            for profile in self.profiles:
                self.build_variant(line, profile)
        self.run.report["status"] = "built"

    def prepare_asset(self, asset: Asset):
        source = self.inputs.find(asset)
        if source is None:
            if not self.args.fetch:
                raise RuntimeError(
                    f"missing pinned input {asset.owner} sha256={asset.sha256}; supply --seed-estate or --fetch"
                )
            downloads = self.estate / "vaso/cache/build-inputs"
            downloads.mkdir(parents=True, exist_ok=True)
            source = downloads / asset.sha256
            partial = downloads / (asset.sha256 + "." + self.run.output.name + ".part")
            for url in asset.urls:
                parsed = urlparse(url)
                if (
                    parsed.scheme != "https"
                    or not parsed.hostname
                    or parsed.hostname.endswith(".invalid")
                ):
                    continue
                pin_env = ["env", f"INSULA_FETCH_SHA256={asset.sha256}"]
                try:
                    self.run.stage(
                        "acquire-" + asset.sha256[:12],
                        [
                            *pin_env,
                            str(self.root / "insula/run.sh"),
                            "--line",
                            self.lines[0],
                            "--workspace",
                            str(self.root),
                            "--phase",
                            "fetch",
                            "--",
                            "/usr/bin/curl",
                            "-fL",
                            "--proto",
                            "=https",
                            "--tlsv1.2",
                            "--retry",
                            "3",
                            "--connect-timeout",
                            "30",
                            "--max-time",
                            "7200",
                            "-o",
                            "/vaso/cache/build-inputs/" + partial.name,
                            url,
                        ],
                        self.lines[0],
                        self.profiles[0],
                    )
                    actual = digest(partial)
                    if actual == asset.sha256:
                        os.replace(partial, source)
                        break
                    self.run.report.setdefault("integrity_failures", []).append(
                        {
                            "owner": asset.owner,
                            "expected": asset.sha256,
                            "actual": actual,
                        }
                    )
                    self.run.save()
                except StageFailure:
                    pass  # Preserve failed acquisition attempts; try the next declared mirror.
                finally:
                    partial.unlink(missing_ok=True)
            else:
                raise RuntimeError(
                    f"cannot acquire pinned input {asset.owner} sha256={asset.sha256}; curated archives need --seed-estate"
                )
        self.inputs.install(asset, source)
        self.run.report["inputs_prepared"] = (
            self.run.report.get("inputs_prepared", 0) + 1
        )

    def build_variant(self, line: str, profile: str):
        with self.run.variant(line, profile) as variant:
            events = f"/vaso/agents/build-variants/{self.run.output.name}/{line}-{profile}-events.jsonl"
            self.run.stage(
                f"build-{line}-{profile}",
                [
                    "foundry/run.sh",
                    "--bazel-build",
                    *TARGETS[profile],
                    *TOKENS[profile],
                    "--remote_download_outputs=all",
                    "--build_event_json_file=" + events,
                ],
                line,
                profile,
            )
            artifacts = build_event_artifacts(
                Path(variant["build_events"]), Path(variant["output_base"])
            )
            variant["artifacts"] = artifacts
            if not artifacts:
                raise RuntimeError(
                    f"{line}/{profile}: build passed but Bazel reported no release wheels; "
                    f"see {variant['build_events']}"
                )
            missing = [
                artifact["path"] for artifact in artifacts if not artifact["exists"]
            ]
            if missing:
                raise RuntimeError(
                    f"{line}/{profile}: Bazel release wheels are not materialized: "
                    + ", ".join(missing)
                )
            mismatches = artifact_size_mismatches(artifacts)
            if mismatches:
                raise RuntimeError(
                    f"{line}/{profile}: release wheel sizes differ from the inventory: "
                    + ", ".join(mismatches)
                    + f"; see {variant['build_events']}"
                )
            uncovered = missing_wheel_targets(
                artifacts, Path(variant["output_base"]), TARGETS[profile]
            )
            if uncovered:
                raise RuntimeError(
                    f"{line}/{profile}: Bazel reported no release wheels for: "
                    + ", ".join(uncovered)
                    + f"; see {variant['build_events']}"
                )

    def preflight(self):
        self.run.set_phase("preflight")
        self.run.stage(
            "materialize",
            [
                sys.executable,
                "-B",
                "insula/estate/estate.py",
                "--root",
                str(self.estate),
                "--required-gib",
                "100",
                "--materialize",
            ],
            self.lines[0],
            self.profiles[0],
        )
        for line in self.lines:
            self.run.stage(
                "doctor-" + line,
                [
                    "insula/execution/doctor.sh",
                    "preflight",
                    "--estate-root",
                    str(self.estate),
                    "--line",
                    line,
                    "--rootfs-lock",
                    "foundry/rootfs/cuda_ecosystem.lock.json",
                ],
                line,
                self.profiles[0],
            )
            self.run.stage(
                "tools-" + line,
                [
                    sys.executable,
                    "-B",
                    "insula/rootfs/foundry_tools.py",
                    "--rootfs",
                    str(self.estate / f"rootfs-lines/{line}/rootfs"),
                    "--verify",
                ],
                line,
                self.profiles[0],
            )

    def prepare_inputs(self):
        self.run.set_phase("input preparation")
        mirror = [
            sys.executable,
            "-B",
            "foundry/tools/bcr_mirror.py",
            "--estate-root",
            str(self.estate),
        ]
        self.run.stage(
            "modules",
            mirror + ([] if self.args.fetch else ["--offline"]),
            self.lines[0],
            self.profiles[0],
        )
        assets = selected_assets(self.root, self.profiles)
        assets.append(rules_python_asset_from_mirror(self.root, self.estate))
        assets = list(dict.fromkeys(assets))
        self.run.report.update(
            inputs_total=len(assets), inputs_prepared=0, current_input=None
        )
        self.run.event("inputs_planned", count=len(assets))
        self.run.save()
        if not self.args.fetch:
            missing = [asset for asset in assets if self.inputs.find(asset) is None]
            if missing:
                inventory = self.run.output / "missing-inputs.json"
                inventory.write_text(
                    json.dumps([vars(asset) for asset in missing], indent=2) + "\n"
                )
                raise RuntimeError(
                    f"{len(missing)} pinned inputs missing; see {inventory}; supply --seed-estate or --fetch"
                )
        for index, asset in enumerate(assets, start=1):
            self.run.report["current_input"] = asset.owner
            self.prepare_asset(asset)
            if index == len(assets) or index % 10 == 0:
                self.run.save()
                self.run.console.stage_progress(
                    {
                        "name": "pinned inputs",
                        "duration_seconds": time.monotonic()
                        - self.run.started_monotonic,
                        "progress": f"{index}/{len(assets)} prepared; {asset.owner}",
                    }
                )
        self.run.report["current_input"] = None
        self.run.save()


def _run(args: argparse.Namespace) -> int:
    """Inspect or execute a parsed request; the CLI owns setup diagnostics."""
    estate = args.estate_root.expanduser().resolve()
    state_root = estate / "agents/build-variants"
    console = Console(color=args.color)
    if args.status:
        loaded = latest_report(state_root)
        if loaded is None:
            print(
                f"no Foundry variant runs recorded under {state_root}", file=sys.stderr
            )
            return 2
        report, _ = loaded
        console.final(report)
        return 0
    lock_path = estate / "agents/build-variants.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(
                "another variant runner owns this estate; inspect it with "
                f"foundry/build.sh --estate-root {estate} --status",
                file=sys.stderr,
            )
            return 2
        stale = abandon_stale_run(state_root)
        if stale is not None:
            print(
                f"Marked stale run {stale.get('run_id', '-')} abandoned; "
                f"summary: {stale.get('summary_path', '-')}",
                flush=True,
            )
        runner = Runner(args, console=console, lock_fd=lock.fileno())
        console.banner(runner.run.report, runner.estate)

        def interrupted(signum, _frame):
            raise InterruptedError(f"runner interrupted by signal {signum}")

        previous_term = signal.signal(signal.SIGTERM, interrupted)
        exit_code = 0
        try:
            runner.execute()
        except (InterruptedError, KeyboardInterrupt) as exc:
            runner.run.finish("interrupted", str(exc) or "runner interrupted")
            exit_code = 130
        except (OSError, ValueError, RuntimeError) as exc:
            runner.run.finish("failed", str(exc))
            exit_code = 1
        else:
            runner.run.finish(runner.run.report["status"])
        finally:
            signal.signal(signal.SIGTERM, previous_term)
        console.final(runner.run.report)
    return exit_code


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="foundry/build.sh",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""cache model:
  Reusing the same estate and line/profile preserves that variant's Bazel
  output base. Profiles on one CUDA line also share its Bazel disk cache.
  Cache hits still require identical declared inputs and action keys.

  --seed-estate imports only SHA-256-verified source blobs. It does not import
  output bases, action-cache entries, native state, or previous build results.
  Use foundry/full_rebuild.sh for a new estate and a cold full-matrix proof.

Bazel progress legend:
  "local" means the displayed action is executing on this host.
  "disk-cache" means the disk-cache backend is enabled, not that the displayed
  action was a cache hit. Native PyTorch and jaxlib are package-sized actions;
  their internal CMake/Ninja compile steps are not separate Bazel actions.
""",
    )
    parser.add_argument(
        "--estate-root",
        type=Path,
        default=os.environ.get("VASO_ESTATE_ROOT"),
        help="owned estate with matching rootfs/tooling; defaults to VASO_ESTATE_ROOT",
    )
    parser.add_argument("--line", choices=(*CUDA_LINES, "all"), default="cu129")
    parser.add_argument("--profile", choices=(*PACKAGES, "all"), default="torch")
    parser.add_argument(
        "--seed-estate",
        type=Path,
        help="reuse verified source blobs from this estate's repository cache/distdirs",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="allow separate pinned acquisition phases; native builds remain offline",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="prepare and verify inputs without starting native builds",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="pretty-print the latest durable run state without starting a build",
    )
    parser.add_argument(
        "--status-interval",
        type=float,
        default=30.0,
        metavar="SECONDS",
        help="live progress and durable-state refresh interval (default: 30)",
    )
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="terminal color mode (default: auto)",
    )
    args = parser.parse_args(argv)
    if not args.estate_root:
        parser.error("--estate-root or VASO_ESTATE_ROOT is required")
    if not math.isfinite(args.status_interval) or args.status_interval <= 0:
        parser.error("--status-interval must be finite and positive")
    args.estate_root = Path(args.estate_root)
    try:
        return _run(args)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Foundry build: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
