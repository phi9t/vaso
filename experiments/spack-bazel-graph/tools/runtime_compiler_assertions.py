#!/usr/bin/env python3
"""Assert the torch-profile runtime JIT compilers resolve to rootfs gcc."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Mapping


EXPECTED_CC = "/usr/bin/gcc"
EXPECTED_CXX = "/usr/bin/g++"
ROOTFS_CC_ALIASES = frozenset({EXPECTED_CC, "/usr/bin/gcc-13"})
ROOTFS_CXX_ALIASES = frozenset({EXPECTED_CXX, "/usr/bin/g++-13"})


def _stringify(value: object) -> str:
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    return str(value)


def _resolve_compiler(value: object) -> str:
    text = _stringify(value).strip()
    if not text:
        return ""
    if os.sep not in text:
        return shutil.which(text) or text
    return text


def _matches_rootfs_compiler(value: object, aliases: frozenset[str]) -> bool:
    text = _resolve_compiler(value)
    if text in aliases:
        return True
    path = Path(text)
    return path.parent == Path("/usr/bin") and path.name in {Path(alias).name for alias in aliases}


def evaluate_runtime_compilers(
    environ: Mapping[str, str],
    *,
    triton_c_compiler: object,
    inductor_cxx_compiler: object,
) -> list[str]:
    errors: list[str] = []
    cc = environ.get("CC", "")
    cxx = environ.get("CXX", "")
    if not _matches_rootfs_compiler(cc, ROOTFS_CC_ALIASES):
        errors.append(f"CC={cc or '<unset>'}; expected {EXPECTED_CC}")
    if not _matches_rootfs_compiler(cxx, ROOTFS_CXX_ALIASES):
        errors.append(f"CXX={cxx or '<unset>'}; expected {EXPECTED_CXX}")
    if not _matches_rootfs_compiler(triton_c_compiler, ROOTFS_CC_ALIASES):
        errors.append(
            f"triton _find_compiler('c') resolved {_stringify(triton_c_compiler) or '<unset>'}; expected {EXPECTED_CC}"
        )
    if not _matches_rootfs_compiler(inductor_cxx_compiler, ROOTFS_CXX_ALIASES):
        errors.append(
            f"inductor get_cpp_compiler() resolved {_stringify(inductor_cxx_compiler) or '<unset>'}; expected {EXPECTED_CXX}"
        )
    return errors


def _site_packages(prefix: Path) -> Path:
    matches = sorted((prefix / "lib").glob("python*/site-packages"))
    if len(matches) != 1:
        raise SystemExit(f"{prefix} must provide exactly one lib/pythonX.Y/site-packages tree")
    return matches[0]


def _lib_dirs(prefix: Path) -> list[str]:
    return [str(prefix / name) for name in ("lib", "lib64") if (prefix / name).is_dir()]


def _parse_prefixed_paths(items: Iterable[str]) -> list[tuple[str, Path]]:
    parsed: list[tuple[str, Path]] = []
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--runtime-prefix must be NAME=PATH, got {item!r}")
        name, raw = item.split("=", 1)
        if not name or not raw:
            raise SystemExit(f"--runtime-prefix must be NAME=PATH, got {item!r}")
        parsed.append((name, Path(raw)))
    return parsed


def _dedupe(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


def runtime_env(
    base_environ: Mapping[str, str],
    *,
    out_dir: Path | None = None,
    python_prefix: Path | None = None,
    torch_prefix: Path | None = None,
    triton_prefix: Path | None = None,
    runtime_prefixes: list[tuple[str, Path]] | None = None,
) -> dict[str, str]:
    env = dict(base_environ)
    runtime_prefixes = runtime_prefixes or []
    pythonpath: list[str] = []
    ld_entries: list[str] = ["/run/nvidia-driver/lib", "/usr/local/cuda/lib64"]
    path_entries: list[str] = []

    if python_prefix is not None:
        path_entries.append(str(python_prefix / "bin"))
    for prefix in (torch_prefix, triton_prefix):
        if prefix is not None:
            site_packages = _site_packages(prefix)
            pythonpath.append(str(site_packages))
            ld_entries.extend(_lib_dirs(prefix))
            path_entries.append(str(prefix / "bin"))
            if prefix == torch_prefix:
                ld_entries.append(str(site_packages / "torch" / "lib"))
    for _, prefix in runtime_prefixes:
        pythonpath.append(str(_site_packages(prefix)))
        ld_entries.extend(_lib_dirs(prefix))
        path_entries.append(str(prefix / "bin"))

    if env.get("PYTHONPATH"):
        pythonpath.extend(item for item in env["PYTHONPATH"].split(os.pathsep) if item)
    if env.get("LD_LIBRARY_PATH"):
        ld_entries.extend(item for item in env["LD_LIBRARY_PATH"].split(os.pathsep) if item)
    if env.get("PATH"):
        path_entries.append(env["PATH"])

    env.update(
        {
            "CC": EXPECTED_CC,
            "CXX": EXPECTED_CXX,
            "PYTHONPATH": os.pathsep.join(_dedupe(pythonpath)),
            "LD_LIBRARY_PATH": os.pathsep.join(_dedupe(ld_entries)),
            "PATH": os.pathsep.join(_dedupe(path_entries)),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        cache_dir = out_dir / "cache"
        env.update(
            {
                "TMPDIR": str(out_dir / "tmp"),
                "TEMP": str(out_dir / "tmp"),
                "TMP": str(out_dir / "tmp"),
                "TORCH_EXTENSIONS_DIR": str(out_dir / "torch_extensions"),
                "TORCHINDUCTOR_CACHE_DIR": str(out_dir / "torchinductor"),
                "TRITON_CACHE_DIR": str(out_dir / "triton"),
                "XDG_CACHE_HOME": str(cache_dir / "xdg"),
                "CUDA_CACHE_PATH": str(cache_dir / "cuda"),
            }
        )
        for key in ("TMPDIR", "XDG_CACHE_HOME", "CUDA_CACHE_PATH", "TORCH_EXTENSIONS_DIR", "TORCHINDUCTOR_CACHE_DIR", "TRITON_CACHE_DIR"):
            Path(env[key]).mkdir(parents=True, exist_ok=True)
    return env


def _probe_triton_c_compiler() -> object:
    from triton.runtime import build

    return build._find_compiler("c")


def _probe_inductor_cxx_compiler() -> object:
    from torch._inductor.cpp_builder import get_cpp_compiler

    return get_cpp_compiler()


def probe() -> tuple[dict[str, object], list[str]]:
    triton_compiler = _probe_triton_c_compiler()
    inductor_compiler = _probe_inductor_cxx_compiler()
    errors = evaluate_runtime_compilers(
        os.environ,
        triton_c_compiler=triton_compiler,
        inductor_cxx_compiler=inductor_compiler,
    )
    doc = {
        "expected": {
            "CC": EXPECTED_CC,
            "CXX": EXPECTED_CXX,
        },
        "environment": {
            "CC": os.environ.get("CC", ""),
            "CXX": os.environ.get("CXX", ""),
        },
        "resolved": {
            "triton_c_compiler": _stringify(triton_compiler),
            "inductor_cxx_compiler": _stringify(inductor_compiler),
        },
        "errors": errors,
        "verdict": "passed" if not errors else "failed",
    }
    return doc, errors


def _write_json(path: Path, doc: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _probe_child(json_out: Path | None) -> int:
    try:
        doc, errors = probe()
    except Exception as exc:
        doc = {
            "errors": [f"{type(exc).__name__}: {exc}"],
            "verdict": "failed",
        }
        errors = list(doc["errors"])
    if json_out is not None:
        _write_json(json_out, doc)
    print(json.dumps(doc, indent=2, sort_keys=True))
    if errors:
        print("runtime compiler assertions failed:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-child", action="store_true")
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--python-prefix", type=Path)
    parser.add_argument("--torch-prefix", type=Path)
    parser.add_argument("--triton-prefix", type=Path)
    parser.add_argument("--runtime-prefix", action="append", default=[])
    args = parser.parse_args(argv)

    if args.probe_child:
        return _probe_child(args.json_out)

    env = runtime_env(
        os.environ,
        out_dir=args.out_dir,
        python_prefix=args.python_prefix,
        torch_prefix=args.torch_prefix,
        triton_prefix=args.triton_prefix,
        runtime_prefixes=_parse_prefixed_paths(args.runtime_prefix),
    )
    python = str(args.python_prefix / "bin" / "python3") if args.python_prefix else sys.executable
    command = [python, str(Path(__file__).resolve()), "--probe-child"]
    if args.json_out is not None:
        command.extend(["--json-out", str(args.json_out)])
    completed = subprocess.run(command, check=False, env=env)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
