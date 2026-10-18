#!/usr/bin/env python3
"""Pin check for the vendored Cargo crates: the tree matches its recorded sha256s."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCKFILE = HERE.parents[1] / "foundry" / "examples" / "consumers" / "rust_pyo3" / "Cargo.lock"


def manifest_rows(root: Path) -> list[dict[str, str]]:
    rows = []
    for line in (root / "vendor-manifest.tsv").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        crate, version, sha256, path = line.split("\t")
        rows.append({"crate": crate, "version": version, "sha256": sha256, "path": path})
    return rows


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def lock_checksums(lockfile: Path) -> dict[tuple[str, str], str]:
    packages = tomllib.loads(lockfile.read_text(encoding="utf-8")).get("package", [])
    return {(p["name"], p["version"]): p["checksum"] for p in packages if "checksum" in p}


def problems(root: Path, lockfile: Path | None = None) -> list[str]:
    """Every way the vendored tree disagrees with its manifest; empty means pinned."""
    found: list[str] = []
    rows = manifest_rows(root)
    if not rows:
        return ["vendor-manifest.tsv lists no crates"]
    recorded = {row["path"] for row in rows}
    for child in sorted((root / "vendor").iterdir()):
        if child.is_dir() and f"vendor/{child.name}" not in recorded:
            found.append(f"vendor/{child.name} is not in the manifest")
    locked = lock_checksums(lockfile) if lockfile is not None else {}
    for row in rows:
        label = f"{row['crate']} {row['version']}"
        crate_dir = root / row["path"]
        if not re.fullmatch(r"[0-9a-f]{64}", row["sha256"]):
            found.append(f"{label}: manifest sha256 is not 64 hex digits")
        if not crate_dir.is_dir():
            found.append(f"{label}: {row['path']} is missing")
            continue
        package = tomllib.loads((crate_dir / "Cargo.toml").read_text(encoding="utf-8")).get("package", {})
        if (package.get("name"), package.get("version")) != (row["crate"], row["version"]):
            found.append(f"{label}: Cargo.toml says {package.get('name')} {package.get('version')}")
        checksum_file = json.loads((crate_dir / ".cargo-checksum.json").read_text(encoding="utf-8"))
        if checksum_file.get("package") != row["sha256"]:
            found.append(f"{label}: .cargo-checksum.json package differs from the manifest sha256")
        if lockfile is not None and locked.get((row["crate"], row["version"])) != row["sha256"]:
            found.append(f"{label}: Cargo.lock checksum differs from the manifest sha256")
        listed = checksum_file.get("files", {})
        for rel, expected in listed.items():
            target = crate_dir / rel
            if not target.is_file():
                found.append(f"{label}: {rel} is missing")
            elif sha256_file(target) != expected:
                found.append(f"{label}: {rel} differs from its recorded sha256")
        present = {
            p.relative_to(crate_dir).as_posix()
            for p in crate_dir.rglob("*")
            if p.is_file() and p.name != ".cargo-checksum.json"
        }
        for rel in sorted(present - set(listed)):
            found.append(f"{label}: {rel} is not recorded in .cargo-checksum.json")
    return found


class VendorPinTest(unittest.TestCase):
    def test_vendored_tree_matches_its_manifest(self) -> None:
        self.assertEqual(problems(HERE, LOCKFILE if LOCKFILE.is_file() else None), [])

    def test_notice_lists_every_vendored_crate_with_its_version(self) -> None:
        notice = (HERE.parent / "NOTICE").read_text(encoding="utf-8")
        listed = {tuple(line.split("\t")[:2]) for line in notice.splitlines() if line.count("\t") == 2}
        expected = {(row["crate"], row["version"]) for row in manifest_rows(HERE)}
        self.assertEqual(listed - {("crate", "version")}, expected)

    def test_a_modified_vendored_file_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "pinned"
            shutil.copytree(HERE, copy, ignore=shutil.ignore_patterns("__pycache__", "*_test.py"))
            first = manifest_rows(copy)[0]
            victim = next((copy / first["path"] / "src").glob("*.rs"))
            victim.write_text(victim.read_text(encoding="utf-8") + "\n// drift\n", encoding="utf-8")

            reported = problems(copy)

            self.assertTrue(any("differs from its recorded sha256" in line for line in reported), reported)

    def test_an_unrecorded_crate_directory_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "pinned"
            shutil.copytree(HERE, copy, ignore=shutil.ignore_patterns("__pycache__", "*_test.py"))
            (copy / "vendor" / "stowaway-0.0.1").mkdir()

            self.assertIn("vendor/stowaway-0.0.1 is not in the manifest", problems(copy))


if __name__ == "__main__":
    unittest.main()
