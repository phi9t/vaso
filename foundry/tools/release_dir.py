#!/usr/bin/env python3
"""Assemble a self-contained Vaso release directory."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path
from typing import Any, Iterable

try:
    from release_wheel_identity import is_vaso_release_wheel_name, validate_release_wheel_commit
except ModuleNotFoundError:
    _IDENTITY_SPEC = importlib.util.spec_from_file_location(
        "release_wheel_identity",
        Path(__file__).with_name("release_wheel_identity.py"),
    )
    if _IDENTITY_SPEC is None or _IDENTITY_SPEC.loader is None:
        raise
    _IDENTITY_MODULE = importlib.util.module_from_spec(_IDENTITY_SPEC)
    _IDENTITY_SPEC.loader.exec_module(_IDENTITY_MODULE)
    is_vaso_release_wheel_name = _IDENTITY_MODULE.is_vaso_release_wheel_name
    validate_release_wheel_commit = _IDENTITY_MODULE.validate_release_wheel_commit


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_once(src: Path, dest: Path) -> None:
    if not src.is_file():
        raise ValueError(f"release input is missing: {src}")
    if dest.exists() and sha256(dest) != sha256(src):
        raise ValueError(f"release dir wheel name collision with different content: {dest.name}")
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def _manifest_items(items: Iterable[str]) -> list[tuple[str, Path]]:
    parsed: list[tuple[str, Path]] = []
    for item in items:
        package, sep, raw_path = item.partition("=")
        if not sep or not package or not raw_path:
            raise ValueError(f"release wheel manifest must be PACKAGE=PATH, got {item!r}")
        parsed.append((package, Path(raw_path)))
    return parsed


def _load_manifest(package: str, manifest: Path) -> dict[str, Any]:
    if not manifest.is_file():
        raise ValueError(f"release wheel manifest is missing for {package}: {manifest}")
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    wheels = doc.get("wheels", []) if isinstance(doc, dict) else []
    if not isinstance(wheels, list) or not wheels:
        raise ValueError(f"release wheel manifest for {package} contains no wheels: {manifest}")
    return doc


def _copy_manifest(
    *,
    release_dir: Path,
    package: str,
    manifest: Path,
    manifest_field: str,
    wheel_field: str,
    wheels_dir: Path,
    release_doc: dict[str, Any],
    required_commit: str | None,
) -> None:
    doc = _load_manifest(package, manifest)
    sources = []
    for wheel in doc["wheels"]:
        if not isinstance(wheel, dict) or not wheel.get("path"):
            raise ValueError(f"invalid release wheel entry for {package}: {manifest}")
        source = Path(str(wheel["path"]))
        if required_commit is not None:
            validate_release_wheel_commit(source, required_commit, wheel_name=str(wheel.get("name") or source.name))
        sources.append(source)

    manifest_dest = release_dir / "provenance" / f"{package}-release-wheel-manifest.json"
    shutil.copy2(manifest, manifest_dest)
    release_doc[manifest_field].append(
        {
            "package": package,
            "path": manifest_dest.relative_to(release_dir).as_posix(),
            "sha256": "sha256:" + sha256(manifest_dest),
        }
    )
    for source in sources:
        dest = wheels_dir / source.name
        copy_once(source, dest)
        release_doc[wheel_field].append(
            {
                "package": package,
                "name": source.name,
                "path": dest.relative_to(release_dir).as_posix(),
                "sha256": "sha256:" + sha256(dest),
            }
        )


def _write_requirements(release_dir: Path, wheels: Iterable[dict[str, str]]) -> None:
    lines = []
    seen: set[str] = set()
    for wheel in wheels:
        rel = wheel["path"]
        if rel in seen:
            continue
        seen.add(rel)
        digest = wheel["sha256"].removeprefix("sha256:")
        lines.append(f"/run/vaso/release/{rel} --hash=sha256:{digest}")
    (release_dir / "requirements.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _refuse_stale_existing_release_wheels(release_dir: Path, commit: str) -> None:
    if not release_dir.exists():
        return
    for wheel in sorted(release_dir.rglob("*.whl")):
        if is_vaso_release_wheel_name(wheel.name):
            validate_release_wheel_commit(wheel, commit)


def assemble_release_dir(
    *,
    release_dir: Path,
    profile: str,
    line: str,
    commit: str,
    sbom: Path,
    license_policy: Path,
    wheel_manifests: Iterable[tuple[str, Path]],
    runtime_wheel_manifests: Iterable[tuple[str, Path]] = (),
) -> None:
    _refuse_stale_existing_release_wheels(release_dir, commit)
    if release_dir.exists():
        shutil.rmtree(release_dir)
    wheels_dir = release_dir / "wheels"
    wheelhouse_dir = release_dir / "wheelhouse"
    provenance_dir = release_dir / "provenance"
    sbom_dir = release_dir / "sbom"
    for path in (wheels_dir, wheelhouse_dir, provenance_dir, sbom_dir):
        path.mkdir(parents=True, exist_ok=True)

    release_doc: dict[str, Any] = {
        "schema_version": 1,
        "profile": profile,
        "line": line,
        "commit": commit,
        "manifests": [],
        "runtime_manifests": [],
        "wheels": [],
        "runtime_wheels": [],
        "requirements": "requirements.txt",
        "sbom": "sbom/cyclonedx.json",
        "license_policy": "sbom/license-policy.json",
    }
    for package, manifest in wheel_manifests:
        _copy_manifest(
            release_dir=release_dir,
            package=package,
            manifest=manifest,
            manifest_field="manifests",
            wheel_field="wheels",
            wheels_dir=wheels_dir,
            release_doc=release_doc,
            required_commit=commit,
        )
    for package, manifest in runtime_wheel_manifests:
        _copy_manifest(
            release_dir=release_dir,
            package=package,
            manifest=manifest,
            manifest_field="runtime_manifests",
            wheel_field="runtime_wheels",
            wheels_dir=wheelhouse_dir,
            release_doc=release_doc,
            required_commit=None,
        )

    copy_once(sbom, sbom_dir / "cyclonedx.json")
    copy_once(license_policy, sbom_dir / "license-policy.json")
    _write_requirements(release_dir, [*release_doc["wheels"], *release_doc["runtime_wheels"]])
    (provenance_dir / "release.json").write_text(
        json.dumps(release_doc, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    entries = []
    for path in sorted(candidate for candidate in release_dir.rglob("*") if candidate.is_file()):
        rel = path.relative_to(release_dir).as_posix()
        if rel == "SHA256SUMS":
            continue
        entries.append(f"{sha256(path)}  {rel}")
    (release_dir / "SHA256SUMS").write_text("\n".join(entries) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--profile", required=True, choices=("torch", "jax"))
    parser.add_argument("--line", required=True, choices=("cu129", "cu130"))
    parser.add_argument("--commit", required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--license-policy", type=Path, required=True)
    parser.add_argument("--wheel-manifest", action="append", default=[])
    parser.add_argument("--runtime-wheel-manifest", action="append", default=[])
    args = parser.parse_args(argv)

    assemble_release_dir(
        release_dir=args.release_dir,
        profile=args.profile,
        line=args.line,
        commit=args.commit,
        sbom=args.sbom,
        license_policy=args.license_policy,
        wheel_manifests=_manifest_items(args.wheel_manifest),
        runtime_wheel_manifests=_manifest_items(args.runtime_wheel_manifest),
    )
    print(f"release dir assembled: {args.release_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
