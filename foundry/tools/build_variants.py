#!/usr/bin/env python3
"""Serial, logged preparation and offline builds of the supported wheel variants."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from urllib.parse import urlparse
import uuid

from bcr_mirror import archive_store_path, load_manifest, seed_repository_cache
from build_progress import (
    Console,
    atomic_json,
    build_event_artifacts,
    latest_pointer,
    latest_report,
    report_text,
    tail_lines,
    utc_now,
)
from native_source_versions import module_text_with_includes

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = {
    "torch": ("pytorch", "torchvision", "torchaudio", "triton"),
    "jax": ("jaxlib",),
}
TARGETS = {
    "torch": [f"//native/{p}:{p}_release_wheel" for p in PACKAGES["torch"]],
    "jax": ["//native/jaxlib:jaxlib_release_wheels"],
}
TOKENS = {
    "torch": [f"--//native/{p}:token=build-native-{p}" for p in PACKAGES["torch"]],
    "jax": ["--//native/jaxlib:token=build-native-llvm"],
}
SHA = re.compile(r"[0-9a-f]{64}\Z")
REPO = re.compile(r"@([A-Za-z0-9_.+-]+)//")
RULES_PYTHON_PLATFORM = "x86_64-unknown-linux-gnu"


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


class StageFailure(RuntimeError):
    """A failed command; acquisition may try another declared mirror."""


@dataclass(frozen=True)
class Asset:
    owner: str
    sha256: str
    urls: tuple[str, ...]
    filename: str | None = None  # Nested JAX inputs also need a distdir filename.


def repo_declarations(text: str) -> dict[str, dict]:
    """Read literal repository attributes, never execute the Starlark module."""
    repos = {}
    for stmt in ast.parse(text).body:
        if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
            continue
        attrs = {}
        for keyword in stmt.value.keywords:
            try:
                attrs[keyword.arg] = ast.literal_eval(keyword.value)
            except (ValueError, TypeError):
                # Non-literal unrelated settings do not supply archive identities.
                if keyword.arg and (
                    "sha256" in keyword.arg or keyword.arg.endswith("urls")
                ):
                    raise ValueError(
                        f"non-literal acquisition attribute: {keyword.arg}"
                    )
        if isinstance(attrs.get("name"), str):
            attrs["__refs__"] = [
                node.value
                for node in ast.walk(stmt.value)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            ]
            repos[attrs["name"]] = attrs
    return repos


def repo_assets(name: str, attrs: dict) -> list[Asset]:
    result = []
    for key, value in attrs.items():
        if not key.endswith(("sha256", "sha256s")):
            continue
        stem = key.removesuffix("sha256s").removesuffix("sha256")
        urls = attrs.get(stem + "urls", attrs.get(stem + "url"))
        if urls is None and stem == "patch_":
            urls = attrs.get("patches")
        if isinstance(urls, str):
            urls = [urls]
        if (
            not isinstance(urls, list)
            or not urls
            or not all(isinstance(u, str) for u in urls)
        ):
            raise ValueError(f"{name}: missing literal URLs for {key}")
        if isinstance(value, list):
            if len(value) != len(urls):
                raise ValueError(f"{name}: resource URL/hash count mismatch")
            pairs = [(sha, (url,)) for sha, url in zip(value, urls)]
        else:
            pairs = [(value, tuple(urls))]
        for sha, mirrors in pairs:
            if not isinstance(sha, str) or not SHA.fullmatch(sha):
                raise ValueError(f"{name}: invalid {key}")
            result.append(Asset(name, sha, mirrors))
    return result


def literal_assignment(text: str, name: str):
    """Read one literal top-level assignment from Python-compatible Starlark."""
    for stmt in ast.parse(text).body:
        if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
            continue
        target = stmt.targets[0]
        if isinstance(target, ast.Name) and target.id == name:
            try:
                return ast.literal_eval(stmt.value)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"non-literal Starlark assignment: {name}") from exc
    raise ValueError(f"missing Starlark assignment: {name}")


def rules_python_default(module_text: str) -> str:
    """Return the default minor selected by rules_python's module extension."""
    versions = []
    for stmt in ast.parse(module_text).body:
        if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
            continue
        call = stmt.value
        if not isinstance(call.func, ast.Attribute) or call.func.attr != "defaults":
            continue
        if not isinstance(call.func.value, ast.Name) or call.func.value.id != "python":
            continue
        for keyword in call.keywords:
            if keyword.arg == "python_version":
                value = ast.literal_eval(keyword.value)
                if isinstance(value, str):
                    versions.append(value)
    if len(versions) != 1:
        raise ValueError(
            f"rules_python: expected one default Python version, found {versions}"
        )
    return versions[0]


