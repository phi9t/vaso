import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "release" / "determinism_compare.py"


def write_wheel(path: Path, files: dict[str, bytes], record: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
        zf.writestr("sample-1.0.dist-info/RECORD", record)


def write_prefix_manifest(
    path: Path,
    *,
    tree_sha256: str,
    entries: list[dict[str, object]],
    name: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document: dict[str, object] = {
        "entries": entries,
        "schema_version": 1,
        "tree_sha256": tree_sha256,
    }
    if name is not None:
        document["name"] = name
    path.write_text(
        json.dumps(document, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_compare(
    tmp_path: Path,
    first: Path,
    second: Path,
    allowlist: dict[str, object] | None = None,
    *,
    first_output_base: Path | None = None,
    second_output_base: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    allowlist_path = tmp_path / "allowlist.json"
    allowlist_path.write_text(json.dumps(allowlist or {"schema_version": 1, "entries": []}) + "\n", encoding="utf-8")
    out = tmp_path / "determinism.json"
    argv = [
        sys.executable,
        str(SCRIPT),
        "--profile",
        "torch",
        "--line",
        "cu130",
        "--first",
        str(first),
        "--second",
        str(second),
        "--allowlist",
        str(allowlist_path),
        "--out",
        str(out),
    ]
    if first_output_base is not None:
        argv.extend(["--first-output-base", str(first_output_base)])
    if second_output_base is not None:
        argv.extend(["--second-output-base", str(second_output_base)])
    return subprocess.run(
        argv,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "VASO_ESTATE_ROOT": str(tmp_path / "estate")},
    )


def test_record_differences_are_normalized(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    wheel_name = "sample-1.0-py3-none-any.whl"
    files = {
        "sample/__init__.py": b"VALUE = 1\n",
        "sample-1.0.dist-info/METADATA": b"Name: sample\nVersion: 1.0\n",
    }
    write_wheel(first / wheel_name, files, "sample/__init__.py,sha256=old,10\nsample-1.0.dist-info/RECORD,,\n")
    write_wheel(second / wheel_name, files, "sample/__init__.py,sha256=new,20\nsample-1.0.dist-info/RECORD,,\n")

    result = run_compare(tmp_path, first, second)

    assert result.returncode == 0, result.stderr
    doc = json.loads((tmp_path / "determinism.json").read_text())
    assert doc["identical"] is True
    assert doc["differing_files"] == []


def test_unallowlisted_content_difference_fails(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    wheel_name = "sample-1.0-py3-none-any.whl"
    write_wheel(first / wheel_name, {"sample/data.txt": b"one\n"}, "sample/data.txt,sha256=a,4\n")
    write_wheel(second / wheel_name, {"sample/data.txt": b"two\n"}, "sample/data.txt,sha256=b,4\n")

    result = run_compare(tmp_path, first, second)

    assert result.returncode == 1
    doc = json.loads((tmp_path / "determinism.json").read_text())
    assert doc["identical"] is False
    assert doc["differing_files"] == [
        {
            "allowed": False,
            "first_sha256": "sha256:2c8b08da5ce60398e1f19af0e5dccc744df274b826abe585eaba68c525434806",
            "path": "sample/data.txt",
            "reason": None,
            "second_sha256": "sha256:27dd8ed44a83ff94d557f9fd0412ed5a8cbca69ea04922d88c01184a07300a5a",
            "wheel": wheel_name,
        }
    ]


def test_allowlisted_difference_passes_with_reason(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    wheel_name = "sample-1.0-py3-none-any.whl"
    write_wheel(first / wheel_name, {"sample/generated.txt": b"one\n"}, "sample/generated.txt,sha256=a,4\n")
    write_wheel(second / wheel_name, {"sample/generated.txt": b"two\n"}, "sample/generated.txt,sha256=b,4\n")

    result = run_compare(
        tmp_path,
        first,
        second,
        {
            "schema_version": 1,
            "entries": [
                {
                    "profile": "torch",
                    "line": "cu130",
                    "wheel": wheel_name,
                    "path": "sample/generated.txt",
                    "reason": "fixture allows this generated metadata field",
                }
            ],
        },
    )

    assert result.returncode == 0, result.stderr
    doc = json.loads((tmp_path / "determinism.json").read_text())
    assert doc["identical"] is False
    assert doc["differing_files"][0]["allowed"] is True
    assert doc["differing_files"][0]["reason"] == "fixture allows this generated metadata field"


def test_unallowlisted_dependency_manifest_mismatch_fails_with_first_differing_file(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    wheel_name = "sample-1.0-py3-none-any.whl"
    write_wheel(first / wheel_name, {"sample/data.txt": b"stable\n"}, "sample/data.txt,sha256=a,7\n")
    write_wheel(second / wheel_name, {"sample/data.txt": b"stable\n"}, "sample/data.txt,sha256=b,7\n")

    first_ob = tmp_path / "first-ob"
    second_ob = tmp_path / "second-ob"
    manifest_rel = Path("external") / "openssl_native" / "prefix_manifest.json"
    stable_entry = {"mode": "0644", "path": "include/openssl/ssl.h", "sha256": "header", "size": 10, "type": "file"}
    write_prefix_manifest(
        first_ob / manifest_rel,
        tree_sha256="tree-first",
        entries=[
            stable_entry,
            {"mode": "0644", "path": "lib/libcrypto.a", "sha256": "archive-first", "size": 11, "type": "file"},
        ],
    )
    write_prefix_manifest(
        second_ob / manifest_rel,
        tree_sha256="tree-second",
        entries=[
            stable_entry,
            {"mode": "0644", "path": "lib/libcrypto.a", "sha256": "archive-second", "size": 12, "type": "file"},
        ],
    )

    result = run_compare(tmp_path, first, second, first_output_base=first_ob, second_output_base=second_ob)

    assert result.returncode == 1
    doc = json.loads((tmp_path / "determinism.json").read_text())
    assert doc["identical"] is False
    assert doc["dep_determinism"] == {
        "first_output_base": str(first_ob),
        "identical": False,
        "mismatched_deps": [
            {
                "allowed": False,
                "dependency": "openssl",
                "differing_files": [
                    {
                        "first": {"mode": "0644", "sha256": "archive-first", "size": 11, "type": "file"},
                        "path": "lib/libcrypto.a",
                        "second": {"mode": "0644", "sha256": "archive-second", "size": 12, "type": "file"},
                    }
                ],
                "first_manifest": "external/openssl_native/prefix_manifest.json",
                "first_tree_sha256": "tree-first",
                "reason": None,
                "second_manifest": "external/openssl_native/prefix_manifest.json",
                "second_tree_sha256": "tree-second",
            }
        ],
        "second_output_base": str(second_ob),
        "unmaterialized_manifests": [],
    }


def test_allowlisted_dependency_manifest_mismatch_passes_with_reason(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    wheel_name = "sample-1.0-py3-none-any.whl"
    write_wheel(first / wheel_name, {"sample/data.txt": b"stable\n"}, "sample/data.txt,sha256=a,7\n")
    write_wheel(second / wheel_name, {"sample/data.txt": b"stable\n"}, "sample/data.txt,sha256=b,7\n")

    first_ob = tmp_path / "first-ob"
    second_ob = tmp_path / "second-ob"
    manifest_rel = Path("external") / "openssl_native" / "prefix_manifest.json"
    write_prefix_manifest(
        first_ob / manifest_rel,
        tree_sha256="tree-first",
        entries=[{"mode": "0644", "path": "lib/libcrypto.a", "sha256": "archive-first", "size": 11, "type": "file"}],
    )
    write_prefix_manifest(
        second_ob / manifest_rel,
        tree_sha256="tree-second",
        entries=[{"mode": "0644", "path": "lib/libcrypto.a", "sha256": "archive-second", "size": 12, "type": "file"}],
    )

    result = run_compare(
        tmp_path,
        first,
        second,
        {
            "schema_version": 1,
            "entries": [],
            "dep_entries": [
                {
                    "profile": "torch",
                    "line": "cu130",
                    "dependency": "openssl",
                    "reason": "PB-C3 fixture allows known OpenSSL archive timestamp drift",
                }
            ],
        },
        first_output_base=first_ob,
        second_output_base=second_ob,
    )

    assert result.returncode == 0, result.stderr
    dep = json.loads((tmp_path / "determinism.json").read_text())["dep_determinism"]
    assert dep["identical"] is False
    assert dep["mismatched_deps"][0]["allowed"] is True
    assert dep["mismatched_deps"][0]["reason"] == "PB-C3 fixture allows known OpenSSL archive timestamp drift"


def test_action_built_dependency_manifest_mismatch_is_reported(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    wheel_name = "sample-1.0-py3-none-any.whl"
    write_wheel(first / wheel_name, {"sample/data.txt": b"stable\n"}, "sample/data.txt,sha256=a,7\n")
    write_wheel(second / wheel_name, {"sample/data.txt": b"stable\n"}, "sample/data.txt,sha256=b,7\n")

    first_ob = tmp_path / "first-ob"
    second_ob = tmp_path / "second-ob"
    manifest_rel = (
        Path("execroot")
        / "_main"
        / "bazel-out"
        / "k8-fastbuild"
        / "bin"
        / "native"
        / "torchvision"
        / "torchvision_action_torch_prefix_manifest.json"
    )
    write_prefix_manifest(
        first_ob / manifest_rel,
        tree_sha256="torch-first",
        entries=[{"mode": "0644", "path": "lib/libtorch.so", "sha256": "torch-first", "size": 11, "type": "file"}],
        name="torch",
    )
    write_prefix_manifest(
        second_ob / manifest_rel,
        tree_sha256="torch-second",
        entries=[{"mode": "0644", "path": "lib/libtorch.so", "sha256": "torch-second", "size": 12, "type": "file"}],
        name="torch",
    )

    result = run_compare(tmp_path, first, second, first_output_base=first_ob, second_output_base=second_ob)

    assert result.returncode == 1
    dep = json.loads((tmp_path / "determinism.json").read_text())["dep_determinism"]
    assert dep["mismatched_deps"] == [
        {
            "allowed": False,
            "dependency": "torch",
            "differing_files": [
                {
                    "first": {"mode": "0644", "sha256": "torch-first", "size": 11, "type": "file"},
                    "path": "lib/libtorch.so",
                    "second": {"mode": "0644", "sha256": "torch-second", "size": 12, "type": "file"},
                }
            ],
            "first_manifest": manifest_rel.as_posix(),
            "first_tree_sha256": "torch-first",
            "reason": None,
            "second_manifest": manifest_rel.as_posix(),
            "second_tree_sha256": "torch-second",
        }
    ]


def test_one_sided_dependency_manifest_is_unmaterialized_not_a_mismatch(tmp_path: Path) -> None:
    # A disk-cache hit does not write non-top-level outputs, so a manifest can
    # exist in one output base only. That is missing evidence, not a mismatch.
    first = tmp_path / "first"
    second = tmp_path / "second"
    wheel_name = "sample-1.0-py3-none-any.whl"
    write_wheel(first / wheel_name, {"sample/data.txt": b"stable\n"}, "sample/data.txt,sha256=a,7\n")
    write_wheel(second / wheel_name, {"sample/data.txt": b"stable\n"}, "sample/data.txt,sha256=a,7\n")

    first_ob = tmp_path / "first-ob"
    second_ob = tmp_path / "second-ob"
    second_ob.mkdir()
    manifest_rel = (
        Path("execroot")
        / "_main"
        / "bazel-out"
        / "k8-fastbuild"
        / "bin"
        / "native"
        / "zstd"
        / "zstd_action_prefix_manifest.json"
    )
    write_prefix_manifest(
        first_ob / manifest_rel,
        tree_sha256="zstd-first",
        entries=[{"mode": "0644", "path": "lib/libzstd.so", "sha256": "zstd", "size": 3, "type": "file"}],
        name="zstd",
    )

    result = run_compare(tmp_path, first, second, first_output_base=first_ob, second_output_base=second_ob)

    assert result.returncode == 1, result.stdout + result.stderr
    dep = json.loads((tmp_path / "determinism.json").read_text())["dep_determinism"]
    assert dep["identical"] is False
    assert dep["mismatched_deps"] == []
    assert dep["unmaterialized_manifests"] == [
        {"dependency": "zstd", "manifest": manifest_rel.as_posix(), "present_in": "first"}
    ]
