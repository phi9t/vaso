#!/usr/bin/env python3
"""Publish and retrieve private release artifacts through ORAS.

Dry-run mode writes to an OCI layout directory under ``$VASO_ESTATE_ROOT`` and
never contacts a registry.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLE_SH = REPO_ROOT / "insula" / "rootfs" / "bundle.sh"
DEFAULT_REGISTRY = "ghcr.io/phi9t"
DEFAULT_MAX_LAYER_BYTES = 2_000_000_000
LINE_CHOICES = ("cu129", "cu130")
PROFILE_CHOICES = ("torch", "jax")
TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
ANNOTATION_PREFIX = "io.vaso.file."


class StoreError(Exception):
    pass


class CommandError(StoreError):
    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


def fail(message: str) -> None:
    raise StoreError(message)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"missing JSON file: {path}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON file: {path}: {exc}")
    if not isinstance(value, dict):
        fail(f"{path}: expected a JSON object")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_rel(path: Path, root: Path) -> str:
    rel = path.relative_to(root).as_posix()
    if rel in {"", ".", ".."} or rel.startswith("../") or rel.startswith("/"):
        fail(f"unsafe artifact path: {rel}")
    if ":" in rel:
        fail(f"artifact paths cannot contain ':' for ORAS file specs: {rel}")
    return rel


def safe_annotation_value(value: object) -> str:
    text = str(value)
    if "\n" in text or "\r" in text:
        fail(f"annotation values cannot contain newlines: {text!r}")
    return text


def media_type(path: str) -> str:
    if path.endswith(".json"):
        return "application/json"
    if path.endswith(".whl"):
        return "application/vnd.python.wheel"
    if path.endswith(".tar.zst") or ".tar.zst.part" in path:
        return "application/vnd.vaso.rootfs.bundle.layer.v1+zstd"
    if path.endswith(".txt") or path == "SHA256SUMS":
        return "text/plain"
    return "application/octet-stream"


def collect_tree_files(root: Path) -> list[dict[str, Any]]:
    if not root.is_dir():
        fail(f"directory not found: {root}")
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            fail(f"refusing to publish symlink: {path}")
        if not path.is_file():
            continue
        rel = safe_rel(path, root)
        entries.append(
            {
                "path": rel,
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
                "media_type": media_type(rel),
            }
        )
    if not entries:
        fail(f"no files found under {root}")
    return entries


def file_annotations(entries: list[dict[str, Any]]) -> dict[str, str]:
    annotations: dict[str, str] = {
        "io.vaso.file.count": str(len(entries)),
    }
    for index, entry in enumerate(entries):
        prefix = f"{ANNOTATION_PREFIX}{index:04d}"
        annotations[f"{prefix}.path"] = safe_annotation_value(entry["path"])
        annotations[f"{prefix}.sha256"] = safe_annotation_value(entry["sha256"])
        annotations[f"{prefix}.size"] = safe_annotation_value(entry["size"])
    return annotations


def parse_file_annotations(annotations: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        count = int(str(annotations["io.vaso.file.count"]))
    except (KeyError, ValueError):
        fail("manifest is missing io.vaso.file.count annotation")
    entries: list[dict[str, Any]] = []
    for index in range(count):
        prefix = f"{ANNOTATION_PREFIX}{index:04d}"
        path = str(annotations.get(f"{prefix}.path") or "")
        digest = str(annotations.get(f"{prefix}.sha256") or "")
        size_s = str(annotations.get(f"{prefix}.size") or "")
        if not path or not re.fullmatch(r"[0-9a-f]{64}", digest):
            fail(f"manifest is missing file annotation set {index:04d}")
        if path.startswith("/") or path in {".", ".."} or path.startswith("../") or "/../" in path:
            fail(f"manifest contains unsafe path annotation: {path}")
        try:
            size = int(size_s)
        except ValueError:
            fail(f"manifest contains invalid file size for {path}: {size_s}")
        entries.append({"path": path, "sha256": digest, "size": size})
    return entries


def verify_files(root: Path, entries: list[dict[str, Any]]) -> None:
    for entry in entries:
        path = root / str(entry["path"])
        if not path.is_file():
            fail(f"pulled artifact is missing file: {entry['path']}")
        size = path.stat().st_size
        if size != int(entry["size"]):
            fail(f"size mismatch for {entry['path']}: expected {entry['size']} actual {size}")
        actual = sha256_file(path)
        if actual != entry["sha256"]:
            fail(f"sha256 mismatch for {entry['path']}: expected {entry['sha256']} actual {actual}")


def estate_root() -> Path:
    raw = os.environ.get("VASO_ESTATE_ROOT")
    if not raw:
        fail("set VASO_ESTATE_ROOT")
    return Path(raw).resolve()


def ensure_under_estate(path: Path, estate: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(estate)
    except ValueError:
        fail(f"{label} must be under VASO_ESTATE_ROOT for dry-run/local OCI layout: {resolved}")
    return resolved


def artifact_package(kind: str, line: str, profile: str | None = None) -> str:
    if kind == "release":
        if not profile:
            fail("release artifacts require --profile")
        return f"vaso-release-{profile}-{line}"
    if kind == "rootfs":
        return f"vaso-rootfs-{line}"
    fail(f"unknown artifact kind: {kind}")
    raise AssertionError("unreachable")


def normalize_digest(value: str) -> str:
    digest = value.strip()
    if re.fullmatch(r"[0-9a-f]{64}", digest):
        return f"sha256:{digest}"
    if re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        return digest
    fail(f"invalid sha256 digest: {value}")
    raise AssertionError("unreachable")


def target_reference(
    *,
    registry: str | None,
    dry_run: bool,
    kind: str,
    line: str,
    profile: str | None,
    tag: str | None,
    digest: str | None = None,
) -> tuple[str, str, Path | None]:
    package = artifact_package(kind, line, profile)
    if dry_run:
        estate = estate_root()
        layout_root = ensure_under_estate(
            Path(registry).expanduser() if registry else estate / "publish" / "oci-layout",
            estate,
            "--registry",
        )
        layout = layout_root / package
        layout.parent.mkdir(parents=True, exist_ok=True)
        base = str(layout)
        if digest:
            return f"{base}@{normalize_digest(digest)}", package, layout
        if not tag:
            fail("--tag is required when no --digest is supplied")
        return f"{base}:{tag}", package, layout
    remote = (registry or DEFAULT_REGISTRY).rstrip("/")
    base = f"{remote}/{package}"
    if digest:
        return f"{base}@{normalize_digest(digest)}", package, None
    if not tag:
        fail("--tag is required when no --digest is supplied")
    return f"{base}:{tag}", package, None


def find_oras(args: argparse.Namespace) -> Path:
    raw = getattr(args, "oras_bin", None) or os.environ.get("ORAS_BIN")
    if raw:
        path = Path(raw)
        if path.is_file():
            return path
        fail(f"oras binary not found: {path}")
    estate = estate_root()
    estate_oras = estate / "opt-vaso" / "bin" / "oras"
    if estate_oras.is_file():
        return estate_oras
    found = shutil.which("oras")
    if found:
        return Path(found)
    fail(f"oras not found; run {REPO_ROOT / 'tools' / 'publish' / 'install_oras.sh'}")
    raise AssertionError("unreachable")


def command_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("GHCR_TOKEN", None)
    if extra:
        env.update(extra)
    return env


def redact(text: str, secrets: list[str]) -> str:
    cleaned = text
    for secret in secrets:
        if secret:
            cleaned = cleaned.replace(secret, "<redacted>")
    return cleaned


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    stdin: str | None = None,
    secrets: list[str] | None = None,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    secret_values = secrets or []
    result = subprocess.run(
        [str(part) for part in command],
        cwd=str(cwd) if cwd is not None else None,
        input=stdin,
        env=command_env(env_extra),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        parts = [f"command failed rc={result.returncode}: {command[0]} {command[1] if len(command) > 1 else ''}".strip()]
        if result.stdout:
            parts.append(redact(result.stdout, secret_values).rstrip())
        if result.stderr:
            parts.append(redact(result.stderr, secret_values).rstrip())
        raise CommandError("\n".join(parts), result.returncode)
    return result


def oras_args(args: argparse.Namespace, command: list[str]) -> list[str]:
    return [str(find_oras(args)), *command]


def run_oras(
    args: argparse.Namespace,
    command: list[str],
    *,
    cwd: Path | None = None,
    stdin: str | None = None,
    secrets: list[str] | None = None,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return run_command(oras_args(args, command), cwd=cwd, stdin=stdin, secrets=secrets, env_extra=env_extra)


def oras_layout_flag(dry_run: bool) -> list[str]:
    return ["--oci-layout"] if dry_run else []


def parse_digest(text: str) -> str:
    match = re.search(r"sha256:[0-9a-f]{64}", text)
    if not match:
        fail(f"could not find digest in ORAS output: {text.strip()}")
    return match.group(0)


def resolve_digest(args: argparse.Namespace, ref: str, env_extra: dict[str, str] | None = None) -> str:
    result = run_oras(args, ["resolve", *oras_layout_flag(args.dry_run), ref], env_extra=env_extra)
    return parse_digest(result.stdout)


def fetch_manifest(args: argparse.Namespace, ref: str, env_extra: dict[str, str] | None = None) -> dict[str, Any]:
    result = run_oras(args, ["manifest", "fetch", *oras_layout_flag(args.dry_run), ref], env_extra=env_extra)
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        fail(f"oras manifest fetch returned invalid JSON: {exc}")
    if not isinstance(value, dict):
        fail("oras manifest fetch did not return a JSON object")
    return value


def emit(args: argparse.Namespace, value: dict[str, Any]) -> int:
    if args.json:
        print(json.dumps(value, indent=2, sort_keys=True))
    else:
        for key in ("reference", "digest", "package", "tag"):
            if key in value:
                print(f"{key}: {value[key]}")
    return 0


def release_metadata(release_dir: Path, profile: str, line: str) -> tuple[str, str | None]:
    cut = read_json_object(release_dir / "cut.json") if (release_dir / "cut.json").exists() else {}
    release = read_json_object(release_dir / "provenance" / "release.json") if (release_dir / "provenance" / "release.json").exists() else {}
    for source_name, source in (("cut.json", cut), ("provenance/release.json", release)):
        for field, expected in (("profile", profile), ("line", line)):
            if source.get(field) and source.get(field) != expected:
                fail(f"{source_name} {field} is {source[field]!r}, expected {expected!r}")
    acceptance = read_json_object(release_dir / "acceptance.json") if (release_dir / "acceptance.json").exists() else {}
    provenance = read_json_object(release_dir / "provenance.json") if (release_dir / "provenance.json").exists() else {}
    commit = str(
        cut.get("sha")
        or cut.get("commit")
        or release.get("sha")
        or release.get("commit")
        or provenance.get("sha")
        or acceptance.get("commit")
        or ""
    ).strip()
    if not commit:
        git = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        commit = git.stdout.strip() if git.returncode == 0 else "unknown"
    version = str(cut.get("version") or "").strip() or None
    return commit, version


def choose_release_tag(args: argparse.Namespace, commit: str, version: str | None) -> str:
    if args.tag:
        tag = args.tag
    elif version and TAG_RE.fullmatch(version):
        tag = version
    else:
        tag = commit[:12] if commit else "unknown"
    if not TAG_RE.fullmatch(tag):
        fail(f"not a valid OCI tag: {tag}")
    return tag


def validate_release_dir(release_dir: Path) -> None:
    if not any(path.is_file() and path.suffix == ".whl" for path in release_dir.rglob("*")):
        fail(f"release dir contains no wheel files: {release_dir}")
    sbom_candidates = [release_dir / "sbom.json", *sorted((release_dir / "sbom").glob("*.json"))]
    provenance_candidates = [
        release_dir / "acceptance.json",
        release_dir / "provenance.json",
        release_dir / "provenance" / "release.json",
        *sorted((release_dir / "provenance").glob("*.json")),
    ]
    if not any(path.is_file() for path in sbom_candidates):
        fail(f"release dir contains no SBOM JSON: {release_dir}")
    if not any(path.is_file() for path in provenance_candidates):
        fail(f"release dir contains no acceptance/provenance JSON: {release_dir}")


def push_with_oras(
    args: argparse.Namespace,
    *,
    ref: str,
    cwd: Path,
    entries: list[dict[str, Any]],
    artifact_type: str,
    annotations: dict[str, str],
    auth_env: dict[str, str] | None = None,
) -> str:
    command = [
        "push",
        *oras_layout_flag(args.dry_run),
        "--artifact-type",
        artifact_type,
    ]
    for key, value in sorted(annotations.items()):
        command.extend(["--annotation", f"{key}={value}"])
    command.append(ref)
    command.extend(f"{entry['path']}:{entry['media_type']}" for entry in entries)
    pushed = run_oras(args, command, cwd=cwd, env_extra=auth_env)
    digest = parse_digest(pushed.stdout)
    try:
        digest = resolve_digest(args, ref, auth_env)
    except StoreError:
        pass
    return digest


def push_release(args: argparse.Namespace) -> int:
    release_dir = args.release_dir.resolve()
    validate_release_dir(release_dir)
    commit, version = release_metadata(release_dir, args.profile, args.line)
    tag = choose_release_tag(args, commit, version)
    entries = collect_tree_files(release_dir)
    annotations = {
        "org.opencontainers.image.created": utc_now(),
        "org.opencontainers.image.revision": commit,
        "io.vaso.artifact.kind": "release",
        "io.vaso.commit": commit,
        "io.vaso.line": args.line,
        "io.vaso.profile": args.profile,
        "io.vaso.version": version or "",
        **file_annotations(entries),
    }
    ref, package, layout = target_reference(
        registry=args.registry,
        dry_run=args.dry_run,
        kind="release",
        profile=args.profile,
        line=args.line,
        tag=tag,
    )
    auth_env = prepare_remote_auth(args) if not args.dry_run else None
    digest = push_with_oras(
        args,
        ref=ref,
        cwd=release_dir,
        entries=entries,
        artifact_type="application/vnd.vaso.release.v1",
        annotations=annotations,
        auth_env=auth_env,
    )
    if not args.dry_run:
        check_ghcr_private(args, package)
    return emit(
        args,
        {
            "kind": "release",
            "package": package,
            "tag": tag,
            "reference": ref,
            "layout": str(layout) if layout is not None else None,
            "digest": digest,
            "files": {str(entry["path"]): entry["sha256"] for entry in entries},
        },
    )


def split_file(src: Path, dst_dir: Path, rel_prefix: str, max_bytes: int) -> list[dict[str, Any]]:
    if max_bytes <= 0:
        fail("--max-layer-bytes must be positive")
    dst_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    index = 0
    with src.open("rb") as fh:
        while True:
            first = fh.read(1)
            if not first:
                break
            rel = f"{rel_prefix}.part{index:04d}"
            path = dst_dir / Path(rel).name
            digest = hashlib.sha256()
            size = 0
            with path.open("wb") as out:
                chunk = first
                while chunk and size < max_bytes:
                    out.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                    if size >= max_bytes:
                        break
                    chunk = fh.read(min(1024 * 1024, max_bytes - size))
            entries.append(
                {
                    "path": f"rootfs/{path.name}",
                    "sha256": digest.hexdigest(),
                    "size": size,
                    "media_type": media_type(path.name),
                }
            )
            index += 1
    if not entries:
        rel = f"{rel_prefix}.part0000"
        path = dst_dir / Path(rel).name
        path.write_bytes(b"")
        entries.append({"path": f"rootfs/{path.name}", "sha256": hashlib.sha256(b"").hexdigest(), "size": 0, "media_type": media_type(path.name)})
    return entries


def build_rootfs_bundle(args: argparse.Namespace, estate: Path, work_root: Path) -> tuple[Path, Path, dict[str, Any]]:
    if args.bundle:
        archive = args.bundle.resolve()
        sidecar = archive.parent / f"rootfs-{args.line}.json"
        if not archive.is_file():
            fail(f"missing prebuilt rootfs bundle: {archive}")
        doc = read_json_object(sidecar)
        if doc.get("line") != args.line:
            fail("prebuilt rootfs bundle sidecar line does not match --line")
        return archive, sidecar, doc
    out_dir = work_root / "bundle"
    out_dir.mkdir(parents=True, exist_ok=True)
    command = [
        "bash",
        str(BUNDLE_SH),
        "--line",
        args.line,
        "--estate-root",
        str(estate),
        "--out-dir",
        str(out_dir),
    ]
    if args.rootfs_lock:
        command.extend(["--lock", str(args.rootfs_lock)])
    env_extra = {"TMPDIR": str(work_root / "tmp")}
    (work_root / "tmp").mkdir(parents=True, exist_ok=True)
    result = run_command(command, env_extra=env_extra)
    archive_lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not archive_lines:
        fail("rootfs bundle script did not report an archive path")
    archive = Path(archive_lines[-1])
    sidecar = out_dir / f"rootfs-{args.line}.json"
    if not archive.is_file():
        fail(f"rootfs bundle script did not create archive: {archive}")
    if not sidecar.is_file():
        fail(f"rootfs bundle script did not create sidecar: {sidecar}")
    sidecar_doc = read_json_object(sidecar)
    return archive, sidecar, sidecar_doc


def push_rootfs(args: argparse.Namespace) -> int:
    estate = estate_root()
    work_root = estate / "publish" / "artifact-store-staging" / f"rootfs-{args.line}-{os.getpid()}"
    if work_root.exists():
        fail(f"staging directory already exists: {work_root}")
    parts_root = work_root / "layers"
    try:
        archive, sidecar, sidecar_doc = build_rootfs_bundle(args, estate, work_root)
        archive_digest = str(sidecar_doc.get("digest") or sidecar_doc.get("sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", archive_digest):
            fail(f"rootfs sidecar has invalid digest: {sidecar}")
        actual_archive_digest = sha256_file(archive)
        if archive_digest != actual_archive_digest:
            fail(f"rootfs archive digest mismatch: expected {archive_digest} actual {actual_archive_digest}")
        rootfs_dir = parts_root / "rootfs"
        part_entries = split_file(archive, rootfs_dir, archive.name, args.max_layer_bytes)
        shutil.copy2(sidecar, parts_root / sidecar.name)
        rootfs_manifest = (
            archive.parent / "rootfs-bundle.json"
            if args.bundle else estate / "rootfs-lines" / args.line / "rootfs-bundle.json"
        )
        if not rootfs_manifest.is_file():
            fail(f"missing rootfs bundle manifest: {rootfs_manifest}")
        shutil.copy2(rootfs_manifest, parts_root / "rootfs-bundle.json")
        entries = collect_tree_files(parts_root)
        tag = args.tag or archive_digest[:12]
        if not TAG_RE.fullmatch(tag):
            fail(f"not a valid OCI tag: {tag}")
        annotations = {
            "org.opencontainers.image.created": utc_now(),
            "io.vaso.artifact.kind": "rootfs",
            "io.vaso.line": args.line,
            "io.vaso.rootfs.archive": archive.name,
            "io.vaso.rootfs.digest": archive_digest,
            "io.vaso.rootfs.size": str(archive.stat().st_size),
            "io.vaso.rootfs.part.count": str(len(part_entries)),
            "io.vaso.rootfs.bundle-manifest.sha256": sha256_file(rootfs_manifest),
            **file_annotations(entries),
        }
        ref, package, layout = target_reference(
            registry=args.registry,
            dry_run=args.dry_run,
            kind="rootfs",
            profile=None,
            line=args.line,
            tag=tag,
        )
        auth_env = prepare_remote_auth(args) if not args.dry_run else None
        digest = push_with_oras(
            args,
            ref=ref,
            cwd=parts_root,
            entries=entries,
            artifact_type="application/vnd.vaso.rootfs.v1",
            annotations=annotations,
            auth_env=auth_env,
        )
        if not args.dry_run:
            check_ghcr_private(args, package)
        return emit(
            args,
            {
                "kind": "rootfs",
                "package": package,
                "tag": tag,
                "reference": ref,
                "layout": str(layout) if layout is not None else None,
                "digest": digest,
                "archive": archive.name,
                "archive_sha256": archive_digest,
                "split_parts": len(part_entries),
                "layers": [{"path": item["path"], "size": item["size"], "sha256": item["sha256"]} for item in part_entries],
                "files": {str(entry["path"]): entry["sha256"] for entry in entries},
            },
        )
    finally:
        shutil.rmtree(work_root, ignore_errors=True)


def stage_target(target: Path) -> Path:
    target = target.resolve()
    if target.exists() and any(target.iterdir() if target.is_dir() else [target]):
        fail(f"target dir must be absent or empty: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.parent / f".{target.name}.artifact-store-pull.{os.getpid()}"
    if stage.exists():
        fail(f"temporary pull directory already exists: {stage}")
    stage.mkdir()
    return stage


def replace_target(stage: Path, target: Path) -> None:
    if target.exists():
        if target.is_dir():
            target.rmdir()
        else:
            fail(f"target exists and is not a directory: {target}")
    stage.rename(target)


def pull_artifact(args: argparse.Namespace) -> int:
    tag = args.tag
    digest = args.digest
    ref, package, layout = target_reference(
        registry=args.registry,
        dry_run=args.dry_run,
        kind=args.artifact,
        profile=args.profile,
        line=args.line,
        tag=tag,
        digest=digest,
    )
    auth_env = prepare_remote_auth(args) if not args.dry_run else None
    manifest = fetch_manifest(args, ref, auth_env)
    annotations = manifest.get("annotations")
    if not isinstance(annotations, dict):
        fail("manifest has no annotations")
    expected_kind = str(annotations.get("io.vaso.artifact.kind") or "")
    if expected_kind != args.artifact:
        fail(f"manifest kind is {expected_kind!r}, expected {args.artifact!r}")
    if str(annotations.get("io.vaso.line") or "") != args.line:
        fail(f"manifest line is {annotations.get('io.vaso.line')!r}, expected {args.line!r}")
    if args.artifact == "release" and str(annotations.get("io.vaso.profile") or "") != args.profile:
        fail(f"manifest profile is {annotations.get('io.vaso.profile')!r}, expected {args.profile!r}")
    entries = parse_file_annotations(annotations)
    target = args.target_dir.resolve()
    stage = stage_target(target)
    try:
        run_oras(args, ["pull", *oras_layout_flag(args.dry_run), ref, "-o", str(stage)], env_extra=auth_env)
        verify_files(stage, entries)
        archive_name = None
        if args.artifact == "rootfs":
            archive_name = reassemble_rootfs(stage, annotations)
        replace_target(stage, target)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return emit(
        args,
        {
            "kind": args.artifact,
            "package": package,
            "tag": tag,
            "digest": digest,
            "reference": ref,
            "layout": str(layout) if layout is not None else None,
            "target_dir": str(target),
            "verified_files": len(entries),
            "archive": archive_name,
        },
    )


def reassemble_rootfs(stage: Path, annotations: dict[str, Any]) -> str:
    archive_name = str(annotations.get("io.vaso.rootfs.archive") or "")
    archive_digest = str(annotations.get("io.vaso.rootfs.digest") or "")
    archive_size = int(str(annotations.get("io.vaso.rootfs.size") or "0"))
    part_count = int(str(annotations.get("io.vaso.rootfs.part.count") or "0"))
    if not archive_name or not re.fullmatch(r"[A-Za-z0-9._+-]+", archive_name):
        fail(f"invalid rootfs archive annotation: {archive_name!r}")
    if not re.fullmatch(r"[0-9a-f]{64}", archive_digest):
        fail(f"invalid rootfs archive digest annotation: {archive_digest!r}")
    if part_count <= 0:
        fail("rootfs part count must be positive")
    parts = [stage / "rootfs" / f"{archive_name}.part{index:04d}" for index in range(part_count)]
    for part in parts:
        if not part.is_file():
            fail(f"missing rootfs part: {part.relative_to(stage)}")
    archive = stage / archive_name
    with archive.open("wb") as out:
        for part in parts:
            with part.open("rb") as fh:
                shutil.copyfileobj(fh, out, 1024 * 1024)
    actual_size = archive.stat().st_size
    if actual_size != archive_size:
        fail(f"rootfs archive size mismatch: expected {archive_size} actual {actual_size}")
    actual_digest = sha256_file(archive)
    if actual_digest != archive_digest:
        fail(f"rootfs archive sha256 mismatch: expected {archive_digest} actual {actual_digest}")
    sidecar = stage / f"rootfs-{annotations.get('io.vaso.line')}.json"
    if sidecar.is_file():
        sidecar_doc = read_json_object(sidecar)
        if str(sidecar_doc.get("digest") or sidecar_doc.get("sha256") or "") != archive_digest:
            fail(f"rootfs sidecar digest does not match archive: {sidecar}")
    for name in (sidecar.name if sidecar.is_file() else "", "rootfs-bundle.json"):
        if not name:
            continue
        src = stage / "rootfs" / name
        if src.is_file():
            shutil.move(str(src), str(stage / name))
    shutil.rmtree(stage / "rootfs", ignore_errors=True)
    return archive_name


def token_from_env_or_file() -> str | None:
    env_token = os.environ.get("GHCR_TOKEN")
    if env_token:
        return env_token
    token_file = estate_root() / "publish" / "ghcr-token"
    if not token_file.exists():
        return None
    mode = token_file.stat().st_mode & 0o777
    if mode != 0o600:
        fail(f"GHCR token file must have mode 600: {token_file}")
    token = token_file.read_text(encoding="utf-8").strip()
    return token or None


def prepare_remote_auth(args: argparse.Namespace) -> dict[str, str]:
    token = token_from_env_or_file()
    if not token:
        fail("set GHCR_TOKEN or create $VASO_ESTATE_ROOT/publish/ghcr-token with mode 600")
    registry = (args.registry or DEFAULT_REGISTRY).rstrip("/")
    host = registry.split("/", 1)[0]
    auth_dir = estate_root() / "publish" / "oras-auth"
    auth_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(auth_dir, 0o700)
    auth_env = {"DOCKER_CONFIG": str(auth_dir)}
    login = ["login", host, "-u", "phi9t", "--password-stdin"]
    run_oras(args, login, stdin=token, secrets=[token], env_extra=auth_env)
    return auth_env


def check_ghcr_private(args: argparse.Namespace, package: str) -> None:
    registry = (args.registry or DEFAULT_REGISTRY).rstrip("/")
    parts = registry.split("/")
    if len(parts) < 2 or parts[0] != "ghcr.io":
        fail(f"cannot verify package visibility for non-GHCR registry: {registry}")
    token = token_from_env_or_file()
    if not token:
        fail("cannot verify GHCR package visibility without a token")
    owner = parts[1]
    package_name = urllib.parse.quote(package, safe="")
    url = f"https://api.github.com/users/{owner}/packages/container/{package_name}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            doc = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        message = redact(str(exc), [token])
        fail(f"failed to verify GHCR package visibility for {package}: {message}")
    visibility = doc.get("visibility") if isinstance(doc, dict) else None
    if visibility != "private":
        fail(f"GHCR package {package} visibility is {visibility!r}; expected 'private'")


def add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--dry-run", action="store_true", help="use an estate-local OCI layout instead of the network")
    p.add_argument("--registry", help="registry root, or an OCI layout root when --dry-run is set")
    p.add_argument("--oras-bin", type=Path, help="path to oras; defaults to $ORAS_BIN or $VASO_ESTATE_ROOT/opt-vaso/bin/oras")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    release = sub.add_parser("push-release", help="push a release-dir as one OCI artifact")
    add_common(release)
    release.add_argument("--release-dir", type=Path, required=True)
    release.add_argument("--line", choices=LINE_CHOICES, required=True)
    release.add_argument("--profile", choices=PROFILE_CHOICES, required=True)
    release.add_argument("--tag", help="override the derived release tag")
    release.set_defaults(func=push_release)

    rootfs = sub.add_parser("push-rootfs", help="bundle and push a rootfs line as split OCI layers")
    add_common(rootfs)
    rootfs.add_argument("--line", choices=LINE_CHOICES, required=True)
    rootfs.add_argument("--bundle", type=Path, help="publish an existing archive with rootfs-<line>.json and rootfs-bundle.json beside it")
    rootfs.add_argument("--tag", help="override the archive-digest-short tag")
    rootfs.add_argument("--max-layer-bytes", type=int, default=DEFAULT_MAX_LAYER_BYTES)
    rootfs.add_argument("--rootfs-lock", type=Path, help="override the rootfs lock passed to bundle.sh")
    rootfs.set_defaults(func=push_rootfs)

    pull = sub.add_parser("pull", help="pull and verify a release or rootfs artifact")
    add_common(pull)
    pull.add_argument("--artifact", choices=("release", "rootfs"), required=True)
    pull.add_argument("--line", choices=LINE_CHOICES, required=True)
    pull.add_argument("--profile", choices=PROFILE_CHOICES)
    pull.add_argument("--tag")
    pull.add_argument("--digest")
    pull.add_argument("--target-dir", type=Path, required=True)
    pull.set_defaults(func=pull_artifact)
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        if getattr(args, "artifact", None) == "release" and not args.profile:
            fail("pull --artifact release requires --profile")
        if getattr(args, "artifact", None) == "rootfs":
            args.profile = None
        if getattr(args, "tag", None) and getattr(args, "digest", None):
            fail("pass only one of --tag or --digest")
        return args.func(args)
    except CommandError as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    except StoreError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
