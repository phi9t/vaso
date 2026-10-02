#!/usr/bin/env python3
"""Import the jaxlib-deps Python closure without importing jax or jaxlib."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable, Mapping


DEFAULT_MODULES = (
    "absl",
    "beniget",
    "build",
    "calver",
    "Cython",
    "flit_core",
    "gast",
    "hatch_fancy_pypi_readme",
    "hatch_vcs",
    "hatchling",
    "mesonpy",
    "ml_dtypes",
    "numpy",
    "opt_einsum",
    "packaging",
    "pathspec",
    "pip",
    "pluggy",
    "ply",
    "pybind11",
    "pyproject_hooks",
    "pyproject_metadata",
    "pythran",
    "scikit_build_core",
    "scipy",
    "setuptools",
    "setuptools_scm",
    "trove_classifiers",
    "wheel",
)
FORBIDDEN_MODULES = frozenset({"jax", "jaxlib"})


def check_imports(
    modules: Iterable[str],
    *,
    importer: Callable[[str], object] = importlib.import_module,
) -> dict[str, object]:
    imported: list[str] = []
    failed: list[dict[str, str]] = []
    for module in modules:
        if module in FORBIDDEN_MODULES:
            failed.append({"module": module, "error": "forbidden jax profile module"})
            continue
        try:
            importer(module)
        except Exception as exc:
            failed.append({"module": module, "error": f"{type(exc).__name__}: {exc}"})
        else:
            imported.append(module)
    return {
        "modules": list(modules),
        "imported": imported,
        "failed": failed,
        "verdict": "passed" if not failed else "failed",
    }


def _site_packages(prefix: Path) -> Path:
    matches = sorted((prefix / "lib").glob("python*/site-packages"))
    if len(matches) != 1:
        raise SystemExit(f"{prefix} must provide exactly one lib/pythonX.Y/site-packages tree")
    return matches[0]


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


def import_env(
    base_environ: Mapping[str, str],
    *,
    python_prefix: Path | None,
    runtime_prefixes: Iterable[tuple[str, Path]],
) -> dict[str, str]:
    env = dict(base_environ)
    pythonpath = [str(_site_packages(prefix)) for _, prefix in runtime_prefixes]
    ld_entries = [
        str(prefix / libdir)
        for _, prefix in runtime_prefixes
        for libdir in ("lib", "lib64")
        if (prefix / libdir).is_dir()
    ]
    path_entries = [str(python_prefix / "bin")] if python_prefix is not None else []
    path_entries.extend(str(prefix / "bin") for _, prefix in runtime_prefixes if (prefix / "bin").is_dir())
    if env.get("PYTHONPATH"):
        pythonpath.extend(item for item in env["PYTHONPATH"].split(os.pathsep) if item)
    if env.get("LD_LIBRARY_PATH"):
        ld_entries.extend(item for item in env["LD_LIBRARY_PATH"].split(os.pathsep) if item)
    if env.get("PATH"):
        path_entries.append(env["PATH"])
    env.update(
        {
            "PYTHONPATH": os.pathsep.join(_dedupe(pythonpath)),
            "LD_LIBRARY_PATH": os.pathsep.join(_dedupe(ld_entries)),
            "PATH": os.pathsep.join(_dedupe(path_entries)),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return env


def _write_json(path: Path, doc: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _child(modules: list[str], json_out: Path | None) -> int:
    result = check_imports(modules)
    if json_out is not None:
        _write_json(json_out, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["verdict"] != "passed":
        failed = [f"{item['module']}: {item['error']}" for item in result["failed"]]  # type: ignore[index]
        print("jaxlib-deps import check failed:\n  " + "\n  ".join(failed), file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", action="append", default=[])
    parser.add_argument("--runtime-prefix", action="append", default=[])
    parser.add_argument("--python-prefix", type=Path)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--child", action="store_true")
    args = parser.parse_args(argv)

    modules = args.module or list(DEFAULT_MODULES)
    if args.child:
        return _child(modules, args.json_out)

    env = import_env(
        os.environ,
        python_prefix=args.python_prefix,
        runtime_prefixes=_parse_prefixed_paths(args.runtime_prefix),
    )
    python = str(args.python_prefix / "bin" / "python3") if args.python_prefix is not None else sys.executable
    command = [python, str(Path(__file__).resolve()), "--child"]
    for module in modules:
        command.extend(["--module", module])
    if args.json_out is not None:
        command.extend(["--json-out", str(args.json_out)])
    return subprocess.run(command, check=False, env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