def rules_python_asset(module_text: str, versions_text: str) -> Asset:
    """Resolve rules_python's default Linux interpreter archive from its source."""
    minor = rules_python_default(module_text)
    mapping = literal_assignment(versions_text, "MINOR_MAPPING")
    versions = literal_assignment(versions_text, "TOOL_VERSIONS")
    base_url = literal_assignment(versions_text, "DEFAULT_RELEASE_BASE_URL")
    try:
        version = mapping[minor]
        release = versions[version]
        sha256 = release["sha256"][RULES_PYTHON_PLATFORM]
        url_templates = release["url"]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            f"rules_python: no {RULES_PYTHON_PLATFORM} runtime for Python {minor}"
        ) from exc
    if isinstance(url_templates, dict):
        url_templates = url_templates[RULES_PYTHON_PLATFORM]
    if isinstance(url_templates, str):
        url_templates = [url_templates]
    if not isinstance(url_templates, list) or not url_templates:
        raise ValueError(f"rules_python: invalid runtime URLs for Python {version}")
    urls = []
    for template in url_templates:
        rendered = template.format(
            platform=RULES_PYTHON_PLATFORM,
            python_version=version,
            build="install_only",
            ext="tar.gz",
        )
        url = rendered if "://" in rendered else base_url.rstrip("/") + "/" + rendered
        if not url.startswith("https://"):
            raise ValueError(f"rules_python: non-HTTPS runtime URL: {url}")
        urls.append(url)
    if not isinstance(sha256, str) or not SHA.fullmatch(sha256):
        raise ValueError(f"rules_python: invalid runtime hash for Python {version}")
    return Asset(
        f"rules_python:python_{minor}_{RULES_PYTHON_PLATFORM}", sha256, tuple(urls)
    )


def rules_python_asset_from_mirror(repo_root: Path, estate: Path) -> Asset:
    """Read rules_python's generated download pin from its verified BCR archive."""
    dependencies = repo_declarations((repo_root / "foundry/MODULE.bazel").read_text())
    try:
        version = dependencies["rules_python"]["version"]
    except (KeyError, TypeError) as exc:
        raise ValueError("missing literal rules_python dependency version") from exc
    manifest = repo_root / "foundry/examples/consumers/bcr-manifest.tsv"
    rows = [
        row
        for row in load_manifest(manifest)
        if row.module == "rules_python"
        and row.version == version
        and row.kind == "source-archive"
    ]
    if len(rows) != 1:
        raise ValueError(
            f"rules_python@{version}: expected one BCR source archive, found {len(rows)}"
        )
    archive = archive_store_path(estate / "sources/bcr", rows[0])
    if not archive.is_file() or digest(archive) != rows[0].sha256:
        raise ValueError(
            f"rules_python@{version}: missing or mismatched BCR source archive: {archive}"
        )
    with tarfile.open(archive, "r:*") as bundle:
        members = {
            member.name: member for member in bundle.getmembers() if member.isfile()
        }

        version_paths = [
            name for name in members if name.endswith("/python/versions.bzl")
        ]
        if len(version_paths) != 1:
            raise ValueError(
                f"rules_python@{version}: expected one python/versions.bzl in source archive, "
                f"found {len(version_paths)}"
            )
        archive_root = version_paths[0].removesuffix("/python/versions.bzl")

        def read_member(relative: str) -> str:
            name = f"{archive_root}/{relative}"
            if name not in members:
                raise ValueError(
                    f"rules_python@{version}: missing {relative} in source archive"
                )
            handle = bundle.extractfile(members[name])
            if handle is None:
                raise ValueError(f"rules_python@{version}: cannot read {name}")
            return handle.read().decode("utf-8")

        return rules_python_asset(
            read_member("MODULE.bazel"), read_member("python/versions.bzl")
        )


