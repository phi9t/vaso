#!/usr/bin/env python3
"""Validate CUDA cubin/PTX coverage in Vaso release wheels."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


_ELF_MAGIC = b"\x7fELF"
_SHARED_OBJECT = re.compile(r"\.so(?:\.|$)")
_SM_TOKEN = re.compile(r"\bsm_([0-9]{2,3}[a-z]?)\b", re.IGNORECASE)
_COMPUTE_TOKEN = re.compile(r"\bcompute_([0-9]{2,3}[a-z]?)\b", re.IGNORECASE)
_PTX_SM_FILE_TOKEN = re.compile(r"\.sm_([0-9]{2,3}[a-z]?)\.ptx\b", re.IGNORECASE)
DEFAULT_MISSING_PTX_ALLOWLIST = Path(__file__).with_name("cubin_guard_missing_ptx_allowlist.json")
RULE_IDS = frozenset(
    (
        "missing-cubin",
        "missing-ptx",
        "missing-declared-architectures",
    )
)


@dataclass(frozen=True)
class ArchRequirement:
    raw: str
    cubin: str | None
    ptx: str | None


@dataclass(frozen=True)
class CudaObject:
    logical_path: str
    cubins: frozenset[str]
    ptx: frozenset[str]


@dataclass(frozen=True)
class Finding:
    wheel: Path
    logical_path: str
    rule_id: str
    message: str


@dataclass(frozen=True)
class AllowlistedFinding:
    wheel: Path
    logical_path: str
    message: str
    reason: str


@dataclass(frozen=True)
class MissingPtxAllowlistEntry:
    path_pattern: str
    reason: str


@dataclass(frozen=True)
class WheelReport:
    wheel: Path
    declared: tuple[ArchRequirement, ...]
    cuda_objects: tuple[CudaObject, ...]
    findings: tuple[Finding, ...]
    warnings: tuple[AllowlistedFinding, ...]
    declaration_source: str | None


def _temp_parent() -> Path:
    for name in ("TEST_TMPDIR", "TMPDIR"):
        value = os.environ.get(name)
        if value:
            return Path(value)
    return Path.cwd()


def _resolve_path(raw: str | Path) -> Path:
    candidate = Path(raw)
    if candidate.exists():
        return candidate
    for env_name in ("RUNFILES_DIR", "TEST_SRCDIR"):
        root = os.environ.get(env_name)
        if not root:
            continue
        for prefix in ("_main", "__main__", ""):
            path = Path(root) / prefix / str(raw) if prefix else Path(root) / str(raw)
            if path.exists():
                return path
    return candidate


def _cuda_prefix_from_file(path: Path) -> Path:
    resolved = _resolve_path(path)
    if not resolved.is_file():
        raise ValueError(f"CUDA prefix file is missing: {path}")
    prefix = Path(resolved.read_text(encoding="utf-8").strip())
    if not prefix.is_dir():
        raise ValueError(f"CUDA prefix from {resolved} is missing: {prefix}")
    return prefix


def _cuobjdump_from_prefix_file(path: Path) -> Path:
    prefix = _cuda_prefix_from_file(path)
    cuobjdump = prefix / "bin" / "cuobjdump"
    if not cuobjdump.is_file():
        raise ValueError(f"CUDA prefix from {path} lacks bin/cuobjdump: {cuobjdump}")
    return cuobjdump


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


def _is_elf(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(4) == _ELF_MAGIC
    except OSError:
        return False


def _is_shared_object(path: Path) -> bool:
    return _SHARED_OBJECT.search(path.name) is not None


def _arch_suffix(value: str) -> str | None:
    value = value.strip().lower()
    if value.startswith("sm_"):
        return value.removeprefix("sm_")
    if value.startswith("compute_"):
        return value.removeprefix("compute_")
    decimal = re.fullmatch(r"([0-9]+)\.([0-9]+)([a-z]?)", value)
    if decimal:
        return f"{decimal.group(1)}{decimal.group(2)}{decimal.group(3)}"
    bare = re.fullmatch(r"[0-9]{2,3}[a-z]?", value)
    if bare:
        return value
    return None


def _split_arch_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part for part in re.split(r"[;,\s]+", value) if part]
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, dict)):
        out: list[str] = []
        for item in value:
            out.extend(_split_arch_values(item))
        return out
    return []


def _parse_arch_requirement(raw: str) -> ArchRequirement:
    value = raw.strip()
    wants_ptx = value.upper().endswith("+PTX")
    base = value[:-4] if wants_ptx else value
    suffix = _arch_suffix(base)
    if suffix is None:
        raise ValueError(f"unsupported CUDA architecture declaration: {raw!r}")

    if base.lower().startswith("compute_"):
        return ArchRequirement(raw=raw, cubin=None, ptx=f"compute_{suffix}")
    return ArchRequirement(
        raw=raw,
        cubin=f"sm_{suffix}",
        ptx=f"compute_{suffix}" if wants_ptx else None,
    )


def _normalize_arch_declarations(values: Iterable[str]) -> tuple[ArchRequirement, ...]:
    out: list[ArchRequirement] = []
    seen: set[tuple[str | None, str | None]] = set()
    for raw in values:
        requirement = _parse_arch_requirement(raw)
        key = (requirement.cubin, requirement.ptx)
        if key in seen:
            continue
        seen.add(key)
        out.append(requirement)
    return tuple(out)


def _declared_arch_values(doc: dict[str, Any]) -> tuple[list[str], str | None]:
    for key in ("declared_cuda_architectures", "cuda_architectures"):
        values = _split_arch_values(doc.get(key))
        if values:
            return values, key

    values = _split_arch_values(doc.get("torch_cuda_arch_list"))
    if values:
        return values, "torch_cuda_arch_list"

    values = _split_arch_values(doc.get("cuda_arch"))
    if values:
        return values, "cuda_arch"

    cuda = doc.get("cuda")
    if isinstance(cuda, dict):
        for key in ("compute_capabilities", "cuda_compute_capabilities", "architectures"):
            values = _split_arch_values(cuda.get(key))
            if values:
                return values, f"cuda.{key}"

    return [], None


def _declared_arches_from_doc(doc: dict[str, Any]) -> tuple[tuple[ArchRequirement, ...], str | None]:
    values, source = _declared_arch_values(doc)
    return _normalize_arch_declarations(values), source


def _read_provenance(wheel: Path) -> tuple[dict[str, Any] | None, str | None]:
    with zipfile.ZipFile(wheel) as zf:
        names = [name for name in zf.namelist() if name.endswith(".dist-info/vaso_provenance.json")]
        if not names:
            return None, None
        if len(names) > 1:
            raise ValueError(f"{wheel} contains multiple vaso_provenance.json files")
        data = json.loads(zf.read(names[0]).decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{wheel}!{names[0]} must contain a JSON object")
        return data, names[0]


def _declared_arches_for_wheel(wheel: Path, build_plans: Iterable[Path]) -> tuple[tuple[ArchRequirement, ...], str | None]:
    provenance, provenance_path = _read_provenance(wheel)
    if provenance is not None:
        declared, source = _declared_arches_from_doc(provenance)
        if declared:
            return declared, f"{provenance_path}:{source}"

    for plan in build_plans:
        doc = json.loads(plan.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            raise ValueError(f"build plan must contain a JSON object: {plan}")
        declared, source = _declared_arches_from_doc(doc)
        if declared:
            return declared, f"{plan}:{source}"

    if provenance_path is not None:
        return (), provenance_path
    return (), None


def _run_cuobjdump(cuobjdump: Path, flag: str, path: Path) -> str:
    result = subprocess.run(
        [str(cuobjdump), flag, str(path)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout + "\n" + result.stderr


def _tokens(pattern: re.Pattern[str], text: str, prefix: str) -> frozenset[str]:
    return frozenset(f"{prefix}_{match.group(1).lower()}" for match in pattern.finditer(text))


def _ptx_tokens(text: str) -> frozenset[str]:
    tokens = set(_tokens(_COMPUTE_TOKEN, text, "compute"))
    tokens.update(f"compute_{match.group(1).lower()}" for match in _PTX_SM_FILE_TOKEN.finditer(text))
    return frozenset(tokens)


def _inspect_cuda_object(cuobjdump: Path, path: Path, logical_path: str) -> CudaObject | None:
    elf_listing = _run_cuobjdump(cuobjdump, "--list-elf", path)
    ptx_listing = _run_cuobjdump(cuobjdump, "--list-ptx", path)
    cubins = _tokens(_SM_TOKEN, elf_listing, "sm")
    ptx = _ptx_tokens(ptx_listing)
    if not cubins and not ptx:
        return None
    return CudaObject(logical_path=logical_path, cubins=cubins, ptx=ptx)


def _load_missing_ptx_allowlist(path: Path) -> tuple[MissingPtxAllowlistEntry, ...]:
    resolved = _resolve_path(path)
    if not resolved.is_file():
        raise ValueError(f"missing PTX allowlist file is missing: {path}")
    doc = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(doc, list):
        raise ValueError(f"missing PTX allowlist must contain a JSON array: {path}")

    entries: list[MissingPtxAllowlistEntry] = []
    for index, item in enumerate(doc, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"missing PTX allowlist entry {index} must be a JSON object: {path}")
        pattern = item.get("path_pattern")
        reason = item.get("reason")
        if not isinstance(pattern, str) or not pattern.strip():
            raise ValueError(f"missing PTX allowlist entry {index} lacks path_pattern: {path}")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"missing PTX allowlist entry {index} lacks reason: {path}")
        entries.append(MissingPtxAllowlistEntry(path_pattern=pattern.strip(), reason=reason.strip()))
    return tuple(entries)


def _wheel_member_path(logical_path: str) -> str:
    if "!" not in logical_path:
        return logical_path
    return logical_path.split("!", 1)[1]


def _matching_missing_ptx_allowlist(
    obj: CudaObject,
    entries: tuple[MissingPtxAllowlistEntry, ...],
) -> MissingPtxAllowlistEntry | None:
    member_path = _wheel_member_path(obj.logical_path)
    for entry in entries:
        if fnmatch.fnmatchcase(member_path, entry.path_pattern) or fnmatch.fnmatchcase(obj.logical_path, entry.path_pattern):
            return entry
    return None


def _check_cuda_object(
    wheel: Path,
    obj: CudaObject,
    declared: tuple[ArchRequirement, ...],
    missing_ptx_allowlist: tuple[MissingPtxAllowlistEntry, ...],
) -> tuple[list[Finding], list[AllowlistedFinding]]:
    if not declared:
        return (
            [
                Finding(
                    wheel=wheel,
                    logical_path=obj.logical_path,
                    rule_id="missing-declared-architectures",
                    message="carries CUDA device code but no declared_cuda_architectures entry was found",
                )
            ],
            [],
        )

    findings: list[Finding] = []
    warnings: list[AllowlistedFinding] = []
    for requirement in declared:
        if requirement.cubin is not None and requirement.cubin not in obj.cubins:
            findings.append(
                Finding(
                    wheel=wheel,
                    logical_path=obj.logical_path,
                    rule_id="missing-cubin",
                    message=f"missing cubin for {requirement.cubin}",
                )
            )
        if requirement.ptx is not None and requirement.ptx not in obj.ptx:
            finding = Finding(
                wheel=wheel,
                logical_path=obj.logical_path,
                rule_id="missing-ptx",
                message=f"missing PTX for {requirement.ptx}",
            )
            entry = _matching_missing_ptx_allowlist(obj, missing_ptx_allowlist)
            if entry is None:
                findings.append(finding)
            else:
                warnings.append(
                    AllowlistedFinding(
                        wheel=wheel,
                        logical_path=obj.logical_path,
                        message=finding.message,
                        reason=entry.reason,
                    )
                )
    return findings, warnings


def check_wheel(
    wheel: Path,
    *,
    cuobjdump: Path,
    build_plans: Iterable[Path] = (),
    missing_ptx_allowlist: Iterable[MissingPtxAllowlistEntry] = (),
) -> WheelReport:
    allowlist = tuple(missing_ptx_allowlist)
    wheel = wheel.resolve()
    declared, source = _declared_arches_for_wheel(wheel, build_plans)
    with tempfile.TemporaryDirectory(prefix="cubin-guard-", dir=_temp_parent()) as temp:
        root = Path(temp) / "wheel"
        root.mkdir()
        _safe_extract(wheel, root)
        cuda_objects: list[CudaObject] = []
        findings: list[Finding] = []
        warnings: list[AllowlistedFinding] = []
        for candidate in sorted(path for path in root.rglob("*") if path.is_file()):
            if not _is_shared_object(candidate) or not _is_elf(candidate):
                continue
            rel = candidate.relative_to(root).as_posix()
            obj = _inspect_cuda_object(cuobjdump, candidate, f"{wheel}!{rel}")
            if obj is None:
                continue
            cuda_objects.append(obj)
            object_findings, object_warnings = _check_cuda_object(wheel, obj, declared, allowlist)
            findings.extend(object_findings)
            warnings.extend(object_warnings)
    return WheelReport(
        wheel=wheel,
        declared=declared,
        cuda_objects=tuple(cuda_objects),
        findings=tuple(findings),
        warnings=tuple(warnings),
        declaration_source=source,
    )


def _format_declared(declared: tuple[ArchRequirement, ...]) -> str:
    values: list[str] = []
    for requirement in declared:
        if requirement.cubin is not None:
            value = requirement.cubin
            if requirement.ptx is not None:
                value += "+PTX"
            values.append(value)
        elif requirement.ptx is not None:
            values.append(requirement.ptx)
    return ",".join(values) if values else "<none>"


def _format_allowlisted_warning(warning: AllowlistedFinding) -> str:
    return (
        f"warning: {warning.logical_path}: {warning.message}; "
        f"allowlisted: {warning.reason}"
    )


def _format_finding(finding: Finding) -> str:
    return f"{finding.logical_path}: {finding.message}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cuda-prefix-file", type=Path, required=True, help="Bazel-provided CUDA prefix_path.txt for this line")
    parser.add_argument(
        "--missing-ptx-allowlist",
        type=Path,
        default=DEFAULT_MISSING_PTX_ALLOWLIST,
        help="JSON allowlist for CUDA objects that are allowed to lack PTX",
    )
    parser.add_argument("--build-plan", type=Path, action="append", default=[], help="Optional build-plan JSON fallback")
    parser.add_argument("wheel", type=Path, nargs="+")
    args = parser.parse_args(argv)

    try:
        cuobjdump = _cuobjdump_from_prefix_file(args.cuda_prefix_file)
        missing_ptx_allowlist = _load_missing_ptx_allowlist(args.missing_ptx_allowlist)
        reports = [
            check_wheel(
                wheel,
                cuobjdump=cuobjdump,
                build_plans=args.build_plan,
                missing_ptx_allowlist=missing_ptx_allowlist,
            )
            for wheel in args.wheel
        ]
    except (OSError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        print(f"cubin guard error: {exc}", file=sys.stderr)
        return 2

    warnings = [warning for report in reports for warning in report.warnings]
    for warning in warnings:
        print(_format_allowlisted_warning(warning), file=sys.stderr)

    findings = [finding for report in reports for finding in report.findings]
    if findings:
        for finding in findings:
            print(_format_finding(finding), file=sys.stderr)
        return 1

    objects = sum(len(report.cuda_objects) for report in reports)
    arch_sets = sorted({_format_declared(report.declared) for report in reports})
    print(
        "cubin guard passed: "
        f"{len(reports)} wheel(s), {objects} CUDA shared object(s), "
        f"declared={';'.join(arch_sets)}, warnings={len(warnings)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
