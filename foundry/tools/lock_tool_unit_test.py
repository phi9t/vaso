#!/usr/bin/env python3
"""Unit tests for lock_tool.py."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("lock_tool.py")


def temporary_directory() -> tempfile.TemporaryDirectory[str]:
    base = os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR")
    return tempfile.TemporaryDirectory(dir=base)


def repo_name(package: str) -> str:
    return "vaso_" + package.replace("-", "_")


def source_entry(filename: str, sha256: str) -> dict[str, object]:
    return {
        "urls": [f"https://example.invalid/{filename}"],
        "sha256": sha256,
        "filename": filename,
    }


def native_node(
    package: str,
    version: str,
    *,
    deps: list[str] | None = None,
    source: dict[str, object] | None = None,
    native_prefix: str | None = None,
) -> dict[str, object]:
    node: dict[str, object] = {
        "package": package,
        "version": version,
        "spack_hash": f"{repo_name(package)}hash",
        "prefix": f"/vaso/cache/spack/opt/spack/linux-icelake/{package}-{version}",
        "build": "native",
        "native_prefix": native_prefix or f"@{package.replace('-', '_')}_native//:lib",
        "link_deps": deps or [],
        "link_libs": [],
        "include_dirs": [],
    }
    if source is not None:
        node["source"] = source
    return node


class LockToolFixture:
    def __init__(self, root: Path):
        self.root = root
        self.lock = root / "locks" / "default.lock.json"
        self.ledger = root / "migration_ledger.json"
        self.overrides = root / "native_overrides.json"
        self.distdir = root / "distdir"
        self.distdir.mkdir(parents=True)

    def write(
        self,
        *,
        zlib_deps: list[str] | None = None,
        zlib_sha: str | None = None,
        zlib_status: str = "native",
    ) -> bytes:
        archive = b"zlib-ng source archive\n"
        good_sha = hashlib.sha256(archive).hexdigest()
        sha = zlib_sha or good_sha
        (self.distdir / "zlib-ng-2.3.3.tar.gz").write_bytes(archive)
        lock = {
            "schema_version": 1,
            "root": "vaso_zlib_ng",
            "packages": {
                "vaso_zlib_ng": native_node(
                    "zlib-ng",
                    "2.3.3",
                    deps=zlib_deps if zlib_deps is not None else ["vaso_zstd"],
                    source=source_entry("zlib-ng-2.3.3.tar.gz", sha),
                ),
                "vaso_zstd": native_node("zstd", "1.5.7"),
            },
        }
        ledger = {
            "packages": {
                "zlib-ng": {"status": zlib_status},
                "zstd": {"status": "native"},
            }
        }
        overrides = {
            "native": {
                "zlib-ng": "@zlib_ng_native//:lib",
                "zstd": "@zstd_native//:lib",
            },
            "provided_versions": {
                "zlib-ng": "2.3.3",
                "zstd": "1.5.7",
            },
            "known_version_substitutions": [],
        }
        self.lock.parent.mkdir(parents=True)
        self.lock.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.ledger.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.overrides.write_text(json.dumps(overrides, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return self.lock.read_bytes()

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=self.root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def validate(self) -> subprocess.CompletedProcess[str]:
        return self.run(
            "validate",
            str(self.lock),
            "--ledger",
            str(self.ledger),
            "--native-overrides",
            str(self.overrides),
            "--distdir",
            str(self.distdir),
        )


class LockToolTest(unittest.TestCase):
    def test_validate_rejects_missing_dependency_closure(self) -> None:
        with temporary_directory() as tmp:
            fixture = LockToolFixture(Path(tmp))
            fixture.write(zlib_deps=["vaso_missing"])

            result = fixture.validate()

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("vaso_zlib_ng: link_dep vaso_missing is missing", result.stderr)

    def test_validate_rejects_bad_source_sha256(self) -> None:
        with temporary_directory() as tmp:
            fixture = LockToolFixture(Path(tmp))
            fixture.write(zlib_sha="0" * 64)

            result = fixture.validate()

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("zlib-ng@2.3.3 source zlib-ng-2.3.3.tar.gz sha256 mismatch", result.stderr)

    def test_validate_rejects_nonterminal_ledger_status(self) -> None:
        with temporary_directory() as tmp:
            fixture = LockToolFixture(Path(tmp))
            fixture.write(zlib_status="recipe-captured")

            result = fixture.validate()

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("zlib-ng: ledger status 'recipe-captured' is non-terminal", result.stderr)

    def test_validate_rejects_unknown_package_record(self) -> None:
        with temporary_directory() as tmp:
            fixture = LockToolFixture(Path(tmp))
            fixture.write()
            document = json.loads(fixture.lock.read_text(encoding="utf-8"))
            document["packages"]["vaso_unknown"] = native_node("unknown", "1.0")
            fixture.lock.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            result = fixture.validate()

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("vaso_unknown: unknown package record 'unknown'", result.stderr)

    def test_validate_rejects_missing_native_provider_label(self) -> None:
        with temporary_directory() as tmp:
            fixture = LockToolFixture(Path(tmp))
            fixture.write()
            document = json.loads(fixture.lock.read_text(encoding="utf-8"))
            document["packages"]["vaso_zlib_ng"].pop("native_prefix")
            fixture.lock.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            result = fixture.validate()

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("vaso_zlib_ng: missing native provider label", result.stderr)

    def test_validate_accepts_complete_lock_without_rewriting(self) -> None:
        with temporary_directory() as tmp:
            fixture = LockToolFixture(Path(tmp))
            before = fixture.write()

            result = fixture.validate()
            after = fixture.lock.read_bytes()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(after, before)
        self.assertIn("validated 1 lock(s)", result.stdout)

    def test_bump_updates_existing_package_source(self) -> None:
        with temporary_directory() as tmp:
            fixture = LockToolFixture(Path(tmp))
            fixture.write()
            new_sha = "1" * 64

            result = fixture.run(
                "bump",
                "zlib-ng",
                "--lock",
                str(fixture.lock),
                "--version",
                "2.4.0",
                "--url",
                "https://example.invalid/zlib-ng-2.4.0.tar.gz",
                "--sha256",
                new_sha,
            )
            document = json.loads(fixture.lock.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        node = document["packages"]["vaso_zlib_ng"]
        self.assertEqual(node["version"], "2.4.0")
        self.assertEqual(
            node["source"],
            {
                "filename": "zlib-ng-2.4.0.tar.gz",
                "sha256": new_sha,
                "urls": ["https://example.invalid/zlib-ng-2.4.0.tar.gz"],
            },
        )

    def test_bump_defaults_to_checked_in_locks_directory(self) -> None:
        with temporary_directory() as tmp:
            fixture = LockToolFixture(Path(tmp))
            fixture.write()
            new_sha = "3" * 64

            result = fixture.run(
                "bump",
                "zlib-ng",
                "--version",
                "2.4.0",
                "--url",
                "https://example.invalid/zlib-ng-2.4.0.tar.gz",
                "--sha256",
                new_sha,
            )
            document = json.loads(fixture.lock.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bumped zlib-ng in 1 lock(s)", result.stdout)
        self.assertEqual(document["packages"]["vaso_zlib_ng"]["source"]["sha256"], new_sha)

    def test_add_and_remove_package_record(self) -> None:
        with temporary_directory() as tmp:
            fixture = LockToolFixture(Path(tmp))
            fixture.write()
            result = fixture.run(
                "add",
                "libffi",
                "--lock",
                str(fixture.lock),
                "--version",
                "3.5.2",
                "--url",
                "https://example.invalid/libffi-3.5.2.tar.gz",
                "--sha256",
                "2" * 64,
                "--native-label",
                "@libffi_native//:lib",
                "--dep",
                "zstd",
                "--link-lib",
                "ffi",
                "--include-dir",
                "include",
            )
            added = json.loads(fixture.lock.read_text(encoding="utf-8"))
            remove = fixture.run("remove", "libffi", "--lock", str(fixture.lock))
            removed = json.loads(fixture.lock.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(added["packages"]["vaso_libffi"]["link_deps"], ["vaso_zstd"])
        self.assertEqual(added["packages"]["vaso_libffi"]["link_libs"], ["ffi"])
        self.assertEqual(remove.returncode, 0, remove.stderr)
        self.assertNotIn("vaso_libffi", removed["packages"])


if __name__ == "__main__":
    unittest.main()
