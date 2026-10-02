#!/usr/bin/env python3
"""ABI-parity gate for a native package flip vs its Spack reference prefix.

A native package (Task 3+) must emit a **prefix-identical, ABI-identical**
install tree so unmigrated Spack consumers `depends_on` it unchanged. This tool
compares a *candidate* prefix (a native Bazel build's install tree) against the
*reference* prefix (the Spack install tree) along three axes and emits a
structured JSON verdict:

  1. layout        — the set of interesting install-tree paths (headers,
                     versioned/unversioned shared libs, static libs, pkgconfig)
                     must match, modulo an allowed-extra allowlist;
  2. soname/symbol — every reference shared lib must exist in the candidate with
                     the same SONAME and the same set of exported dynamic
                     symbols (`abidiff` when available, else `readelf -d`
                     SONAME + `nm -D` exported-symbol set);
  3. link-and-run  — an optional downstream consumer source is compiled and
                     linked against the candidate prefix AND the reference
                     prefix; both must build, run, and print identical stdout.

The gate is `ok` only when every requested axis passes. Designed to run inside
the sealed CUDA insula (all toolchains hermetic); binutils/gcc are located on
PATH, overridable via env (NM, READELF, ABIDIFF, CC).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Install-tree entries we consider part of the ABI-relevant contract. Anything
# else (docs, share/, spack metadata) is ignored for the layout diff.
_HEADER_SUFFIXES = (".h", ".hpp", ".hh", ".hxx", ".inc")
_LIB_DIRS = ("lib", "lib64")


def _tool(name: str, env_key: str) -> str:
    return os.environ.get(env_key) or shutil.which(name) or name


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _run_bytes(cmd: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=False, env=env)


def _json_text(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _rel_paths(
    prefix: Path,
    data_paths: list[str] | None = None,
    exec_paths: list[str] | None = None,
) -> set[str]:
    """ABI-relevant relative paths under a prefix.

    Headers under include/, every shared/static lib and its version symlinks
    under lib*/, and pkgconfig .pc files. Regular files and symlinks both count
    (a versioned soname chain is part of the contract).
    """
    out: set[str] = set()
    inc = prefix / "include"
    if inc.is_dir():
        for p in inc.rglob("*"):
            if p.is_file() or p.is_symlink():
                if p.suffix in _HEADER_SUFFIXES or "include" in p.parts:
                    out.add(str(p.relative_to(prefix)))
    for libdir in _LIB_DIRS:
        base = prefix / libdir
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.is_dir():
                continue
            name = p.name
            rel = str(p.relative_to(prefix))
            if ".so" in name or name.endswith(".a") or name.endswith(".pc"):
                out.add(rel)
    for rel in data_paths or []:
        p = prefix / rel
        if p.is_file() or p.is_symlink():
            out.add(rel)
    for rel in exec_paths or []:
        p = prefix / rel
        if p.is_file() or p.is_symlink():
            out.add(rel)
    return out


def _shared_libs(prefix: Path) -> list[Path]:
    """Resolved (non-symlink) shared objects under the prefix's lib dirs."""
    libs: list[Path] = []
    seen: set[str] = set()
    for libdir in _LIB_DIRS:
        base = prefix / libdir
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.is_symlink() or not p.is_file():
                continue
            if ".so" in p.name:
                real = str(p.resolve())
                if real not in seen:
                    seen.add(real)
                    libs.append(p)
    return sorted(libs, key=lambda x: x.name)


def _soname(readelf: str, lib: Path) -> str | None:
    out = _run([readelf, "-d", str(lib)])
    if out.returncode != 0:
        return None
    for line in out.stdout.splitlines():
        if "SONAME" in line and "Library soname:" in line:
            # e.g. "0x...(SONAME) Library soname: [libz.so.1]"
            return line.split("[", 1)[1].rstrip("]").strip()
    return None


def _dyn_symbols(nm: str, lib: Path) -> set[str] | None:
    """Exported (defined, global) dynamic symbols. Version tags stripped."""
    out = _run([nm, "-D", "--defined-only", str(lib)])
    if out.returncode != 0:
        # nm without --defined-only support: fall back and filter locally.
        out = _run([nm, "-D", str(lib)])
        if out.returncode != 0:
            return None
    syms: set[str] = set()
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        # "<addr> <type> <name>" or "<type> <name>" for undefined.
        typ = parts[-2] if len(parts) >= 3 else parts[0]
        name = parts[-1]
        # Only defined, exported code/data: T/t? we keep uppercase (global
        # defined). Absolute version nodes (A) and undefined (U) are excluded.
        if typ in ("T", "W", "D", "B", "R"):
            syms.add(name.split("@", 1)[0])
    return syms


