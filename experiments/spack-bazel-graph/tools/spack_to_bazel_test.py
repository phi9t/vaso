#!/usr/bin/env python3
"""Regression tests for spack_to_bazel.py."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).with_name("spack_to_bazel.py")
EXPERIMENT_ROOT = SCRIPT.parent.parent
SPEC = importlib.util.spec_from_file_location("spack_to_bazel", SCRIPT)
assert SPEC is not None
spack_to_bazel = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = spack_to_bazel
SPEC.loader.exec_module(spack_to_bazel)


class SpackToBazelTest(unittest.TestCase):
    def test_libbsd_overlay_include_directory_is_not_exported_as_global_include(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp)
            (prefix / "include" / "bsd" / "sys").mkdir(parents=True)
            (prefix / "include" / "bsd" / "sys" / "cdefs.h").write_text("", encoding="utf-8")
            (prefix / "include" / "bsd" / "string.h").write_text("", encoding="utf-8")

            self.assertEqual(spack_to_bazel.include_dirs(str(prefix)), ["include"])

    def test_non_overlay_nested_include_directory_is_exported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp)
            (prefix / "include" / "libxml2" / "libxml").mkdir(parents=True)
            (prefix / "include" / "libxml2" / "libxml" / "parser.h").write_text("", encoding="utf-8")

            self.assertEqual(
                spack_to_bazel.include_dirs(str(prefix)),
                ["include", "include/libxml2"],
            )

    def test_py_protobuf_family_versions_normalize_for_every_modern_major(self) -> None:
        cases = {
            ("py-protobuf", "3.13.0"): "3.13.0",
            ("py-protobuf", "4.21.12"): "21.12",
            ("py-protobuf", "5.26.1"): "26.1",
            ("py-protobuf", "6.32.1"): "32.1",
            ("protobuf", "21.12"): "21.12",
        }
        for (name, version), expected in cases.items():
            with self.subTest(name=name, version=version):
                self.assertEqual(spack_to_bazel.odr_family_version(name, version), expected)

    def test_native_overrides_do_not_require_installed_spack_prefixes(self) -> None:
        spec = {
            "spec": {
                "nodes": [
                    {
                        "name": "cuda",
                        "version": "12.9.1",
                        "hash": "cudahash",
                        "dependencies": [
                            {
                                "name": "coreutils",
                                "hash": "corehash",
                                "parameters": {"deptypes": ["build"]},
                            }
                        ],
                    },
                    {
                        "name": "coreutils",
                        "version": "9.10",
                        "hash": "corehash",
                        "dependencies": [],
                    },
                ]
            }
        }

        with mock.patch.object(spack_to_bazel, "optional_spack_prefix", return_value="") as optional_spack_prefix:
            lock = spack_to_bazel.build_lock(
                spec,
                "/unused/spack",
                native_overrides={
                    "cuda": "@cuda_native//:lib",
                    "coreutils": "@coreutils_native//:lib",
                },
            )

        self.assertEqual(optional_spack_prefix.call_count, 2)
        self.assertEqual(lock["packages"]["spack_cuda"]["build"], "native")
        self.assertEqual(lock["packages"]["spack_cuda"]["native_prefix"], "@cuda_native//:lib")
        self.assertEqual(lock["packages"]["spack_cuda"]["prefix"], "")
        self.assertEqual(lock["packages"]["spack_cuda"]["link_deps"], [])
        self.assertEqual(lock["packages"]["spack_coreutils"]["build"], "native")

    def test_version_qualified_native_override_does_not_match_other_versions(self) -> None:
        spec = {
            "spec": {
                "nodes": [
                    {
                        "name": "protobuf",
                        "version": "32.1",
                        "hash": "protobuf321hash",
                        "dependencies": [],
                    },
                ]
            }
        }

        with mock.patch.object(spack_to_bazel, "spack_prefix", return_value="/spack/protobuf-32.1") as spack_prefix:
            lock = spack_to_bazel.build_lock(
                spec,
                "/unused/spack",
                native_overrides={"protobuf@3.13.0": "@protobuf_native//:lib"},
            )

        self.assertEqual(spack_prefix.call_count, 1)
        self.assertEqual(lock["packages"]["spack_protobuf"]["build"], "spack")
        self.assertNotIn("native_prefix", lock["packages"]["spack_protobuf"])

    def test_version_qualified_native_override_matches_exact_version(self) -> None:
        spec = {
            "spec": {
                "nodes": [
                    {
                        "name": "protobuf",
                        "version": "3.13.0",
                        "hash": "protobuf313hash",
                        "dependencies": [],
                    },
                ]
            }
        }

        with mock.patch.object(spack_to_bazel, "optional_spack_prefix", return_value="") as optional_spack_prefix:
            lock = spack_to_bazel.build_lock(
                spec,
                "/unused/spack",
                native_overrides={"protobuf@3.13.0": "@protobuf_native//:lib"},
            )

        self.assertEqual(optional_spack_prefix.call_count, 1)
        self.assertEqual(lock["packages"]["spack_protobuf"]["build"], "native")
        self.assertEqual(lock["packages"]["spack_protobuf"]["native_prefix"], "@protobuf_native//:lib")

    def test_odr_sensitive_override_must_be_version_qualified(self) -> None:
        spec = {
            "spec": {
                "nodes": [
                    {
                        "name": "protobuf",
                        "version": "32.1",
                        "hash": "protobuf321hash",
                        "dependencies": [],
                    },
                ]
            }
        }

        with self.assertRaises(SystemExit) as err:
            spack_to_bazel.build_lock(
                spec,
                "/unused/spack",
                native_overrides={"protobuf": "@protobuf_native//:lib"},
            )

        self.assertIn("must be version-qualified", str(err.exception))

    def test_odr_sensitive_override_key_is_rejected_even_when_package_not_in_lock(self) -> None:
        spec = {
            "spec": {
                "nodes": [
                    {
                        "name": "zlib-ng",
                        "version": "2.2.5",
                        "hash": "zlibhash",
                        "dependencies": [],
                    },
                ]
            }
        }

        with self.assertRaises(SystemExit) as err:
            spack_to_bazel.build_lock(
                spec,
                "/unused/spack",
                native_overrides={"boost": "@boost_native//:lib"},
            )

        self.assertIn("must be version-qualified", str(err.exception))

    def test_inactive_odr_sensitive_exact_overrides_do_not_conflict(self) -> None:
        spec = {
            "spec": {
                "nodes": [
                    {
                        "name": "zlib-ng",
                        "version": "2.2.5",
                        "hash": "zlibhash",
                        "dependencies": [],
                    },
                ]
            }
        }

        with mock.patch.object(spack_to_bazel, "spack_prefix", return_value="/spack/prefix"):
            lock = spack_to_bazel.build_lock(
                spec,
                "/unused/spack",
                native_overrides={
                    "protobuf@3.13.0": "@protobuf_native//:lib",
                    "py-protobuf@6.32.1": "@py_protobuf_native//:lib",
                },
            )

        self.assertEqual(lock["packages"]["spack_zlib_ng"]["build"], "spack")

    def test_active_odr_sensitive_exact_overrides_reject_mixed_family_versions(self) -> None:
        spec = {
            "spec": {
                "nodes": [
                    {
                        "name": "protobuf",
                        "version": "3.13.0",
                        "hash": "protobuf313hash",
                        "dependencies": [],
                    },
                    {
                        "name": "py-protobuf",
                        "version": "6.32.1",
                        "hash": "pyprotobuf6321hash",
                        "dependencies": [],
                    },
                ]
            }
        }

        with mock.patch.object(spack_to_bazel, "spack_prefix", return_value="/spack/prefix"):
            with self.assertRaises(SystemExit) as err:
                spack_to_bazel.build_lock(
                    spec,
                    "/unused/spack",
                    native_overrides={
                        "protobuf@3.13.0": "@protobuf_native//:lib",
                        "py-protobuf@6.32.1": "@py_protobuf_native//:lib",
                    },
                )

        self.assertIn("ODR-sensitive provider family 'protobuf'", str(err.exception))

    def test_odr_sensitive_exact_override_matches_current_version(self) -> None:
        spec = {
            "spec": {
                "nodes": [
                    {
                        "name": "py-protobuf",
                        "version": "6.32.1",
                        "hash": "pyprotobuf6321hash",
                        "dependencies": [],
                    },
                ]
            }
        }

        with mock.patch.object(spack_to_bazel, "optional_spack_prefix", return_value="") as optional_spack_prefix:
            lock = spack_to_bazel.build_lock(
                spec,
                "/unused/spack",
                native_overrides={"py-protobuf@6.32.1": "@py_protobuf_native//:lib"},
            )

        self.assertEqual(optional_spack_prefix.call_count, 1)
        self.assertEqual(lock["packages"]["spack_py_protobuf"]["build"], "native")
        self.assertEqual(
            lock["packages"]["spack_py_protobuf"]["native_prefix"],
            "@py_protobuf_native//:lib",
        )

    def test_odr_sensitive_duplicate_versions_are_rejected(self) -> None:
        spec = {
            "spec": {
                "nodes": [
                    {
                        "name": "boost",
                        "version": "1.90.0",
                        "hash": "boost190hash",
                        "dependencies": [],
                    },
                    {
                        "name": "boost",
                        "version": "1.91.0",
                        "hash": "boost191hash",
                        "dependencies": [],
                    },
                ]
            }
        }

        with mock.patch.object(spack_to_bazel, "spack_prefix", return_value="/spack/prefix"):
            with self.assertRaises(SystemExit) as err:
                spack_to_bazel.build_lock(spec, "/unused/spack", native_overrides={})

        self.assertIn("appears at multiple versions", str(err.exception))

    def test_abseil_cpp_duplicate_versions_are_rejected(self) -> None:
        spec = {
            "spec": {
                "nodes": [
                    {
                        "name": "abseil-cpp",
                        "version": "20250127.1",
                        "hash": "abseil20250127hash",
                        "dependencies": [],
                    },
                    {
                        "name": "abseil-cpp",
                        "version": "20250814.0",
                        "hash": "abseil20250814hash",
                        "dependencies": [],
                    },
                ]
            }
        }

        with mock.patch.object(spack_to_bazel, "spack_prefix", return_value="/spack/prefix"):
            with self.assertRaises(SystemExit) as err:
                spack_to_bazel.build_lock(spec, "/unused/spack", native_overrides={})

        self.assertIn("ODR-sensitive package 'abseil-cpp'", str(err.exception))

    def test_odr_sensitive_family_versions_are_rejected(self) -> None:
        spec = {
            "spec": {
                "nodes": [
                    {
                        "name": "grpc",
                        "version": "1.76.0",
                        "hash": "grpc176hash",
                        "dependencies": [],
                    },
                    {
                        "name": "grpc-cpp",
                        "version": "1.77.0",
                        "hash": "grpccpp177hash",
                        "dependencies": [],
                    },
                ]
            }
        }

        with mock.patch.object(spack_to_bazel, "spack_prefix", return_value="/spack/prefix"):
            with self.assertRaises(SystemExit) as err:
                spack_to_bazel.build_lock(spec, "/unused/spack", native_overrides={})

        self.assertIn("ODR-sensitive provider family 'grpc'", str(err.exception))

    def test_py_protobuf_6_and_protobuf_32_share_canonical_version(self) -> None:
        spec = {
            "spec": {
                "nodes": [
                    {
                        "name": "protobuf",
                        "version": "32.1",
                        "hash": "protobuf321hash",
                        "dependencies": [],
                    },
                    {
                        "name": "py-protobuf",
                        "version": "6.32.1",
                        "hash": "pyprotobuf6321hash",
                        "dependencies": [],
                    },
                ]
            }
        }

        with mock.patch.object(spack_to_bazel, "spack_prefix", return_value="/spack/prefix"):
            lock = spack_to_bazel.build_lock(spec, "/unused/spack", native_overrides={})

        self.assertEqual(lock["packages"]["spack_protobuf"]["build"], "spack")
        self.assertEqual(lock["packages"]["spack_py_protobuf"]["build"], "spack")

    def test_py_protobuf_4_and_protobuf_21_share_canonical_version(self) -> None:
        spec = {
            "spec": {
                "nodes": [
                    {
                        "name": "protobuf",
                        "version": "21.12",
                        "hash": "protobuf32112hash",
                        "dependencies": [],
                    },
                    {
                        "name": "py-protobuf",
                        "version": "4.21.12",
                        "hash": "pyprotobuf42112hash",
                        "dependencies": [],
                    },
                ]
            }
        }

        with mock.patch.object(spack_to_bazel, "spack_prefix", return_value="/spack/prefix"):
            lock = spack_to_bazel.build_lock(spec, "/unused/spack", native_overrides={})

        self.assertEqual(lock["packages"]["spack_protobuf"]["build"], "spack")
        self.assertEqual(lock["packages"]["spack_py_protobuf"]["build"], "spack")

    def test_py_protobuf_3_and_protobuf_3_share_canonical_version(self) -> None:
        spec = {
            "spec": {
                "nodes": [
                    {
                        "name": "protobuf",
                        "version": "3.13.0",
                        "hash": "protobuf313hash",
                        "dependencies": [],
                    },
                    {
                        "name": "py-protobuf",
                        "version": "3.13.0",
                        "hash": "pyprotobuf313hash",
                        "dependencies": [],
                    },
                ]
            }
        }

        with mock.patch.object(spack_to_bazel, "spack_prefix", return_value="/spack/prefix"):
            lock = spack_to_bazel.build_lock(spec, "/unused/spack", native_overrides={})

        self.assertEqual(lock["packages"]["spack_protobuf"]["build"], "spack")
        self.assertEqual(lock["packages"]["spack_py_protobuf"]["build"], "spack")

    def test_repository_overrides_capture_pytorch_cpp_protobuf_family_keys(self) -> None:
        spec = {
            "spec": {
                "nodes": [
                    {
                        "name": "protobuf",
                        "version": "21.12",
                        "hash": "protobuf32112hash",
                        "dependencies": [],
                    },
                    {
                        "name": "py-protobuf",
                        "version": "4.21.12",
                        "hash": "pyprotobuf42112hash",
                        "dependencies": [],
                    },
                ]
            }
        }
        native_overrides = spack_to_bazel.read_native_overrides(EXPERIMENT_ROOT / "native_overrides.json")

        with (
            mock.patch.object(spack_to_bazel, "optional_spack_prefix", return_value="") as optional_spack_prefix,
            mock.patch.object(spack_to_bazel, "spack_prefix", return_value="/spack/py-protobuf-4.21.12"),
        ):
            lock = spack_to_bazel.build_lock(spec, "/unused/spack", native_overrides=native_overrides)

        self.assertEqual(
            optional_spack_prefix.call_args_list,
            [mock.call("/unused/spack", "protobuf32112hash"), mock.call("/unused/spack", "pyprotobuf42112hash")],
        )
        self.assertEqual(lock["packages"]["spack_protobuf"]["build"], "native")
        self.assertEqual(lock["packages"]["spack_protobuf"]["native_prefix"], "@protobuf_native//:lib")
        self.assertEqual(lock["packages"]["spack_py_protobuf"]["build"], "native")
        self.assertEqual(lock["packages"]["spack_py_protobuf"]["native_prefix"], "@py_protobuf_native//:lib")

    def test_py_protobuf_6_and_protobuf_3_are_rejected(self) -> None:
        spec = {
            "spec": {
                "nodes": [
                    {
                        "name": "protobuf",
                        "version": "3.13.0",
                        "hash": "protobuf313hash",
                        "dependencies": [],
                    },
                    {
                        "name": "py-protobuf",
                        "version": "6.32.1",
                        "hash": "pyprotobuf6321hash",
                        "dependencies": [],
                    },
                ]
            }
        }

        with mock.patch.object(spack_to_bazel, "spack_prefix", return_value="/spack/prefix"):
            with self.assertRaises(SystemExit) as err:
                spack_to_bazel.build_lock(spec, "/unused/spack", native_overrides={})

        self.assertIn("ODR-sensitive provider family 'protobuf'", str(err.exception))

    def test_native_lock_is_reusable_without_spack_prefix_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "lock.json"
            lock.write_text(
                """{
                  "root": "spack_cuda",
                  "packages": {
                    "spack_cuda": {
                      "package": "cuda",
                      "prefix": "",
                      "build": "native",
                      "include_dirs": [],
                      "native_prefix": "@cuda_native//:lib"
                    }
                  }
                }
                """
            )

            self.assertTrue(spack_to_bazel.lock_is_valid(lock, "spack_cuda"))

    def test_spack_lock_is_not_reusable_without_spack_prefix_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "lock.json"
            lock.write_text(
                """{
                  "root": "spack_cuda",
                  "packages": {
                    "spack_cuda": {
                      "package": "cuda",
                      "prefix": "",
                      "build": "spack",
                      "include_dirs": []
                    }
                  }
                }
                """
            )

            self.assertFalse(spack_to_bazel.lock_is_valid(lock, "spack_cuda"))


class NativeProviderVersionTest(unittest.TestCase):
    PYTHON_SPEC = {
        "spec": {
            "nodes": [
                {
                    "name": "python",
                    "version": "3.13.13",
                    "hash": "python31313hash",
                    "dependencies": [],
                }
            ]
        }
    }
    PYTHON_SUBSTITUTION = {
        "key": "python",
        "graph_version": "3.13.13",
        "provided": "3.14.5",
        "ticket": ".scratch/pytorch-frontier-convergence/issues/07-native-python-313-provider.md",
    }

    def test_build_lock_refuses_native_flip_at_another_version(self) -> None:
        with mock.patch.object(spack_to_bazel, "optional_spack_prefix", return_value=""):
            with self.assertRaises(SystemExit) as err:
                spack_to_bazel.build_lock(
                    self.PYTHON_SPEC,
                    "/unused/spack",
                    native_overrides={"python": "@python_native//:lib"},
                    provided_versions={"python": "3.14.5"},
                    known_version_substitutions=[],
                )

        self.assertIn("builds python@3.14.5 but serves graph node python@3.13.13", str(err.exception))

    def test_build_lock_accepts_allowlisted_substitution(self) -> None:
        with mock.patch.object(spack_to_bazel, "optional_spack_prefix", return_value=""):
            lock = spack_to_bazel.build_lock(
                self.PYTHON_SPEC,
                "/unused/spack",
                native_overrides={"python": "@python_native//:lib"},
                provided_versions={"python": "3.14.5"},
                known_version_substitutions=[self.PYTHON_SUBSTITUTION],
            )

        self.assertEqual(lock["packages"]["spack_python"]["build"], "native")

    def test_build_lock_does_not_report_allowlist_absent_from_partial_lock(self) -> None:
        spec = {"spec": {"nodes": [{"name": "zstd", "version": "1.5.7", "hash": "zstdhash", "dependencies": []}]}}
        with mock.patch.object(spack_to_bazel, "optional_spack_prefix", return_value=""):
            lock = spack_to_bazel.build_lock(
                spec,
                "/unused/spack",
                native_overrides={"python": "@python_native//:lib", "zstd": "@zstd_native//:lib"},
                provided_versions={"python": "3.14.5", "zstd": "1.5.7"},
                known_version_substitutions=[self.PYTHON_SUBSTITUTION],
            )

        self.assertEqual(lock["packages"]["spack_zstd"]["build"], "native")

    def test_adhoc_overrides_without_map_use_canonical_versions(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMPDIR")) as tmp:
            root = Path(tmp)
            (root / "native_overrides.json").write_text(
                '{"native": {"python": "@python_native//:lib", "zstd": "@zstd_native//:lib"}, '
                '"provided_versions": {"python": "3.14.5", "zstd": "1.5.7"}, '
                '"known_version_substitutions": [{"key": "python", "graph_version": "3.13.13", '
                '"provided": "3.14.5", "ticket": "07"}]}\n'
            )
            adhoc = root / ".tmp_zstd_native_overrides.json"
            adhoc.write_text('{"native": {"zstd": "@zstd_native//:lib"}}\n')

            provided, substitutions = spack_to_bazel.read_provider_versions(adhoc)

        self.assertEqual(provided, {"zstd": "1.5.7"})
        self.assertEqual(substitutions, [])

    def test_adhoc_override_with_no_version_anywhere_fails(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMPDIR")) as tmp:
            root = Path(tmp)
            (root / "native_overrides.json").write_text(
                '{"native": {"zstd": "@zstd_native//:lib"}, "provided_versions": {"zstd": "1.5.7"}}\n'
            )
            adhoc = root / ".tmp_libraqm_native_overrides.json"
            adhoc.write_text('{"native": {"libraqm": "@libraqm_native//:lib"}}\n')

            with self.assertRaises(SystemExit) as err:
                spack_to_bazel.read_provider_versions(adhoc)

        self.assertIn("records no provided version for: libraqm", str(err.exception))

    def test_adhoc_override_fallback_still_rejects_substitution_at_lock_time(self) -> None:
        lock = {"packages": {"spack_python": {"package": "python", "version": "3.13.13", "build": "spack"}}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "native_overrides.json").write_text(
                '{"native": {"python": "@python_native//:lib"}, "provided_versions": {"python": "3.14.5"}}\n'
            )
            adhoc = root / ".tmp_python_native_overrides.json"
            adhoc.write_text('{"native": {"python": "@python_native//:lib"}}\n')

            with self.assertRaises(SystemExit) as err:
                spack_to_bazel.normalize_providers(lock, adhoc)

        self.assertIn("builds python@3.14.5 but serves graph node python@3.13.13", str(err.exception))

    def test_exact_key_must_provide_its_own_version(self) -> None:
        errors = spack_to_bazel.native_provider_version_static_errors(
            {"protobuf@21.12": "@protobuf_native//:lib"},
            {"protobuf@21.12": "3.21.12"},
            [],
        )

        self.assertEqual(
            errors,
            ["exact native override 'protobuf@21.12' must provide 21.12, but provided_versions says 3.21.12"],
        )

    def test_normalize_providers_enforces_provided_versions_from_file(self) -> None:
        lock = {
            "packages": {
                "spack_python": {"package": "python", "version": "3.13.13", "build": "spack"},
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            overrides = Path(tmp) / "native_overrides.json"
            overrides.write_text(
                '{"native": {"python": "@python_native//:lib"}, "provided_versions": {"python": "3.14.5"}}\n'
            )
            with self.assertRaises(SystemExit) as err:
                spack_to_bazel.normalize_providers(lock, overrides)

        self.assertIn("native provider version mismatch", str(err.exception))

    def test_repository_overrides_record_every_provided_version(self) -> None:
        overrides_path = EXPERIMENT_ROOT / "native_overrides.json"
        native = spack_to_bazel.read_native_overrides(overrides_path)
        provided_versions, substitutions = spack_to_bazel.read_provider_versions(overrides_path)

        self.assertEqual(set(native), set(provided_versions))
        for entry in substitutions:
            self.assertTrue(entry.get("ticket"), entry)


if __name__ == "__main__":
    unittest.main()
