#!/usr/bin/env python3
"""Repair native release wheels for the Vaso rootfs runtime contract."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

try:
    from native.common.release_wheel import is_under_rootfs_dir, load_rootfs_lock, rootfs_library_dirs
except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
    from release_wheel import is_under_rootfs_dir, load_rootfs_lock, rootfs_library_dirs


_ELF_MAGIC = b"\x7fELF"
_DYNAMIC_ENTRY = re.compile(r"\((?P<tag>NEEDED|RUNPATH|RPATH|SONAME)\).*?\[(?P<value>[^\]]*)\]")
_CUDA_FAMILY_SONAME = re.compile(
    r"^("
    r"libcuda|libcudart|libcublas|libcublasLt|libcufft|libcurand|libcusolver|"
    r"libcusparse|libcusparseLt|libcudnn|libnccl|libnvshmem|libnvshmem_host|"
    r"libnvrtc|libnvJitLink|libcudss|"
    r"libnvfatbin|libnvptxcompiler|libnvvm|libcupti|libcufile|libnvidia-ml|"
    r"libnvinfer|libnvonnxparsers"
    r")\.so(?:\.|$)"
)
_PLATFORM_LIBS = frozenset(
    (
        "ld-linux-x86-64.so.2",
        "libanl.so.1",
        "libc.so.6",
        "libdl.so.2",
        "libgcc_s.so.1",
        "libm.so.6",
        "libpthread.so.0",
        "librt.so.1",
        "libstdc++.so.6",
        "libutil.so.1",
    )
)
_FORBIDDEN_STATIC_NEEDLES = (
    b"/vaso/",
    b".vaso-estate",
)
_FORBIDDEN_PATH_MARKERS = (
    b"/output-base",
    b"\\output-base",
    b"output-base/",
    b"/output_base/",
    b"\\output_base\\",
    b"output_base/",
)
_BUILD_PATH_TOKEN_BOUNDARIES = frozenset(b"\0\r\n\t '\"`<>|;")
_PREFIX_MAP_FLAG = re.compile(r"(?<!\S)-f(?:file|debug|macro)-prefix-map=\S+")
_CURRENT_DEFAULT_DECLARED_ARCHITECTURES = {
    "py-torch": "10.0",
    "py-torchvision": "10.0",
    "py-torchaudio": "10.0",
    "py-jaxlib": "sm_100",
}


@dataclass(frozen=True)
class DynamicInfo:
    needed: tuple[str, ...]
    rpaths: tuple[str, ...]
    soname: str | None


@dataclass(frozen=True)
class ElfFile:
    rel: PurePosixPath
    path: Path


@dataclass(frozen=True)
class WheelName:
    distribution: str
    version: str
    suffix: str

    @classmethod
    def parse(cls, path: Path) -> "WheelName":
        if path.suffix != ".whl":
            raise ValueError(f"expected a .whl file, got {path}")
        parts = path.name[:-4].split("-")
        if len(parts) < 5:
            raise ValueError(f"wheel filename has too few fields: {path.name}")
        return cls(distribution=parts[0], version=parts[1], suffix="-".join(parts[2:]))

    def with_version(self, version: str) -> str:
        return f"{self.distribution}-{version}-{self.suffix}.whl"


def _wheel_distribution_name(name: str) -> str:
    return re.sub(r"[-.]+", "_", name).lower()


def _temp_parent() -> Path | None:
    value = os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR")
    return Path(value) if value else None


def _safe_extract(wheel: Path, target: Path) -> None:
    with zipfile.ZipFile(wheel) as zf:
        for info in zf.infolist():
            rel = PurePosixPath(info.filename)
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError(f"unsafe wheel member path {info.filename!r}")
            if info.is_dir():
                (target / rel.as_posix()).mkdir(parents=True, exist_ok=True)
                continue
            out = target / rel.as_posix()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(zf.read(info))
            mode = (info.external_attr >> 16) & 0o777
            if mode:
                out.chmod(mode)


def _is_elf(path: Path) -> bool:
    try:
        return path.read_bytes()[:4] == _ELF_MAGIC
    except OSError:
        return False


def _readelf_dynamic(path: Path, readelf: str) -> DynamicInfo:
    result = subprocess.run(
        [readelf, "-d", str(path)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        return DynamicInfo(needed=(), rpaths=(), soname=None)
    needed: list[str] = []
    rpaths: list[str] = []
    soname: str | None = None
    for line in result.stdout.splitlines():
        match = _DYNAMIC_ENTRY.search(line)
        if not match:
            continue
        tag = match.group("tag")
        value = match.group("value")
        if tag == "NEEDED":
            needed.append(value)
        elif tag in {"RUNPATH", "RPATH"}:
            rpaths.extend(part for part in value.split(":") if part)
        elif tag == "SONAME":
            soname = value
    return DynamicInfo(needed=tuple(needed), rpaths=tuple(rpaths), soname=soname)


def _patchelf_capture(patchelf: str, *args: str) -> tuple[int, str]:
    result = subprocess.run(
        [patchelf, *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.returncode, result.stdout


def _dynamic_info(path: Path, *, patchelf: str, readelf: str) -> DynamicInfo:
    needed_rc, needed_out = _patchelf_capture(patchelf, "--print-needed", str(path))
    rpath_rc, rpath_out = _patchelf_capture(patchelf, "--print-rpath", str(path))
    soname_rc, soname_out = _patchelf_capture(patchelf, "--print-soname", str(path))
    if rpath_rc == 0 and soname_rc == 0 and needed_rc == 0:
        return DynamicInfo(
            needed=tuple(line for line in needed_out.splitlines() if line),
            rpaths=tuple(part for part in rpath_out.strip().split(":") if part),
            soname=soname_out.strip() or None,
        )
    return _readelf_dynamic(path, readelf)


def _run_patchelf(patchelf: str, *args: str) -> None:
    subprocess.run([patchelf, *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _is_origin_relative(path: str) -> bool:
    return path == "$ORIGIN" or path.startswith("$ORIGIN/") or path == "${ORIGIN}" or path.startswith("${ORIGIN}/")


def _is_jax_nvidia_origin(path: str) -> bool:
    return path.startswith("$ORIGIN/../nvidia/") or path.startswith("${ORIGIN}/../nvidia/")


def _is_cuda_family(soname: str) -> bool:
    return _CUDA_FAMILY_SONAME.match(PurePosixPath(soname).name) is not None


def _is_platform_library(soname: str) -> bool:
    return "/" not in soname and soname in _PLATFORM_LIBS


def _provider_lib_dirs(provider_prefixes: Iterable[Path]) -> list[Path]:
    dirs: list[Path] = []
    seen: set[Path] = set()
    for prefix in provider_prefixes:
        for rel in ("lib", "lib64"):
            candidate = prefix / rel
            if candidate.is_dir() and candidate not in seen:
                dirs.append(candidate)
                seen.add(candidate)
    return dirs


def _find_provider_library(soname: str, provider_lib_dirs: Iterable[Path]) -> Path | None:
    for lib_dir in provider_lib_dirs:
        direct = lib_dir / soname
        if direct.is_file():
            return direct
        for candidate in sorted(lib_dir.rglob(soname)):
            if candidate.is_file():
                return candidate
    return None


def _load_provider_metadata(metadata_path: Path | None) -> dict[str, Any]:
    if metadata_path is None:
        raise ValueError("provider metadata is required for release wheel provenance")
    if not metadata_path.is_file():
        raise ValueError(f"provider metadata is missing: {metadata_path}")
    doc = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"provider metadata must be a JSON object: {metadata_path}")
    return doc


def _provider_prefix_base(metadata_path: Path) -> Path | None:
    for parent in (metadata_path.parent, *metadata_path.parents):
        if (parent / "external").is_dir() and (parent / "execroot").is_dir():
            return parent
    for parent in (metadata_path.parent, *metadata_path.parents):
        if (parent / "external").is_dir() and (parent / "bazel-out").is_dir():
            return parent
    return None


def _provider_prefixes_from_metadata(doc: dict[str, Any], *, relative_to: Path | None = None) -> list[Path]:
    prefixes = doc.get("input_prefixes", {})
    if not isinstance(prefixes, dict):
        return []
    out: list[Path] = []
    for value in prefixes.values():
        if not isinstance(value, str) or not value:
            continue
        path = Path(value)
        if not path.is_absolute() and relative_to is not None:
            path = relative_to / path
        out.append(path)
    return out


def _dist_info_dirs(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.dist-info") if path.is_dir())


def _metadata_for(root: Path) -> tuple[Path, dict[str, str]]:
    candidates = [path for path in _dist_info_dirs(root) if (path / "METADATA").is_file()]
    if len(candidates) != 1:
        raise ValueError(f"expected exactly one dist-info METADATA file, found {len(candidates)}")
    metadata = candidates[0] / "METADATA"
    parsed = Parser().parsestr(metadata.read_text(encoding="utf-8"))
    name = parsed.get("Name")
    version = parsed.get("Version")
    if not name or not version:
        raise ValueError(f"{metadata} must contain Name and Version")
    return candidates[0], {"Name": name, "Version": version}


def _wheel_name_from_archive(dist_info: Path, metadata: dict[str, str]) -> WheelName:
    wheel_metadata = dist_info / "WHEEL"
    parsed = Parser().parsestr(wheel_metadata.read_text(encoding="utf-8"))
    tags = parsed.get_all("Tag") or []
    if not tags:
        raise ValueError(f"{wheel_metadata} must contain at least one Tag")
    return WheelName(
        distribution=_wheel_distribution_name(metadata["Name"]),
        version=metadata["Version"],
        suffix=".".join(tags),
    )


def _with_vaso_version(upstream_version: str, line: str, git_sha: str) -> str:
    sha8 = (git_sha or "00000000")[:8]
    public_version = upstream_version.split("+", 1)[0]
    return f"{public_version}+vaso.{line}.g{sha8}"


def _rewrite_metadata_version(path: Path, version: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    replaced = False
    out: list[str] = []
    for line in lines:
        if line.startswith("Version: ") and not replaced:
            out.append(f"Version: {version}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"Version: {version}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _rename_dist_info(dist_info: Path, upstream_version: str, release_version: str) -> Path:
    new_name = dist_info.name.replace(f"-{upstream_version}.dist-info", f"-{release_version}.dist-info")
    if new_name == dist_info.name:
        stem = dist_info.name.removesuffix(".dist-info")
        distribution = stem.rsplit("-", 1)[0]
        new_name = f"{distribution}-{release_version}.dist-info"
    target = dist_info.with_name(new_name)
    if target != dist_info:
        if target.exists():
            shutil.rmtree(target)
        dist_info.rename(target)
    return target


def _normalize_torch_aten_config(root: Path) -> None:
    path = root / "torch" / "share" / "cmake" / "ATen" / "ATenConfig.cmake"
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    normalized = re.sub(
        r'set\(ATEN_INCLUDE_DIR\s+"[^"]*/torch/include"\)',
        'set(ATEN_INCLUDE_DIR "${CMAKE_CURRENT_LIST_DIR}/../../../include")',
        text,
    )
    if normalized != text:
        path.write_text(normalized, encoding="utf-8")


def _forbidden_build_path_needles() -> tuple[bytes, ...]:
    needles = list(_FORBIDDEN_STATIC_NEEDLES) + list(_FORBIDDEN_PATH_MARKERS)
    for name in ("VASO_ESTATE_ROOT", "VASO_BAZEL_OB"):
        value = os.environ.get(name)
        if value:
            path = value.rstrip("/")
            if path:
                needles.append(f"{path}/".encode("utf-8"))
    return tuple(dict.fromkeys(needles))


def _drop_prefix_map_flags(data: bytes) -> bytes:
    if b"-fprefix-map=" not in data and b"-ffile-prefix-map=" not in data and b"-fdebug-prefix-map=" not in data and b"-fmacro-prefix-map=" not in data:
        return data
    if b"\0" in data:
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    rewritten = _PREFIX_MAP_FLAG.sub("", text)
    rewritten = re.sub(r"[ \t]{2,}", " ", rewritten)
    return rewritten.encode("utf-8")


def _build_path_spans(data: bytes) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for needle in _forbidden_build_path_needles():
        if not needle:
            continue
        start = 0
        while True:
            index = data.find(needle, start)
            if index < 0:
                break
            left = index
            while left > 0 and data[left - 1] not in _BUILD_PATH_TOKEN_BOUNDARIES:
                left -= 1
            right = index + len(needle)
            while right < len(data) and data[right] not in _BUILD_PATH_TOKEN_BOUNDARIES:
                right += 1
            spans.append((left, right))
            start = index + len(needle)
    if not spans:
        return []
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _scrub_build_path_bytes(data: bytes) -> bytes:
    spans = _build_path_spans(data)
    if not spans:
        return data
    rewritten = bytearray(data)
    for start, end in spans:
        rewritten[start:end] = b"_" * (end - start)
    return bytes(rewritten)


def _scrub_build_paths(root: Path) -> None:
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        if _is_elf(path):
            continue
        original = path.read_bytes()
        rewritten = _drop_prefix_map_flags(original)
        rewritten = _scrub_build_path_bytes(rewritten)
        if rewritten != original:
            path.write_bytes(rewritten)


def _iter_elfs(root: Path) -> list[ElfFile]:
    return [
        ElfFile(path.relative_to(root), path)
        for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file())
        if _is_elf(path)
    ]


def _wheel_basenames(root: Path) -> set[str]:
    return {path.name for path in root.rglob("*") if path.is_file()}


def _relative_runpath(from_dir: PurePosixPath, target_dir: PurePosixPath) -> str:
    rel = os.path.relpath(target_dir.as_posix(), start=from_dir.as_posix() or ".")
    return "$ORIGIN" if rel == "." else "$ORIGIN/" + rel


def _normalized_runpath_entries(
    elf: ElfFile,
    dynamic: DynamicInfo,
    libs_dir: PurePosixPath | None,
    rootfs_dirs: tuple[str, ...],
) -> list[str]:
    entries: list[str] = []
    for existing in dynamic.rpaths:
        if _is_jax_nvidia_origin(existing):
            continue
        if _is_origin_relative(existing) or is_under_rootfs_dir(existing, rootfs_dirs):
            entries.append(existing)
    if libs_dir is not None:
        entries.append(_relative_runpath(elf.rel.parent, libs_dir))
    entries.extend(rootfs_dirs)

    deduped: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if entry and entry not in seen:
            seen.add(entry)
            deduped.append(entry)
    return deduped


def _needed_basename(needed: str) -> str:
    return PurePosixPath(needed).name


def _copy_vendored_library(source: Path, destination: Path) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        return False
    shutil.copy2(source, destination)
    destination.chmod(destination.stat().st_mode | stat.S_IWUSR)
    return True


def _repair_dynamic_entries(
    root: Path,
    package_libs_dir: PurePosixPath,
    rootfs_dirs: tuple[str, ...],
    provider_prefixes: list[Path],
    patchelf: str,
    readelf: str,
    *,
    include_rootfs_runpaths: bool = True,
) -> None:
    provider_lib_dirs = _provider_lib_dirs(provider_prefixes)
    vendored_any = False
    replacement_needed: dict[Path, list[tuple[str, str]]] = {}

    changed = True
    while changed:
        changed = False
        basenames = _wheel_basenames(root)
        for elf in _iter_elfs(root):
            dynamic = _dynamic_info(elf.path, patchelf=patchelf, readelf=readelf)
            for needed in dynamic.needed:
                basename = _needed_basename(needed)
                if needed.startswith("/") and not is_under_rootfs_dir(needed, rootfs_dirs):
                    replacements = replacement_needed.setdefault(elf.path, [])
                    replacement = (needed, basename)
                    if replacement not in replacements:
                        replacements.append(replacement)
                if basename in basenames or _is_platform_library(basename):
                    continue
                if _is_cuda_family(basename):
                    continue
                if needed.startswith("/") and Path(needed).is_file():
                    source = Path(needed)
                else:
                    source = _find_provider_library(basename, provider_lib_dirs)
                if source is None:
                    continue
                destination = root / package_libs_dir.as_posix() / basename
                if _copy_vendored_library(source, destination):
                    vendored_any = True
                    changed = True

    for elf in _iter_elfs(root):
        for old, new in replacement_needed.get(elf.path, []):
            _run_patchelf(patchelf, "--replace-needed", old, new, str(elf.path))

    libs_dir = package_libs_dir if vendored_any else None
    runpath_rootfs_dirs = rootfs_dirs if include_rootfs_runpaths else ()
    for elf in _iter_elfs(root):
        dynamic = _dynamic_info(elf.path, patchelf=patchelf, readelf=readelf)
        runpath_entries = _normalized_runpath_entries(elf, dynamic, libs_dir, runpath_rootfs_dirs)
        if runpath_entries:
            _run_patchelf(patchelf, "--set-rpath", ":".join(runpath_entries), str(elf.path))
        else:
            _run_patchelf(patchelf, "--remove-rpath", str(elf.path))


def repair_prefix_runtime_closure(
    prefix: Path,
    *,
    package_libs_dir: PurePosixPath,
    lock_path: Path,
    line: str,
    provider_prefixes: Iterable[Path] = (),
    provider_metadata: Path | None = None,
    include_rootfs_runpaths: bool = False,
    patchelf: str = "patchelf",
    readelf: str = "readelf",
) -> None:
    """Vendor provider DSOs into a prefix and rewrite ELF runpaths.

    This is the dynamic-linker repair used by release wheel construction, factored
    so the public torch artifact can carry the same provider closure without
    rebuilding the native torch action. Artifact consumers keep rootfs/driver
    libraries external, so callers normally leave include_rootfs_runpaths false.
    """

    lock = load_rootfs_lock(lock_path)
    rootfs_dirs = rootfs_library_dirs(lock, line)
    all_provider_prefixes = list(provider_prefixes)
    if provider_metadata is not None:
        provider_doc = _load_provider_metadata(provider_metadata)
        all_provider_prefixes.extend(
            _provider_prefixes_from_metadata(
                provider_doc,
                relative_to=_provider_prefix_base(provider_metadata),
            )
        )

    _repair_dynamic_entries(
        prefix,
        package_libs_dir,
        rootfs_dirs,
        all_provider_prefixes,
        patchelf,
        readelf,
        include_rootfs_runpaths=include_rootfs_runpaths,
    )
    _scrub_build_paths(prefix)


def _wheel_timestamp() -> tuple[int, int, int, int, int, int]:
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0") or "0")
    epoch = max(epoch, 315532800)
    return time.gmtime(epoch)[:6]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _normalize_sha256(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        return value
    if re.fullmatch(r"[0-9a-f]{64}", value):
        return "sha256:" + value
    return value


def _find_digest(doc: Any) -> str | None:
    if not isinstance(doc, dict):
        return None
    for key in ("rootfs_bundle_digest", "bundle_digest", "digest", "sha256"):
        digest = _normalize_sha256(doc.get(key))
        if digest:
            return digest
    for key in ("rootfs_bundle", "bundle", "archive", "tarball"):
        digest = _find_digest(doc.get(key))
        if digest:
            return digest
    return None


def _rootfs_bundle_digest(provider_doc: dict[str, Any]) -> str | None:
    manifest = provider_doc.get("rootfs_manifest")
    if not isinstance(manifest, str) or not manifest:
        return None
    manifest_path = Path(manifest)
    if not manifest_path.is_file():
        return None
    return _find_digest(json.loads(manifest_path.read_text(encoding="utf-8")))


def _split_arch_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part for part in re.split(r"[;,\s]+", value) if part]
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            out.extend(_split_arch_values(item))
        return out
    return []


def _normalized_cuda_architecture(value: str) -> str | None:
    raw = value.strip()
    wants_ptx = raw.upper().endswith("+PTX")
    base = raw[:-4] if wants_ptx else raw
    lowered = base.lower()
    if lowered.startswith("sm_"):
        suffix = lowered.removeprefix("sm_")
    elif lowered.startswith("compute_"):
        suffix = lowered.removeprefix("compute_")
    else:
        match = re.fullmatch(r"([0-9]+)\.([0-9]+)([a-z]?)", lowered)
        if match:
            suffix = f"{match.group(1)}{match.group(2)}{match.group(3)}"
        elif re.fullmatch(r"[0-9]{2,3}[a-z]?", lowered):
            suffix = lowered
        else:
            return None
    normalized = f"sm_{suffix}"
    return normalized + "+PTX" if wants_ptx else normalized


def _normalize_cuda_architectures(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalized_cuda_architecture(value)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def _declared_cuda_architectures(provider_doc: dict[str, Any]) -> list[str]:
    values = _split_arch_values(provider_doc.get("declared_cuda_architectures"))
    if values:
        return _normalize_cuda_architectures(values)

    values = _split_arch_values(provider_doc.get("torch_cuda_arch_list"))
    if values:
        return _normalize_cuda_architectures(values)

    cuda = provider_doc.get("cuda")
    if isinstance(cuda, dict):
        values = _split_arch_values(cuda.get("compute_capabilities"))
        if values:
            return _normalize_cuda_architectures(values)

    package = provider_doc.get("package")
    if isinstance(package, str):
        values = _split_arch_values(_CURRENT_DEFAULT_DECLARED_ARCHITECTURES.get(package))
        if values:
            return _normalize_cuda_architectures(values)

    return []


def _write_vaso_provenance(
    path: Path,
    *,
    provider_doc: dict[str, Any],
    git_sha: str,
    profile: str,
    line: str,
    lock_path: Path,
    spack_lock_path: Path,
    upstream_version: str,
) -> None:
    action_input_digest = provider_doc.get("action_input_digest")
    if not isinstance(action_input_digest, str) or not action_input_digest:
        raise ValueError("provider metadata is missing action_input_digest")
    provenance = {
        "schema_version": 1,
        "action_input_digest": action_input_digest,
        "declared_cuda_architectures": _declared_cuda_architectures(provider_doc),
        "git_sha": git_sha,
        "line": line,
        "profile": profile,
        "provider_package": provider_doc.get("package"),
        "rootfs_bundle_digest": _rootfs_bundle_digest(provider_doc),
        "rootfs_lock_sha256": _file_sha256(lock_path),
        "spack_lock_sha256": _file_sha256(spack_lock_path),
        "upstream_version": upstream_version,
    }
    path.write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _record_hash(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode("ascii").rstrip("=")
    return f"sha256={digest}"


def _write_record(root: Path, record_rel: PurePosixPath) -> None:
    record_path = root / record_rel.as_posix()
    rows: list[list[str]] = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        rel = PurePosixPath(path.relative_to(root).as_posix())
        if rel == record_rel:
            continue
        data = path.read_bytes()
        rows.append([rel.as_posix(), _record_hash(data), str(len(data))])
    rows.append([record_rel.as_posix(), "", ""])
    with record_path.open("w", encoding="utf-8", newline="") as fh:
        csv.writer(fh, lineterminator="\n").writerows(rows)


def _write_wheel(root: Path, output: Path) -> None:
    timestamp = _wheel_timestamp()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
            rel = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(rel, date_time=timestamp)
            mode = path.stat().st_mode & 0o777
            info.external_attr = (mode or 0o644) << 16
            zf.writestr(info, path.read_bytes())


def repair_wheel(
    wheel: Path,
    *,
    out_dir: Path,
    lock_path: Path,
    spack_lock_path: Path,
    profile: str,
    line: str,
    git_sha: str,
    provider_prefixes: Iterable[Path] = (),
    provider_metadata: Path | None = None,
    patchelf: str = "patchelf",
    readelf: str = "readelf",
) -> Path:
    lock = load_rootfs_lock(lock_path)
    rootfs_dirs = rootfs_library_dirs(lock, line)
    provider_doc = _load_provider_metadata(provider_metadata)
    all_provider_prefixes = list(provider_prefixes) + _provider_prefixes_from_metadata(
        provider_doc,
        relative_to=_provider_prefix_base(provider_metadata),
    )
    with tempfile.TemporaryDirectory(dir=_temp_parent()) as temp:
        unpacked = Path(temp) / "wheel"
        unpacked.mkdir()
        _safe_extract(wheel, unpacked)
        dist_info, metadata = _metadata_for(unpacked)
        try:
            name = WheelName.parse(wheel)
        except ValueError:
            name = _wheel_name_from_archive(dist_info, metadata)
        package_name = re.sub(r"[-.]+", "_", metadata["Name"]).lower()
        release_version = _with_vaso_version(metadata["Version"], line, git_sha)
        _rewrite_metadata_version(dist_info / "METADATA", release_version)
        dist_info = _rename_dist_info(dist_info, metadata["Version"], release_version)

        _repair_dynamic_entries(
            unpacked,
            PurePosixPath(f"{package_name}.libs"),
            rootfs_dirs,
            all_provider_prefixes,
            patchelf,
            readelf,
        )
        _normalize_torch_aten_config(unpacked)
        _scrub_build_paths(unpacked)

        _write_vaso_provenance(
            dist_info / "vaso_provenance.json",
            provider_doc=provider_doc,
            git_sha=git_sha,
            profile=profile,
            line=line,
            lock_path=lock_path,
            spack_lock_path=spack_lock_path,
            upstream_version=metadata["Version"],
        )
        record_rel = PurePosixPath(dist_info.relative_to(unpacked).as_posix()) / "RECORD"
        _write_record(unpacked, record_rel)
        output = out_dir / name.with_version(release_version)
        _write_wheel(unpacked, output)
    return output


def repair_wheelhouse(
    wheelhouse: Path,
    *,
    out_dir: Path,
    lock_path: Path,
    spack_lock_path: Path,
    profile: str,
    line: str,
    git_sha: str,
    provider_prefixes: Iterable[Path] = (),
    provider_metadata: Path | None = None,
    patchelf: str = "patchelf",
    readelf: str = "readelf",
) -> list[Path]:
    wheels = sorted(wheelhouse.glob("*.whl"))
    if not wheels:
        raise ValueError(f"wheelhouse contains no wheels: {wheelhouse}")
    return [
        repair_wheel(
            wheel,
            out_dir=out_dir,
            lock_path=lock_path,
            spack_lock_path=spack_lock_path,
            profile=profile,
            line=line,
            git_sha=git_sha,
            provider_prefixes=provider_prefixes,
            provider_metadata=provider_metadata,
            patchelf=patchelf,
            readelf=readelf,
        )
        for wheel in wheels
    ]


def _write_manifest(path: Path, wheels: Iterable[Path]) -> None:
    entries = []
    for wheel in sorted(wheels):
        data = wheel.read_bytes()
        entries.append(
            {
                "name": wheel.name,
                "path": str(wheel),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "wheels": entries}, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, action="append", default=[])
    parser.add_argument("--wheelhouse", type=Path, action="append", default=[])
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--manifest-out", type=Path)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--spack-lock", type=Path, required=True)
    parser.add_argument("--profile", required=True, choices=("torch", "jax"))
    parser.add_argument("--line", required=True, choices=("cu129", "cu130"))
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--provider-prefix", type=Path, action="append", default=[])
    parser.add_argument("--provider-metadata", type=Path, required=True)
    parser.add_argument("--patchelf", default=os.environ.get("PATCHELF", "patchelf"))
    parser.add_argument("--readelf", default=os.environ.get("READELF", "readelf"))
    args = parser.parse_args(argv)

    if not args.wheel and not args.wheelhouse:
        parser.error("at least one --wheel or --wheelhouse is required")

    if args.out_dir.exists():
        shutil.rmtree(args.out_dir)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    repaired: list[Path] = []
    for wheel in args.wheel:
        repaired.append(
            repair_wheel(
                wheel,
                out_dir=args.out_dir,
                lock_path=args.lock,
                spack_lock_path=args.spack_lock,
                profile=args.profile,
                line=args.line,
                git_sha=args.git_sha,
                provider_prefixes=args.provider_prefix,
                provider_metadata=args.provider_metadata,
                patchelf=args.patchelf,
                readelf=args.readelf,
            )
        )
    for wheelhouse in args.wheelhouse:
        repaired.extend(
            repair_wheelhouse(
                wheelhouse,
                out_dir=args.out_dir,
                lock_path=args.lock,
                spack_lock_path=args.spack_lock,
                profile=args.profile,
                line=args.line,
                git_sha=args.git_sha,
                provider_prefixes=args.provider_prefix,
                provider_metadata=args.provider_metadata,
                patchelf=args.patchelf,
                readelf=args.readelf,
            )
        )

    if args.manifest_out is not None:
        _write_manifest(args.manifest_out, repaired)
    for wheel in sorted(repaired):
        print(wheel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