def diff_layout(reference: Path, candidate: Path, allow_extra: list[str],
                data_paths: list[str], exec_paths: list[str]) -> dict:
    ref = _rel_paths(reference, data_paths, exec_paths)
    cand = _rel_paths(candidate, data_paths, exec_paths)
    missing = sorted(ref - cand)
    extra = sorted(p for p in (cand - ref) if not any(p.startswith(a) for a in allow_extra))
    return {
        "reference_count": len(ref),
        "candidate_count": len(cand),
        "missing_in_candidate": missing,
        "extra_in_candidate": extra,
        "ok": not missing and not extra,
    }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _static_archive_symbols(path: Path) -> dict:
    """Return a metadata-normalized static-archive ABI summary.

    `ar` archives can differ byte-for-byte because their member object files
    carry debug/build-path metadata. For ABI gates, the stable contract is the
    member set plus globally defined symbol set.
    """
    ar = _tool("ar", "AR")
    nm = _tool("nm", "NM")
    members = _run([ar, "t", str(path)])
    symbols = _run([nm, "-g", "--defined-only", str(path)])
    summary: dict = {
        "tool": "ar+nm",
        "members_returncode": members.returncode,
        "symbols_returncode": symbols.returncode,
        "members": sorted(members.stdout.splitlines()) if members.returncode == 0 else [],
        "symbols": [],
    }
    if symbols.returncode == 0:
        out = set()
        for line in symbols.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[-2] in ("T", "W", "D", "B", "R"):
                out.add(parts[-1])
        summary["symbols"] = sorted(out)
    return summary


def _static_archive_parity(reference: Path, candidate: Path) -> dict:
    ref = _static_archive_symbols(reference)
    cand = _static_archive_symbols(candidate)
    members_ok = ref["members_returncode"] == 0 and cand["members_returncode"] == 0 and ref["members"] == cand["members"]
    symbols_ok = ref["symbols_returncode"] == 0 and cand["symbols_returncode"] == 0 and ref["symbols"] == cand["symbols"]
    return {
        "reference_members": ref["members"],
        "candidate_members": cand["members"],
        "missing_symbols": sorted(set(ref["symbols"]) - set(cand["symbols"])),
        "added_symbols": sorted(set(cand["symbols"]) - set(ref["symbols"])),
        "reference_symbol_count": len(ref["symbols"]),
        "candidate_symbol_count": len(cand["symbols"]),
        "members_ok": members_ok,
        "symbols_ok": symbols_ok,
        "ok": members_ok and symbols_ok,
        "tool": "ar+nm",
    }


def _prefix_aliases(prefix: Path) -> set[str]:
    """Return path spellings that generated prefix metadata may print."""
    roots = {str(prefix), str(prefix.resolve())}
    prefix_markers = ("/bin", "/include", "/lib", "/lib64", "/share")
    pkgconfig_dirs = [prefix / libdir / "pkgconfig" for libdir in _LIB_DIRS]
    pkgconfig_dirs.append(prefix / "share" / "pkgconfig")
    for pkgconfig_dir in pkgconfig_dirs:
        for pc in pkgconfig_dir.glob("*.pc"):
            try:
                for line in pc.read_text(errors="replace").splitlines():
                    if "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip()
                    if key == "prefix" and value.startswith("/"):
                        roots.add(value)
                    elif value.startswith("/"):
                        for marker in prefix_markers:
                            if value.endswith(marker):
                                roots.add(value[: -len(marker)])
                                break
            except OSError:
                pass
    bindir = prefix / "bin"
    if bindir.is_dir():
        for script in bindir.iterdir():
            if not script.is_file():
                continue
            try:
                data = script.read_text(errors="replace")
            except OSError:
                continue
            for match in re.finditer(r'(?m)^prefix=(["\']?)([^"\'\n]+)\1$', data):
                roots.add(match.group(2))
    return {root for root in roots if root}


def _looks_like_prefix_alias(root: str, prefix: Path) -> bool:
    """Whether an embedded absolute path root is another spelling of prefix."""
    candidate = Path(root)
    names = {prefix.name, prefix.resolve().name}
    parent_names = {prefix.parent.name, prefix.resolve().parent.name}
    if candidate.name not in names:
        return False
    # Bazel external repos commonly end in ".../<repo>/prefix"; require the
    # repo marker to match so an unrelated "/.../prefix" path is not hidden.
    if candidate.name == "prefix" and candidate.parent.name not in parent_names:
        return False
    return True


def _embedded_prefix_aliases(data: str, prefix: Path) -> set[str]:
    """Find generated-script path literals that point inside this prefix."""
    roots: set[str] = set()
    for match in re.finditer(r"/[A-Za-z0-9_@+.:=~/-]+", data):
        token = match.group(0).rstrip(".,;:")
        for marker in ("/bin/", "/etc/", "/include/", "/lib/", "/lib64/", "/share/"):
            marker_dir = marker.rstrip("/")
            if marker in token:
                root = token.split(marker, 1)[0]
                if _looks_like_prefix_alias(root, prefix):
                    roots.add(root)
                break
            if token.endswith(marker_dir):
                root = token[: -len(marker_dir)]
                if _looks_like_prefix_alias(root, prefix):
                    roots.add(root)
                break
    return roots