def jax_cccl_asset(repo_root: Path) -> Asset:
    """Read the separately cached CCCL source pin required by JAX preparation."""
    support = (
        repo_root / "foundry/native/jaxlib/committed_plan_support.py"
    ).read_text()
    sha256 = literal_assignment(support, "JAX_LOCAL_CCCL_ARCHIVE_SHA256")
    filename = (
        literal_assignment(support, "JAX_LOCAL_CCCL_ARCHIVE_STRIP_PREFIX") + ".tar.gz"
    )
    tag = filename.removeprefix("cccl-src-").removesuffix(".tar.gz")
    if not isinstance(sha256, str) or not SHA.fullmatch(sha256):
        raise ValueError("jaxlib: invalid CCCL archive hash")
    if not tag.startswith("v") or Path(filename).name != filename:
        raise ValueError(f"jaxlib: invalid CCCL release filename: {filename}")
    url = f"https://github.com/NVIDIA/cccl/releases/download/{tag}/{filename}"
    return Asset("jaxlib:cuda_cccl", sha256, (url,))


def selected_assets(repo_root: Path, profiles: list[str]) -> list[Asset]:
    foundry = repo_root / "foundry"
    repos = repo_declarations(module_text_with_includes(foundry))
    pending = []
    for profile in profiles:
        for package in PACKAGES[profile]:
            for path in (foundry / "native" / package).iterdir():
                if path.name == "BUILD.bazel" or path.suffix == ".bzl":
                    pending.extend(REPO.findall(path.read_text()))
    visited = set()
    assets = []
    while pending:
        name = pending.pop()
        if name in visited or name not in repos:
            continue  # Bzlmod libraries are provisioned by bcr_mirror.py.
        visited.add(name)
        attrs = repos[name]
        pending.extend(REPO.findall(repr(attrs)))
        assets.extend(repo_assets(name, attrs))
    if "jax" in profiles:
        manifest = json.loads(
            (foundry / "native/jaxlib/nested_http_archives.json").read_text()
        )
        archives = manifest["archives"]
        if manifest["archive_count"] != len(archives):
            raise ValueError("JAX nested archive count mismatch")
        for item in archives:
            name, sha, filename, urls = (
                item[k] for k in ("name", "sha256", "filename", "urls")
            )
            if (
                not SHA.fullmatch(sha)
                or Path(filename).name != filename
                or filename in ("", ".", "..")
            ):
                raise ValueError(f"invalid nested JAX input: {name}")
            if (
                not isinstance(urls, list)
                or not urls
                or any(not u.startswith("https://") for u in urls)
            ):
                raise ValueError(f"invalid nested JAX URLs: {name}")
            assets.append(Asset("jax-nested:" + name, sha, tuple(urls), filename))
        assets.append(jax_cccl_asset(repo_root))
    if not assets:
        raise ValueError("selected build has no declared source closure")
    return list(dict.fromkeys(assets))


def source_identity(repo_root: Path) -> str:
    """Detect source edits between stages, including uncommitted runner changes."""
    names = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=repo_root,
    ).split(b"\0")
    result = hashlib.sha256()
    for raw in sorted(set(names) - {b""}):
        path = repo_root / os.fsdecode(raw)
        result.update(raw + b"\0")
        if path.is_symlink():
            result.update(b"link\0" + os.fsencode(os.readlink(path)))
        elif path.is_file():
            result.update(str(path.stat().st_mode & 0o777).encode() + b"\0")
            result.update(bytes.fromhex(digest(path)))
        else:
            result.update(b"missing")
    return result.hexdigest()


def planned_variants(
    estate: Path, output: Path, lines: list[str], profiles: list[str]
) -> list[dict]:
    """Describe the complete requested matrix before the first build starts."""

    return [
        {
            "line": line,
            "profile": profile,
            "status": "pending",
            "targets": TARGETS[profile],
            "build_events": str(output / f"{line}-{profile}-events.jsonl"),
            "output_base": str(
                estate / f"vaso/lines/{line}/cache/bazel/output-base-{profile}"
            ),
            "artifacts": [],
        }
        for line in lines
        for profile in profiles
    ]


