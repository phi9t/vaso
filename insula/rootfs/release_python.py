#!/usr/bin/env python3
"""Seal a copied, qualified Python prefix and its native dependency closure.

Run inside insula with the copied prefix writable and its original Bazel output
base mounted read-only at /vaso-bazel-ob. The resulting prefix needs neither
that output base nor any other build prefix when deployed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path


SYSTEM_LIBRARIES = {"libc.so.6", "libm.so.6", "libpthread.so.0", "libdl.so.2", "librt.so.1", "libutil.so.1"}
SYSTEM_DIRS = (Path("/lib/x86_64-linux-gnu"), Path("/usr/lib/x86_64-linux-gnu"), Path("/lib64"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_elf(path: Path) -> bool:
    with path.open("rb") as stream:
        return stream.read(4) == b"\x7fELF"


def dynamic(path: Path) -> tuple[list[str], list[str]]:
    text = subprocess.check_output(["/usr/bin/readelf", "-d", str(path)], text=True)
    needed = re.findall(r"\(NEEDED\).*\[([^]]+)\]", text)
    paths = re.findall(r"\((?:RPATH|RUNPATH)\).*\[([^]]*)\]", text)
    search = [entry.replace("${ORIGIN}", str(path.parent)).replace("$ORIGIN", str(path.parent))
              for group in paths for entry in group.split(":") if entry]
    return needed, search


def seal(prefix: Path, qualified: Path) -> dict[str, object]:
    if os.environ.get("VASO_IN_INSULA") != "1":
        raise ValueError("release Python sealing must execute inside insula")
    prefix = prefix.resolve()
    qualified = qualified.resolve()
    provider_root = Path("/vaso-bazel-ob/external").resolve()
    if not qualified.is_relative_to(provider_root):
        raise ValueError("qualified prefix must belong to the declared provider output base")
    library_dir = prefix / "lib"
    queue: list[tuple[Path, Path]] = []
    sources: dict[str, str] = {}
    inherited_search: list[str] = []
    for path in sorted(prefix.rglob("*")):
        if path.is_file() and not path.is_symlink() and is_elf(path):
            original = qualified / path.relative_to(prefix)
            if digest(path) != digest(original):
                raise ValueError("copied Python differs from qualified input")
            queue.append((path, original))
            for directory in dynamic(original)[1]:
                if directory not in inherited_search:
                    inherited_search.append(directory)
    processed: set[Path] = set()
    while queue:
        target, original = queue.pop()
        if target in processed:
            continue
        processed.add(target)
        needed, search = dynamic(original)
        for name in needed:
            if Path(name).name != name:
                raise ValueError("DT_NEEDED must use a library name")
            if name in SYSTEM_LIBRARIES:
                if not any((directory / name).is_file() for directory in SYSTEM_DIRS):
                    raise ValueError("missing rootfs system library: " + name)
                continue
            # DT_RPATH on the Python executable also resolves dependencies of
            # extension libraries that carry no search path of their own.
            candidates = [Path(directory) / name for directory in search + inherited_search]
            source = next((path.resolve() for path in candidates if path.is_file()), None)
            if source is None:
                source = next((directory / name for directory in SYSTEM_DIRS if (directory / name).is_file()), None)
                if source is None:
                    raise ValueError("unresolved native dependency: " + name)
                continue
            if not source.is_relative_to(provider_root):
                raise ValueError("dependency escaped the declared provider output base: " + name)
            destination = library_dir / name
            if destination.exists():
                if digest(destination) != digest(source):
                    raise ValueError("conflicting native dependency: " + name)
                destination = destination.resolve()
            else:
                shutil.copy2(source, destination)
            sources[name] = digest(source)
            queue.append((destination, source))
    # Only our copied interpreter/dependency files are changed. The supplied
    # CUDA rootfs and vendor binaries retain their original bytes.
    for path in sorted(processed):
        relative = os.path.relpath(library_dir, path.parent)
        rpath = "$ORIGIN" if relative == "." else "$ORIGIN/" + relative
        subprocess.run(["/usr/bin/patchelf", "--force-rpath", "--set-rpath", rpath, str(path)], check=True)
    for path in (prefix / "bin").iterdir():
        if path.is_symlink() or not path.is_file() or is_elf(path):
            continue
        data = path.read_bytes()
        path.write_bytes(data.replace(str(qualified).encode(), str(prefix).encode()))
    version = subprocess.check_output([
        str(prefix / "bin/python3"), "-I", "-c",
        "import sys; print('.'.join(map(str, sys.version_info[:3])))",
    ], text=True).strip()
    if version != "3.13.13":
        raise ValueError("release wheels require Python 3.13.13, got " + version)
    return {
        "schema_version": 1,
        "version": version,
        "python": str(prefix / "bin/python3"),
        "qualified_dependency_sha256": sources,
        "installed_files": {str(p.relative_to(prefix)): digest(p) for p in sorted(prefix.rglob("*"))
                            if p.is_file() and not p.is_symlink()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, default=Path("/opt/vaso/python"))
    parser.add_argument("--qualified-prefix", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=Path("/opt/vaso/release-python.json"))
    args = parser.parse_args()
    manifest = seal(args.prefix, args.qualified_prefix)
    args.manifest.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print("Sealed Python 3.13.13 and", len(manifest["qualified_dependency_sha256"]), "native libraries")


if __name__ == "__main__":
    main()