def _normalized_metadata_sha256(
    path: Path,
    prefix: Path,
    extra_prefixes: list[Path] | None = None,
) -> str:
    data = path.read_text(errors="replace")
    for root in sorted(_prefix_aliases(prefix) | _embedded_prefix_aliases(data, prefix), key=len, reverse=True):
        data = data.replace(root, "${ABI_PARITY_PREFIX}")
    for idx, extra in enumerate(extra_prefixes or []):
        aliases = _prefix_aliases(extra) | _embedded_prefix_aliases(data, extra)
        for root in sorted(aliases, key=len, reverse=True):
            data = data.replace(root, "${ABI_PARITY_DEP_PREFIX_%d}" % idx)
    data = _normalize_python_venv_prompt_identity(data, prefix)
    data = _normalize_generated_command_spacing(_normalize_compiler_wrapper_identity(data))
    return hashlib.sha256(data.encode()).hexdigest()


def _normalize_compiler_wrapper_identity(data: str) -> str:
    """Treat Spack's compiler-wrapper path as the underlying compiler identity.

    Config scripts such as curl-config preserve the concrete compiler command
    observed by configure. A Bazel-native rule should not bake the hermetic
    Spack compiler-wrapper prefix into its emitted metadata, but matching the
    same underlying compiler (`gcc`) is acceptable for prefix/ABI parity.
    """
    return re.sub(
        r"(?<![A-Za-z0-9_./+-])(?:/[^'\" \t\n:]*)?/compiler-wrapper-[^'\" \t\n:]*/libexec/spack/gcc/(g(?:cc|\+\+))",
        r"\1",
        data,
    )


def _normalize_generated_command_spacing(data: str) -> str:
    """Normalize harmless whitespace drift in generated command metadata."""
    command_keys = ("COMPILE", "GENLIB")
    out = []
    for line in data.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        newline = line[len(body):]
        if any(body.startswith(key + "=") for key in command_keys):
            body = re.sub(r" {2,}(?=-(?:c|shared)\b)", " ", body)
        out.append(body + newline)
    return "".join(out)


def _normalize_python_venv_prompt_identity(data: str, prefix: Path) -> str:
    """Normalize prompt names generated from a Python venv's prefix basename."""
    if not (prefix / "pyvenv.cfg").is_file():
        return data
    name = re.escape(prefix.name)
    data = re.sub(
        rf"(?m)^(VIRTUAL_ENV_PROMPT=){name}$",
        r"\1${ABI_PARITY_VENV_NAME}",
        data,
    )
    data = re.sub(
        rf"(?m)^(setenv VIRTUAL_ENV_PROMPT ){name}$",
        r"\1${ABI_PARITY_VENV_NAME}",
        data,
    )
    data = re.sub(
        rf"(?m)^(set -gx VIRTUAL_ENV_PROMPT ){name}$",
        r"\1${ABI_PARITY_VENV_NAME}",
        data,
    )
    data = re.sub(
        rf'(?m)^(\s*printf "%s\(%s\)%s " \(set_color [^)]+\) ){name}( \(set_color normal\))$',
        r"\1${ABI_PARITY_VENV_NAME}\2",
        data,
    )
    return data.replace(
        '("' + prefix.name + '")',
        '("${ABI_PARITY_VENV_NAME}")',
    )


def _normalize_text_with_prefix_aliases(
    data: str,
    prefix: Path,
    extra_prefixes: list[Path] | None = None,
    aliases: dict[str, str] | None = None,
) -> str:
    for root in sorted(_prefix_aliases(prefix) | _embedded_prefix_aliases(data, prefix), key=len, reverse=True):
        data = data.replace(root, "${ABI_PARITY_PREFIX}")
    for idx, extra in enumerate(extra_prefixes or []):
        extra_aliases = _prefix_aliases(extra) | _embedded_prefix_aliases(data, extra)
        for root in sorted(extra_aliases, key=len, reverse=True):
            data = data.replace(root, "${ABI_PARITY_DEP_PREFIX_%d}" % idx)
    for value, replacement in sorted((aliases or {}).items(), key=lambda item: len(item[0]), reverse=True):
        if value:
            data = data.replace(value, replacement)
    data = _normalize_python_venv_prompt_identity(data, prefix)
    return _normalize_generated_command_spacing(_normalize_compiler_wrapper_identity(data))