def abandon_stale_run(state_root: Path) -> dict | None:
    """Close a running record after its estate lock has become available."""

    loaded = latest_report(state_root)
    if loaded is None:
        return None
    report, summary = loaded
    if report.get("status") != "running":
        return None
    try:
        summary.parent.relative_to(state_root.resolve())
    except ValueError as exc:
        raise RuntimeError(
            f"refusing stale summary outside state root: {summary}"
        ) from exc
    now = utc_now()
    reason = "previous runner stopped without recording a terminal state"
    for stage in report.get("stages", []):
        if isinstance(stage, dict) and stage.get("status") == "running":
            stage.update(status="abandoned", finished_at=now)
            if stage.get("log"):
                stage["tail"] = tail_lines(Path(stage["log"]), count=8)
    for variant in report.get("variants", []):
        if isinstance(variant, dict) and variant.get("status") == "running":
            variant.update(status="abandoned", finished_at=now)
    report.update(
        status="abandoned",
        phase="complete",
        current_stage=None,
        finished_at=now,
        updated_at=now,
        error=reason,
    )
    report.setdefault("summary_path", str(summary))
    report.setdefault("events_path", str(summary.parent / "events.jsonl"))
    report.setdefault("result_path", str(summary.parent / "result.txt"))
    events = Path(report["events_path"])
    events.parent.mkdir(parents=True, exist_ok=True)
    with events.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"at": now, "event": "run_abandoned", "reason": reason}) + "\n"
        )
        handle.flush()
        os.fsync(handle.fileno())
    atomic_json(summary, report)
    Path(report["result_path"]).write_text(report_text(report), encoding="utf-8")
    atomic_json(state_root / "latest.json", latest_pointer(report))
    return report


