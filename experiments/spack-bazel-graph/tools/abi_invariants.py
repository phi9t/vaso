#!/usr/bin/env python3
"""Check torch-profile ABI invariants on built prefixes and runtime maps."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


MAX_GLIBCXX = (3, 4, 33)
ROOTFS_LIBSTDCXX = "/usr/lib/x86_64-linux-gnu/libstdc++.so.6.0.33"
CUDA_MAJOR_BY_LINE = {"cu129": "12", "cu130": "13"}
OLD_CXX98_ABI_PATTERNS = ("_ZNSs", "_ZNKSs", "_ZNSbIw")
STATIC_LIBSTDCXX_SYMBOLS = {"__cxa_throw", "__gxx_personality_v0", "_ZSt9terminatev"}
STATIC_LIBSTDCXX_STRING_MARKER = "basic_string::_M_construct null not valid"
UNWINDER_SYMBOLS = {"_Unwind_RaiseException"}
ODR_DUPLICATE_SYMBOL_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"^_Z(?:N|NK|TI|TS|TV)4absl",
        r"^_Z(?:N|NK|TI|TS|TV)4grpc",
        r"^_Z(?:N|NK|TI|TS|TV)5boost",
        r"^_Z(?:N|NK|TI|TS|TV)4llvm",
        r"^_Z(?:N|NK|TI|TS|TV)4mlir",
        r"^_Z(?:N|NK|TI|TS|TV)6google8protobuf",
    )
)


@dataclass(frozen=True)
class ElfFacts:
    path: Path
    needed: tuple[str, ...] = ()
    undefined: tuple[str, ...] = ()
    defined: tuple[str, ...] = ()
    comments: tuple[str, ...] = ()
    string_markers: tuple[str, ...] = ()


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=False, capture_output=True, text=True)


def _tool(name: str, env_key: str) -> str:
    return os.environ.get(env_key) or shutil.which(name) or name


def _strip_version(symbol: str) -> str:
    return symbol.split("@", 1)[0]


def _is_elf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) == b"\x7fELF"
    except OSError:
        return False


def discover_elfs(prefixes: Iterable[Path]) -> list[Path]:
    elfs: list[Path] = []
    seen: set[str] = set()
    for prefix in prefixes:
        if not prefix.is_dir():
            continue
        for path in prefix.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                key = str(path.resolve())
            except OSError:
                key = str(path)
            if key in seen or not _is_elf(path):
                continue
            seen.add(key)
            elfs.append(path)
    return sorted(elfs)


def parse_needed(text: str) -> list[str]:
    return re.findall(r"Shared library: \[([^\]]+)\]", text)


def parse_readelf_comment(text: str) -> list[str]:
    comments: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("String dump of section"):
            continue
        match = re.match(r"^\[\s*[0-9a-fA-F]+\]\s*(?P<comment>.*)$", line)
        if match:
            comment = match.group("comment").strip()
            if comment:
                comments.append(comment)
    return comments


def parse_nm_symbols(text: str) -> list[str]:
    symbols: list[str] = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        symbols.append(parts[-1])
    return symbols


def _strings_contains(path: Path, marker: str, strings_tool: str) -> bool:
    result = _run([strings_tool, str(path)])
    return result.returncode == 0 and marker in result.stdout


def collect_elf_facts(paths: Iterable[Path]) -> list[ElfFacts]:
    readelf = _tool("readelf", "READELF")
    nm = _tool("nm", "NM")
    strings_tool = _tool("strings", "STRINGS")
    facts: list[ElfFacts] = []
    for path in paths:
        needed_result = _run([readelf, "-d", str(path)])
        undefined_result = _run([nm, "-D", "--undefined-only", str(path)])
        defined_result = _run([nm, "-D", "--defined-only", str(path)])
        comment_result = _run([readelf, "-p", ".comment", str(path)])
        facts.append(
            ElfFacts(
                path=path,
                needed=tuple(parse_needed(needed_result.stdout) if needed_result.returncode == 0 else ()),
                undefined=tuple(parse_nm_symbols(undefined_result.stdout) if undefined_result.returncode == 0 else ()),
                defined=tuple(parse_nm_symbols(defined_result.stdout) if defined_result.returncode == 0 else ()),
                comments=tuple(parse_readelf_comment(comment_result.stdout) if comment_result.returncode == 0 else ()),
                string_markers=(
                    (STATIC_LIBSTDCXX_STRING_MARKER,)
                    if _strings_contains(path, STATIC_LIBSTDCXX_STRING_MARKER, strings_tool)
                    else ()
                ),
            )
        )
    return facts


def collect_llvm_archive_comments(llvm_prefix: Path) -> dict[Path, list[str]]:
    readelf = _tool("readelf", "READELF")
    comments: dict[Path, list[str]] = {}
    lib_dir = llvm_prefix / "lib"
    archives = sorted([*lib_dir.glob("libLLVM*.a"), *lib_dir.glob("libMLIR*.a")])
    for archive in archives:
        result = _run([readelf, "-p", ".comment", str(archive)])
        comments[archive] = parse_readelf_comment(result.stdout) if result.returncode == 0 else []
    return comments


def _glibcxx_version(symbol: str) -> tuple[int, int, int] | None:
    match = re.search(r"GLIBCXX_(\d+)\.(\d+)(?:\.(\d+))?", symbol)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or "0"))


def _format_glibcxx(version: tuple[int, int, int]) -> str:
    return f"GLIBCXX_{version[0]}.{version[1]}.{version[2]}"


def _is_triton_so(path: Path) -> bool:
    return path.name == "libtriton.so" or path.name.startswith("libtriton.so.")


def _has_gcc_133(comments: Iterable[str]) -> bool:
    return any("GCC: (Ubuntu 13.3.0" in comment for comment in comments)


def _has_clang(comments: Iterable[str]) -> bool:
    return any("clang version" in comment for comment in comments)


def _is_odr_sensitive_duplicate_symbol(symbol: str) -> bool:
    return any(pattern.search(symbol) for pattern in ODR_DUPLICATE_SYMBOL_PATTERNS)


def check_static_invariants(
    facts: Iterable[ElfFacts],
    *,
    profile: str = "torch",
    llvm_archive_comments: Mapping[Path, Iterable[str]] | None = None,
) -> list[str]:
    errors: list[str] = []
    facts = list(facts)

    exported_by_symbol: dict[str, list[Path]] = {}
    for fact in facts:
        for needed in fact.needed:
            if needed.startswith("libc++.so") or needed.startswith("libc++abi.so"):
                errors.append(f"I1 {fact.path}: NEEDED forbidden C++ runtime {needed}")
            if needed.startswith("libunwind.so"):
                errors.append(f"I4 {fact.path}: NEEDED forbidden unwinder {needed}")

        for symbol in fact.undefined:
            version = _glibcxx_version(symbol)
            if version is not None and version > MAX_GLIBCXX:
                errors.append(
                    f"I1 {fact.path}: imports {_format_glibcxx(version)} above max {_format_glibcxx(MAX_GLIBCXX)}"
                )
            if any(pattern in symbol for pattern in OLD_CXX98_ABI_PATTERNS):
                errors.append(f"I3 {fact.path}: imports old C++98 string ABI symbol {symbol}")

        for symbol in fact.defined:
            stripped = _strip_version(symbol)
            if stripped in STATIC_LIBSTDCXX_SYMBOLS:
                errors.append(f"I2 {fact.path}: exports static-libstdc++ symbol {stripped}")
            if stripped in UNWINDER_SYMBOLS:
                errors.append(f"I4 {fact.path}: exports unwinder symbol {stripped}")
            exported_by_symbol.setdefault(stripped, []).append(fact.path)

        if STATIC_LIBSTDCXX_STRING_MARKER in fact.string_markers:
            errors.append(f"I2 {fact.path}: contains static-libstdc++ string marker")

        if profile == "torch" and _has_clang(fact.comments):
            errors.append(f"I8 {fact.path}: torch profile ELF has clang .comment")

        if _is_triton_so(fact.path):
            for symbol in sorted({_strip_version(symbol) for symbol in fact.defined} - {"PyInit_libtriton"}):
                errors.append(f"I6 {fact.path}: libtriton exports {symbol}; expected only PyInit_libtriton")
            if profile == "torch" and not _has_gcc_133(fact.comments):
                errors.append(f"I7 {fact.path}: Triton .comment lacks GCC: (Ubuntu 13.3.0")

    for symbol, paths in sorted(exported_by_symbol.items()):
        if not _is_odr_sensitive_duplicate_symbol(symbol):
            continue
        unique_paths = sorted({str(path) for path in paths})
        if len(unique_paths) > 1:
            errors.append(f"I6 duplicate exported symbol {symbol}: " + ", ".join(unique_paths))

    if profile == "torch" and llvm_archive_comments is not None:
        if not llvm_archive_comments:
            errors.append("I7 LLVM archive .comment data is missing")
        for archive, comments_iter in sorted(llvm_archive_comments.items(), key=lambda item: str(item[0])):
            comments = list(comments_iter)
            if not comments:
                errors.append(f"I7 {archive}: LLVM archive has no .comment data")
            if _has_clang(comments):
                errors.append(f"I7 {archive}: LLVM archive has clang .comment in torch profile")
            if comments and not _has_gcc_133(comments):
                errors.append(f"I7 {archive}: LLVM archive .comment lacks GCC: (Ubuntu 13.3.0")
    return errors


def _maps_paths(maps_text: str) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for line in maps_text.splitlines():
        parts = line.split()
        if not parts:
            continue
        path = parts[-1]
        if not path.startswith("/"):
            continue
        if path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return paths


def _normalize_maps_path(path: str) -> str:
    if path.startswith("/lib/x86_64-linux-gnu/"):
        return "/usr" + path
    return path


def check_probe_maps(maps_text: str, *, line: str) -> list[str]:
    if line not in CUDA_MAJOR_BY_LINE:
        raise SystemExit(f"unknown CUDA line {line!r}; expected cu129 or cu130")
    errors: list[str] = []
    paths = [_normalize_maps_path(path) for path in _maps_paths(maps_text)]

    libstdcxx = [path for path in paths if Path(path).name.startswith("libstdc++.so.6")]
    if len(libstdcxx) != 1:
        errors.append(f"I1 maps: expected exactly one libstdc++.so.6, found {len(libstdcxx)}")
    elif libstdcxx[0] != ROOTFS_LIBSTDCXX:
        errors.append(f"I1 maps: libstdc++.so.6 is {libstdcxx[0]}; expected {ROOTFS_LIBSTDCXX}")

    for path in paths:
        name = Path(path).name
        if name.startswith("libomp.so") or name.startswith("libiomp5.so"):
            errors.append(f"I5 maps: forbidden OpenMP runtime mapped {path}")
    libgomp = [path for path in paths if Path(path).name.startswith("libgomp.so")]
    if len(libgomp) > 1:
        errors.append(f"I5 maps: expected at most one libgomp.so.1, found {len(libgomp)}")
    elif len(libgomp) == 1 and not libgomp[0].startswith("/usr/lib/x86_64-linux-gnu/"):
        errors.append(f"I5 maps: libgomp is {libgomp[0]}; expected rootfs /usr/lib/x86_64-linux-gnu")

    libcuda = [path for path in paths if Path(path).name == "libcuda.so.1"]
    if len(libcuda) != 1:
        errors.append(f"I11 maps: expected exactly one libcuda.so.1, found {len(libcuda)}")
    expected_major = CUDA_MAJOR_BY_LINE[line]
    for path in paths:
        match = re.search(r"libcudart\.so\.(\d+)", Path(path).name)
        if match and match.group(1) != expected_major:
            errors.append(f"I11 maps: libcudart {path} does not match {line} CUDA major {expected_major}")
    return errors


def _parse_prefixes(items: Iterable[str]) -> dict[str, Path]:
    prefixes: dict[str, Path] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--prefix must be NAME=PATH, got {item!r}")
        name, raw_path = item.split("=", 1)
        if not name or not raw_path:
            raise SystemExit(f"--prefix must be NAME=PATH, got {item!r}")
        prefixes[name] = Path(raw_path)
    return prefixes


def _site_packages(prefix: Path) -> Path:
    matches = sorted((prefix / "lib").glob("python*/site-packages"))
    if len(matches) != 1:
        raise SystemExit(f"{prefix} must provide exactly one lib/pythonX.Y/site-packages tree")
    return matches[0]


def _lib_dirs(prefix: Path) -> list[str]:
    return [str(prefix / name) for name in ("lib", "lib64") if (prefix / name).is_dir()]


def _dedupe(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


def probe_env(
    base_environ: Mapping[str, str],
    *,
    out_dir: Path,
    python_prefix: Path,
    prefixes: Mapping[str, Path],
    runtime_prefixes: Mapping[str, Path],
) -> dict[str, str]:
    pythonpath = [str(Path(__file__).resolve().parents[1])]
    ld_entries = ["/run/nvidia-driver/lib", "/usr/local/cuda/lib64"]
    path_entries = [str(python_prefix / "bin")]
    for name in ("torch", "torchvision", "torchaudio", "triton"):
        prefix = prefixes.get(name)
        if prefix is None:
            continue
        site_packages = _site_packages(prefix)
        pythonpath.append(str(site_packages))
        ld_entries.extend(_lib_dirs(prefix))
        path_entries.append(str(prefix / "bin"))
        if name == "torch":
            ld_entries.append(str(site_packages / "torch" / "lib"))
    for prefix in runtime_prefixes.values():
        pythonpath.append(str(_site_packages(prefix)))
        ld_entries.extend(_lib_dirs(prefix))
        path_entries.append(str(prefix / "bin"))
    env = dict(base_environ)
    if env.get("PYTHONPATH"):
        pythonpath.extend(item for item in env["PYTHONPATH"].split(os.pathsep) if item)
    if env.get("LD_LIBRARY_PATH"):
        ld_entries.extend(item for item in env["LD_LIBRARY_PATH"].split(os.pathsep) if item)
    if env.get("PATH"):
        path_entries.append(env["PATH"])
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = out_dir / "tmp"
    cache_dir = out_dir / "cache"
    cuda_home = base_environ.get("VASO_CUDA_HOME") or "/usr/local/cuda"
    cuda_bin = Path(cuda_home) / "bin"
    env.update(
        {
            "CC": "/usr/bin/gcc",
            "CXX": "/usr/bin/g++",
            "PYTHONPATH": os.pathsep.join(_dedupe(pythonpath)),
            "LD_LIBRARY_PATH": os.pathsep.join(_dedupe(ld_entries)),
            "PATH": os.pathsep.join(_dedupe(path_entries)),
            "TMPDIR": str(tmp_dir),
            "TEMP": str(tmp_dir),
            "TMP": str(tmp_dir),
            "XDG_CACHE_HOME": str(cache_dir / "xdg"),
            "CUDA_CACHE_PATH": str(cache_dir / "cuda"),
            "TORCH_EXTENSIONS_DIR": str(out_dir / "torch_extensions"),
            "TORCHINDUCTOR_CACHE_DIR": str(out_dir / "torchinductor"),
            "TRITON_CACHE_DIR": str(out_dir / "triton"),
            "TRITON_CUDACRT_PATH": str(Path(cuda_home) / "include"),
            "TRITON_CUDART_PATH": str(Path(cuda_home) / "include"),
            "TRITON_CUOBJDUMP_PATH": str(cuda_bin / "cuobjdump"),
            "TRITON_LIBCUDA_PATH": "/run/nvidia-driver/lib",
            "TRITON_LIBDEVICE_PATH": str(Path(cuda_home) / "nvvm" / "libdevice" / "libdevice.10.bc"),
            "TRITON_NVDISASM_PATH": str(cuda_bin / "nvdisasm"),
            "TRITON_PTXAS_BLACKWELL_PATH": str(cuda_bin / "ptxas"),
            "TRITON_PTXAS_PATH": str(cuda_bin / "ptxas"),
            "MOSAIC_GPU_NVSHMEM_BC_PATH": str(Path(cuda_home) / "lib64" / "libnvshmem_device.bc"),
            "MOSAIC_GPU_NVSHMEM_SO_PATH": str(Path(cuda_home) / "lib64" / "libnvshmem_host.so.3"),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    for key in ("TMPDIR", "XDG_CACHE_HOME", "CUDA_CACHE_PATH", "TORCH_EXTENSIONS_DIR", "TORCHINDUCTOR_CACHE_DIR", "TRITON_CACHE_DIR"):
        Path(env[key]).mkdir(parents=True, exist_ok=True)
    return env


def _run_torch_probe(line: str) -> dict[str, object]:
    import torch
    import torchaudio  # noqa: F401
    import torchvision  # noqa: F401
    import triton  # noqa: F401

    if not torch.cuda.is_available():
        raise RuntimeError("torch.cuda.is_available() is false")
    x = torch.randn((16, 16), device="cuda")
    y = x @ x
    torch.cuda.synchronize()

    def fn(tensor):
        return (tensor.sin() + 1.0) * 2.0

    compiled = torch.compile(fn)
    z = compiled(y)
    torch.cuda.synchronize()
    maps_text = Path("/proc/self/maps").read_text(encoding="utf-8", errors="replace")
    errors = check_probe_maps(maps_text, line=line)
    return {
        "line": line,
        "torch_version": torch.__version__,
        "cuda_available": True,
        "cuda_op_sum": float(y.sum().detach().cpu().item()),
        "torch_compile_sum": float(z.sum().detach().cpu().item()),
        "maps_errors": errors,
        "verdict": "passed" if not errors else "failed",
    }


def _write_json(path: Path, doc: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _static_result(prefixes: Mapping[str, Path], llvm_prefix: Path | None, profile: str) -> dict[str, object]:
    missing_prefix_errors = [
        f"prefix {name} is missing: {path}"
        for name, path in sorted(prefixes.items())
        if not path.is_dir()
    ]
    facts = collect_elf_facts(discover_elfs(prefixes.values()))
    llvm_comments = collect_llvm_archive_comments(llvm_prefix) if llvm_prefix is not None else None
    errors = missing_prefix_errors + check_static_invariants(facts, profile=profile, llvm_archive_comments=llvm_comments)
    return {
        "mode": "static",
        "profile": profile,
        "prefixes": {name: str(path) for name, path in sorted(prefixes.items())},
        "elf_count": len(facts),
        "llvm_archive_count": len(llvm_comments or {}),
        "errors": errors,
        "verdict": "passed" if not errors else "failed",
    }


def _probe_child(line: str, json_out: Path | None) -> int:
    try:
        doc = _run_torch_probe(line)
    except Exception as exc:
        doc = {
            "line": line,
            "errors": [f"{type(exc).__name__}: {exc}"],
            "verdict": "failed",
        }
    if json_out is not None:
        _write_json(json_out, doc)
    print(json.dumps(doc, indent=2, sort_keys=True))
    if doc.get("verdict") != "passed":
        print("ABI probe failed:\n  " + "\n  ".join(str(item) for item in doc.get("errors", doc.get("maps_errors", []))), file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="torch", choices=("torch", "jax"))
    parser.add_argument("--prefix", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--runtime-prefix", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--llvm-prefix", type=Path, default=Path("/usr/lib/llvm-23"))
    parser.add_argument("--line", default=os.environ.get("VASO_CUDA_LINE", ""))
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--probe-child", action="store_true")
    parser.add_argument("--python-prefix", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)

    if args.probe_child:
        if not args.line:
            print("--line is required for --probe-child", file=sys.stderr)
            return 2
        return _probe_child(args.line, args.json_out)

    prefixes = _parse_prefixes(args.prefix)
    if args.probe:
        if not args.line:
            print("--line is required for --probe", file=sys.stderr)
            return 2
        if args.python_prefix is None:
            print("--python-prefix is required for --probe", file=sys.stderr)
            return 2
        if args.out_dir is None:
            print("--out-dir is required for --probe", file=sys.stderr)
            return 2
        runtime_prefixes = _parse_prefixes(args.runtime_prefix)
        env = probe_env(
            os.environ,
            out_dir=args.out_dir,
            python_prefix=args.python_prefix,
            prefixes=prefixes,
            runtime_prefixes=runtime_prefixes,
        )
        command = [
            str(args.python_prefix / "bin" / "python3"),
            str(Path(__file__).resolve()),
            "--probe-child",
            "--line",
            args.line,
        ]
        if args.json_out is not None:
            command.extend(["--json-out", str(args.json_out)])
        return subprocess.run(command, check=False, env=env).returncode

    result = _static_result(prefixes, args.llvm_prefix, args.profile)
    if args.json_out is not None:
        _write_json(args.json_out, result)
    if result["errors"]:
        print("ABI invariant static check failed:\n  " + "\n  ".join(result["errors"]), file=sys.stderr)
        return 1
    print(
        f"ABI invariant static check passed profile={args.profile}: "
        f"elfs={result['elf_count']} llvm_archives={result['llvm_archive_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