def _is_prefix_normalized_text(rel: str) -> bool:
    path = Path(rel)
    return (
        path.suffix == ".pc"
        or path.name == "pyvenv.cfg"
        or path.suffix == ".pm"
        or path.name.endswith("-config")
        or (len(path.parts) >= 2 and path.parts[0] == "bin")
        or (
            len(path.parts) >= 3
            and path.parts[0] == "share"
            and path.parts[1] == "man"
        )
        or (
            len(path.parts) >= 3
            and path.parts[0] == "share"
            and path.parts[1] == "zsh"
            and path.parts[2] == "site-functions"
        )
        or (
            len(path.parts) >= 3
            and path.parts[0] == "etc"
            and path.parts[1] == "luarocks"
            and path.name.startswith("config-")
            and path.suffix == ".lua"
        )
        or (
            len(path.parts) == 2
            and path.parts[0] == "etc"
            and path.name in ("ssh_config", "sshd_config")
        )
        or (
            len(path.parts) == 3
            and path.parts[0] == "etc"
            and path.parts[1] == "fonts"
            and path.name == "fonts.conf"
        )
        or (
            len(path.parts) == 3
            and path.parts[0] == "lib"
            and path.parts[1] == "icu"
            and path.suffix == ".inc"
        )
    )


def _symlink_contract_target(path: Path, prefix: Path, extra_prefixes: list[Path] | None = None) -> str:
    """Return the install-tree symlink target after unwrapping runfiles hops."""
    target = os.readlink(path)
    target_path = Path(target)
    resolved_target = target_path if target_path.is_absolute() else path.parent / target_path
    seen = {path}
    while resolved_target.is_symlink() and resolved_target not in seen:
        seen.add(resolved_target)
        target = os.readlink(resolved_target)
        target_path = Path(target)
        resolved_target = target_path if target_path.is_absolute() else resolved_target.parent / target_path
    return _normalize_text_with_prefix_aliases(target, prefix, extra_prefixes)


def diff_data_files(
    reference: Path,
    candidate: Path,
    data_paths: list[str],
    reference_link_prefixes: list[Path] | None = None,
    candidate_link_prefixes: list[Path] | None = None,
) -> dict:
    """Byte-compare explicit data files that are part of a non-ELF prefix."""
    entries = []
    ok = True
    for rel in data_paths:
        ref = reference / rel
        cand = candidate / rel
        ref_exists = ref.exists() or ref.is_symlink()
        cand_exists = cand.exists() or cand.is_symlink()
        entry = {"path": rel, "reference_exists": ref_exists, "candidate_exists": cand_exists}
        if ref.is_symlink() and cand.is_symlink():
            ref_target = _symlink_contract_target(ref, reference, reference_link_prefixes)
            cand_target = _symlink_contract_target(cand, candidate, candidate_link_prefixes)
            entry.update({
                "kind": "symlink",
                "reference_target": ref_target,
                "candidate_target": cand_target,
                "ok": ref_target == cand_target,
            })
        elif ref.is_dir() and cand.is_dir():
            entry.update({
                "kind": "directory",
                "ok": True,
            })
        elif ref.is_file() and cand.is_file():
            ref_hash = _sha256(ref)
            cand_hash = _sha256(cand)
            entry.update({
                "kind": "file",
                "reference_sha256": ref_hash,
                "candidate_sha256": cand_hash,
                "ok": ref_hash == cand_hash,
            })
            if not entry["ok"] and _is_prefix_normalized_text(rel):
                ref_norm = _normalized_metadata_sha256(
                    ref,
                    reference,
                    reference_link_prefixes,
                )
                cand_norm = _normalized_metadata_sha256(
                    cand,
                    candidate,
                    candidate_link_prefixes,
                )
                entry["prefix_normalized"] = {
                    "reference_sha256": ref_norm,
                    "candidate_sha256": cand_norm,
                    "ok": ref_norm == cand_norm,
                }
                entry["ok"] = ref_norm == cand_norm
            elif not entry["ok"] and rel.endswith(".a"):
                entry["static_archive_abi"] = _static_archive_parity(ref, cand)
                entry["ok"] = entry["static_archive_abi"]["ok"]
        else:
            entry["ok"] = False
        ok = ok and entry["ok"]
        entries.append(entry)
    return {"files": entries, "ok": ok}


def _needed(readelf: str, exe: Path) -> list[str] | None:
    out = _run([readelf, "-d", str(exe)])
    if out.returncode != 0:
        return None
    needed = []
    for line in out.stdout.splitlines():
        if "NEEDED" in line and "Shared library:" in line:
            needed.append(line.split("[", 1)[1].rstrip("]").strip())
    return sorted(needed)


def diff_executables(reference: Path, candidate: Path, exec_paths: list[str]) -> dict:
    """Compare executable presence and dynamic dependency contracts."""
    readelf = _tool("readelf", "READELF")
    entries = []
    ok = True
    for rel in exec_paths:
        ref = reference / rel
        cand = candidate / rel
        entry = {
            "path": rel,
            "reference_exists": ref.exists(),
            "candidate_exists": cand.exists(),
        }
        if ref.is_file() and cand.is_file():
            ref_needed = _needed(readelf, ref)
            cand_needed = _needed(readelf, cand)
            entry["needed"] = {
                "reference": ref_needed,
                "candidate": cand_needed,
                "ok": ref_needed == cand_needed,
            }
            entry["ok"] = entry["needed"]["ok"]
        else:
            entry["ok"] = False
        ok = ok and entry["ok"]
        entries.append(entry)
    return {"files": entries, "ok": ok}


