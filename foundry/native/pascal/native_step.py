"""Package phases for real Pascal native actions; execution belongs to Bazel."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile


def prepare_torch(source: Path, profile: dict) -> None:
    for name in profile["critical_source_files"]:
        if not (source / name).is_file():
            raise ValueError("recursive Torch archive lacks required input: " + name)
    for edit in profile.get("source_edits", []):
        rel = Path(edit["path"])
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("source edit escapes archive")
        path = source / rel
        before, after = edit["before_text"].encode(), edit["after_text"].encode()
        if path.read_bytes() != before:
            raise ValueError("source edit preimage mismatch: " + str(rel))
        text = before.decode()
        if text.count(edit["old"]) != 1:
            raise ValueError("source edit must match once")
        result = text.replace(edit["old"], edit["new"], 1).encode()
        if result != after:
            raise ValueError("source edit postimage mismatch")
        path.write_bytes(result)


def validate_wheel(path: Path, package: str) -> dict:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        metadata = [n for n in names if n.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise ValueError("wheel needs exactly one metadata record")
        contents = archive.read(metadata[0]).decode()
        expected = "torch/_C.cpython-312-x86_64-linux-gnu.so" if package == "torch" else "triton/_C/libtriton.so"
        if expected not in names:
            raise ValueError("wheel lacks native extension: " + expected)
        versions = [line.removeprefix("Version: ") for line in contents.splitlines() if line.startswith("Version: ")]
        expected_version = "2.15.0+vaso.cu126.sm61" if package == "torch" else "3.8.0"
        if versions != [expected_version]:
            raise ValueError("native wheel version mismatch: " + repr(versions))
    return {"name": path.name, "bytes": path.stat().st_size, "version": expected_version, "python_abi": "cp312"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("phase", choices=["configure", "build", "install"])
    parser.add_argument("--package", required=True, choices=["torch", "triton"])
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.phase == "configure":
        if args.package == "torch":
            prepare_torch(args.source, json.loads(args.profile.read_text()))
        (args.work / "wheelhouse").mkdir(exist_ok=True)
    elif args.phase == "build":
        command = [
            "/opt/vaso/pascal/bin/uv", "build", "--python", "/usr/local/bin/python3",
            "--no-python-downloads", "--no-build-isolation", "--offline",
            "--no-index", "--no-config", "--no-cache", "--wheel",
            "--out-dir", str(args.work / "wheelhouse"), str(args.source),
        ]
        return subprocess.call(command, cwd=args.source)
    else:
        wheels = sorted((args.work / "wheelhouse").glob(args.package + "-*.whl"))
        if len(wheels) != 1:
            raise ValueError("expected exactly one newly built " + args.package + " wheel")
        validate_wheel(wheels[0], args.package)
        args.wheel.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(wheels[0], args.wheel)
        print(json.dumps({"native_wheel": validate_wheel(args.wheel, args.package)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
