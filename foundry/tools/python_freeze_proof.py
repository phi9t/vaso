#!/usr/bin/env python3
"""Prove an install prefix carries no Python-ABI-bound artifacts.

The py-* freeze (.scratch/pytorch-frontier-convergence, ruling on issue 11)
blocks packages whose installed tree binds to a concrete CPython line, because
the PyTorch closure moves from 3.14 to python@3.13.13. It does not block plain
Python source that some other program's embedded interpreter loads, such as
Valgrind's GDB monitor scripts, or Spack's own `.spack/` metadata.

Rejected (exit 1):
  site-packages   a site-packages/ or dist-packages/ directory
  python-abi-path a path segment naming a CPython line (python3.X)
  extension-module  *.cpython-3XY*.so or *.abi3.so
  python-shebang  an executable whose shebang pins a Python interpreter: an
                  absolute interpreter path (#!/prefix/bin/python3) or a
                  versioned name (#!/usr/bin/env python3.14)
  libpython-needed  an ELF file with libpython in its DT_NEEDED entries

Allowed, and reported with --notes (record them in the recipe evidence):
  note-plain-python-source  non-executable .py files outside those locations,
                            e.g. Valgrind's GDB monitor scripts
  note-env-python-script    executables run via `#!/usr/bin/env python3`,
                            e.g. Valgrind's cg_annotate; they bind to no
                            prefix or CPython line
  note-system-python-script executables pinned to a system interpreter
                            (#!/usr/bin/python, #!/bin/python3), e.g. git's
                            git-p4: a hermeticity smell, not our Python line
Everything under .spack/ is ignored.

Run inside the insula against both the Spack reference prefix and the native
candidate prefix before flipping a package that the freeze allows.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


PYTHON_ABI_SEGMENT = re.compile(r"^python3\.\d+t?$")
EXTENSION_MODULE = re.compile(r"\.(cpython-3\d+[^/]*|abi3)\.so(\.\d+)*$")
ENV_SHEBANG = re.compile(rb"^#!\s*/usr/bin/env\s+(?:-\S+\s+)*(\S+)")
DIRECT_SHEBANG = re.compile(rb"^#!\s*(\S+)")
PYTHON_NAME = re.compile(rb"^python(3(\.\d+t?)?)?$")


@dataclass(frozen=True)
class Finding:
    kind: str
    path: str


def classify_shebang(first_line: bytes) -> str | None:
    """'pinned' (prefix- or version-bound), 'env' (env python3), 'system' (/usr/bin/python)."""
    env = ENV_SHEBANG.match(first_line)
    if env:
        name = env.group(1).rsplit(b"/", 1)[-1]
        if not PYTHON_NAME.match(name):
            return None
        return "pinned" if b"." in name else "env"
    direct = DIRECT_SHEBANG.match(first_line)
    if direct and PYTHON_NAME.match(direct.group(1).rsplit(b"/", 1)[-1]):
        directory = direct.group(1).rsplit(b"/", 1)[0]
        name = direct.group(1).rsplit(b"/", 1)[-1]
        if directory in (b"/usr/bin", b"/bin") and b"." not in name:
            return "system"
        return "pinned"
    return None


def _readelf_needed(path: Path) -> list[str]:
    result = subprocess.run(["readelf", "-d", str(path)], capture_output=True, text=True)
    if result.returncode != 0:
        return []
    return re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", result.stdout)


def _is_elf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) == b"\x7fELF"
    except OSError:
        return False


def scan(
    prefix: Path,
    needed: Callable[[Path], list[str]] = _readelf_needed,
    include_notes: bool = False,
) -> list[Finding]:
    findings: list[Finding] = []
    flagged_dirs: list[str] = []
    for path in sorted(prefix.rglob("*")):
        rel = path.relative_to(prefix)
        parts = rel.parts
        if parts and parts[0] == ".spack":
            continue
        rel_text = rel.as_posix()
        if any(rel_text.startswith(flagged + "/") for flagged in flagged_dirs):
            continue  # inside an already-reported directory: report the outermost only
        if path.is_symlink():
            # Spack prefixes hold absolute symlinks that only resolve inside the
            # insula; never follow them. The target, if inside the prefix, is
            # scanned on its own; the link name can still be ABI-bound.
            if PYTHON_ABI_SEGMENT.match(path.name) or path.name in {"site-packages", "dist-packages"}:
                findings.append(Finding("python-abi-path", rel_text))
            elif EXTENSION_MODULE.search(path.name):
                findings.append(Finding("extension-module", rel_text))
            continue
        if path.is_dir():
            if PYTHON_ABI_SEGMENT.match(path.name):
                findings.append(Finding("python-abi-path", rel_text))
                flagged_dirs.append(rel_text)
            elif path.name in {"site-packages", "dist-packages"}:
                findings.append(Finding("site-packages", rel_text))
                flagged_dirs.append(rel_text)
            continue
        if EXTENSION_MODULE.search(path.name):
            findings.append(Finding("extension-module", rel_text))
            continue
        executable = path.stat().st_mode & 0o111
        if executable and not _is_elf(path):
            try:
                with path.open("rb") as handle:
                    first = handle.readline(256)
            except OSError:
                first = b""
            shebang = classify_shebang(first)
            if shebang == "pinned":
                findings.append(Finding("python-shebang", rel_text))
                continue
            if shebang in ("env", "system"):
                if include_notes:
                    findings.append(Finding(f"note-{shebang}-python-script", rel_text))
                continue
        if _is_elf(path) and any(lib.startswith("libpython") for lib in needed(path)):
            findings.append(Finding("libpython-needed", rel_text))
            continue
        if include_notes and path.suffix == ".py" and not executable:
            findings.append(Finding("note-plain-python-source", rel_text))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("prefix", type=Path)
    parser.add_argument("--notes", action="store_true", help="also list allowed plain Python sources")
    parser.add_argument("--no-readelf", action="store_true", help="skip the DT_NEEDED check (tests only)")
    args = parser.parse_args(argv)
    if not args.prefix.is_dir():
        print(f"input error: {args.prefix} is not a directory", file=sys.stderr)
        return 2
    findings = scan(
        args.prefix,
        needed=(lambda path: []) if args.no_readelf else _readelf_needed,
        include_notes=args.notes,
    )
    blocking = [finding for finding in findings if not finding.kind.startswith("note-")]
    for finding in findings:
        print(f"{finding.kind}: {finding.path}", file=sys.stderr if finding in blocking else sys.stdout)
    if blocking:
        print(f"FROZEN: {len(blocking)} Python-ABI-bound artifact(s) in {args.prefix}", file=sys.stderr)
        return 1
    print(f"ALLOWED: no Python-ABI-bound artifacts in {args.prefix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