def diff_explicit_elfs(reference: Path, candidate: Path, elf_paths: list[str]) -> dict:
    """Compare ELF ABI for explicit paths outside the prefix's lib dirs.

    Python extension modules live under site-packages, not lib/lib64, so the
    normal shared-library scan intentionally misses them. These paths are part
    of the ABI surface for PythonPackage native flips.
    """
    readelf = _tool("readelf", "READELF")
    nm = _tool("nm", "NM")
    abidiff = os.environ.get("ABIDIFF") or shutil.which("abidiff")
    entries = []
    ok = True
    for rel in elf_paths:
        ref = reference / rel
        cand = candidate / rel
        entry: dict = {
            "path": rel,
            "reference_exists": ref.exists(),
            "candidate_exists": cand.exists(),
        }
        if ref.is_file() and cand.is_file():
            ref_needed = _needed(readelf, ref)
            cand_needed = _needed(readelf, cand)
            needed_ok = ref_needed is not None and cand_needed is not None and ref_needed == cand_needed
            entry["needed"] = {
                "reference": ref_needed,
                "candidate": cand_needed,
                "ok": needed_ok,
            }
            ref_soname = _soname(readelf, ref)
            cand_soname = _soname(readelf, cand)
            entry["soname"] = {
                "reference": ref_soname,
                "candidate": cand_soname,
                "ok": ref_soname == cand_soname,
            }
            if abidiff and needed_ok:
                res = _run([abidiff, str(ref), str(cand)])
                abi_ok = (res.returncode & 0x04) == 0 and (res.returncode & 0x08) == 0
                entry["abidiff"] = {
                    "tool": "abidiff",
                    "returncode": res.returncode,
                    "ok": abi_ok,
                    "report": res.stdout[-4000:],
                }
                symbols_ok = abi_ok
            else:
                ref_syms = _dyn_symbols(nm, ref) or set()
                cand_syms = _dyn_symbols(nm, cand) or set()
                missing = sorted(ref_syms - cand_syms)
                added = sorted(cand_syms - ref_syms)
                symbols_ok = not missing and not added and ref_needed is not None and cand_needed is not None
                entry["symbols"] = {
                    "tool": "nm",
                    "missing": missing,
                    "added": added,
                    "reference_count": len(ref_syms),
                    "candidate_count": len(cand_syms),
                    "ok": symbols_ok,
                }
            entry["ok"] = needed_ok and entry["soname"]["ok"] and symbols_ok
        else:
            entry["ok"] = False
        ok = ok and entry["ok"]
        entries.append(entry)
    return {"tool": "abidiff" if abidiff else "readelf+nm", "files": entries, "ok": ok}


def diff_abi(reference: Path, candidate: Path) -> dict:
    readelf = _tool("readelf", "READELF")
    nm = _tool("nm", "NM")
    abidiff = os.environ.get("ABIDIFF") or shutil.which("abidiff")

    per_lib: list[dict] = []
    ok = True
    for ref_lib in _shared_libs(reference):
        cand_lib = candidate / ref_lib.relative_to(reference)
        entry: dict = {"lib": str(ref_lib.relative_to(reference))}
        if not cand_lib.exists():
            entry.update(ok=False, reason="missing in candidate")
            per_lib.append(entry)
            ok = False
            continue

        ref_soname = _soname(readelf, ref_lib)
        cand_soname = _soname(readelf, cand_lib)
        entry["soname"] = {"reference": ref_soname, "candidate": cand_soname,
                           "ok": ref_soname == cand_soname}

        if abidiff:
            res = _run([abidiff, str(ref_lib), str(cand_lib)])
            # abidiff exit code bit 2 (ABI change) is the interesting failure.
            abi_ok = (res.returncode & 0x04) == 0 and (res.returncode & 0x08) == 0
            entry["abidiff"] = {"tool": "abidiff", "returncode": res.returncode,
                                "ok": abi_ok, "report": res.stdout[-4000:]}
            sym_ok = abi_ok
        else:
            ref_syms = _dyn_symbols(nm, ref_lib) or set()
            cand_syms = _dyn_symbols(nm, cand_lib) or set()
            missing = sorted(ref_syms - cand_syms)
            added = sorted(cand_syms - ref_syms)
            sym_ok = not missing and not added
            entry["symbols"] = {"tool": "nm", "missing": missing, "added": added,
                                "reference_count": len(ref_syms),
                                "candidate_count": len(cand_syms), "ok": sym_ok}

        entry["ok"] = entry["soname"]["ok"] and sym_ok
        ok = ok and entry["ok"]
        per_lib.append(entry)

    return {"tool": "abidiff" if abidiff else "readelf+nm", "libs": per_lib, "ok": ok}