class Runner:
    def __init__(
        self,
        args: argparse.Namespace,
        repo_root: Path = ROOT,
        console: Console | None = None,
        lock_fd: int | None = None,
    ):
        self.args, self.root = args, repo_root
        self.estate = args.estate_root.expanduser().resolve()
        self.lines = ["cu129", "cu130"] if args.line == "all" else [args.line]
        self.profiles = ["torch", "jax"] if args.profile == "all" else [args.profile]
        run_id = (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + uuid.uuid4().hex[:8]
        )
        self.state_root = self.estate / "agents/build-variants"
        self.output = self.state_root / run_id
        self.output.mkdir(parents=True)
        self.console = console if console is not None else Console(color=args.color)
        self.lock_fd = lock_fd
        self.started_monotonic = time.monotonic()
        self.event_sequence = 0
        self.identity = source_identity(self.root)
        summary = self.output / "summary.json"
        self.report = {
            "schema_version": 2,
            "run_id": run_id,
            "status": "running",
            "phase": "starting",
            "pid": os.getpid(),
            "started_at": utc_now(),
            "updated_at": utc_now(),
            "duration_seconds": 0.0,
            "lines": self.lines,
            "profiles": self.profiles,
            "source_identity": self.identity,
            "estate_root": str(self.estate),
            "summary_path": str(summary),
            "events_path": str(self.output / "events.jsonl"),
            "result_path": str(self.output / "result.txt"),
            "console_mode": self.console.mode,
            "current_stage": None,
            "stages": [],
            "variants": planned_variants(
                self.estate, self.output, self.lines, self.profiles
            ),
            "qualification": "not performed",
        }
        self.event("run_started", lines=self.lines, profiles=self.profiles)
        self.save()

    def event(self, name: str, **fields):
        self.event_sequence += 1
        record = {
            "sequence": self.event_sequence,
            "at": utc_now(),
            "event": name,
            **fields,
        }
        path = Path(self.report["events_path"])
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def save(self):
        self.report["updated_at"] = utc_now()
        if "finished_at" not in self.report:
            self.report["duration_seconds"] = round(
                max(0.0, time.monotonic() - self.started_monotonic), 3
            )
        atomic_json(Path(self.report["summary_path"]), self.report)
        atomic_json(self.state_root / "latest.json", latest_pointer(self.report))

    def set_phase(self, phase: str):
        if self.report.get("phase") == phase:
            return
        self.report["phase"] = phase
        self.event("phase_changed", phase=phase)
        self.save()
        self.console.phase(phase)

    def finish(self, status: str, error: str | None = None):
        now = utc_now()
        if status == "prepared":
            for variant in self.report["variants"]:
                if variant["status"] == "pending":
                    variant["status"] = "skipped"
        elif status in {"failed", "interrupted"}:
            for variant in self.report["variants"]:
                if variant["status"] == "running":
                    variant["status"] = status
                    variant["finished_at"] = now
        self.report.update(
            status=status,
            phase="complete",
            current_stage=None,
            finished_at=now,
            duration_seconds=round(
                max(0.0, time.monotonic() - self.started_monotonic), 3
            ),
        )
        if error:
            self.report["error"] = error
        self.event("run_finished", status=status, error=error)
        self.save()
        Path(self.report["result_path"]).write_text(
            report_text(self.report), encoding="utf-8"
        )

    def check_source(self):
        if source_identity(self.root) != self.identity:
            raise RuntimeError(
                "checkout changed during execution; stop and restart with a fixed checkout"
            )

    def stage(self, name: str, command: list[str], line: str, profile: str):
        self.check_source()
        log = self.output / f"{len(self.report['stages']) + 1:03d}-{name}.log"
        env = dict(
            os.environ,
            VASO_ESTATE_ROOT=str(self.estate),
            VASO_CUDA_LINE=line,
            VASO_PROFILE=profile,
            VASO_AGENT="build-variants",
            VASO_GPUS="0",
        )
        # Each variant owns outputs; retain constant sandbox paths and canonical flags.
        env["VASO_BAZEL_OB"] = str(
            self.estate / f"vaso/lines/{line}/cache/bazel/output-base-{profile}"
        )
        env["VASO_BAZEL_DISK_CACHE"] = str(
            self.estate / f"vaso/lines/{line}/cache/bazel/disk-cache"
        )
        started_at = utc_now()
        started_monotonic = time.monotonic()
        entry = {
            "name": name,
            "line": line,
            "profile": profile,
            "log": str(log),
            "status": "running",
            "started_at": started_at,
            "duration_seconds": 0.0,
            "log_bytes": 0,
        }
        self.report["stages"].append(entry)
        self.report["current_stage"] = name
        self.event(
            "stage_started",
            stage=name,
            line=line,
            profile=profile,
            log=str(log),
        )
        self.save()
        self.console.stage_started(entry)
        interrupted = []
        try:
            with log.open("wb") as handle:
                with subprocess.Popen(
                    command,
                    cwd=self.root,
                    env=env,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                    pass_fds=(self.lock_fd,) if self.lock_fd is not None else (),
                ) as proc:
                    entry["pid"] = proc.pid
                    self.save()
                    previous = {}

                    def forward(signum, _frame):
                        interrupted.append(signum)
                        try:
                            os.killpg(proc.pid, signum)
                        except ProcessLookupError:
                            pass

                    interval = max(0.1, float(self.args.status_interval))
                    next_update = time.monotonic() + interval
                    try:
                        for signum in (signal.SIGINT, signal.SIGTERM):
                            previous[signum] = signal.signal(signum, forward)
                        while True:
                            try:
                                rc = proc.wait(timeout=min(1.0, interval))
                                break
                            except subprocess.TimeoutExpired:
                                pass
                            now = time.monotonic()
                            if now < next_update:
                                continue
                            progress = tail_lines(log, count=1)
                            entry.update(
                                duration_seconds=round(
                                    max(0.0, now - started_monotonic), 3
                                ),
                                log_bytes=log.stat().st_size if log.exists() else 0,
                                progress=progress[-1] if progress else "working",
                            )
                            self.save()
                            self.console.stage_progress(entry)
                            next_update = now + interval
                    finally:
                        for signum, handler in previous.items():
                            signal.signal(signum, handler)
        except OSError as exc:
            entry.update(status="failed", error=str(exc))
            rc = getattr(exc, "errno", 1) or 1
        status = (
            "interrupted"
            if interrupted or rc < 0
            else "passed"
            if rc == 0
            else "failed"
        )
        tail = tail_lines(log, count=1 if status == "passed" else 8)
        entry.update(
            returncode=rc,
            status=status,
            finished_at=utc_now(),
            duration_seconds=round(max(0.0, time.monotonic() - started_monotonic), 3),
            log_bytes=log.stat().st_size if log.exists() else 0,
            tail=tail,
        )
        if tail:
            entry["progress"] = tail[-1]
        self.report["current_stage"] = None
        self.event(
            "stage_finished",
            stage=name,
            status=status,
            returncode=rc,
            duration_seconds=entry["duration_seconds"],
            log=str(log),
        )
        self.save()
        self.console.stage_finished(entry)
        if status == "interrupted":
            raise InterruptedError(f"{name} interrupted; see {log}")
        if rc:
            raise StageFailure(f"{name} failed (exit {rc}); see {log}")
        self.check_source()

    def available(self, asset: Asset) -> Path | None:
        cache_rel = (
            Path("vaso/cache/bazel/repository-cache/content_addressable/sha256")
            / asset.sha256
            / "file"
        )
        candidates = [self.estate / cache_rel]
        if self.args.seed_estate:
            candidates.append(self.args.seed_estate.expanduser().resolve() / cache_rel)
        filenames = {Path(urlparse(url).path).name for url in asset.urls}
        if asset.filename:
            filenames.add(asset.filename)
        for estate in [
            self.estate,
            *(
                [self.args.seed_estate.expanduser().resolve()]
                if self.args.seed_estate
                else []
            ),
        ]:
            for sub in ("native", "pytorch", "triton", "jax", "bazel"):
                candidates.extend(
                    estate / "sources" / sub / "distdir" / name
                    for name in filenames
                    if name
                )
        for candidate in candidates:
            if candidate.is_file() and digest(candidate) == asset.sha256:
                return candidate
        return None

    def prepare_asset(self, asset: Asset):
        source = self.available(asset)
        if source is None:
            if not self.args.fetch:
                raise RuntimeError(
                    f"missing pinned input {asset.owner} sha256={asset.sha256}; supply --seed-estate or --fetch"
                )
            downloads = self.estate / "vaso/cache/build-inputs"
            downloads.mkdir(parents=True, exist_ok=True)
            source = downloads / asset.sha256
            partial = downloads / (asset.sha256 + "." + self.output.name + ".part")
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
                    self.stage(
                        "acquire-" + asset.sha256[:12],
                        [
                            *pin_env,
                            str(self.root / "insula/execution/insula.sh"),
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
                    self.report.setdefault("integrity_failures", []).append(
                        {
                            "owner": asset.owner,
                            "expected": asset.sha256,
                            "actual": actual,
                        }
                    )
                    self.save()
                except StageFailure:
                    pass  # Preserve failed acquisition attempts; try the next declared mirror.
                finally:
                    partial.unlink(missing_ok=True)
            else:
                raise RuntimeError(
                    f"cannot acquire pinned input {asset.owner} sha256={asset.sha256}; curated archives need --seed-estate"
                )
        cache = self.estate / "vaso/cache/bazel/repository-cache"
        seed_repository_cache(
            cache,
            source,
            asset.sha256,
            canonical_ids=(*asset.urls, " ".join(asset.urls)),
        )
        if asset.filename:
            dest = self.estate / "sources/jax/distdir" / asset.filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.is_file() and digest(dest) != asset.sha256:
                raise RuntimeError(f"conflicting nested archive: {dest}")
            if not dest.exists():
                temp = dest.with_name(dest.name + "." + self.output.name + ".tmp")
                shutil.copyfile(source, temp)
                os.replace(temp, dest)
        self.report["inputs_prepared"] = self.report.get("inputs_prepared", 0) + 1

    def build_variant(self, line: str, profile: str):
        variant = next(
            item
            for item in self.report["variants"]
            if item["line"] == line and item["profile"] == profile
        )
        started_monotonic = time.monotonic()
        variant.update(status="running", started_at=utc_now())
        self.event("variant_started", line=line, profile=profile)
        self.save()
        events = f"/vaso/agents/build-variants/{self.output.name}/{line}-{profile}-events.jsonl"
        try:
            self.stage(
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
        except (OSError, ValueError, RuntimeError, KeyboardInterrupt) as exc:
            status = (
                "interrupted"
                if isinstance(exc, (InterruptedError, KeyboardInterrupt))
                else "failed"
            )
            variant.update(
                status=status,
                finished_at=utc_now(),
                duration_seconds=round(
                    max(0.0, time.monotonic() - started_monotonic), 3
                ),
                error=str(exc),
            )
            if self.report["stages"]:
                variant["log"] = self.report["stages"][-1].get("log")
            self.event("variant_finished", line=line, profile=profile, status=status)
            self.save()
            raise
        variant.update(
            status="built",
            finished_at=utc_now(),
            duration_seconds=round(max(0.0, time.monotonic() - started_monotonic), 3),
            artifacts=artifacts,
        )
        if self.report["stages"]:
            variant["log"] = self.report["stages"][-1].get("log")
        self.event(
            "variant_finished",
            line=line,
            profile=profile,
            status="built",
            artifacts=len(artifacts),
        )
        self.save()

    def execute(self):
        self.set_phase("preflight")
        self.stage(
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
        expected = digest(self.root / "foundry/rootfs/cuda_ecosystem.lock.json")
        for line in self.lines:
            manifest = self.estate / f"rootfs-lines/{line}/rootfs-bundle.json"
            if (
                not manifest.is_file()
                or json.loads(manifest.read_text()).get("lock", {}).get("sha256")
                != expected
            ):
                raise RuntimeError(
                    f"{line}: supply a prepared rootfs matching this checkout's ecosystem lock; see docs/estate.md"
                )
            self.stage(
                "tools-" + line,
                [
                    sys.executable,
                    "-B",
                    "insula/rootfs/foundry_tools.py",
                    "--rootfs",
                    str(manifest.parent / "rootfs"),
                    "--verify",
                ],
                line,
                self.profiles[0],
            )
            self.stage(
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
        self.set_phase("input preparation")
        mirror = [
            sys.executable,
            "-B",
            "foundry/tools/bcr_mirror.py",
            "--estate-root",
            str(self.estate),
        ]
        self.stage(
            "modules",
            mirror + ([] if self.args.fetch else ["--offline"]),
            self.lines[0],
            self.profiles[0],
        )
        assets = selected_assets(self.root, self.profiles)
        assets.append(rules_python_asset_from_mirror(self.root, self.estate))
        assets = list(dict.fromkeys(assets))
        self.report.update(
            inputs_total=len(assets), inputs_prepared=0, current_input=None
        )
        self.event("inputs_planned", count=len(assets))
        self.save()
        if not self.args.fetch:
            missing = [asset for asset in assets if self.available(asset) is None]
            if missing:
                inventory = self.output / "missing-inputs.json"
                inventory.write_text(
                    json.dumps([vars(asset) for asset in missing], indent=2) + "\n"
                )
                raise RuntimeError(
                    f"{len(missing)} pinned inputs missing; see {inventory}; supply --seed-estate or --fetch"
                )
        for index, asset in enumerate(assets, start=1):
            self.report["current_input"] = asset.owner
            self.prepare_asset(asset)
            if index == len(assets) or index % 10 == 0:
                self.save()
                self.console.stage_progress(
                    {
                        "name": "pinned inputs",
                        "duration_seconds": time.monotonic() - self.started_monotonic,
                        "progress": f"{index}/{len(assets)} prepared; {asset.owner}",
                    }
                )
        self.report["current_input"] = None
        self.save()
        self.check_source()
        if self.args.prepare_only:
            self.report["status"] = "prepared"
            return
        self.set_phase("native builds")
        for line in self.lines:
            for profile in self.profiles:
                self.build_variant(line, profile)
        self.report["status"] = "built"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--estate-root",
        type=Path,
        default=os.environ.get("VASO_ESTATE_ROOT"),
        help="owned estate with matching rootfs/tooling; defaults to VASO_ESTATE_ROOT",
    )
    parser.add_argument("--line", choices=("cu129", "cu130", "all"), default="cu129")
    parser.add_argument("--profile", choices=("torch", "jax", "all"), default="torch")
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
    if args.status_interval <= 0:
        parser.error("--status-interval must be greater than zero")
    args.estate_root = Path(args.estate_root)
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
        console.banner(runner.report, runner.estate)

        def interrupted(signum, _frame):
            raise InterruptedError(f"runner interrupted by signal {signum}")

        previous_term = signal.signal(signal.SIGTERM, interrupted)
        exit_code = 0
        try:
            runner.execute()
        except (InterruptedError, KeyboardInterrupt) as exc:
            runner.finish("interrupted", str(exc) or "runner interrupted")
            exit_code = 130
        except (OSError, ValueError, RuntimeError) as exc:
            runner.finish("failed", str(exc))
            exit_code = 1
        else:
            runner.finish(runner.report["status"])
        finally:
            signal.signal(signal.SIGTERM, previous_term)
        console.final(runner.report)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