def link_and_run(consumer: Path, reference: Path, candidate: Path,
                 link_libs: list[str], include_dirs: list[str],
                 link_flags_extra: list[str],
                 reference_link_prefixes: list[Path],
                 candidate_link_prefixes: list[Path],
                 include_link_prefix_headers: bool = False,
                 link_prefix_top_include_only: bool = False) -> dict:
    """Compile+link+run the consumer against each prefix; stdout must match."""
    cc = _tool("cc", "CC")

    def default_include_flags(prefix: Path, *, nested: bool = True) -> list[str]:
        flags = []
        inc = prefix / "include"
        if not inc.is_dir():
            return flags
        flags.extend(["-I", str(inc)])
        if not nested:
            return flags
        # Many packages require an additional nested include path (e.g.
        # include/libxml2) so headers are included as <libxml/...>.
        for child in sorted(inc.iterdir(), key=lambda p: p.name):
            if child.is_dir():
                flags.extend(["-I", str(child)])
        return flags

    def include_flags(prefix: Path, extra_prefixes: list[Path]) -> list[str]:
        flags = []
        if include_dirs:
            for rel in include_dirs:
                flags.extend(["-I", str(prefix / rel)])
        else:
            flags.extend(default_include_flags(prefix))
        if include_link_prefix_headers:
            for dep in extra_prefixes:
                flags.extend(default_include_flags(dep, nested=not link_prefix_top_include_only))
        return flags

    def link_flags(prefix: Path, extra_prefixes: list[Path]) -> list[str]:
        flags = []
        for p in [prefix, *extra_prefixes]:
            libdir = p / ("lib64" if (p / "lib64").is_dir() else "lib")
            flags.extend(["-L", str(libdir), f"-Wl,-rpath,{libdir}"])
        return flags

    def build_run(prefix: Path, extra_prefixes: list[Path], tag: str) -> dict:
        libdir = prefix / ("lib64" if (prefix / "lib64").is_dir() else "lib")
        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / f"consumer_{tag}"
            cmd = [cc, str(consumer), *include_flags(prefix, extra_prefixes),
                   *link_flags(prefix, extra_prefixes), "-o", str(exe)]
            for lib in link_libs:
                cmd.append("-l" + lib)
            cmd.extend(link_flags_extra)
            comp = _run(cmd)
            if comp.returncode != 0:
                return {"built": False, "compile_stderr": comp.stderr[-2000:], "ok": False}
            run = _run([str(exe)])
            return {"built": True, "returncode": run.returncode,
                    "stdout": run.stdout, "stderr": run.stderr[-1000:],
                    "ok": run.returncode == 0}

    ref_res = build_run(reference, reference_link_prefixes, "ref")
    cand_res = build_run(candidate, candidate_link_prefixes, "cand")
    stdout_match = ref_res.get("stdout") == cand_res.get("stdout")
    return {
        "reference": ref_res,
        "candidate": cand_res,
        "stdout_match": stdout_match,
        "ok": ref_res.get("ok", False) and cand_res.get("ok", False) and stdout_match,
    }


def exec_and_compare(
    reference: Path,
    candidate: Path,
    specs: list[str],
    reference_link_prefixes: list[Path],
    candidate_link_prefixes: list[Path],
) -> dict:
    """Run executable parity specs against both prefixes and compare outputs.

    Each spec is JSON:
      {"argv": ["bin/diff", "{left}", "{right}"],
       "files": {"left": "a\n", "right": "b\n"},
       "returncodes": [0, 1]}

    The first argv entry is resolved relative to the prefix. Any `{name}` token
    in later argv entries is replaced by a temporary file declared in `files`.
    """

    def normalize_output(
        data: bytes,
        prefix: Path,
        extra_prefixes: list[Path],
        aliases: dict[str, str] | None = None,
    ) -> bytes:
        text = _json_text(data)
        if text is None:
            out = data
            for value, replacement in sorted((aliases or {}).items(), key=lambda item: len(item[0]), reverse=True):
                value_b = value.encode()
                if value_b:
                    out = out.replace(value_b, replacement.encode())
            return out
        return _normalize_text_with_prefix_aliases(
            text,
            prefix,
            extra_prefixes,
            aliases,
        ).encode()

    def runtime_env(prefix: Path, extra_prefixes: list[Path]) -> dict[str, str]:
        env = dict(os.environ)
        libdirs = []
        bindirs = []
        pythonpaths = []
        for p in [prefix, *extra_prefixes]:
            bindir = p / "bin"
            if bindir.is_dir():
                bindirs.append(str(bindir))
            for libdir in _LIB_DIRS:
                path = p / libdir
                if path.is_dir():
                    libdirs.append(str(path))
                for site_packages in path.glob("python*/site-packages"):
                    if site_packages.is_dir():
                        pythonpaths.append(str(site_packages))
        if bindirs:
            previous_path = env.get("PATH")
            env["PATH"] = ":".join(bindirs + ([previous_path] if previous_path else []))
        if libdirs:
            previous = env.get("LD_LIBRARY_PATH")
            env["LD_LIBRARY_PATH"] = ":".join(libdirs + ([previous] if previous else []))
        if pythonpaths:
            previous_pythonpath = env.get("PYTHONPATH")
            env["PYTHONPATH"] = ":".join(
                pythonpaths + ([previous_pythonpath] if previous_pythonpath else [])
            )
        return env

    def replace_tokens(arg: str, repl: dict[str, str]) -> str:
        out = arg
        for token, value in repl.items():
            out = out.replace(token, value)
        return out

    def command(prefix: Path, argv: list[str], repl: dict[str, str]) -> list[str]:
        cmd = [str(prefix / argv[0])]
        for arg in argv[1:]:
            cmd.append(replace_tokens(arg, repl))
        return cmd

    def one(prefix: Path, extra_prefixes: list[Path], spec: dict, tag: str) -> dict:
        argv = spec["argv"]
        allowed = spec.get("returncodes", [0])
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            repl = {}
            for name, content in spec.get("files", {}).items():
                path = tmp / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
                repl["{" + name + "}"] = str(path)
            for name in spec.get("dirs", []):
                if not isinstance(name, str) or not name:
                    raise SystemExit(f"exec-test dir names must be non-empty strings: {json.dumps(spec)}")
                path = tmp / name
                path.mkdir(parents=True, exist_ok=True)
                repl["{" + name + "}"] = str(path)
            for name in spec.get("outputs", []):
                if not isinstance(name, str) or not name:
                    raise SystemExit(f"exec-test output names must be non-empty strings: {json.dumps(spec)}")
                path = tmp / name
                path.parent.mkdir(parents=True, exist_ok=True)
                repl["{" + name + "}"] = str(path)
            output_aliases = {
                path: token for token, path in repl.items()
                if token.startswith("{") and token.endswith("}")
            }
            repl["{prefix}"] = str(prefix)
            repl["{libdir}"] = str(prefix / ("lib64" if (prefix / "lib64").is_dir() else "lib"))
            repl["{datadir}"] = str(prefix / "share")
            cwd = None
            if spec.get("cwd") == "tmp":
                cwd = tmp
            elif "cwd" in spec:
                raise SystemExit(f"unsupported exec-test cwd {spec['cwd']!r}: {json.dumps(spec)}")
            env = runtime_env(prefix, extra_prefixes)
            setup_results = []
            for setup_argv in spec.get("setup", []):
                setup_cmd = command(prefix, setup_argv, repl)
                setup_run = subprocess.run(
                    setup_cmd,
                    capture_output=True,
                    text=False,
                    env=env,
                    cwd=cwd,
                )
                setup_results.append({
                    "argv": setup_cmd,
                    "returncode": setup_run.returncode,
                    "stdout": _json_text(normalize_output(setup_run.stdout, prefix, extra_prefixes, output_aliases)),
                    "stderr": _json_text(normalize_output(setup_run.stderr, prefix, extra_prefixes, output_aliases)),
                    "ok": setup_run.returncode == 0,
                })
                if setup_run.returncode != 0:
                    return {
                        "tag": tag,
                        "argv": command(prefix, argv, repl),
                        "setup": setup_results,
                        "returncode": None,
                        "stdout": "",
                        "stderr": "",
                        "stdout_sha256": hashlib.sha256(b"").hexdigest(),
                        "stderr_sha256": hashlib.sha256(b"").hexdigest(),
                        "ok": False,
                    }
            cmd = command(prefix, argv, repl)
            run = subprocess.run(
                cmd,
                capture_output=True,
                text=False,
                env=env,
                cwd=cwd,
            )
            stdout = normalize_output(run.stdout, prefix, extra_prefixes, output_aliases)
            stderr = normalize_output(run.stderr, prefix, extra_prefixes, output_aliases)
            outputs = {}
            for name in spec.get("outputs", []):
                path = Path(repl["{" + name + "}"])
                if path.is_file():
                    data = normalize_output(path.read_bytes(), prefix, extra_prefixes, output_aliases)
                    outputs[name] = {
                        "exists": True,
                        "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                else:
                    outputs[name] = {
                        "exists": False,
                        "size": 0,
                        "sha256": None,
                    }
            return {
                "tag": tag,
                "argv": cmd,
                "setup": setup_results,
                "returncode": run.returncode,
                "stdout": _json_text(stdout),
                "stderr": _json_text(stderr),
                "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
                "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
                "outputs": outputs,
                "ok": run.returncode in allowed,
            }

    entries = []
    ok = True
    for raw in specs:
        spec = json.loads(raw)
        if "argv" not in spec or not spec["argv"]:
            raise SystemExit(f"exec-test requires non-empty argv: {raw}")
        ref_res = one(reference, reference_link_prefixes, spec, "reference")
        cand_res = one(candidate, candidate_link_prefixes, spec, "candidate")
        stdout_match = ref_res["stdout_sha256"] == cand_res["stdout_sha256"]
        stderr_match = ref_res["stderr_sha256"] == cand_res["stderr_sha256"]
        returncode_match = ref_res["returncode"] == cand_res["returncode"]
        outputs_match = ref_res.get("outputs", {}) == cand_res.get("outputs", {})
        entry = {
            "spec": spec,
            "reference": ref_res,
            "candidate": cand_res,
            "stdout_match": stdout_match,
            "stderr_match": stderr_match,
            "returncode_match": returncode_match,
            "outputs_match": outputs_match,
            "ok": (
                ref_res["ok"] and cand_res["ok"] and
                stdout_match and stderr_match and returncode_match and
                outputs_match
            ),
        }
        ok = ok and entry["ok"]
        entries.append(entry)
    return {"tests": entries, "ok": ok}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reference", type=Path, required=True, help="Spack prefix")
    ap.add_argument("--candidate", type=Path, required=True, help="native prefix")
    ap.add_argument("--consumer", type=Path, default=None,
                    help="optional C source to link-and-run against both prefixes")
    ap.add_argument("--link-lib", action="append", default=[], dest="link_libs",
                    help="soname stem to pass as -l for the consumer (repeatable)")
    ap.add_argument("--link-flag", action="append", default=[], dest="link_flags",
                    help="extra linker flag for the consumer, e.g. -pthread (repeatable)")
    ap.add_argument("--include-dir", action="append", default=[],
                    help="consumer include dir relative to each prefix; repeatable. "
                         "Defaults to include plus first-level nested include dirs.")
    ap.add_argument("--reference-link-prefix", action="append", default=[],
                    type=Path,
                    help="extra dependency prefix to add to the reference link line")
    ap.add_argument("--candidate-link-prefix", action="append", default=[],
                    type=Path,
                    help="extra dependency prefix to add to the candidate link line")
    ap.add_argument("--include-link-prefix-headers", action="store_true",
                    help="also add include dirs from link prefixes to the consumer compile line")
    ap.add_argument("--link-prefix-top-include-only", action="store_true",
                    help="when adding link-prefix headers, add only <prefix>/include, not nested include dirs")
    ap.add_argument("--allow-extra", action="append", default=[],
                    help="relpath prefix allowed to exist only in the candidate")
    ap.add_argument("--data-path", action="append", default=[],
                    help="explicit data-file relpath to include in layout and SHA256 parity")
    ap.add_argument("--exec-path", action="append", default=[],
                    help="explicit executable relpath to include in layout and dynamic-dep parity")
    ap.add_argument("--elf-path", action="append", default=[],
                    help="explicit ELF relpath outside lib*/ to include in layout and ABI parity")
    ap.add_argument("--exec-test", action="append", default=[],
                    help="JSON executable test spec to run under both prefixes")
    ap.add_argument("--out", type=Path, default=None, help="write JSON verdict here")
    args = ap.parse_args(argv)

    for p in (args.reference, args.candidate):
        if not p.is_dir():
            raise SystemExit(f"prefix not a directory: {p}")
    for p in [*args.reference_link_prefix, *args.candidate_link_prefix]:
        if not p.is_dir():
            raise SystemExit(f"link prefix not a directory: {p}")

    verdict: dict = {
        "reference": str(args.reference),
        "candidate": str(args.candidate),
        "layout": diff_layout(
            args.reference, args.candidate, args.allow_extra,
            args.data_path + args.elf_path, args.exec_path),
        "abi": diff_abi(args.reference, args.candidate),
    }
    if args.data_path:
        verdict["data_files"] = diff_data_files(
            args.reference,
            args.candidate,
            args.data_path,
            args.reference_link_prefix,
            args.candidate_link_prefix,
        )
    if args.consumer is not None:
        verdict["link_and_run"] = link_and_run(
            args.consumer, args.reference, args.candidate,
            args.link_libs, args.include_dir, args.link_flags,
            args.reference_link_prefix, args.candidate_link_prefix,
            args.include_link_prefix_headers,
            args.link_prefix_top_include_only)
    if args.exec_path:
        verdict["executables"] = diff_executables(
            args.reference, args.candidate, args.exec_path)
    if args.elf_path:
        verdict["explicit_elfs"] = diff_explicit_elfs(
            args.reference, args.candidate, args.elf_path)
    if args.exec_test:
        verdict["exec_and_run"] = exec_and_compare(
            args.reference, args.candidate, args.exec_test,
            args.reference_link_prefix, args.candidate_link_prefix)

    verdict["ok"] = all(
        v.get("ok", True) for k, v in verdict.items()
        if isinstance(v, dict)
    )

    text = json.dumps(verdict, indent=2, sort_keys=True) + "\n"
    if args.out is not None:
        args.out.write_text(text)
    print(text, end="")
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
